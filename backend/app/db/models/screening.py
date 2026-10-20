"""筛选工作台数据模型（Phase 2 · PRISMA）。

设计要点（见计划 §0.2 冻结决策）：
- 检索结果**落快照**（`ScreeningRecord`），筛选不依赖实时重查，保证可复现；
- 决策**追加式**（`ScreeningDecision` 每次新增一行），当前状态由最新决策派生，
  这样才有审计轨迹与双人分歧追溯；
- PRISMA 各阶段计数**不落库**，由明细派生（避免汇总与明细不一致）；
- `SearchLog.hits_by_source` 保存**去重前**的各源命中数——PRISMA「识别」阶段的
  计数依据；若从去重后的快照反推，会让「各源合计」小于 `hits_total`，流程图自相矛盾。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DecisionStage(StrEnum):
    """筛选阶段（PRISMA 2020 三阶段）。"""

    TITLE_ABSTRACT = "title_abstract"
    """标题/摘要筛选。"""

    FULLTEXT = "fulltext"
    """全文评估。"""

    INCLUDED = "included"
    """最终纳入（终态）。"""


class ScreeningVerdict(StrEnum):
    """单次决策结论。"""

    INCLUDE = "include"
    """纳入（推进到下一阶段）。"""

    EXCLUDE = "exclude"
    """排除（必须给理由）。"""

    MAYBE = "maybe"
    """待定（停留当前阶段，待复核）。"""


class FulltextStatus(StrEnum):
    """全文可得性。

    版权约束下多数源只给元数据 + 摘要；无全文的记录必须显式标记，
    PRISMA 2020 允许报告「未获取全文」。
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_REQUIRED = "not_required"


class SearchLog(Base):
    """一次检索的**可复现日志**：检索式、源、命中数、去重后数。

    这是 PRISMA「识别」阶段计数的唯一真源。
    """

    __tablename__ = "search_log"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, comment="检索日志ID"
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="所属任务",
    )
    query: Mapped[str] = mapped_column(Text, nullable=False, comment="检索式（原样保存）")
    sources: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, comment="本次检索的源列表"
    )
    hits_total: Mapped[int] = mapped_column(Integer, nullable=False, comment="各源命中数之和")
    hits_by_source: Mapped[dict[str, int]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="各源原始命中数（去重前）——PRISMA「识别」阶段计数的依据",
    )
    hits_deduped: Mapped[int] = mapped_column(Integer, nullable=False, comment="去重后记录数")
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="检索执行时间（UTC）",
    )


class ScreeningRecord(Base):
    """一条候选文献（检索结果快照）。"""

    __tablename__ = "screening_record"
    __table_args__ = (
        UniqueConstraint(
            "search_log_id", "source", "source_id", name="uq_screening_record_log_source_id"
        ),
        Index("ix_screening_record_task_stage", "task_id", "current_stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="记录ID")
    search_log_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("search_log.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="来源检索",
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="所属任务",
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="来源（arxiv/openalex/…）"
    )
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="来源内 ID")
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="标题")
    authors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="作者")
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="摘要")
    doi: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="归一化 DOI")
    fulltext_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=FulltextStatus.UNAVAILABLE.value,
        comment="全文可得性",
    )
    current_stage: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=DecisionStage.TITLE_ABSTRACT.value,
        comment="当前所处阶段（由最新决策派生后缓存）",
    )


class ScreeningDecision(Base):
    """一次筛选决策（**追加式**，不覆盖）。

    当前状态由「该记录该阶段的最新决策」派生；历史行保留作审计轨迹。
    """

    __tablename__ = "screening_decision"
    __table_args__ = (
        # 排除必须给理由（PRISMA 2020 要求报告排除理由分类）
        CheckConstraint(
            "verdict != 'exclude' OR (exclusion_reason IS NOT NULL AND exclusion_reason != '')",
            name="ck_screening_decision_exclude_requires_reason",
        ),
        Index("ix_screening_decision_record_stage", "record_id", "stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="决策ID")
    record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("screening_record.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="候选记录",
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False, comment="筛选阶段")
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, comment="结论")
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="筛选者",
    )
    exclusion_reason: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="排除理由（PRISMA 分类）"
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注")


__all__ = [
    "DecisionStage",
    "FulltextStatus",
    "ScreeningDecision",
    "ScreeningRecord",
    "ScreeningVerdict",
    "SearchLog",
]
