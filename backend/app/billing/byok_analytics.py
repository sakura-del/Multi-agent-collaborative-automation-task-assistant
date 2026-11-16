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
| 有凭据用户占比 | 活跃用户中有凭据的人 | 窗口内跑过任务的用户 |

「有凭据」不等于「用过」：用户可能配了 key 但一直跑平台模型。
两个数分开看，才能区分「没配置」与「配置了但没切」。

## 成本估算

与 `/billing/usage/cost` 同一套单价目录和拆分口径：
- BYOK 任务按快照里的 `provider/model` 计价；
- 平台任务没有模型快照，按 `settings.llm.default_model` 估算（标记为估算）；
- 历史任务没有输入/输出拆分，按 `billing.cost_output_ratio` 拆分；
- 未收录单价的 token 不计入金额，单独返回。

## 数据量

管理端窗口最多 90 天，任务量级在千级；这里直接取行在 Python 聚合，
跨 SQLite/PostgreSQL 不用写两套 JSON/日期函数。任务量上到十万级时
再改成 SQL 聚合 + 物化视图，不要提前优化。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.model_prices import estimate_cost
from app.core.config import get_settings
from app.db.models.llm_credential import LLMCredential
from app.db.models.task import Task
from app.db.models.user import User

_ZERO = Decimal("0")


def _as_utc(value: datetime) -> datetime:
    """SQLite 的 `CURRENT_TIMESTAMP` 是 naive UTC；统一补时区再取日期。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _money(value: Decimal) -> str:
    return f"{value:.6f}"


def _split_tokens(prompt: int, completion: int, used: int, ratio: Decimal) -> tuple[int, int, bool]:
    """有实测拆分用实测；只有总量时按输出占比估算并标记。"""
    if prompt + completion > 0:
        return prompt, completion, False
    if used <= 0:
        return 0, 0, False
    completion_est = int(Decimal(used) * ratio)
    return used - completion_est, completion_est, True


async def build_byok_overview(
    session: AsyncSession,
    *,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构建管理员看板数据（窗口截止到 `now`）。"""
    end = now or datetime.now(UTC)
    start = end - timedelta(days=days)
    settings = get_settings()
    ratio = Decimal(str(settings.billing.cost_output_ratio))

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
        await session.execute(
            select(LLMCredential.user_id, LLMCredential.status, LLMCredential.created_at)
        )
    ).all()
    ever_byok_users = set(
        (await session.execute(select(Task.user_id).where(Task.is_byok.is_(True)).distinct()))
        .scalars()
        .all()
    )

    total_tasks = len(tasks)
    byok_tasks = 0
    total_tokens = 0
    byok_tokens = 0
    active_users: set[uuid.UUID] = set()
    byok_users: set[uuid.UUID] = set()
    per_user: dict[uuid.UUID, dict[str, Any]] = defaultdict(
        lambda: {
            "tasks": 0,
            "tokens": 0,
            "byok_tasks": 0,
            "byok_tokens": 0,
            "byok_cost": _ZERO,
            "byok_unpriced_tokens": 0,
        }
    )
    per_provider: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"tasks": 0, "tokens": 0, "cost": _ZERO, "unpriced_tokens": 0}
    )
    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "tasks": 0,
            "tokens": 0,
            "byok_tasks": 0,
            "byok_tokens": 0,
            "cost": _ZERO,
            "byok_cost": _ZERO,
        }
    )

    # 先把窗口内每一天都填 0：折线/柱状图不能因为某天没任务就断档
    cursor = start.date()
    while cursor <= end.date():
        daily[cursor.isoformat()]
        cursor += timedelta(days=1)

    byok_cost = _ZERO
    platform_cost = _ZERO
    unpriced_byok_tokens = 0
    unpriced_platform_tokens = 0
    split_estimated_tokens = 0

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

        snapshot = task.model_snapshot if isinstance(task.model_snapshot, dict) else {}
        if task.is_byok:
            provider = str(snapshot.get("provider") or "unknown")
            model = str(snapshot.get("model") or "unknown")
        else:
            provider = settings.llm.default_provider
            model = settings.llm.default_model

        prompt, completion, estimated = _split_tokens(
            int(task.prompt_tokens or 0),
            int(task.completion_tokens or 0),
            tokens,
            ratio,
        )
        cost = estimate_cost(model, prompt, completion)
        if estimated:
            split_estimated_tokens += tokens
        bucket["cost"] += cost or _ZERO

        if not task.is_byok:
            if cost is None:
                unpriced_platform_tokens += tokens
            else:
                platform_cost += cost
            continue

        byok_tasks += 1
        byok_tokens += tokens
        byok_users.add(task.user_id)
        bucket["byok_tasks"] += 1
        bucket["byok_tokens"] += tokens

        provider_bucket = per_provider[(provider, model)]
        provider_bucket["tasks"] += 1
        provider_bucket["tokens"] += tokens
        user_bucket["byok_tasks"] += 1
        user_bucket["byok_tokens"] += tokens

        if cost is None:
            unpriced_byok_tokens += tokens
            user_bucket["byok_unpriced_tokens"] += tokens
            provider_bucket["unpriced_tokens"] += tokens
        else:
            byok_cost += cost
            bucket["byok_cost"] += cost
            user_bucket["byok_cost"] += cost
            provider_bucket["cost"] += cost

    # 只统计**活跃用户中**的凭据持有者：全站凭据用户可以远多于窗口内活跃用户，
    # 直接相除会得到 >100% 的「渗透率」，运营会以为数据坏了。
    credential_users = {row[0] for row in credential_rows} & active_users
    active_credential_users = {
        row[0] for row in credential_rows if row[1] == "active"
    } & active_users
    credential_counts = {"total": len(credential_rows), "active": 0, "invalid": 0, "disabled": 0}
    for _, status, _created in credential_rows:
        if status in credential_counts and status != "total":
            credential_counts[status] += 1

    # ===== 「配了但没用」：全站历史口径 =====
    # 有凭据，但**从未**跑过 BYOK 任务。这是推动切换的靶子名单：
    # 他们已经完成最麻烦的接入步骤，只差在任务里选一次自己的模型。
    cred_by_user: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for user_id, status, created_at in credential_rows:
        cred_by_user[user_id].append((status, created_at))
    dormant_ids = set(cred_by_user) - ever_byok_users
    dormant_stats: dict[uuid.UUID, dict[str, int]] = {}
    if dormant_ids:
        rows = (
            await session.execute(
                select(
                    Task.user_id,
                    func.count(Task.id),
                    func.coalesce(func.sum(Task.used_tokens), 0),
                )
                .where(Task.user_id.in_(dormant_ids))
                .group_by(Task.user_id)
            )
        ).all()
        dormant_stats = {
            row[0]: {"tasks": int(row[1] or 0), "tokens": int(row[2] or 0)} for row in rows
        }

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
    name_ids = list(set(top_ids) | dormant_ids)
    usernames: dict[uuid.UUID, str] = {}
    if name_ids:
        name_rows = (
            await session.execute(select(User.id, User.username).where(User.id.in_(name_ids)))
        ).all()
        usernames = {row[0]: row[1] for row in name_rows}

    top_users = []
    for uid in top_ids:
        user = per_user[uid]
        all_unpriced = (
            user["byok_tokens"] > 0 and user["byok_unpriced_tokens"] == user["byok_tokens"]
        )
        top_users.append(
            {
                "user_id": str(uid),
                "username": usernames.get(uid, "已删除用户"),
                "tasks": user["tasks"],
                "tokens": user["tokens"],
                "byok_tasks": user["byok_tasks"],
                "byok_tokens": user["byok_tokens"],
                "byok_token_share": _share(user["byok_tokens"], user["tokens"]),
                "byok_cost": None if all_unpriced else _money(user["byok_cost"]),
            }
        )

    dormant_users = []
    for uid in sorted(
        dormant_ids, key=lambda item: dormant_stats.get(item, {}).get("tokens", 0), reverse=True
    )[:20]:
        credentials = cred_by_user[uid]
        latest = max((created for _, created in credentials if created is not None), default=None)
        dormant_users.append(
            {
                "user_id": str(uid),
                "username": usernames.get(uid, "已删除用户"),
                "credentials": len(credentials),
                "active_credentials": sum(1 for status, _ in credentials if status == "active"),
                "latest_credential_at": latest,
                "days_since_credential": (
                    max(0, (end - _as_utc(latest)).days) if latest is not None else None
                ),
                "platform_tasks": dormant_stats.get(uid, {}).get("tasks", 0),
                "platform_tokens": dormant_stats.get(uid, {}).get("tokens", 0),
            }
        )

    providers = []
    for (provider, model), values in sorted(
        per_provider.items(), key=lambda item: item[1]["tokens"], reverse=True
    )[:10]:
        all_unpriced = values["tokens"] > 0 and values["unpriced_tokens"] == values["tokens"]
        providers.append(
            {
                "provider": provider,
                "model": model,
                "tasks": values["tasks"],
                "tokens": values["tokens"],
                "cost": None if all_unpriced else _money(values["cost"]),
                "unpriced_tokens": values["unpriced_tokens"],
            }
        )

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
        "cost": {
            "currency": "USD",
            "byok": _money(byok_cost),
            "platform": _money(platform_cost),
            "total": _money(byok_cost + platform_cost),
            "unpriced_byok_tokens": unpriced_byok_tokens,
            "unpriced_platform_tokens": unpriced_platform_tokens,
            "split_estimated_tokens": split_estimated_tokens,
        },
        "trend": [
            {
                "date": key,
                "tasks": values["tasks"],
                "tokens": values["tokens"],
                "byok_tasks": values["byok_tasks"],
                "byok_tokens": values["byok_tokens"],
                "cost": _money(values["cost"]),
                "byok_cost": _money(values["byok_cost"]),
            }
            for key, values in sorted(daily.items(), key=lambda item: item[0])
        ],
        "providers": providers,
        "top_users": top_users,
        "dormant_count": len(dormant_ids),
        "dormant_users": dormant_users,
    }
