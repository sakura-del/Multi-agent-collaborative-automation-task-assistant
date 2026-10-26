"""历史任务用量回填 CLI（2026-10-26 用量看板修）。

## 背景

`sum_actual_tokens` 曾只读 `output["prompt_tokens"]`，而生产载荷把 token 放在
`output["execution_result"]` 里 → 所有历史任务的 `task.used_tokens` 都是 0。

服务器实测（2026-10-26）：

- 42 个任务的 `subtask.output_data` 里能算出 **169372** token；
- `task.used_tokens` 全部为 0（含 36 个 `quota_settled=true` 的）。

修好读取逻辑只对**新任务**生效。存量数据不修，用量看板会继续显示「累计 0 token」——
用户看到的是错数字，而不是"新功能还没数据"。

## 为什么用脚本而不是迁移

1. **迁移不该做业务计算**：Alembic 迁移里解析 JSON 载荷、算 token 和，等于把
   业务逻辑塞进 schema 版本链，日后读迁移看不懂；
2. **可重跑**：`--dry-run` 先看规模，实际执行幂等（只写 `used_tokens == 0` 的行）；
3. **能只跑一次并回查**（AGENTS §5 #11：验证脚本必须回查，不能只打印成功）。

## 语义约束（重要）

- **只回填 `used_tokens == 0` 的任务**：已有非 0 值的是新逻辑写的，不能覆盖；
- **不回填 `quota_settled=true` 的任务的配额**：配额已按 0 用量结算过（退满了），
  现在补记用量不会去反向补扣用户额度——那会让用户莫名欠费。
  回填**只修看板数字**，不动钱。这条必须写进输出，避免误读；
- **不新增/不删除任何行**，只 UPDATE 一列。

## 用法

```bash
# 看规模（只读）
python -m scripts.backfill_used_tokens --dry-run

# 实际回填
python -m scripts.backfill_used_tokens

# 回查（AGENTS §5 #11：必须验证结果，不能只看「已回填」）
python -m scripts.backfill_used_tokens --verify
```
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.backfill_used_tokens",
        description="把历史任务的实际 token 用量从子任务载荷回填到 task.used_tokens",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计可回填的任务数与 token 总量，不写库",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="回查：统计仍为 0 但载荷里有 token 的任务（应为 0 条）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理多少个任务（0 = 不限；便于先小样验证）",
    )
    return parser.parse_args(argv)


def _tokens_from_payload(payload: Any) -> int:
    """从一份 `execution_result` 载荷读 token 合计（缺省按 0）。"""
    if not isinstance(payload, dict):
        return 0
    return int(payload.get("prompt_tokens") or 0) + int(payload.get("completion_tokens") or 0)


async def _collect() -> list[tuple[Any, int]]:
    """返回 [(task_id, 从载荷算出的真实 token)]，只含 `used_tokens == 0` 的任务。

    **与 `task_execution.sum_actual_tokens` 同一套读法**：嵌套优先、只算一次。
    刻意不复用那个函数是因为它是「按本次调度的 node_results 求和」，
    而这里要「按库里全部 subtask 求和」——续跑任务需要累加多段。
    """
    from sqlalchemy import select

    from app.db.models.task import Subtask, Task
    from app.db.session import AsyncSessionLocal

    result: list[tuple[Any, int]] = []
    async with AsyncSessionLocal() as session:
        # 只挑仍为 0 的任务（已有值的是新逻辑写的，不能覆盖）
        zero_rows = await session.execute(select(Task.id).where(Task.used_tokens == 0))
        zero_ids = [row[0] for row in zero_rows]

        for task_id in zero_ids:
            subtask_rows = await session.execute(
                select(Subtask.output_data).where(
                    Subtask.task_id == task_id,
                    Subtask.output_data.is_not(None),
                )
            )
            total = 0
            for (output_data,) in subtask_rows:
                if not isinstance(output_data, dict):
                    continue
                output = output_data.get("output")
                if not isinstance(output, dict):
                    continue
                nested = output.get("execution_result")
                if isinstance(nested, dict):
                    total += _tokens_from_payload(nested)
                else:
                    total += _tokens_from_payload(output)
            if total > 0:
                result.append((task_id, total))
    return result


async def _apply(pairs: list[tuple[Any, int]]) -> int:
    """把算出的用量写回 `task.used_tokens`（只写仍为 0 的行）。

    ## ⚠️ 必须显式保留 `updated_at`（2026-10-26 实测踩坑）

    `Base.updated_at` 带 `onupdate=func.now()`，**Core 的 `update()` 也会触发它**。
    首版没管这件事，结果 28 个历史任务的 `updated_at` 全被刷成同一个时刻，
    而 `/observability/overview` 的耗时用 `updated_at - created_at` 计算 →
    看板上的「耗时」从真实的 90 秒变成 **9708 分**（约 6.7 天）。

    这是**改了业务数据**，不只是"统计不准"：`updated_at` 是审计字段，
    任务列表的「更新时间」也用它排序展示。

    修法：在 `values()` 里显式带上 `updated_at=Task.updated_at`（即库里的原值），
    让 onupdate 被显式赋值覆盖。这样回填**只动 `used_tokens` 一列**。
    """
    from sqlalchemy import update

    from app.db.models.task import Task
    from app.db.session import AsyncSessionLocal

    updated = 0
    async with AsyncSessionLocal() as session:
        for task_id, tokens in pairs:
            # 条件里再断言 used_tokens == 0：防止并发写入被覆盖（幂等）
            result = await session.execute(
                update(Task)
                .where(Task.id == task_id, Task.used_tokens == 0)
                .values(
                    used_tokens=tokens,
                    # 显式写回原值，压掉 onupdate=func.now()
                    updated_at=Task.updated_at,
                )
            )
            # CursorResult 才有 rowcount（mypy 只看到 Result 基类）
            updated += int(getattr(result, "rowcount", 0) or 0)
        await session.commit()
    return updated


async def _verify() -> tuple[int, int]:
    """回查：仍为 0 但载荷里有 token 的任务数（期望 0），以及已回填总量。"""
    from sqlalchemy import func, select

    from app.db.models.task import Task
    from app.db.session import AsyncSessionLocal

    remaining = await _collect()
    async with AsyncSessionLocal() as session:
        total = await session.execute(select(func.coalesce(func.sum(Task.used_tokens), 0)))
    return len(remaining), int(total.scalar_one())


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.verify:
        remaining, total_tokens = await _verify()
        print(f"[verify] 仍为 0 但载荷里有 token 的任务数: {remaining}")
        print(f"[verify] task.used_tokens 全库合计: {total_tokens}")
        if remaining == 0:
            print("[verify] OK —— 没有漏回填的任务")
            return 0
        print(f"[verify] FAIL —— 还有 {remaining} 个任务未回填", file=sys.stderr)
        return 1

    pairs = await _collect()
    if args.limit > 0:
        pairs = pairs[: args.limit]

    total_tokens = sum(tokens for _, tokens in pairs)
    print(f"[scan] 可回填任务数: {len(pairs)}")
    print(f"[scan] 可回填 token 总量: {total_tokens}")

    if args.dry_run:
        print("[dry-run] 未写库。去掉 --dry-run 实际执行。")
        return 0

    updated = await _apply(pairs)
    print(f"[apply] 实际更新行数: {updated}")
    print(
        "[apply] 注意：已结算（quota_settled=true）的任务只修看板数字，"
        "**不反向补扣配额**（那会让用户莫名欠费）"
    )

    # AGENTS §5 #11：验证必须回查，不能只打印「已回填」
    remaining, total_tokens_after = await _verify()
    print(f"[verify] 回查仍为 0 的任务数: {remaining}")
    print(f"[verify] 回查 task.used_tokens 全库合计: {total_tokens_after}")
    if remaining != 0:
        print("[verify] FAIL —— 回填后仍有遗漏", file=sys.stderr)
        return 1
    print("[verify] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
