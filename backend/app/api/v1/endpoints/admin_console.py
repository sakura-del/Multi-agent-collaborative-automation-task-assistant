"""后台管理控制台（Issue #12）：全站 KPI + 跨用户任务/用户列表。

## 只读原则

管理端**只读**：不代用户改任务、不代删数据。需要人工处置时走用户侧已有的
控制命令（pause/resume/cancel/replan），保持「谁能操作谁负责」的审计边界。
唯一例外是已有的 BYOK 看板与套餐开通（各自独立端点）。

## 为什么 KPI 一次请求返回

首页 6 张卡如果各发一个请求，首屏要等 6 个 RTT，且数字之间自然不同步
（用户数比任务数晚 300ms → 看着像 bug）。聚合在一个事务里查，数字同快照。

## 为什么按 `created_at` 而不是 `completed_at` 划窗口

「这周失败率」的直觉是「这周提交的任务里失败了多少」，而不是「这周完成的
任务里失败了多少」——后者会把积压的旧任务算进来，且周一早上分母接近 0。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user
from app.db.models.audit import AuditLog
from app.db.models.billing import Plan, Subscription
from app.db.models.llm_credential import LLMCredential
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.admin_console import (
    AdminAuditPage,
    AdminAuditRow,
    AdminOverviewOut,
    AdminTaskPage,
    AdminTaskRow,
    AdminTaskStats,
    AdminUserPage,
    AdminUserRow,
    AdminUserStats,
)

router = APIRouter(prefix="/admin/console", tags=["admin"])

AdminDep = Annotated[User, Depends(get_current_admin_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]

#: 已结束状态（失败率分母）
_FINISHED = ("completed", "failed", "cancelled")
#: 需要人处理的状态（首页「待处理」卡）
_PENDING = ("waiting_approval", "waiting_human")


@router.get("/overview", response_model=AdminOverviewOut, summary="后台 KPI 概览")
async def overview(
    _admin: AdminDep,
    db: DbDep,
    days: Annotated[int, Query(ge=1, le=90, description="统计窗口（天）")] = 7,
) -> AdminOverviewOut:
    since = datetime.now(UTC) - timedelta(days=days)

    users_total = await db.scalar(select(func.count()).select_from(User)) or 0
    users_new = (
        await db.scalar(select(func.count()).select_from(User).where(User.created_at >= since)) or 0
    )
    users_active = (
        await db.scalar(
            select(func.count(func.distinct(Task.user_id))).where(Task.created_at >= since)
        )
        or 0
    )

    tasks_total = await db.scalar(select(func.count()).select_from(Task)) or 0
    tasks_window = (
        await db.scalar(select(func.count()).select_from(Task).where(Task.created_at >= since)) or 0
    )
    status_rows = (await db.execute(select(Task.status, func.count()).group_by(Task.status))).all()
    by_status = {str(status): int(count) for status, count in status_rows}

    failed_window = (
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.status == "failed", Task.created_at >= since)
        )
        or 0
    )
    finished_window = (
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.status.in_(_FINISHED), Task.created_at >= since)
        )
        or 0
    )
    pending_action = (
        await db.scalar(select(func.count()).select_from(Task).where(Task.status.in_(_PENDING)))
        or 0
    )

    tokens_window = (
        await db.scalar(
            select(func.coalesce(func.sum(Task.used_tokens), 0)).where(Task.created_at >= since)
        )
        or 0
    )
    byok_window = (
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.is_byok.is_(True), Task.created_at >= since)
        )
        or 0
    )
    active_credentials = (
        await db.scalar(
            select(func.count()).select_from(LLMCredential).where(LLMCredential.status == "active")
        )
        or 0
    )

    return AdminOverviewOut(
        days=days,
        users=AdminUserStats(
            total=users_total, new_in_window=users_new, active_in_window=users_active
        ),
        tasks=AdminTaskStats(
            total=tasks_total,
            created_in_window=tasks_window,
            by_status=by_status,
            failed_in_window=failed_window,
            finished_in_window=finished_window,
            failure_rate=(failed_window / finished_window) if finished_window else 0.0,
            pending_action=pending_action,
        ),
        tokens_used_in_window=int(tokens_window),
        byok_tasks_in_window=byok_window,
        byok_ratio=(byok_window / tasks_window) if tasks_window else 0.0,
        active_credentials=active_credentials,
    )


def _model_label(snapshot: Any) -> str | None:
    """快照 → 可读标签；平台任务（无快照）返回 None。"""
    if not isinstance(snapshot, dict):
        return None
    name = str(snapshot.get("name") or "").strip()
    model = str(snapshot.get("model") or "").strip()
    if name and model:
        return f"{name}（{model}）"
    return name or model or None


@router.get("/tasks", response_model=AdminTaskPage, summary="跨用户任务列表（只读）")
async def list_all_tasks(
    _admin: AdminDep,
    db: DbDep,
    status: Annotated[str | None, Query(max_length=32, description="单状态筛选")] = None,
    q: Annotated[str | None, Query(max_length=100, description="标题关键词")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminTaskPage:
    base = select(Task, User.username).join(User, User.id == Task.user_id)
    count_query = select(func.count()).select_from(Task).join(User, User.id == Task.user_id)
    if status:
        base = base.where(Task.status == status)
        count_query = count_query.where(Task.status == status)
    if q:
        pattern = f"%{q.strip()}%"
        base = base.where(Task.title.ilike(pattern))
        count_query = count_query.where(Task.title.ilike(pattern))

    total = int(await db.scalar(count_query) or 0)
    rows = (
        await db.execute(base.order_by(Task.created_at.desc()).offset(offset).limit(limit))
    ).all()
    items = [
        AdminTaskRow(
            task_id=task.id,
            title=task.title,
            status=task.status,
            owner_id=task.user_id,
            owner_username=username,
            is_byok=bool(task.is_byok),
            model_label=_model_label(task.model_snapshot),
            used_tokens=int(task.used_tokens or 0),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        for task, username in rows
    ]
    return AdminTaskPage(total=total, items=items)


@router.get("/users", response_model=AdminUserPage, summary="用户列表（只读）")
async def list_users(
    _admin: AdminDep,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=100, description="用户名/邮箱关键词")] = None,
    role: Annotated[Literal["admin", "user"] | None, Query(description="角色筛选")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminUserPage:
    # 套餐名（同一用户同时只有一条 active 订阅，见 Subscription 模型）
    active_plan = (
        select(Subscription.user_id.label("user_id"), Plan.name.label("plan_name"))
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.status == "active")
        .subquery()
    )
    task_counts = (
        select(Task.user_id.label("user_id"), func.count().label("task_count"))
        .group_by(Task.user_id)
        .subquery()
    )
    cred_counts = (
        select(LLMCredential.user_id.label("user_id"), func.count().label("credential_count"))
        .group_by(LLMCredential.user_id)
        .subquery()
    )

    base = (
        select(
            User,
            active_plan.c.plan_name,
            func.coalesce(task_counts.c.task_count, 0),
            func.coalesce(cred_counts.c.credential_count, 0),
        )
        .outerjoin(active_plan, active_plan.c.user_id == User.id)
        .outerjoin(task_counts, task_counts.c.user_id == User.id)
        .outerjoin(cred_counts, cred_counts.c.user_id == User.id)
    )
    count_base = select(func.count()).select_from(User)
    if q:
        pattern = f"%{q.strip()}%"
        cond = User.username.ilike(pattern) | User.email.ilike(pattern)
        base = base.where(cond)
        count_base = count_base.where(cond)
    if role:
        base = base.where(User.role == role)
        count_base = count_base.where(User.role == role)

    total = int(await db.scalar(count_base) or 0)
    rows = (
        await db.execute(base.order_by(User.created_at.desc()).offset(offset).limit(limit))
    ).all()
    items = [
        AdminUserRow(
            user_id=user.id,
            username=user.username,
            email=user.email,
            role=user.role,
            is_active=bool(user.is_active),
            created_at=user.created_at,
            plan_name=plan_name,
            task_count=int(task_count or 0),
            credential_count=int(credential_count or 0),
        )
        for user, plan_name, task_count, credential_count in rows
    ]
    return AdminUserPage(total=total, items=items)


@router.get("/audit", response_model=AdminAuditPage, summary="审计日志（只读）")
async def list_audit(
    _admin: AdminDep,
    db: DbDep,
    action: Annotated[
        str | None, Query(max_length=64, description="动作键，如 credential.create")
    ] = None,
    target_type: Annotated[str | None, Query(max_length=32)] = None,
    actor: Annotated[str | None, Query(max_length=64, description="操作者用户名关键词")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminAuditPage:
    """关键操作留痕（凭据/计费），按时间倒序。"""
    base: Select[Any] = select(AuditLog)
    count_query: Select[Any] = select(func.count()).select_from(AuditLog)
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if target_type:
        filters.append(AuditLog.target_type == target_type)
    if actor:
        filters.append(AuditLog.actor_name.ilike(f"%{actor.strip()}%"))
    for cond in filters:
        base = base.where(cond)
        count_query = count_query.where(cond)

    total = int(await db.scalar(count_query) or 0)
    rows = (
        (await db.execute(base.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return AdminAuditPage(
        total=total,
        items=[
            AdminAuditRow(
                id=row.id,
                actor_id=row.actor_id,
                actor_name=row.actor_name,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                metadata=row.metadata_json or {},
                ip=row.ip,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )
