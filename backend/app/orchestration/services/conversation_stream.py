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
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select

from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.db.models.conversation import Conversation, Message
from app.llm.models import Message as LLMMessage
from app.llm.models import MessageRole, ToolCall, ToolSchema
from app.tools.agent import ToolContext, get_tool, invoke_tool, list_tools

logger = get_logger(__name__)

#: 会话系统提示（P0 只做定位与边界声明；工具清单 P1 接入）
SYSTEM_PROMPT = (
    "你是 VeriPaper 的研究助理，帮助用户检索文献、生成带引用的文献综述。"
    "回答用简体中文，简洁、有据可依；不确定时直说不确定，不要编造文献。"
    "涉及具体综述任务时，提示用户可以新建任务（左侧「新建任务」或直接说主题）。"
)

#: 单轮上下文回溯上限（沿 parent 链；更早的轮次不进上下文——P1 接会话摘要）
MAX_CONTEXT_MESSAGES = 20

#: 工具循环的最大轮数（模型→工具→模型）。超过后最后一次不带工具收口。
MAX_TOOL_ROUNDS = 4

#: 单条回答里最多提议几次写操作（防止「确认一次又提一次」无限套娃）
MAX_WRITE_PROPOSALS = 5

#: 工具使用策略（只在启用工具时插入系统提示）
TOOL_POLICY = (
    "\n\n你可以调用工具替用户完成操作：查询任务/配额/引用库/知识库、联网搜索、"
    "创建或取消综述任务、导入文献。"
    "只读工具可直接调用；创建/取消/重试/导入等写操作会先请用户确认，"
    "不要在回复里假装已经执行。工具结果以实际返回为准，不要编造。"
    "\n写操作被用户确认后，你会**自动收到执行结果并继续这一轮回答**，"
    "因此需要多步才能完成的目标（例如「建任务 → 确认规划 → 重试节点」）"
    "应逐步进行：先提议下一步，拿到结果再决定下一步，不要一次抛出一串操作，"
    "也不要在用户拒绝后重复提议同一个操作。"
)

#: 周期落库的 delta 计数（客户端断连/进程被杀时，已生成内容不丢）
PARTIAL_FLUSH_EVERY = 20

# ===== 协同停止（生产实测 2026-11-19：仅靠客户端 abort 感知不到断开）=====
# 循环挂在 `async for delta` 上时没有 send，客户端断开不会立刻触发取消；
# 必须由停止端点写入进程内标志，循环每次迭代检查后主动退出。
_STOP_REQUESTS: dict[str, float] = {}
_STOP_TTL_S = 300.0


def request_stop(message_id: uuid.UUID | str) -> None:
    """登记停止请求（TTL 5 分钟，防僵尸条目）。"""
    _STOP_REQUESTS[str(message_id)] = time.monotonic()


def _should_stop(message_id: uuid.UUID | str) -> bool:
    key = str(message_id)
    stamp = _STOP_REQUESTS.get(key)
    if stamp is None:
        return False
    if time.monotonic() - stamp > _STOP_TTL_S:
        _STOP_REQUESTS.pop(key, None)
        return False
    return True


def _clear_stop(message_id: uuid.UUID | str) -> None:
    _STOP_REQUESTS.pop(str(message_id), None)


def _is_connection_error(exc: Exception) -> bool:
    """连接类异常（客户端断开/取消）→ 记 interrupted 而不是 failed。"""
    name = type(exc).__name__
    return (
        isinstance(exc, (BrokenPipeError, ConnectionError, GeneratorExit))
        or "Broken" in name
        or "Cancel" in name
        or "Disconnect" in name
    )


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
        content = row.content
        # 附件注入（P2）：模型需要知道「这条消息带了哪些知识库文档」，
        # 否则用户说「用这篇建综述」时它无从指认。只注入标题与 document_id
        # （供后续工具调用使用），**不注入正文**——正文检索走知识库工具，
        # 直接把全文塞进上下文会打穿 token 预算。
        attachments = row.attachments or []
        if row.role == "user" and attachments:
            lines = "\n".join(
                f"- {item.get('title', '未命名文档')}（document_id={item.get('document_id', '')}）"
                for item in attachments
            )
            content = f"{content}\n\n[本条消息附带的知识库文档]\n{lines}"
        llm_messages.append(
            LLMMessage(
                role=MessageRole.USER if row.role == "user" else MessageRole.ASSISTANT,
                content=content,
            )
        )
    return llm_messages


def _replay_tool_history(
    context: list[LLMMessage],
    records: list[dict[str, Any]],
    assistant_text: str,
) -> None:
    """把已执行工具按 OpenAI 协议回灌：assistant(tool_calls) + 逐条 tool。

    为什么必须成对：协议要求每个 `tool_call_id` 都能找到对应的 tool 消息，
    少一条就 400。因此只回放**已有结果**的记录——尚未确认的写操作不能出现在
    assistant.tool_calls 里，否则上下文非法。
    """
    executed = [item for item in records if item.get("id") and item.get("result_preview")]
    if not executed:
        return
    context.append(
        LLMMessage(
            role=MessageRole.ASSISTANT,
            content=assistant_text or None,
            tool_calls=[
                ToolCall(
                    id=str(item["id"]),
                    name=str(item.get("name", "")),
                    arguments=json.dumps(item.get("arguments") or {}, ensure_ascii=False),
                )
                for item in executed
            ],
        )
    )
    for item in executed:
        context.append(
            LLMMessage(
                role=MessageRole.TOOL,
                content=str(item["result_preview"]),
                tool_call_id=str(item["id"]),
                name=str(item.get("name", "")),
            )
        )


def _was_rejected(records: list[dict[str, Any]], name: str, args: dict[str, Any]) -> bool:
    """同一操作被用户拒绝过就不再打扰：拒绝后重复弹同一张卡是最糟的体验。"""
    return any(
        item.get("status") == "rejected"
        and item.get("name") == name
        and (item.get("arguments") or {}) == args
        for item in records
    )


class ConversationStreamer:
    """把 assistant 消息的生成过程编排成 SSE 帧，并负责持久化终态。

    ## 工具循环（P3）

    启用工具时走 `stream_chat_events`：文本照常逐 token 下发；一轮结束后若
    模型要求调用工具，只读工具立即执行并把结果回灌，继续下一轮；写工具
    **暂停**为 `waiting_approval`，由用户在前端确认后执行。

    为什么不让模型直接执行写操作：一次幻觉触发的「取消任务」不可恢复。
    """

    def __init__(
        self,
        session_factory: Any,
        llm: Any,
        *,
        tool_context: ToolContext | None = None,
        tools_enabled: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._tool_context = tool_context
        self._tools_enabled = tools_enabled

    def _tool_schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=item["name"],
                description=item["summary"],
                parameters=item["parameters"],
            )
            for item in list_tools()
        ]

    async def stream(
        self,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        *,
        resume: bool = False,
    ) -> AsyncIterator[str]:
        """生成 SSE 流：start → (delta | tool_call | tool_result)* → done/error。

        `resume=True` 是「写操作确认后的续跑」：保留已有正文与工具轨迹，把已执行
        工具的结果按 `assistant(tool_calls)` + `tool` 的顺序回灌，再让模型继续
        **同一条消息**的生成。多步写操作链（建任务 → 确认规划 → 重试节点）靠它
        才能连贯——否则用户每确认一步都要重新发一句话，模型也看不到上一步结果。
        """
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

            use_tools = self._tools_enabled and self._tool_context is not None
            context = await build_context(session, message)
            if use_tools and context:
                # ⚠️ 必须**合并**进原系统提示，不能追加第二条 system：
                # 平台网关（Qwen）只允许 system 出现在最前且不接受第二条，
                # 否则 400「System message must be at the beginning」。
                context[0] = LLMMessage(
                    role=MessageRole.SYSTEM,
                    content=f"{context[0].content or ''}{TOOL_POLICY}",
                )
            tools = self._tool_schemas() if use_tools else None

            buffer: list[str] = []
            records: list[dict[str, Any]] = []
            if resume:
                if not (message.tool_calls or []):
                    yield sse(
                        "error",
                        {"code": "nothing_to_resume", "message": "没有可续跑的工具结果"},
                    )
                    return
                buffer.append(message.content or "")
                records = [dict(item) for item in (message.tool_calls or [])]
                _replay_tool_history(context, records, "".join(buffer))

            message.status = "streaming"
            message.pending_tool = None
            if not resume:
                message.content = ""
                message.error = None
                message.tool_calls = []
            await session.commit()

            state: dict[str, bool] = {"paused": False, "stopped": False}
            yield sse(
                "start",
                {
                    "message_id": str(message.id),
                    "model": self._llm.model,
                    "tools": use_tools,
                    "resumed": resume,
                },
            )

            try:
                _clear_stop(message_id)
                async for frame in self._run_tool_loop(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    context=context,
                    tools=tools,
                    buffer=buffer,
                    records=records,
                    state=state,
                ):
                    yield frame

                # 暂停等确认 / 用户停止：都已落库终态，这里不能再覆盖
                if state["paused"] or state["stopped"]:
                    return

                text = "".join(buffer)
                await self._finalize(
                    message_id,
                    text,
                    "completed",
                    None,
                    tool_calls=records,
                    model=self._llm.model,
                    touch_conversation=conversation_id,
                )
                logger.info(
                    "conversation_message_completed",
                    conversation_id=str(conversation_id),
                    message_id=str(message_id),
                    chars=len(text),
                    tool_calls=len(records),
                    resumed=resume,
                )
                yield sse(
                    "done",
                    {
                        "message_id": str(message_id),
                        "chars": len(text),
                        "tool_calls": len(records),
                    },
                )
            except asyncio.CancelledError:
                await self._finalize(
                    message_id, "".join(buffer), "interrupted", None, tool_calls=records
                )
                logger.warning(
                    "conversation_message_interrupted",
                    conversation_id=str(conversation_id),
                    message_id=str(message_id),
                    chars=len("".join(buffer)),
                )
                raise
            except Exception as exc:
                error_text = str(exc)[:500] or "生成失败"
                if _should_stop(message_id) or _is_connection_error(exc):
                    await self._finalize(
                        message_id, "".join(buffer), "interrupted", None, tool_calls=records
                    )
                    _clear_stop(message_id)
                    logger.info(
                        "conversation_message_interrupted",
                        conversation_id=str(conversation_id),
                        message_id=str(message_id),
                        chars=len("".join(buffer)),
                    )
                    yield sse("done", {"message_id": str(message_id), "interrupted": True})
                    return
                await self._finalize(
                    message_id, "".join(buffer), "failed", error_text, tool_calls=records
                )
                logger.error(
                    "conversation_message_failed",
                    conversation_id=str(conversation_id),
                    message_id=str(message_id),
                    error=error_text,
                )
                yield sse("error", {"code": "llm_error", "message": error_text})

    async def _run_tool_loop(
        self,
        *,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        context: list[LLMMessage],
        tools: list[ToolSchema] | None,
        buffer: list[str],
        records: list[dict[str, Any]],
        state: dict[str, bool],
    ) -> AsyncIterator[str]:
        """模型 ↔ 工具的多轮循环（首次生成与确认后续跑共用同一实现）。

        暂停信号通过 `state["paused"]` 回传，而不是用返回值——异步生成器拿不到
        `return` 值，用状态位最直白。
        """
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            round_tools = tools if round_index < MAX_TOOL_ROUNDS else None
            tool_calls = None
            delta_count = 0
            async for event in self._llm.stream_chat_events(
                context,
                tools=round_tools,
                max_tokens=2000,
            ):
                if event.type == "delta" and event.text:
                    if _should_stop(message_id):
                        await self._finalize(
                            message_id,
                            "".join(buffer),
                            "interrupted",
                            "用户停止生成",
                            tool_calls=records,
                        )
                        _clear_stop(message_id)
                        state["stopped"] = True
                        logger.info(
                            "conversation_message_stopped",
                            conversation_id=str(conversation_id),
                            message_id=str(message_id),
                            chars=len("".join(buffer)),
                        )
                        yield sse("done", {"message_id": str(message_id), "interrupted": True})
                        return
                    buffer.append(event.text)
                    delta_count += 1
                    if delta_count % PARTIAL_FLUSH_EVERY == 0:
                        await self._persist_partial(message_id, "".join(buffer))
                    yield sse("delta", {"text": event.text})
                elif event.type == "done":
                    tool_calls = event.tool_calls

            if not tool_calls:
                return

            # 把「模型要调用工具」这一轮写回上下文，保持多轮连贯
            context.append(
                LLMMessage(
                    role=MessageRole.ASSISTANT,
                    content="".join(buffer) or None,
                    tool_calls=tool_calls,
                )
            )

            for call in tool_calls:
                args = _parse_arguments(call.arguments)
                try:
                    spec = get_tool(call.name)
                    summary = spec.summary
                    write = spec.write
                except AppException:
                    spec = None
                    summary = call.name
                    write = False

                record: dict[str, Any] = {
                    "id": call.id,
                    "name": call.name,
                    "summary": summary,
                    "status": "ok",
                    "arguments": args,
                    "result_preview": None,
                    "write": write,
                }
                records.append(record)
                yield sse(
                    "tool_call",
                    {"id": call.id, "name": call.name, "summary": summary, "write": write},
                )

                if spec is None:
                    record["status"] = "error"
                    record["result_preview"] = "未知工具"
                    context.append(
                        LLMMessage(
                            role=MessageRole.TOOL,
                            content=json.dumps({"error": "未知工具"}, ensure_ascii=False),
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                    yield sse(
                        "tool_result",
                        {"id": call.id, "name": call.name, "ok": False, "preview": "未知工具"},
                    )
                    continue

                if write:
                    blocked, reason = self._write_blocked(records, call.name, args)
                    if blocked:
                        # 被拒过 / 超过上限：不弹卡，直接把原因喂回模型让它改主意
                        record["status"] = "error"
                        record["result_preview"] = json.dumps({"error": reason}, ensure_ascii=False)
                        context.append(
                            LLMMessage(
                                role=MessageRole.TOOL,
                                content=str(record["result_preview"]),
                                tool_call_id=call.id,
                                name=call.name,
                            )
                        )
                        yield sse(
                            "tool_result",
                            {"id": call.id, "name": call.name, "ok": False, "preview": reason},
                        )
                        continue

                    # 写操作：暂停，交前端确认卡；确认后由 resume 续跑
                    record["status"] = "pending_approval"
                    pending = {
                        "id": call.id,
                        "name": call.name,
                        "summary": summary,
                        "arguments": args,
                    }
                    await self._save_pending(message_id, "".join(buffer), records, pending)
                    state["paused"] = True
                    logger.info(
                        "conversation_tool_approval_required",
                        conversation_id=str(conversation_id),
                        message_id=str(message_id),
                        tool=call.name,
                        proposals=sum(1 for item in records if item.get("write")),
                    )
                    yield sse("tool_approval", pending)
                    yield sse(
                        "done",
                        {
                            "message_id": str(message_id),
                            "status": "waiting_approval",
                            "tool": call.name,
                        },
                    )
                    return

                # use_tools 为真时 tool_context 必非空（构造参数保证）
                assert self._tool_context is not None
                try:
                    outcome = await invoke_tool(
                        self._tool_context, call.name, args, confirmed=False
                    )
                    result: Any = outcome.get("result", {})
                    record["status"] = "ok"
                except AppException as exc:
                    result = {"error": exc.message}
                    record["status"] = "error"
                except Exception as exc:
                    result = {"error": str(exc)[:300]}
                    record["status"] = "error"

                preview = json.dumps(result, ensure_ascii=False, default=str)[:600]
                record["result_preview"] = preview
                context.append(
                    LLMMessage(
                        role=MessageRole.TOOL,
                        content=preview,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
                yield sse(
                    "tool_result",
                    {
                        "id": call.id,
                        "name": call.name,
                        "ok": record["status"] == "ok",
                        "preview": preview,
                    },
                )

            await self._persist_tools(message_id, records)

    @staticmethod
    def _write_blocked(
        records: list[dict[str, Any]],
        name: str,
        args: dict[str, Any],
    ) -> tuple[bool, str]:
        """判断这次写提议是否该被拦下（不弹确认卡）。"""
        if _was_rejected(records, name, args):
            return True, "用户已拒绝过这个操作，不要重复提议；改用文字说明或换个做法。"
        proposals = sum(1 for item in records if item.get("write"))
        if proposals > MAX_WRITE_PROPOSALS:
            return True, f"本次回答的写操作已达上限（{MAX_WRITE_PROPOSALS} 次），请直接总结给用户。"
        return False, ""

    async def _persist_partial(self, message_id: uuid.UUID, text: str) -> None:
        """周期落库：保持 streaming 状态，只更新已生成内容。"""
        async with self._session_factory() as session:
            row = await session.get(Message, message_id)
            if row is None or row.status != "streaming":
                return
            row.content = text
            await session.commit()

    async def _persist_tools(self, message_id: uuid.UUID, records: list[dict[str, Any]]) -> None:
        async with self._session_factory() as session:
            row = await session.get(Message, message_id)
            if row is None or row.status != "streaming":
                return
            row.tool_calls = list(records)
            await session.commit()

    async def _save_pending(
        self,
        message_id: uuid.UUID,
        text: str,
        records: list[dict[str, Any]],
        pending: dict[str, Any],
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(Message, message_id)
            if row is None:
                return
            row.content = text
            row.tool_calls = list(records)
            row.pending_tool = pending
            row.status = "waiting_approval"
            row.model = self._llm.model
            await session.commit()

    async def _finalize(
        self,
        message_id: uuid.UUID,
        text: str,
        status: str,
        error: str | None,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        model: str | None = None,
        touch_conversation: uuid.UUID | None = None,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(Message, message_id)
            if row is None:
                return
            row.content = text
            row.status = status
            row.error = error
            if tool_calls is not None:
                row.tool_calls = list(tool_calls)
            if model is not None:
                row.model = model
            if touch_conversation is not None:
                conversation = await session.get(Conversation, touch_conversation)
                if conversation is not None:
                    conversation.last_message_at = row.updated_at
            await session.commit()


def _parse_arguments(raw: str) -> dict[str, Any]:
    """工具参数容错解析：空/非法 JSON 一律给空 dict，由工具校验报可读错误。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        return {}


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
