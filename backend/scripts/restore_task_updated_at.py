"""恢复被回填脚本刷掉的 `task.updated_at`（2026-10-26 数据修复）。

## 事故经过

`backfill_used_tokens` 首版用 Core `update()` 改 `used_tokens`，而
`Base.updated_at` 带 `onupdate=func.now()` —— Core update 同样触发它。
结果 28 个历史任务的 `updated_at` 全被刷成同一个时刻（`2026-10-26 10:47:51`）。

影响面（实测）：

- `/observability/overview` 的「耗时」用 `updated_at - created_at` → 真实 90 秒
  显示成 **9708 分**（6.7 天）；
- 任务列表的「更新时间」同样错。

## 恢复依据（不猜，全部来自库里已有记录）

按优先级取第一个可用的真实活动时间：

1. `completed_at`（终态任务一定有，且它就是最后活动时刻）—— 23 个；
2. `execution_log.logged_at` 的最大值（执行日志最后一条）—— 覆盖剩余 5 个；
3. `subtask.updated_at` 的最大值（子任务最后更新）；
4. 都取不到 → **跳过并报出来**，不瞎填。

## 安全性

- 只改 `updated_at` 一列，且只改**被污染的那一批**；
- `--dry-run` 先列出将改的行与依据，人工确认后再执行；
- 恢复后回查：`updated_at >= created_at` 且耗时在合理范围（< 7 天）。

## 怎么认定「被污染的那一批」（不硬编码时刻）

批量 UPDATE 的特征是**多个任务共享完全相同的 `updated_at`**，而正常业务流程
每个任务的更新时间互不相同。脚本因此**自动检测**：找出 `updated_at` 出现次数
超过阈值（默认 5）的时刻作为污染时刻。

首版硬编码了一个时刻字符串，且按 naive datetime 构造 —— 而该列是
`TIMESTAMP WITH TIME ZONE`，asyncpg 返回带时区的值，比较永远不相等，
脚本报告「被污染任务数: 0」（比报错更危险：看起来像没事）。
自动检测同时解决了「时区怎么填」和「时刻记错」两个问题。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: 同一 `updated_at` 出现多少次算「批量 UPDATE」而不是正常业务。
#: 正常流程每个任务的更新时间互不相同；同秒并发最多两三个。
SUSPICIOUS_SHARED_COUNT = 5

#: 合理耗时上限（超过就说明恢复逻辑取错了时间源）
MAX_REASONABLE_DURATION = timedelta(days=7)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.restore_task_updated_at",
        description="恢复被回填脚本刷掉的 task.updated_at",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出将改的行与依据")
    parser.add_argument(
        "--polluted-at",
        default=None,
        help=(
            "手动指定污染时刻（ISO 格式，可带时区）。" "默认自动检测：找出多个任务共享的 updated_at"
        ),
    )
    return parser.parse_args(argv)


async def _detect_polluted_at() -> list[datetime]:
    """自动检测污染时刻：`updated_at` 被多个任务共享的时刻。

    为什么要自动检测而不是硬编码：批量 UPDATE 的时刻取决于脚本什么时候跑，
    写死在代码里既会过期、又要人肉填时区。而「多个任务共享同一 updated_at」
    这个特征本身就是异常——正常业务里每个任务更新时间互不相同。
    """
    from sqlalchemy import func, select

    from app.db.models.task import Task
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Task.updated_at, func.count().label("n"))
            .group_by(Task.updated_at)
            .having(func.count() >= SUSPICIOUS_SHARED_COUNT)
            .order_by(func.count().desc())
        )
        return [timestamp for timestamp, _count in rows]


async def _plan(polluted_at: datetime) -> list[dict[str, Any]]:
    """为每个被污染的任务算出应恢复的 updated_at 与依据。"""
    from sqlalchemy import func, select

    from app.db.models.log import ExecutionLog
    from app.db.models.task import Subtask, Task
    from app.db.session import AsyncSessionLocal

    plan: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Task.id, Task.status, Task.created_at, Task.completed_at).where(
                Task.updated_at == polluted_at
            )
        )
        for task_id, status, created_at, completed_at in rows:
            source = ""
            restored: datetime | None = None

            if completed_at is not None:
                restored, source = completed_at, "completed_at"
            else:
                last_log = await session.execute(
                    select(func.max(ExecutionLog.logged_at)).where(ExecutionLog.task_id == task_id)
                )
                candidate = last_log.scalar_one_or_none()
                if candidate is not None:
                    restored, source = candidate, "execution_log.logged_at"
                else:
                    last_subtask = await session.execute(
                        select(func.max(Subtask.updated_at)).where(Subtask.task_id == task_id)
                    )
                    candidate = last_subtask.scalar_one_or_none()
                    if candidate is not None:
                        restored, source = candidate, "subtask.updated_at"

            plan.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "created_at": created_at,
                    "restored": restored,
                    "source": source or "NONE",
                }
            )
    return plan


async def _apply(plan: list[dict[str, Any]]) -> int:
    """写回 updated_at（只改 updated_at 一列，且显式压掉 onupdate）。"""
    from sqlalchemy import update

    from app.db.models.task import Task
    from app.db.session import AsyncSessionLocal

    changed = 0
    async with AsyncSessionLocal() as session:
        for item in plan:
            if item["restored"] is None:
                continue
            result = await session.execute(
                update(Task).where(Task.id == item["task_id"]).values(updated_at=item["restored"])
            )
            changed += int(getattr(result, "rowcount", 0) or 0)
        await session.commit()
    return changed


async def _verify(polluted_times: list[datetime]) -> int:
    """回查：仍等于污染时刻的行数（期望 0）+ 耗时是否合理。"""
    from sqlalchemy import select

    from app.db.models.task import Task
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        remaining = await session.execute(
            select(Task.id, Task.created_at, Task.updated_at).where(
                Task.updated_at.in_(polluted_times)
            )
        )
        left = list(remaining)

        # 耗时合理性：所有任务的 updated_at 必须 >= created_at，且不超过 7 天
        bad = await session.execute(
            select(Task.id, Task.created_at, Task.updated_at).where(
                Task.updated_at < Task.created_at
            )
        )
        backwards = list(bad)

        too_long = await session.execute(
            select(Task.id, Task.created_at, Task.updated_at).where(
                Task.updated_at - Task.created_at > MAX_REASONABLE_DURATION
            )
        )
        outliers = list(too_long)

    print(f"[verify] 仍为污染时刻的任务数: {len(left)}")
    print(f"[verify] updated_at < created_at 的任务数: {len(backwards)}")
    print(f"[verify] 耗时超过 7 天的任务数: {len(outliers)}")
    for task_id, created, updated in outliers[:10]:
        print(f"[verify]   异常: {str(task_id)[:8]} created={created} updated={updated}")

    if left or backwards or outliers:
        return 1
    print("[verify] OK")
    return 0


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 污染时刻：手动指定优先，否则自动检测（见 _detect_polluted_at 的注释）
    if args.polluted_at:
        # 允许用户给「无时区」的本地时间：按系统本地时区补全，
        # 避免首版「naive 与 tz-aware 比较永远不等」的坑
        parsed = datetime.fromisoformat(args.polluted_at)
        polluted_list = [parsed if parsed.tzinfo else parsed.astimezone()]
    else:
        polluted_list = await _detect_polluted_at()

    if not polluted_list:
        print("[scan] 未检测到批量更新痕迹（没有多个任务共享同一 updated_at）")
        print("[scan] 若确认有污染，请用 --polluted-at 手动指定时刻")
        return 0

    print(f"[scan] 检测到 {len(polluted_list)} 个可疑批量更新时刻")
    total_plan: list[dict[str, Any]] = []
    for polluted_at in polluted_list:
        plan = await _plan(polluted_at)
        print(f"[scan] {polluted_at.isoformat()} → {len(plan)} 个任务")
        total_plan.extend(plan)

    restorable = [item for item in total_plan if item["restored"] is not None]
    unclaimable = [item for item in total_plan if item["restored"] is None]

    print(f"[scan] 被污染任务数: {len(total_plan)}")
    print(f"[scan] 可恢复: {len(restorable)}，无法恢复: {len(unclaimable)}")
    for item in restorable:
        duration = (item["restored"] - item["created_at"]).total_seconds()
        print(
            f"[scan]   {str(item['task_id'])[:8]} {item['status']:<15} "
            f"依据={item['source']:<24} 恢复后耗时={int(duration)}s"
        )
    for item in unclaimable:
        print(f"[scan]   {str(item['task_id'])[:8]} {item['status']:<15} 无可用时间源（跳过）")

    if args.dry_run:
        print("[dry-run] 未写库。")
        return 0

    changed = await _apply(total_plan)
    print(f"[apply] 实际恢复行数: {changed}")

    # AGENTS §5 #11：必须回查，不能只打印成功
    return await _verify(polluted_list)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
