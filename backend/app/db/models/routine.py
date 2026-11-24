"""TASK_ROUTINE / TASK_REFRESH：定时重跑与增量刷新记录（Issue #27）。

## 两张表各管什么

| 表 | 语义 | 基数 |
| --- | --- | --- |
| `task_routine` | 「这个任务多久重跑一次、跑完要不要发邮件」 | 每任务 0..1 条 |
| `task_refresh` | 每一次刷新的运行记录（谁触发、窗口、新增几篇、摘要正文） | 每任务 0..N 条 |

## 为什么刷新记录单独存表

刷新是**重复发生的事件**：要能按时间列历史、对比「这次新增 3 篇、上次 0 篇」。
塞进 `task.config` 的 JSON 既无法按时间查询，也会让 task 行随历史无限膨胀
（每次摘要正文有几 KB）。

## 为什么刷新不扣「篇数」

篇数是「本月能生成多少篇综述」。刷新不生成新综述，只是把同一篇的参考文献
增量补齐 —— 按篇扣费等于让用户为同一篇文章付两次钱。token 用量如实记在
本表（`tokens_used`），成本可查；BYOK 任务由用户自付。

## 为什么窗口要留 overlap

文献源有索引延迟（arXiv 的 announce 与 API 可见性差几天，OpenAlex 更新更慢）。
窗口严格从「上次运行时刻」开始会漏掉这段时间差里入库的文章，所以
`window_from = 上次运行 − overlap_days`，重复部分靠去重消化。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskRoutine(Base):
    """任务的重跑排期（每任务最多一条）。"""

    __tablename__ = "task_routine"
    __table_args__ = (UniqueConstraint("task_id", name="uq_task_routine_task"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="被排期的任务",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="任务所有者（配额与邮件都发给他）",
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False, comment="是否启用"
    )
    #: weekly / monthly。刻意不支持 daily：综述主题一天内不会长出新证据，
    #: 日更只会烧 token 和邮箱。
    cadence: Mapped[str] = mapped_column(
        String(16), default="weekly", nullable=False, comment="重跑频率：weekly / monthly"
    )
    #: none / email。email 只在「本期有新增」时发，避免每周一封「0 篇新增」的噪音。
    notify: Mapped[str] = mapped_column(
        String(16), default="email", nullable=False, comment="汇报方式：none / email"
    )

    #: 下次应跑的 UTC 时刻。调度器按 (enabled, next_run_at) 扫描。
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, comment="下次运行时刻（UTC）"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="上次运行时刻（UTC）"
    )
    #: 跨期增量记忆（Issue #42 轻量版）：存原综述已知空白、近几期已报要点与失效检索式，
    #: 供增量摘要 LOAD 并在刷新结束时确定性 OFFLOAD；None 表示尚未初始化。
    memory_brief: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="跨期增量状态记忆（结构化 JSON）"
    )


class TaskRefresh(Base):
    """一次增量刷新的运行记录。"""

    __tablename__ = "task_refresh"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="被刷新的任务",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="发起者（手动=本人，定时=任务所有者）",
    )

    #: manual / schedule
    trigger: Mapped[str] = mapped_column(
        String(16), default="manual", nullable=False, comment="触发方式：manual / schedule"
    )
    #: running / completed / failed。completed 且 new_count=0 就是「本期无新增」。
    status: Mapped[str] = mapped_column(
        String(16), default="running", index=True, nullable=False, comment="运行状态"
    )

    #: 本次检索窗口（UTC）。窗口左端 = 上次运行 − overlap_days
    window_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 实际使用的检索式（来自任务计划的 search_queries，最多取前 5 条）
    queries: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    hits_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="上游命中总数（去重前）"
    )
    new_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="本次新增文献数"
    )
    duplicate_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
        comment="与前次重复（含跨源重复）的条数",
    )
    #: 各源命中原生分布，例如 {"arxiv": 12, "openalex": 9}
    sources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    #: 增量摘要（Markdown）。要点由模型生成，文末的「新增文献」清单是确定性拼装，
    #: 保证 `[E#]` 与编号一一对应（引用一致性闸门要求）。
    summary_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 摘要是否通过引用一致性校验；不通过时已回落到确定性清单（仍可用）
    citation_ok: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )

    tokens_used: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False, comment="本次摘要消耗 token"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: 是否已发送汇报邮件（幂等：调度重试时不重复发信）
    notified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False, comment="是否已发汇报邮件"
    )
