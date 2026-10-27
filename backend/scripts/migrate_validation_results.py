"""历史校验结论迁移 CLI（2026-10-27 校验落库的存量补齐）。

## 背景

`validation_result` 表从建库起就存在，但直到 `90dfd6a`（2026-10-27）才接上写入方。
在此之前，校验结论只被塞进 `subtask.output_data` 的 JSON —— 不可查询、不可聚合。

服务器实测（2026-10-27）：

- **188 个 subtask** 的 `output_data.validation` 非空；
- `validation_result` 表 **0 行**。

修好写入只对**新任务**生效。不迁移的话，`validation_result` 永远缺这段历史，
「校验通过率」「按失败原因聚合」只能覆盖新任务 —— 而这正是当初加这张表的目的。

## 为什么用脚本而不是 Alembic 迁移

与 `backfill_used_tokens.py` 同一理由：

1. **迁移不该做业务计算**：Alembic 里解析 JSON 载荷、归一化分数，等于把业务逻辑
   塞进 schema 版本链，日后读迁移看不懂；
2. **可重跑**：`--dry-run` 先看规模，实际执行幂等（只补表里还没有的 subtask）；
3. **能只跑一次并回查**（AGENTS §5 #11：验证脚本必须回查，不能只打印成功）。

## 语义约束（重要）

- **只补 `validation_result` 里还没有的 subtask** —— 幂等，重跑不会产生重复行；
- **`score` 必须归一化到 0–1**：历史载荷里是 **80.0~100.0**（契约 0–100），
  而列语义是 0–1。与 `app/db/repositories/validation.py::normalize_score` 同一套规则，
  直接复用那个函数，**不另写一份**（否则两处规则漂移）；
- **不改 `output_data`**：只新增表行。快照/复盘 Agent 还在读它；
- **缺字段从严**：`passed` 非 true 即 False、`score` 缺失即 0.0、`checks` 缺失即空列表。
  历史里确实有 16 条只有 `score`/`passed` 的载荷，不能崩。

## 用法

```bash
# 看规模（只读）
python -m scripts.migrate_validation_results --dry-run

# 实际迁移
python -m scripts.migrate_validation_results

# 回查（AGENTS §5 #11：必须验证结果）
python -m scripts.migrate_validation_results --verify
```
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@dataclass(frozen=True)
class Candidate:
    """一个待迁移的 subtask 及其历史校验载荷。"""

    subtask_id: UUID
    payload: dict[str, Any]


def payload_to_row_values(payload: Any) -> dict[str, Any]:
    """历史载荷 → `ValidationResult` 列值。

    形状参照服务器 188 条实测（AGENTS §5 #33：夹具照抄真实载荷）：
    `score`/`passed` 全有；`checks`/`feedback`/`retryable` 有 172 条；
    `semantic_check`/`rule_check` 有 41 条。

    缺字段一律**从严**：宁可留下「有失败」，也不要伪造「通过」。
    """
    from app.db.repositories.validation import normalize_score

    body = payload if isinstance(payload, dict) else {}

    checks = body.get("checks")
    rule_results = checks if isinstance(checks, list) else []

    semantic = body.get("semantic_check")
    llm_report = semantic if isinstance(semantic, dict) and semantic else None

    feedback = body.get("feedback")

    return {
        "passed": body.get("passed") is True,
        # 契约 0–100 → 列 0–1，复用仓储层同一函数，避免两处规则漂移
        "score": normalize_score(body.get("score")),
        "rule_results": rule_results,
        "llm_report": llm_report,
        "report": str(feedback) if feedback is not None else None,
        "validated_at": datetime.now(UTC),
    }


async def collect_candidates(session: Any) -> list[Candidate]:
    """找出「有历史校验载荷、但表里还没有行」的 subtask。"""
    from sqlalchemy import select

    from app.db.models.log import ValidationResult
    from app.db.models.task import Subtask

    migrated = select(ValidationResult.subtask_id)

    rows = await session.execute(
        select(Subtask.id, Subtask.output_data).where(
            Subtask.output_data.is_not(None),
            Subtask.id.not_in(migrated),
        )
    )

    candidates: list[Candidate] = []
    for subtask_id, output_data in rows:
        if not isinstance(output_data, dict):
            continue
        validation = output_data.get("validation")
        # `null` 与缺键都视为「没有校验」（search 节点就是这样）
        if not isinstance(validation, dict) or not validation:
            continue
        candidates.append(Candidate(subtask_id=subtask_id, payload=validation))
    return candidates


async def migrate(session: Any, candidates: list[Candidate]) -> int:
    """写入候选行；只 flush，commit 由调用方控制。返回写入行数。"""
    from app.db.models.log import ValidationResult

    written = 0
    for candidate in candidates:
        values = payload_to_row_values(candidate.payload)
        session.add(ValidationResult(subtask_id=candidate.subtask_id, **values))
        written += 1
    if written:
        await session.flush()
    return written


async def _counts(session: Any) -> tuple[int, int]:
    """返回 (带校验载荷的 subtask 数, 表行数)。"""
    from sqlalchemy import func, select

    from app.db.models.log import ValidationResult
    from app.db.models.task import Subtask

    rows = await session.execute(
        select(Subtask.output_data).where(Subtask.output_data.is_not(None))
    )
    with_validation = 0
    for (output_data,) in rows:
        if not isinstance(output_data, dict):
            continue
        validation = output_data.get("validation")
        if isinstance(validation, dict) and validation:
            with_validation += 1

    table_rows = await session.scalar(select(func.count()).select_from(ValidationResult))
    return with_validation, int(table_rows or 0)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_validation_results",
        description="把历史 subtask 载荷里的校验结论迁移到 validation_result 表",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计规模，不写库")
    parser.add_argument("--verify", action="store_true", help="回查：仍待迁移的数量（应为 0）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条（0 = 不限）")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    from app.db.session import AsyncSessionLocal

    args = _parse_args(argv)

    if args.verify:
        async with AsyncSessionLocal() as session:
            candidates = await collect_candidates(session)
            with_validation, table_rows = await _counts(session)
        print(f"[verify] 带校验载荷的 subtask 数: {with_validation}")
        print(f"[verify] validation_result 表行数: {table_rows}")
        print(f"[verify] 仍待迁移: {len(candidates)}")
        if not candidates:
            print("[verify] OK —— 没有漏迁移的校验结论")
            return 0
        print(f"[verify] FAIL —— 还有 {len(candidates)} 条未迁移", file=sys.stderr)
        return 1

    async with AsyncSessionLocal() as session:
        candidates = await collect_candidates(session)
        with_validation, table_rows = await _counts(session)

    if args.limit > 0:
        candidates = candidates[: args.limit]

    print(f"[scan] 带校验载荷的 subtask 数: {with_validation}")
    print(f"[scan] validation_result 现有行数: {table_rows}")
    print(f"[scan] 待迁移条数: {len(candidates)}")

    if args.dry_run:
        print("[dry-run] 未写库。去掉 --dry-run 实际执行。")
        return 0

    async with AsyncSessionLocal() as session:
        written = await migrate(session, candidates)
        await session.commit()
    print(f"[apply] 实际写入行数: {written}")

    # AGENTS §5 #11：必须回查，不能只打印「已迁移」
    async with AsyncSessionLocal() as session:
        remaining = await collect_candidates(session)
        with_validation_after, table_rows_after = await _counts(session)
    print(f"[verify] 回查带校验载荷的 subtask 数: {with_validation_after}")
    print(f"[verify] 回查 validation_result 行数: {table_rows_after}")
    print(f"[verify] 回查仍待迁移: {len(remaining)}")
    if remaining:
        print("[verify] FAIL —— 迁移后仍有遗漏", file=sys.stderr)
        return 1
    print("[verify] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
