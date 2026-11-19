"""会话工具循环回归（P3）。

⚠️ 铁律 #0：本机不跑 pytest；在测试机 10.0.10.2 执行。

覆盖：
- 只读工具：自动执行 → tool_call/tool_result 事件 → 模型拿到结果后继续回答；
- 写工具：**不执行**，消息落库为 waiting_approval + pending_tool；
- 工具轨迹持久化（刷新后仍能看到执行过程）。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.db.models.conversation import Conversation, Message
from app.llm.models import StreamEvent, ToolCall
from app.orchestration.services.conversation_stream import ConversationStreamer
from app.tools.agent import ToolContext


class _Ctx:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _Session:
    def __init__(self, rows: dict[uuid.UUID, Any]) -> None:
        self.rows = rows

    async def get(self, model: Any, key: uuid.UUID) -> Any:
        return self.rows.get(key)

    async def commit(self) -> None:
        return None


class _Factory:
    def __init__(self, session: _Session) -> None:
        self._session = session

    def __call__(self) -> _Ctx:
        return _Ctx(self._session)


class _PlanStub:
    async def get_usage(self, user_id: uuid.UUID) -> dict[str, Any]:
        return {
            "plan_name": "专业版",
            "period_key": "2026-11",
            "reviews_used": 3,
            "monthly_reviews": 50,
            "reviews_remaining": 47,
            "period_end": None,
        }


class _LLMStub:
    model = "stub-model"

    def __init__(self, rounds: list[list[StreamEvent]]) -> None:
        self.rounds = rounds
        self.calls = 0

    async def stream_chat_events(self, messages: Any, **kwargs: Any) -> Any:
        events = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        for event in events:
            yield event


def _fixture(
    pending: bool = False,
) -> tuple[ConversationStreamer, Message, dict[uuid.UUID, Any], _LLMStub]:
    conversation = Conversation(id=uuid.uuid4(), user_id=uuid.uuid4(), title="t")
    user = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        parent_id=None,
        role="user",
        content="我还剩几篇？",
        status="completed",
        attachments=[],
        tool_calls=[],
    )
    assistant = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        parent_id=user.id,
        role="assistant",
        content="",
        status="pending",
        attachments=[],
        tool_calls=[],
        pending_tool=None,
    )
    rows: dict[uuid.UUID, Any] = {
        user.id: user,
        assistant.id: assistant,
        conversation.id: conversation,
    }
    name = "create_task" if pending else "get_quota"
    arguments = '{"topic":"多智能体协作综述","keywords":["多智能体"]}' if pending else "{}"
    rounds = [
        [
            StreamEvent(
                type="done",
                tool_calls=[ToolCall(id="call-1", name=name, arguments=arguments)],
            )
        ],
        [StreamEvent(type="delta", text="已完成。"), StreamEvent(type="done")],
    ]
    llm = _LLMStub(rounds)
    context = ToolContext(
        db=None,  # type: ignore[arg-type]
        user_id=conversation.user_id,
        execution=None,  # type: ignore[arg-type]
        plan_service=_PlanStub(),  # type: ignore[arg-type]
        quota=None,
        workspace_ids=(),
    )
    streamer = ConversationStreamer(
        _Factory(_Session(rows)), llm, tool_context=context, tools_enabled=True
    )
    return streamer, assistant, rows, llm


async def _collect(streamer: ConversationStreamer, assistant: Message) -> list[str]:
    return [frame async for frame in streamer.stream(assistant.conversation_id, assistant.id)]


async def test_read_tool_executes_and_loop_continues() -> None:
    streamer, assistant, _, llm = _fixture()
    frames = await _collect(streamer, assistant)

    joined = "".join(frames)
    assert '"tool":"get_quota"' not in joined  # 事件里是 name 而不是信封
    assert "event: tool_call" in joined and "get_quota" in joined
    assert "event: tool_result" in joined
    assert "event: delta" in joined
    assert llm.calls == 2, "只读工具执行后必须再问模型一轮"
    assert assistant.status == "completed"
    assert assistant.content == "已完成。"
    assert assistant.tool_calls and assistant.tool_calls[0]["status"] == "ok"


async def test_write_tool_pauses_for_confirmation() -> None:
    streamer, assistant, _rows, llm = _fixture(pending=True)
    frames = await _collect(streamer, assistant)

    joined = "".join(frames)
    assert "event: tool_approval" in joined
    assert "create_task" in joined
    # 关键：写操作绝不能被执行——只问了一轮，没有第二轮生成，也没有任务创建
    assert llm.calls == 1
    assert assistant.status == "waiting_approval"
    assert assistant.pending_tool is not None
    assert assistant.pending_tool["name"] == "create_task"
    assert assistant.tool_calls and assistant.tool_calls[0]["status"] == "pending_approval"
    # 结果里不应出现「已完成」的生成内容
    assert assistant.content == ""
