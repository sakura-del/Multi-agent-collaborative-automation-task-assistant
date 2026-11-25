"""任务快照必须包含 `final_output`，否则 MCP/开放 API 取不到正文（2026-10-23 巡检发现）。

## 缺陷

- `task_execution.py` 完成时**写入** `task.final_output`（复盘正文）；
- 但 `TaskSnapshot`（`orchestration/contracts.py`）**没有 `final_output` 字段**，
  且 `extra="forbid"` —— 序列化时该字段被丢掉。

后果：**MCP 的 `get_task_output` 永远返回 `markdown: null`**：

```python
# app/mcp/tools.py
"markdown": snapshot.get("final_output"),   # ← 永远是 None
"has_output": bool(snapshot.get("final_output")),  # ← 永远 False
```

`get_task_status` 也永远告诉模型「尚无产出」。

## 为什么既有测试全绿（假测试）

`tests/test_mcp_tools.py` 用手写的 dict 替身伪造了 `final_output`：

```python
return httpx.Response(200, json={"task_id": ..., "final_output": "很长的正文" * 500})
```

替身里有，真实 API 里没有 —— 测试测的是替身，不是契约。
**这是本项目第二次遇到「假测试掩盖真实缺陷」**（第一次是前端 flushPromises）。

## 本文件锁定

1. `TaskSnapshot` 必须携带 `final_output`（可选，默认 None）；
2. 任务执行层构造快照时把 `task.final_output` 传进去；
3. MCP 的 `get_task_output` 在真实快照上能拿到正文；
4. 无产出时仍是 None（不能变成空串，前端/工具靠 None 判「还没产出」）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.orchestration.contracts import TaskSnapshot


class TestSnapshotContract:
    def test_snapshot_has_final_output_field(self) -> None:
        """**核心断言**：契约里有 `final_output`。"""
        fields = set(TaskSnapshot.model_fields)
        assert "final_output" in fields, (
            "TaskSnapshot 缺 final_output —— MCP get_task_output 读的就是它，"
            f"当前字段：{sorted(fields)}"
        )

    def test_final_output_optional_default_none(self) -> None:
        """默认 None（不能是空串：调用方靠 None 判「还没产出」）。"""
        snapshot = TaskSnapshot(task_id=uuid.uuid4(), status="pending", title="t")
        assert snapshot.final_output is None

    def test_final_output_roundtrip(self) -> None:
        """带正文的快照能序列化出来（供 HTTP 响应 / MCP 读取）。"""
        snapshot = TaskSnapshot(
            task_id=uuid.uuid4(),
            status="completed",
            title="综述",
            final_output="# 综述\n\n正文内容",
        )
        payload = snapshot.model_dump(mode="json")
        assert payload["final_output"] == "# 综述\n\n正文内容"

    def test_final_output_not_truncated(self) -> None:
        """长正文不能被截断（综述可能几万字）。"""
        long_text = "段落内容。" * 20_000
        snapshot = TaskSnapshot(
            task_id=uuid.uuid4(), status="completed", title="t", final_output=long_text
        )
        assert snapshot.final_output == long_text
        assert len(snapshot.model_dump(mode="json")["final_output"]) == len(long_text)


class TestSnapshotStillForbidsExtras:
    def test_unknown_field_rejected(self) -> None:
        """`extra="forbid"` 不能被破坏（加了字段不等于放开校验）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TaskSnapshot(
                task_id=uuid.uuid4(), status="pending", title="t", definitely_not_a_field=1
            )


class _FakeTask:
    """`Task` ORM 行替身（只带快照构造用到的字段）。"""

    def __init__(self, **kwargs: object) -> None:
        self.id = uuid.uuid4()
        self.status = "completed"
        self.title = "综述"
        self.plan_version = 1
        self.revision = 2
        self.plan = None
        self.artifacts = []
        self.review_report = None
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)
        self.final_output = None
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeSession:
    """`_build_snapshot` 只用到 `execute()`（查 subtask 与时间戳）。"""

    async def execute(self, stmt: object) -> object:
        class _Result:
            def scalars(self) -> _Result:
                return self

            def all(self) -> list[object]:
                return []

            def one_or_none(self) -> tuple[object, object]:
                return (None, None)

        return _Result()


class TestExecutionBuildsSnapshotWithOutput:
    @pytest.mark.asyncio
    async def test_builder_carries_final_output(self) -> None:
        """执行层构造快照时必须把 `final_output` 带上。

        直接调真实的 `_build_snapshot`（async），而不是手写 dict ——
        手写 dict 正是当初假测试的成因。
        """
        from app.orchestration.services.task_execution import TaskExecution

        builder = getattr(TaskExecution, "_build_snapshot", None)
        assert builder is not None, (
            "TaskExecution 缺少 _build_snapshot 统一构造入口；"
            "若快照在各处手写，final_output 迟早再次漏掉"
        )

        task = _FakeTask(final_output="# 正文\n\n真实内容")
        snapshot = await TaskExecution._build_snapshot(None, _FakeSession(), task)  # type: ignore[arg-type]

        assert (
            snapshot.final_output == "# 正文\n\n真实内容"
        ), "快照没带上 final_output —— MCP get_task_output 会返回 null"

    @pytest.mark.asyncio
    async def test_builder_keeps_none_when_no_output(self) -> None:
        from app.orchestration.services.task_execution import TaskExecution

        snapshot = await TaskExecution._build_snapshot(  # type: ignore[arg-type]
            None, _FakeSession(), _FakeTask(final_output=None)
        )
        assert snapshot.final_output is None


class TestSnapshotTopLevelReasons:
    def test_snapshot_defaults_new_fields_to_none_and_forbids_extras(self) -> None:
        from pydantic import ValidationError

        snapshot = TaskSnapshot(task_id=uuid.uuid4(), status="pending", title="t")
        assert snapshot.error is None
        assert snapshot.waiting_reason is None
        assert snapshot.workflow_preset is None
        with pytest.raises(ValidationError):
            TaskSnapshot(task_id=uuid.uuid4(), status="pending", title="t", unknown_x=1)

    @pytest.mark.asyncio
    async def test_builder_populates_workflow_preset_waiting_reason_and_error(self) -> None:
        from app.orchestration.services.task_execution import TaskExecution

        waiting_task = _FakeTask(
            status="waiting_human",
            config={
                "workflow_preset": "research_gaps",
                "_waiting_reason": "规划失败: 模型连接超时",
            },
        )
        waiting_snap = await TaskExecution._build_snapshot(  # type: ignore[arg-type]
            None, _FakeSession(), waiting_task
        )
        assert waiting_snap.workflow_preset == "research_gaps"
        assert waiting_snap.waiting_reason == "规划失败: 模型连接超时"
        assert waiting_snap.error is None

        failed_task = _FakeTask(
            status="failed",
            config={
                "workflow_preset": "verify_citations",
                "_error": "引用一致性校验未通过：缺少 [E2]",
            },
        )
        failed_snap = await TaskExecution._build_snapshot(  # type: ignore[arg-type]
            None, _FakeSession(), failed_task
        )
        assert failed_snap.workflow_preset == "verify_citations"
        assert failed_snap.error == "引用一致性校验未通过：缺少 [E2]"
        assert failed_snap.waiting_reason is None

    @pytest.mark.asyncio
    async def test_builder_gates_reasons_by_current_status(self) -> None:
        from app.orchestration.services.task_execution import TaskExecution

        for status in ("completed", "running", "planning", "waiting_approval"):
            task = _FakeTask(
                status=status,
                config={
                    "_waiting_reason": "旧等待原因",
                    "_error": "旧失败原因",
                },
            )
            snap = await TaskExecution._build_snapshot(  # type: ignore[arg-type]
                None, _FakeSession(), task
            )
            assert snap.waiting_reason is None
            assert snap.error is None

    @pytest.mark.asyncio
    async def test_builder_fallbacks_to_subtask_and_execution_log_when_config_missing(self) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        import app.db.models  # noqa: F401
        from app.db.base import Base
        from app.db.models.task import Subtask, Task
        from app.db.repositories.event import EventRepository
        from app.orchestration.services.task_execution import TaskExecution

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with factory() as session:
                # 1) waiting_human 有失败子节点但 config 无 _waiting_reason
                t1 = Task(
                    user_id=uuid.uuid4(),
                    title="子任务失败兜底",
                    description="d",
                    status="waiting_human",
                    config={"workflow_preset": "literature_review"},
                )
                # 2) waiting_human 规划失败无子节点、无 config._waiting_reason，仅 ExecutionLog 有记录
                t2 = Task(
                    user_id=uuid.uuid4(),
                    title="规划日志兜底",
                    description="d",
                    status="waiting_human",
                    config={},
                )
                # 3) failed 任务无 config._error，仅 ExecutionLog 有 task_failed
                t3 = Task(
                    user_id=uuid.uuid4(),
                    title="失败日志兜底",
                    description="d",
                    status="failed",
                    config={},
                )
                session.add_all([t1, t2, t3])
                await session.flush()

                session.add(
                    Subtask(
                        task_id=t1.id,
                        node_id="n2",
                        title="提取证据",
                        description="d",
                        status="waiting_human",
                        output_data={"error": "节点超时未返回"},
                    )
                )
                event_repo = EventRepository(session)
                await event_repo.append(
                    t2.id,
                    event_type="task_waiting_human",
                    status="waiting_human",
                    payload={"reason": "规划失败: 401"},
                )
                await event_repo.append(
                    t3.id,
                    event_type="task_failed",
                    status="failed",
                    payload={"reason": "引用缺失"},
                )
                await session.commit()

                exec_stub = TaskExecution.__new__(TaskExecution)
                snap1 = await TaskExecution._build_snapshot(exec_stub, session, t1)
                snap2 = await TaskExecution._build_snapshot(exec_stub, session, t2)
                snap3 = await TaskExecution._build_snapshot(exec_stub, session, t3)

                assert snap1.waiting_reason is not None and "节点超时未返回" in snap1.waiting_reason
                assert snap1.error is None
                assert snap2.waiting_reason == "规划失败: 401"
                assert snap2.error is None
                assert snap3.error == "引用缺失"
                assert snap3.waiting_reason is None
        finally:
            await engine.dispose()
