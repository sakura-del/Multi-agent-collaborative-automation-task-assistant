"""工具网关的调用上下文。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.services.plan_service import PlanService
from app.core.quota import QuotaService
from app.orchestration.services.task_execution import TaskExecution


@dataclass(slots=True)
class ToolContext:
    """一次工具调用可用的依赖（请求作用域内构造）。

    `db` 只用于读模型（引用库/知识库）；写任务走 `execution` 自己的事务，
    避免和请求级会话的提交边界打架。
    """

    db: AsyncSession
    user_id: UUID
    execution: TaskExecution
    plan_service: PlanService
    quota: QuotaService | None = None
    workspace_ids: Sequence[UUID] = field(default_factory=tuple)
