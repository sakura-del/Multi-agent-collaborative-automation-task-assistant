"""会话流式生成（SSE）：上下文组装 → LLM 流 → 增量落库 → 终态收敛。

## 与任务执行的关系

聊天**不**复制 DAG 编排：会话层只做「多轮对话 + 文本生成」；
真正的综述执行仍由 `TaskScheduler` 负责（P1 的工具调用会创建任务并挂进会话）。
所以这里没有任何节点/子任务概念，只有消息。

## 中断与错误语义（P0 必须做对）

- **客户端断开**（关小窗/切会话）：已生成的内容必须留住 → `interrupted`；
- **上游错误**：`failed` + 可读 error，前端可点重试；
- **正常结束**：`completed` + 用量；
- 消息状态机：pending → streaming → completed / interrupted / failed。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.conversation import Conversation, Message
from app.llm.models import Message as LLMMessage
from app.llm.models import MessageRole

logger = get_logger(__name__)

#: 会话系统提示（P0 只做定位与边界声明；工具清单 P1 接入）
SYSTEM_PROMPT = (
    "你是 VeriPaper 的研究助理，帮助用户检索文献、生成带引用的文献综述。"
    "回答用简体中文，简洁、有据可依；不确定时直说不确定，不要编造文献。"
    "涉及具体综述任务时，提示用户可以新建任务（左侧「新建任务」或直接说主题）。"
)

#: 单轮上下文回溯上限（沿 parent 链；更早的轮次不进上下文——P1 接会话摘要）
MAX_CONTEXT_MESSAGES = 20


def sse(event: str, data: dict[str, Any]) -> str:
    """SSE 帧：`event:` 行 + 单行 JSON data（含换行的 JSON 会破坏协议，必须紧凑化）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def build_context(session: Any, message: Message) -> list[LLMMessage]:
    """沿 parent 链回溯组装上下文（祖先 → 当前消息的父节点），倒序再翻转。"""
    chain: list[Message] = []
    cursor: uuid.UUID | None = message.parent_id
    while cursor is not None and len(chain) < MAX_CONTEXT_MESSAGES:
        row = await session.get(Message, cursor)
        if row is None or row.conversation_id != message.conversation_id:
            break
        chain.append(row)
        cursor = row.parent_id
    chain.reverse()

    llm_messages: list[LLMMessage] = [LLMMessage(role=MessageRole.SYSTEM, content=SYSTEM_PROMPT)]
    for row in chain:
        if not row.content.strip():
            continue
        llm_messages.append(
            LLMMessage(
                role=MessageRole.USER if row.role == "user" else MessageRole.ASSISTANT,
                content=row.content,
            )
        )
    return llm_messages


class ConversationStreamer:
    """把 assistant 消息的生成过程编排成 SSE 帧，并负责持久化终态。"""

    def __init__(self, session_factory: Any, llm: Any) -> None:
        self._session_factory = session_factory
        self._llm = llm

    async def stream(self, conversation_id: uuid.UUID, message_id: uuid.UUID) -> AsyncIterator[str]:
        """生成 SSE 流。前 3 个事件固定：start → delta* → done/error。"""
        async with self._session_factory() as session:
            message = await session.get(Message, message_id)
            if message is None or message.conversation_id != conversation_id:
                yield sse("error", {"code": "not_found", "message": "消息不存在"})
                return
            if message.role != "assistant":
                yield sse("error", {"code": "bad_role", "message": "只能生成 assistant 消息"})
                return
            if message.status == "streaming":
                yield sse("error", {"code": "already_streaming", "message": "该消息正在生成中"})
                return

            context = await build_context(session, message)
            message.status = "streaming"
            message.content = ""
            message.error = None
            await session.commit()

            yield sse("start", {"message_id": str(message.id), "model": self._llm.model})

            buffer: list[str] = []
            try:
                async for delta in self._llm.stream_chat(
                    context,
                    max_tokens=2000,
                ):
                    if not delta:
                        continue
                    buffer.append(delta)
                    yield sse("delta", {"text": delta})
                text = "".join(buffer)
                async with self._session_factory() as finish_session:
                    row = await finish_session.get(Message, message_id)
                    if row is not None:
                        row.content = text
                        row.status = "completed"
                        row.model = self._llm.model
                        conv = await finish_session.get(Conversation, conversation_id)
                        if conv is not None:
                            conv.last_message_at = row.updated_at
                        await finish_session.commit()
                logger.info(
                    "conversation_message_completed",
                    conversation_id=str(conversation_id),
                    message_id=str(message_id),
                    chars=len(text),
                )
                yield sse("done", {"message_id": str(message_id), "chars": len(text)})
            except asyncio.CancelledError:
                # 客户端断开 / 服务端取消：保留已生成部分
                await self._finalize(message_id, "".join(buffer), "interrupted", None)
                logger.warning(
                    "conversation_message_interrupted",
                    conversation_id=str(conversation_id),
                    message_id=str(message_id),
                    chars=len("".join(buffer)),
                )
                raise
            except Exception as exc:
                error_text = str(exc)[:500] or "生成失败"
                await self._finalize(message_id, "".join(buffer), "failed", error_text)
                logger.error(
                    "conversation_message_failed",
                    conversation_id=str(conversation_id),
                    message_id=str(message_id),
                    error=error_text,
                )
                yield sse("error", {"code": "llm_error", "message": error_text})

    async def _finalize(
        self, message_id: uuid.UUID, text: str, status: str, error: str | None
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(Message, message_id)
            if row is None:
                return
            row.content = text
            row.status = status
            row.error = error
            await session.commit()


async def latest_leaf(session: Any, conversation_id: uuid.UUID) -> uuid.UUID | None:
    """取会话的最新叶子消息（无子节点、最新创建）。用于列表默认分支。"""
    rows = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = rows.scalars().all()
    parents = {m.parent_id for m in messages if m.parent_id is not None}
    leaves = [m.id for m in messages if m.id not in parents]
    return leaves[-1] if leaves else None
