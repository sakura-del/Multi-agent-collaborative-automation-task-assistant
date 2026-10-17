"""运行时持久化契约测试（Plan 2 Task 1）。

覆盖：
- Task / ExecutionLog 的新增运行时字段与可空约束
- Subtask、ExecutionLog 的唯一约束
- Task / ExecutionLog 默认值与唯一约束在数据库中的实际行为
- contracts.py 共享契约的字段校验（extra=forbid、ID 格式、枚举）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.db.models.log import ExecutionLog
from app.db.models.task import Subtask, Task
from app.orchestration.contracts import (
    Evidence,
    ExecutionResult,
    PlanNode,
    ReviewReport,
    TaskCommand,
    TaskEvent,
    TaskPlan,
    TaskStatus,
    ValidationCheck,
    ValidationReport,
)

TASK_RUNTIME_FIELDS = {
    "task_type",
    "config",
    "plan",
    "plan_version",
    "paused_from_status",
    "review_report",
    "artifacts",
}
LOG_RUNTIME_FIELDS = {"event_type", "event_seq", "attempt"}


def test_task_has_runtime_columns() -> None:
    columns = {column.name: column for column in Task.__table__.columns}
    assert TASK_RUNTIME_FIELDS <= set(columns)

    for name in ("task_type", "config", "plan_version", "artifacts"):
        assert columns[name].nullable is False, name
    for name in ("plan", "review_report", "paused_from_status"):
        assert columns[name].nullable is True, name


def test_execution_log_has_event_columns() -> None:
    columns = {column.name: column for column in ExecutionLog.__table__.columns}
    assert LOG_RUNTIME_FIELDS <= set(columns)

    for name in LOG_RUNTIME_FIELDS:
        assert columns[name].nullable is False, name


def test_subtask_task_node_is_unique() -> None:
    constraint_names = {constraint.name for constraint in Subtask.__table__.constraints}
    assert "uq_subtask_task_node" in constraint_names


def test_execution_log_task_event_seq_is_unique() -> None:
    constraint_names = {constraint.name for constraint in ExecutionLog.__table__.constraints}
    assert "uq_execution_log_task_event_seq" in constraint_names


async def test_task_runtime_defaults(db_session) -> None:
    task = Task(user_id=uuid.uuid4(), title="默认值任务", description="校验运行时字段默认值")
    db_session.add(task)
    await db_session.flush()

    assert task.task_type == "literature_review"
    assert task.config == {}
    assert task.plan_version == 0
    assert task.artifacts == []
    assert task.plan is None
    assert task.paused_from_status is None
    assert task.review_report is None


async def test_execution_log_event_seq_rejects_duplicates(db_session) -> None:
    task = Task(user_id=uuid.uuid4(), title="事件序号任务", description="校验事件序号唯一约束")
    db_session.add(task)
    await db_session.flush()

    logged_at = datetime.now(UTC)
    db_session.add_all(
        [
            ExecutionLog(
                task_id=task.id,
                message="第一条",
                logged_at=logged_at,
                event_type="log",
                event_seq=1,
                attempt=0,
            ),
            ExecutionLog(
                task_id=task.id,
                message="重复序号",
                logged_at=logged_at,
                event_type="log",
                event_seq=1,
                attempt=0,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_contract_enums_and_node_validation() -> None:
    assert TaskStatus.WAITING_APPROVAL == "waiting_approval"
    assert TaskStatus.CANCELLED == "cancelled"
    assert TaskCommand.RETRY_NODE == "retry_node"

    with pytest.raises(ValidationError):
        PlanNode(id="N1-bad", type="search", title="非法节点 ID")

    node = PlanNode(id="n1", type="search", title="合法节点", dependencies=[])
    assert node.required_evidence == 1


def test_contract_models_forbid_extra_fields() -> None:
    payloads = {
        Evidence: {"excerpt": "原文片段"},
        PlanNode: {"id": "n1", "type": "write", "title": "节点"},
        ExecutionResult: {"node_id": "n1", "content": "结果", "duration_ms": 1},
        ValidationCheck: {"name": "结构", "passed": True, "message": "ok"},
        TaskEvent: {
            "event_id": 1,
            "task_id": uuid.uuid4(),
            "event_type": "node_completed",
            "status": "running",
            "occurred_at": datetime.now(UTC),
        },
        ReviewReport: {
            "final_markdown": "# 综述",
            "bibliography": [],
            "scores": {},
            "issues": [],
            "stats": {},
        },
    }

    for model, payload in payloads.items():
        model(**payload)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            model(**{**payload, "unexpected_field": True})  # type: ignore[arg-type]


def test_task_plan_requires_bounded_content() -> None:
    plan = TaskPlan(
        version=1,
        topic="检索增强生成与证据核验",
        research_questions=["RAG 如何减少幻觉？"],
        search_queries=["RAG hallucination"],
        acceptance_criteria=["引用可回溯"],
        nodes=[PlanNode(id="n1", type="search", title="检索文献")],
    )
    assert plan.version == 1

    with pytest.raises(ValidationError):
        TaskPlan(
            version=0,
            topic="检索增强生成与证据核验",
            research_questions=["问题"],
            search_queries=["query"],
            acceptance_criteria=["标准"],
            nodes=[PlanNode(id="n1", type="search", title="检索")],
        )

    with pytest.raises(ValidationError):
        ValidationReport(passed=False, score=101, checks=[], retryable=True, feedback="超范围分数")
