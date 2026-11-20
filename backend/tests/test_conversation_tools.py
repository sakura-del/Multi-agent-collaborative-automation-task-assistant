"""会话工具循环回归（P3）。

⚠️ 铁律 #0：本机不跑 pytest；在测试机 10.0.10.2 执行。

覆盖：
- 只读工具：自动执行 → tool_call/tool_result 事件 → 模型拿到结果后继续回答；
- 写工具：**不执行**，消息落库为 waiting_approval + pending_tool；
- 工具轨迹持久化（刷新后仍能看到执行过程）；
- 确认后续跑（resume）：结果回灌 → 模型继续同一条消息，正文/轨迹不丢；
- 被拒绝的写操作不重复弹卡；空轨迹 resume 报错。
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


async def _collect(
    streamer: ConversationStreamer, assistant: Message, *, resume: bool = False
) -> list[str]:
    return [
        frame
        async for frame in streamer.stream(assistant.conversation_id, assistant.id, resume=resume)
    ]


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


# ===== 确认后续跑（多步连续写操作的核心机制）=====


def _approve_state(assistant: Message) -> None:
    """模拟 confirm_tool 端点确认后的落库状态（与真实语义一致）。"""
    records = [dict(item) for item in (assistant.tool_calls or [])]
    for item in records:
        if item.get("status") == "pending_approval":
            item["status"] = "ok"
            item["result_preview"] = '{"task_id": "t-123", "status": "planning"}'
    assistant.tool_calls = records
    assistant.pending_tool = None
    assistant.status = "completed"
    # 与端点 _append_note 语义一致：空基底不留前导空行
    base = (assistant.content or "").rstrip()
    assistant.content = (
        f"{base}\n\n已创建综述任务 `t-123`。" if base else "已创建综述任务 `t-123`。"
    )


async def test_resume_after_approval_continues_same_message() -> None:
    """确认 → resume：结果回灌，模型在同一消息上继续，正文与轨迹都保留。"""
    streamer, assistant, _rows, llm = _fixture(pending=True)
    await _collect(streamer, assistant)
    assert assistant.status == "waiting_approval"
    assert llm.calls == 1

    _approve_state(assistant)
    frames2 = await _collect(streamer, assistant, resume=True)

    joined = "".join(frames2)
    assert "event: start" in joined and '"resumed": true' in joined
    assert "event: delta" in joined
    assert "event: tool_approval" not in joined, "续跑第一轮不应再次暂停"
    # 正文 = 确认摘要（基底）+ 续跑生成，不能互相覆盖
    assert assistant.content.startswith("已创建综述任务 `t-123`。")
    assert assistant.content.endswith("已完成。")
    assert assistant.status == "completed"
    # 工具轨迹保留执行记录（不因续跑清零）
    assert [item["status"] for item in assistant.tool_calls] == ["ok"]
    # 模型确实收到了工具结果（第二问的上下文里有回灌的 tool 消息）
    assert llm.calls == 2


async def test_resume_replays_tool_result_into_context() -> None:
    """续跑请求的上下文必须包含 assistant(tool_calls) + tool 结果，且成对合法。"""
    streamer, assistant, _rows, llm = _fixture(pending=True)
    await _collect(streamer, assistant)
    _approve_state(assistant)

    seen: list[list[Any]] = []

    async def capture(messages: Any, **kwargs: Any) -> Any:  # type: ignore[no-untyped-def]
        seen.append(list(messages))
        for event in llm.rounds[min(llm.calls, len(llm.rounds) - 1)]:
            yield event
        llm.calls += 1

    llm.stream_chat_events = capture  # type: ignore[method-assign]
    await _collect(streamer, assistant, resume=True)

    context = seen[-1]
    roles = [m.role.value if hasattr(m.role, "value") else str(m.role) for m in context]
    assert roles.count("tool") == 1, "工具结果必须以 role=tool 回灌"
    tool_msg = next(
        m for m in context if (m.role.value if hasattr(m.role, "value") else str(m.role)) == "tool"
    )
    assert "t-123" in (tool_msg.content or "")
    assistant_calls = [
        m
        for m in context
        if (m.role.value if hasattr(m.role, "value") else str(m.role)) == "assistant"
        and m.tool_calls
    ]
    assert assistant_calls, "assistant(tool_calls) 必须出现在 tool 消息之前"
    assert assistant_calls[-1].tool_calls[0].id == "call-1"


async def test_resume_without_tool_history_errors() -> None:
    """没有工具轨迹却请求续跑 → 明确报错，而不是拿空上下文瞎生成。"""
    streamer, assistant, _rows, _llm = _fixture()
    assistant.status = "completed"
    frames = await _collect(streamer, assistant, resume=True)
    joined = "".join(frames)
    assert "event: error" in joined
    assert "nothing_to_resume" in joined


async def test_rejected_tool_is_not_proposed_again() -> None:
    """被拒绝过的（工具, 参数）组合再次提议 → 直接拦下并回灌原因，不弹卡。"""
    same_args = {"topic": "多智能体协作综述", "keywords": ["多智能体"]}
    records = [
        {
            "id": "call-0",
            "name": "create_task",
            "summary": "创建综述任务",
            "status": "rejected",
            "arguments": same_args,
            "result_preview": "用户取消",
            "write": True,
        }
    ]
    blocked, reason = ConversationStreamer._write_blocked(records, "create_task", dict(same_args))
    assert blocked and "拒绝" in reason
    # 参数不同（换了主题）不算重复提议，应放行
    blocked2, _ = ConversationStreamer._write_blocked(
        records, "create_task", {"topic": "另一个主题", "keywords": []}
    )
    assert not blocked2
