"""筛选数据访问：检索日志 / 候选记录 / 追加式决策。

约定（与既有 Repository 一致）：本层只 add/flush，不 commit；commit 由 service 层控制。

设计要点：
- **决策追加式**：`append_decision` 只新增行，从不更新既有行——审计轨迹与
  双人分歧追溯都依赖这一点；
- **冲突判定用「每人最新决策」**：同一筛选者可能改主意，拿历史行判定会误报；
- **排除必须有理由**：服务层抛 `ValueError`（给调用方可读错误），
  数据库另有 CheckConstraint 兜底（防止绕过服务层直接写库）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.screening import (
    DecisionStage,
    ScreeningDecision,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)
from app.db.repositories.base import BaseRepository

#: 单次查询候选记录的上限（防止一次加载过多）。
_MAX_RECORDS = 1000


@dataclass(frozen=True)
class Conflict:
    """一条记录在某个阶段出现筛选者结论分歧。"""

    record_id: uuid.UUID
    """冲突的候选记录。"""

    verdicts: dict[str, str]
    """`reviewer_id(str)` → 该筛选者的**最新**结论。"""


class ScreeningRepository(BaseRepository[ScreeningRecord]):
    """筛选工作台数据访问。"""

    model = ScreeningRecord

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    # ===== 检索日志 =====

    async def add_search_log(self, log: SearchLog) -> SearchLog:
        self.db.add(log)
        await self.db.flush()
        await self.db.refresh(log)
        return log

    async def list_search_logs(self, task_id: uuid.UUID) -> list[SearchLog]:
        """按执行时间正序列出任务的检索日志（复现顺序）。"""
        stmt = (
            select(SearchLog)
            .where(SearchLog.task_id == task_id)
            .order_by(SearchLog.executed_at.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    # ===== 候选记录 =====

    async def add_record(self, record: ScreeningRecord) -> ScreeningRecord:
        self.db.add(record)
        await self.db.flush()
        await self.db.refresh(record)
        return record

    def list_records_stmt(
        self,
        task_id: uuid.UUID,
        *,
        stage: DecisionStage | None = None,
        limit: int = 200,
    ) -> Select[tuple[ScreeningRecord]]:
        """构造候选记录查询（**排序稳定**）。

        为什么必须显式带 `id`：一次检索的多条记录写在同一个事务里，
        `created_at` 由 `server_default now()` 生成 → 时间戳完全相同。
        只按 `created_at` 排序时 PostgreSQL 对并列行返回**任意顺序**，
        用户每刷新一次候选列表顺序就可能变（找不到刚看的那条），
        浏览器 E2E 也会因「取第 N 行」而指到别的记录。
        （同类问题在 Task 2 已用 `screening_decision.seq` 修过一次。）
        """
        stmt = select(ScreeningRecord).where(ScreeningRecord.task_id == task_id)
        if stage is not None:
            stmt = stmt.where(ScreeningRecord.current_stage == stage.value)
        return stmt.order_by(
            ScreeningRecord.created_at.asc(),
            ScreeningRecord.id.asc(),
        ).limit(max(1, min(limit, _MAX_RECORDS)))

    async def list_records(
        self,
        task_id: uuid.UUID,
        *,
        stage: DecisionStage | None = None,
        limit: int = 200,
    ) -> list[ScreeningRecord]:
        """列出任务的候选记录；可按当前阶段过滤。"""
        stmt = self.list_records_stmt(task_id, stage=stage, limit=limit)
        return list((await self.db.execute(stmt)).scalars().all())

    # ===== 决策（追加式）=====

    async def append_decision(
        self,
        record_id: uuid.UUID,
        *,
        stage: DecisionStage,
        verdict: ScreeningVerdict,
        reviewer_id: uuid.UUID,
        exclusion_reason: str | None = None,
        note: str = "",
    ) -> ScreeningDecision:
        """追加一条决策（**不更新历史行**）。

        排除必须给理由：服务层抛 `ValueError`（调用方可映射成 400），
        数据库另有 CheckConstraint 兜底。

        `seq` 按当前最大值 +1 赋值：**不能依赖 `created_at` 排序**——同一事务内
        多行 `server_default` 的时间精度不足会并列，导致「最新决策」取错
        （本任务 TDD 时实际踩到）。并发下极端情况可能撞号，但决策是**追加**语义，
        撞号只会让排序在两条同时刻的决策间不确定，不影响正确性（都保留在历史里）。
        """
        if verdict is ScreeningVerdict.EXCLUDE and not (exclusion_reason or "").strip():
            raise ValueError("排除决策必须提供 exclusion_reason（PRISMA 2020 要求报告排除理由）")
        next_seq = await self._next_seq(record_id)
        row = ScreeningDecision(
            record_id=record_id,
            seq=next_seq,
            stage=stage.value,
            verdict=verdict.value,
            reviewer_id=reviewer_id,
            exclusion_reason=(exclusion_reason or "").strip() or None,
            note=note,
        )
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def _next_seq(self, record_id: uuid.UUID) -> int:
        """该记录已有决策的最大 `seq` + 1（无则 1）。"""
        stmt = select(func.max(ScreeningDecision.seq)).where(
            ScreeningDecision.record_id == record_id
        )
        current = (await self.db.execute(stmt)).scalar_one_or_none()
        return int(current or 0) + 1

    async def list_decisions(
        self, record_id: uuid.UUID, stage: DecisionStage | None = None
    ) -> list[ScreeningDecision]:
        """按**决策先后**列出某记录的决策历史（可限定阶段）。"""
        stmt = select(ScreeningDecision).where(ScreeningDecision.record_id == record_id)
        if stage is not None:
            stmt = stmt.where(ScreeningDecision.stage == stage.value)
        stmt = stmt.order_by(ScreeningDecision.seq.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def latest_decision(
        self, record_id: uuid.UUID, stage: DecisionStage, reviewer_id: uuid.UUID
    ) -> ScreeningDecision | None:
        """某筛选者对某记录某阶段的**最新**决策。"""
        stmt = (
            select(ScreeningDecision)
            .where(
                ScreeningDecision.record_id == record_id,
                ScreeningDecision.stage == stage.value,
                ScreeningDecision.reviewer_id == reviewer_id,
            )
            .order_by(ScreeningDecision.seq.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_conflicts(self, task_id: uuid.UUID, stage: DecisionStage) -> list[Conflict]:
        """找出「同一记录同一阶段，不同筛选者结论不一致」的记录。

        判定口径：取**每位筛选者的最新决策**，结论集合大小 > 1 即冲突。
        只被一个人筛过的记录不算冲突（尚未进入分歧阶段）。
        """
        records = await self.list_records(task_id, limit=_MAX_RECORDS)
        conflicts: list[Conflict] = []
        for record in records:
            decisions = await self.list_decisions(record.id, stage)
            if not decisions:
                continue
            # list_decisions 按时间正序，后者覆盖前者 → 得到每人最新
            latest: dict[str, str] = {}
            for decision in decisions:
                latest[str(decision.reviewer_id)] = decision.verdict
            if len(set(latest.values())) > 1:
                conflicts.append(Conflict(record_id=record.id, verdicts=latest))
        return conflicts


__all__ = ["Conflict", "ScreeningRepository"]
