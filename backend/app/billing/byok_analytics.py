"""管理员侧 BYOK 渗透率统计（Issue #15 P2）。

## 为什么按「窗口」而不是自然月

渗透率是运营指标，观察的是趋势（这两周有没有更多用户接自己的 key），
不是账单周期。窗口默认 30 天，可选 7/30/90，与自然月解耦。

## 口径（必须在页面上写清）

| 指标 | 分子 | 分母 |
| --- | --- | --- |
| BYOK 任务占比 | 窗口内 `is_byok=true` 的任务 | 窗口内全部任务 |
| BYOK token 占比 | BYOK 任务 `used_tokens` | 全部任务 `used_tokens` |
| BYOK 用户渗透率 | 窗口内**跑过** BYOK 任务的用户 | 窗口内跑过任务的用户 |
| 有凭据用户占比 | 有任意凭据的用户 | 窗口内跑过任务的用户 |

「有凭据」不等于「用过」：用户可能配了 key 但一直跑平台模型。
两个数分开看，才能区分「没配置」与「配置了但没切」。

## 数据量

管理端窗口最多 90 天，任务量级在千级；这里直接取行在 Python 聚合，
跨 SQLite/PostgreSQL 不用写两套 JSON/日期函数。任务量上到十万级时
再改成 SQL 聚合 + 物化视图，不要提前优化。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.llm_credential import LLMCredential
from app.db.models.task import Task
from app.db.models.user import User


def _as_utc(value: datetime) -> datetime:
    """SQLite 的 `CURRENT_TIMESTAMP` 是 naive UTC；统一补时区再取日期。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


async def build_byok_overview(
    session: AsyncSession,
    *,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构建管理员看板数据（窗口截止到 `now`）。"""
    end = now or datetime.now(UTC)
    start = end - timedelta(days=days)

    tasks = (
        (
            await session.execute(
                select(Task).where(Task.created_at >= start, Task.created_at <= end)
            )
        )
        .scalars()
        .all()
    )
    credential_rows = (
        await session.execute(select(LLMCredential.user_id, LLMCredential.status))
    ).all()

    total_tasks = len(tasks)
    byok_tasks = 0
    total_tokens = 0
    byok_tokens = 0
    active_users: set[uuid.UUID] = set()
    byok_users: set[uuid.UUID] = set()
    per_user: dict[uuid.UUID, dict[str, int]] = defaultdict(
        lambda: {"tasks": 0, "tokens": 0, "byok_tasks": 0, "byok_tokens": 0}
    )
    per_provider: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"tasks": 0, "tokens": 0}
    )
    daily: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tasks": 0, "tokens": 0, "byok_tasks": 0, "byok_tokens": 0}
    )

    # 先把窗口内每一天都填 0：折线/柱状图不能因为某天没任务就断档
    cursor = start.date()
    while cursor <= end.date():
        daily[cursor.isoformat()]
        cursor += timedelta(days=1)

    for task in tasks:
        tokens = int(task.used_tokens or 0)
        total_tokens += tokens
        active_users.add(task.user_id)
        bucket = daily[_as_utc(task.created_at).date().isoformat()]
        bucket["tasks"] += 1
        bucket["tokens"] += tokens

        user_bucket = per_user[task.user_id]
        user_bucket["tasks"] += 1
        user_bucket["tokens"] += tokens

        if not task.is_byok:
            continue

        byok_tasks += 1
        byok_tokens += tokens
        byok_users.add(task.user_id)
        bucket["byok_tasks"] += 1
        bucket["byok_tokens"] += tokens
        user_bucket["byok_tasks"] += 1
        user_bucket["byok_tokens"] += tokens

        snapshot = task.model_snapshot if isinstance(task.model_snapshot, dict) else {}
        provider = str(snapshot.get("provider") or "unknown")
        model = str(snapshot.get("model") or "unknown")
        provider_bucket = per_provider[(provider, model)]
        provider_bucket["tasks"] += 1
        provider_bucket["tokens"] += tokens

    # 只统计**活跃用户中**的凭据持有者：全站凭据用户可以远多于窗口内活跃用户，
    # 直接相除会得到 >100% 的「渗透率」，运营会以为数据坏了。
    credential_users = {row[0] for row in credential_rows} & active_users
    active_credential_users = {
        row[0] for row in credential_rows if row[1] == "active"
    } & active_users
    credential_counts = {"total": len(credential_rows), "active": 0, "invalid": 0, "disabled": 0}
    for _, status in credential_rows:
        if status in credential_counts and status != "total":
            credential_counts[status] += 1

    # 只列真正用过 BYOK 的用户：把 0 个 BYOK 任务的人放进「BYOK Top 用户」
    # 会让运营误以为他在用自己的 key。
    top_ids = [
        uid
        for uid in sorted(
            per_user,
            key=lambda uid: (per_user[uid]["byok_tokens"], per_user[uid]["byok_tasks"]),
            reverse=True,
        )
        if per_user[uid]["byok_tasks"] > 0
    ][:10]
    usernames: dict[uuid.UUID, str] = {}
    if top_ids:
        rows = (
            await session.execute(select(User.id, User.username).where(User.id.in_(top_ids)))
        ).all()
        usernames = {row[0]: row[1] for row in rows}

    top_users = [
        {
            "user_id": str(uid),
            "username": usernames.get(uid, "已删除用户"),
            "tasks": per_user[uid]["tasks"],
            "tokens": per_user[uid]["tokens"],
            "byok_tasks": per_user[uid]["byok_tasks"],
            "byok_tokens": per_user[uid]["byok_tokens"],
            "byok_token_share": _share(per_user[uid]["byok_tokens"], per_user[uid]["tokens"]),
        }
        for uid in top_ids
    ]

    providers = [
        {
            "provider": provider,
            "model": model,
            "tasks": values["tasks"],
            "tokens": values["tokens"],
        }
        for (provider, model), values in sorted(
            per_provider.items(), key=lambda item: item[1]["tokens"], reverse=True
        )[:10]
    ]

    return {
        "window_days": days,
        "period_start": start,
        "period_end": end,
        "totals": {
            "tasks": total_tasks,
            "tokens": total_tokens,
            "byok_tasks": byok_tasks,
            "byok_tokens": byok_tokens,
            "byok_task_share": _share(byok_tasks, total_tasks),
            "byok_token_share": _share(byok_tokens, total_tokens),
        },
        "users": {
            "active": len(active_users),
            "byok_active": len(byok_users),
            "with_credentials": len(credential_users),
            "with_active_credentials": len(active_credential_users),
            "byok_penetration": _share(len(byok_users), len(active_users)),
            "credential_penetration": _share(len(credential_users), len(active_users)),
        },
        "credentials": credential_counts,
        "trend": [
            {"date": key, **values}
            for key, values in sorted(daily.items(), key=lambda item: item[0])
        ],
        "providers": providers,
        "top_users": top_users,
    }
