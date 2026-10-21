"""可观测性看板端点（Phase 2 · 可观测性 Task 5）。

## 与 `/metrics` 的分工

- `/metrics`：**运维视角**，全实例聚合、无鉴权、无 PII、Prometheus 格式；
- `/api/v1/observability/overview`：**用户视角**，owner-scoped（只看自己的任务），
  JSON 契约，前端看板直接消费。

## 为什么越权返回空集而不是 403

`user_id` 隔离是既有口径（所有查询都已 owner-scoped）。查询"别人的统计"时返回
**自己的空数据**而不是 403 —— 403 会泄漏"那个 id 存在且有多少数据"这一信息。
这与既有 `GET /tasks/{id}` 的 404 统一处理是同一思路（不区分"不存在"与"不是你的"）。

## 数据来源

全部来自 `task` 表（`status` / `used_tokens` / `created_at` / `updated_at`），
不另外建统计表 —— 避免"汇总与明细不一致"（与 PRISMA 计数派生的原则一致）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/observability", tags=["observability"])

#: 最近任务列表的上限（看板只展示最近这些）
_RECENT_LIMIT = 10

#: 计入「成功」的状态（只有 completed 算成功；
#: waiting_human 是"等人工"，不算成功也不算失败）
_SUCCESS_STATUS = "completed"


@router.get("/overview", summary="我的任务健康与成本概览")
async def overview(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """当前用户的任务统计（**只含自己的数据**）。

    响应契约（前端看板依赖，改动需同步前端）：

    ```json
    {
      "tasks": {"total": 0, "completed": 0, "failed": 0, "cancelled": 0, "waiting_human": 0},
      "success_rate": 0.0,
      "tokens": {"total_used": 0, "avg_per_task": 0},
      "recent": [{"task_id": "...", "status": "...", "used_tokens": 0, "duration_seconds": 0}]
    }
    ```
    """
    # 各状态计数：一次查询用条件聚合拿全（避免 5 次往返）
    status_counts = await db.execute(
        select(
            Task.status,
            func.count().label("n"),
        )
        .where(Task.user_id == current_user.id)
        .group_by(Task.status)
    )
    counts: dict[str, int] = {str(row.status): int(row.n) for row in status_counts}

    totals = await db.execute(
        select(
            func.count().label("total"),
            func.coalesce(func.sum(Task.used_tokens), 0).label("tokens"),
        ).where(Task.user_id == current_user.id)
    )
    row = totals.one()
    total = int(row.total)
    total_tokens = int(row.tokens)

    terminal = (
        counts.get("completed", 0)
        + counts.get("failed", 0)
        + counts.get("cancelled", 0)
        + counts.get("waiting_human", 0)
    )
    # 成功率的分母：只算**已进入终态**的任务（等待规划/执行中的不该拉低成功率）
    rate = (counts.get(_SUCCESS_STATUS, 0) / terminal) if terminal else 0.0

    recent_rows = await db.execute(
        select(Task)
        .where(Task.user_id == current_user.id)
        .order_by(Task.created_at.desc(), Task.id.desc())  # 稳定排序（并列时用 id）
        .limit(_RECENT_LIMIT)
    )
    recent = [
        {
            "task_id": str(task.id),
            "status": task.status,
            "used_tokens": int(task.used_tokens or 0),
            "duration_seconds": _duration_seconds(task.created_at, task.updated_at),
        }
        for task in recent_rows.scalars().all()
    ]

    return {
        "tasks": {
            "total": total,
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "waiting_human": counts.get("waiting_human", 0),
        },
        "success_rate": round(rate, 4),
        "tokens": {
            "total_used": total_tokens,
            "avg_per_task": int(total_tokens / total) if total else 0,
        },
        "recent": recent,
    }


def _duration_seconds(created: datetime | None, updated: datetime | None) -> int:
    """任务耗时（秒，向下取整，负数归 0）。

    用 `updated_at - created_at` 而非 `completed_at`：只有 completed 才有
    `completed_at`，而取消/转人工的任务也需要一个可比的耗时。
    """
    if created is None or updated is None:
        return 0
    delta = (updated - created).total_seconds()
    return max(0, int(delta))
