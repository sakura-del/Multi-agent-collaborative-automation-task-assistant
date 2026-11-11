# PRISMA 筛选工作台 + 可复现检索日志 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 让用户能对检索结果做三阶段 PRISMA 筛选（含双人独立与分歧解决），并把检索式与各阶段计数持久化为可复现、可导出的日志与流程图。

**Architecture:** 检索结果先落库为 `search_log` + `screening_record` 快照（不依赖实时重查），筛选决策以**追加式** `screening_decision` 记录（含筛选者/阶段/决定/理由），当前状态与 PRISMA 各阶段计数全部由这两类明细**派生**（不另存汇总数字，避免汇总与明细不一致）。HTTP 层只做校验与响应映射，业务在 `ScreeningService` 深模块。

**Tech Stack:** FastAPI · SQLAlchemy 2.0 (async) · Alembic · Pydantic v2 · pytest/pytest-asyncio · Vue 3 + Pinia + Vitest

**上游：** [Phase 2 总纲](2026-10-20-00-phase2-overview.md) · [产品路线图](../product/2026-10-18-demo-to-product-roadmap.md) §5

---

## 0. 背景与冻结决策

### 0.1 为什么做这个

路线图 §3 诊断出的核心断层：**「检索↔筛选」**——Elicit 检索不跑 PRISMA，
Rayyan/Covidence 跑流程但不检索。谁打通中间段谁就有差异化。
同时 PRISMA 2020 是系统综述的**发表门槛**：检索式、各库命中数、排除理由
都必须可报告。当前系统**完全没有**这些能力。

### 0.2 冻结决策（实现方不得擅自更改）

1. **筛选对象是检索结果快照**，不是实时检索。上游源内容会变，若筛选时重查，
   同一份 PRISMA 数字将无法复现。
2. **决策追加式**：每次决策新增一行，**不 update 覆盖**。当前状态由
   「该记录该阶段的最新决策」派生。这样才有审计轨迹与分歧追溯。
3. **PRISMA 计数派生，不落库**：`SearchLog`/`ScreeningRecord`/`ScreeningDecision`
   是唯一真源；流程图数字实时算。
4. **不自动筛选**：本计划只做人工工作台 + 记录 + 导出。AI 辅助筛选需单独立项
   （PRISMA 要求人工决策可追溯，自动筛选易被质疑方法学）。
5. **全文阶段只对「有全文」的记录开放**：版权约束下多数源只给元数据+摘要；
   无全文的记录明确标记 `fulltext_status=unavailable` 并记录该状态
   （PRISMA 2020 允许报告「未获取全文」）。

### 0.3 PRISMA 2020 计数项（数据模型的依据）

流程图需要的 27 个计数项，本计划覆盖其中可在本系统内产生的主要项：

| 阶段 | 计数项 | 来源 |
| --- | --- | --- |
| 识别 | 各数据库/登记平台命中数 | `search_log.hits_total` 按 `source` 分组 |
| 识别 | 去重后记录数 | `search_log.hits_deduped` |
| 筛选 | 标题摘要阶段筛查数 / 排除数 | `screening_decision` 阶段=`title_abstract` |
| 筛选 | 全文阶段评估数 / 排除数（按理由分类） | 阶段=`fulltext` + `exclusion_reason` |
| 纳入 | 最终纳入研究数 | 阶段=`included` 的最新决策为 include |
| 排除理由 | 各类排除理由计数 | `screening_decision.exclusion_reason` 分组 |

---

## 1. 文件结构

| 文件 | 职责 |
| --- | --- |
| `backend/app/db/models/screening.py` | ORM：`SearchLog` / `ScreeningRecord` / `ScreeningDecision` |
| `backend/app/db/repositories/screening.py` | 三张表的查询与追加写入 |
| `backend/app/schemas/screening.py` | 请求/响应契约（`extra="forbid"`） |
| `backend/app/knowledge/services/screening_service.py` | 深模块：记录检索、落快照、追加决策、派生状态、算 PRISMA 计数 |
| `backend/app/tools/prisma/flow.py` | 纯函数：计数 → PRISMA 2020 流程图（Markdown 表格 + Mermaid） |
| `backend/app/api/v1/endpoints/screening.py` | HTTP adapter |
| `backend/alembic/versions/<rev>_add_screening.py` | 迁移（含 `created_at`/`updated_at`） |
| `backend/tests/test_screening_models.py` | ORM + 迁移一致性 |
| `backend/tests/test_screening_service.py` | 服务层：追加决策、状态派生、双人分歧 |
| `backend/tests/test_prisma_flow.py` | 流程图纯函数 |
| `backend/tests/test_screening_api.py` | HTTP 端点 |
| `frontend/src/api/screening.ts` + `.test.ts` | 前端 API 层 |
| `frontend/src/views/ScreeningView.vue` + `.test.ts` | 筛选工作台页面 |

**Lead 独占**（其他任务不要改）：`app/db/models/__init__.py`、`app/api/v1/router.py`、
`config/default.yaml`、`app/core/config.py`。

---

## Task 1: ORM 模型与迁移

**Files:**
- Create: `backend/app/db/models/screening.py`
- Create: `backend/alembic/versions/b8e5f1c3d9a2_add_screening.py`
- Modify: `backend/app/db/models/__init__.py`（**Lead 做**）
- Test: `backend/tests/test_screening_models.py`

- [x] **Step 1: 写失败测试**

```python
# backend/tests/test_screening_models.py
"""筛选数据层测试：ORM 约束 + 迁移与模型一致。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.screening import (
    DecisionStage,
    ScreeningDecision,
    ScreeningRecord,
    SearchLog,
    ScreeningVerdict,
)


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_search_log_persists_query_and_counts() -> None:
    engine = _engine()
    with Session(engine) as session:
        log = SearchLog(
            task_id=uuid.uuid4(),
            query="retrieval augmented generation",
            sources=["openalex", "arxiv"],
            hits_total=42,
            hits_by_source={"openalex": 30, "arxiv": 12},
            hits_deduped=30,
        )
        session.add(log)
        session.commit()
        assert log.id is not None
        assert log.hits_total == 42


def test_screening_record_unique_per_log_and_source_id() -> None:
    """同一检索日志下，同一来源记录不得重复落库（去重的数据层保证）。"""
    engine = _engine()
    task_id = uuid.uuid4()
    with Session(engine) as session:
        log = SearchLog(
            task_id=task_id,
            query="q" * 5,
            sources=["arxiv"],
            hits_total=1,
            hits_by_source={"arxiv": 1},
            hits_deduped=1,
        )
        session.add(log)
        session.flush()
        session.add(
            ScreeningRecord(
                search_log_id=log.id,
                task_id=task_id,
                source="arxiv",
                source_id="2401.00001v1",
                title="T",
                fulltext_status="unavailable",
            )
        )
        session.commit()

        session.add(
            ScreeningRecord(
                search_log_id=log.id,
                task_id=task_id,
                source="arxiv",
                source_id="2401.00001v1",
                title="T again",
                fulltext_status="unavailable",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_decision_stage_and_verdict_enums() -> None:
    assert DecisionStage.TITLE_ABSTRACT.value == "title_abstract"
    assert DecisionStage.FULLTEXT.value == "fulltext"
    assert DecisionStage.INCLUDED.value == "included"
    assert ScreeningVerdict.INCLUDE.value == "include"
    assert ScreeningVerdict.EXCLUDE.value == "exclude"
    assert ScreeningVerdict.MAYBE.value == "maybe"


def test_decision_requires_reason_when_excluding() -> None:
    """排除必须给理由（PRISMA 2020 要求报告排除理由分类）。"""
    engine = _engine()
    task_id = uuid.uuid4()
    with Session(engine) as session:
        log = SearchLog(
            task_id=task_id,
            query="q" * 5,
            sources=["arxiv"],
            hits_total=1,
            hits_by_source={"arxiv": 1},
            hits_deduped=1,
        )
        session.add(log)
        session.flush()
        record = ScreeningRecord(
            search_log_id=log.id,
            task_id=task_id,
            source="arxiv",
            source_id="x",
            title="T",
            fulltext_status="unavailable",
        )
        session.add(record)
        session.flush()
        session.add(
            ScreeningDecision(
                record_id=record.id,
                stage=DecisionStage.TITLE_ABSTRACT.value,
                verdict=ScreeningVerdict.EXCLUDE.value,
                reviewer_id=uuid.uuid4(),
                exclusion_reason=None,  # 缺理由
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_migration_creates_all_orm_columns() -> None:
    """迁移建出的列必须覆盖 ORM 全部列（Phase 1 的 email_token 生产 500 教训）。"""
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b8e5f1c3d9a2_add_screening.py"
    spec = importlib.util.spec_from_file_location("m", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        # 前置表（FK 目标）：最小 DDL
        connection.exec_driver_sql(
            "CREATE TABLE task (id CHAR(32) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE user (id CHAR(32) PRIMARY KEY)"
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()

        for table in ("search_log", "screening_record", "screening_decision"):
            orm_columns = {c.name for c in Base.metadata.tables[table].columns}
            migrated = {c["name"] for c in inspect(connection).get_columns(table)}
            assert not (orm_columns - migrated), f"{table} 迁移缺列：{sorted(orm_columns - migrated)}"
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_models.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db.models.screening'`

- [x] **Step 3: 写实现**

```python
# backend/app/db/models/screening.py
"""筛选工作台数据模型（Phase 2 · PRISMA）。

设计要点（见计划 §0.2 冻结决策）：
- 检索结果**落快照**（`ScreeningRecord`），筛选不依赖实时重查，保证可复现；
- 决策**追加式**（`ScreeningDecision` 每次新增一行），当前状态由最新决策派生，
  这样才有审计轨迹与双人分歧追溯；
- PRISMA 各阶段计数**不落库**，由明细派生（避免汇总与明细不一致）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DecisionStage(StrEnum):
    """筛选阶段（PRISMA 2020 三阶段）。"""

    TITLE_ABSTRACT = "title_abstract"
    FULLTEXT = "fulltext"
    INCLUDED = "included"


class ScreeningVerdict(StrEnum):
    """单次决策结论。"""

    INCLUDE = "include"
    EXCLUDE = "exclude"
    MAYBE = "maybe"


class FulltextStatus(StrEnum):
    """全文可得性（版权约束下多数源只有元数据+摘要）。"""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_REQUIRED = "not_required"


class SearchLog(Base):
    """一次检索的**可复现日志**：检索式、源、命中数、去重后数。

    这是 PRISMA「识别」阶段计数的唯一真源。
    """

    __tablename__ = "search_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="检索日志ID")
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"), index=True, nullable=False, comment="所属任务"
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
        DateTime(timezone=True), nullable=False, comment="检索执行时间（UTC）"
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
        ForeignKey("search_log.id", ondelete="CASCADE"), index=True, nullable=False, comment="来源检索"
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"), index=True, nullable=False, comment="所属任务"
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, comment="来源（arxiv/openalex/…）")
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="来源内 ID")
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="标题")
    authors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="作者")
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="摘要")
    doi: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="归一化 DOI")
    fulltext_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FulltextStatus.UNAVAILABLE.value, comment="全文可得性"
    )
    current_stage: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DecisionStage.TITLE_ABSTRACT.value, comment="当前所处阶段（派生缓存）"
    )


class ScreeningDecision(Base):
    """一次筛选决策（**追加式**，不覆盖）。"""

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
        ForeignKey("screening_record.id", ondelete="CASCADE"), index=True, nullable=False, comment="候选记录"
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False, comment="筛选阶段")
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, comment="结论")
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False, comment="筛选者"
    )
    exclusion_reason: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="排除理由（PRISMA 分类）"
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注")
```

迁移文件 `backend/alembic/versions/b8e5f1c3d9a2_add_screening.py`：

```python
"""add screening workbench tables

Revision ID: b8e5f1c3d9a2
Revises: a7d4e2f9c1b8
Create Date: 2026-10-20 17:00:00.000000

Phase 2：PRISMA 筛选工作台（检索日志 / 候选记录 / 追加式决策）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e5f1c3d9a2"
down_revision: str | None = "a7d4e2f9c1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_log",
        sa.Column("id", sa.Uuid(), nullable=False, comment="检索日志ID"),
        sa.Column("task_id", sa.Uuid(), nullable=False, comment="所属任务"),
        sa.Column("query", sa.Text(), nullable=False, comment="检索式（原样保存）"),
        sa.Column("sources", sa.JSON(), nullable=False, comment="本次检索的源列表"),
        sa.Column("hits_total", sa.Integer(), nullable=False, comment="各源命中数之和"),
        sa.Column("hits_by_source", sa.JSON(), nullable=False, comment="各源原始命中数（去重前）"),
        sa.Column("hits_deduped", sa.Integer(), nullable=False, comment="去重后记录数"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False, comment="检索执行时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="更新时间"),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], name="fk_search_log_task_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_search_log"),
        comment="检索可复现日志",
    )
    op.create_index("ix_search_log_task_id", "search_log", ["task_id"], unique=False)

    op.create_table(
        "screening_record",
        sa.Column("id", sa.Uuid(), nullable=False, comment="记录ID"),
        sa.Column("search_log_id", sa.Uuid(), nullable=False, comment="来源检索"),
        sa.Column("task_id", sa.Uuid(), nullable=False, comment="所属任务"),
        sa.Column("source", sa.String(length=32), nullable=False, comment="来源"),
        sa.Column("source_id", sa.String(length=64), nullable=False, comment="来源内 ID"),
        sa.Column("title", sa.Text(), nullable=False, comment="标题"),
        sa.Column("authors", sa.JSON(), nullable=False, comment="作者"),
        sa.Column("abstract", sa.Text(), nullable=False, comment="摘要"),
        sa.Column("doi", sa.String(length=256), nullable=True, comment="归一化 DOI"),
        sa.Column("fulltext_status", sa.String(length=16), nullable=False, comment="全文可得性"),
        sa.Column("current_stage", sa.String(length=16), nullable=False, comment="当前阶段"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="更新时间"),
        sa.ForeignKeyConstraint(["search_log_id"], ["search_log.id"], name="fk_screening_record_search_log_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], name="fk_screening_record_task_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_screening_record"),
        sa.UniqueConstraint("search_log_id", "source", "source_id", name="uq_screening_record_log_source_id"),
        comment="候选文献（检索结果快照）",
    )
    op.create_index("ix_screening_record_search_log_id", "screening_record", ["search_log_id"], unique=False)
    op.create_index("ix_screening_record_task_id", "screening_record", ["task_id"], unique=False)
    op.create_index("ix_screening_record_task_stage", "screening_record", ["task_id", "current_stage"], unique=False)

    op.create_table(
        "screening_decision",
        sa.Column("id", sa.Uuid(), nullable=False, comment="决策ID"),
        sa.Column("record_id", sa.Uuid(), nullable=False, comment="候选记录"),
        sa.Column("stage", sa.String(length=16), nullable=False, comment="筛选阶段"),
        sa.Column("verdict", sa.String(length=16), nullable=False, comment="结论"),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False, comment="筛选者"),
        sa.Column("exclusion_reason", sa.String(length=256), nullable=True, comment="排除理由"),
        sa.Column("note", sa.Text(), nullable=False, comment="备注"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="更新时间"),
        sa.CheckConstraint(
            "verdict != 'exclude' OR (exclusion_reason IS NOT NULL AND exclusion_reason != '')",
            name="ck_screening_decision_exclude_requires_reason",
        ),
        sa.ForeignKeyConstraint(["record_id"], ["screening_record.id"], name="fk_screening_decision_record_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["user.id"], name="fk_screening_decision_reviewer_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_screening_decision"),
        comment="筛选决策（追加式）",
    )
    op.create_index("ix_screening_decision_record_id", "screening_decision", ["record_id"], unique=False)
    op.create_index("ix_screening_decision_reviewer_id", "screening_decision", ["reviewer_id"], unique=False)
    op.create_index("ix_screening_decision_record_stage", "screening_decision", ["record_id", "stage"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_screening_decision_record_stage", table_name="screening_decision")
    op.drop_index("ix_screening_decision_reviewer_id", table_name="screening_decision")
    op.drop_index("ix_screening_decision_record_id", table_name="screening_decision")
    op.drop_table("screening_decision")
    op.drop_index("ix_screening_record_task_stage", table_name="screening_record")
    op.drop_index("ix_screening_record_task_id", table_name="screening_record")
    op.drop_index("ix_screening_record_search_log_id", table_name="screening_record")
    op.drop_table("screening_record")
    op.drop_index("ix_search_log_task_id", table_name="search_log")
    op.drop_table("search_log")
```

- [x] **Step 4: 跑测试确认通过**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_models.py -q --no-cov`
Expected: PASS（5 passed）

同时验证迁移链单 head 与全新库可升级：

```powershell
.\.venv\Scripts\python.exe -m alembic heads          # 期望：b8e5f1c3d9a2 (head)
.\.venv\Scripts\python.exe -m pytest tests/test_migration_schema_consistency.py -q --no-cov
```

- [x] **Step 5: Lead 接线 + 提交**

Lead 执行（**其他任务不要改这些文件**）：
1. `app/db/models/__init__.py` 导出 `SearchLog` / `ScreeningRecord` / `ScreeningDecision`
2. 跑 `black app tests` / `ruff check --fix` / `mypy app`

```bash
git add backend/app/db/models/screening.py backend/alembic/versions/b8e5f1c3d9a2_add_screening.py \
        backend/tests/test_screening_models.py backend/app/db/models/__init__.py
git commit -m "feat(screening): add PRISMA screening data model and migration"
```

---

## Task 2: Repository 层

**Files:**
- Create: `backend/app/db/repositories/screening.py`
- Test: `backend/tests/test_screening_repository.py`

- [x] **Step 1: 写失败测试**

```python
# backend/tests/test_screening_repository.py
"""筛选 Repository 测试：追加式决策、最新决策派生、分歧查询。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.screening import (
    DecisionStage,
    ScreeningDecision,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)
from app.db.repositories.screening import ScreeningRepository


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[ScreeningRecord, uuid.UUID, uuid.UUID]:
    task_id = uuid.uuid4()
    reviewer_a, reviewer_b = uuid.uuid4(), uuid.uuid4()
    log = SearchLog(
        task_id=task_id,
        query="q" * 5,
        sources=["arxiv"],
        hits_total=1,
        hits_by_source={"arxiv": 1},
        hits_deduped=1,
    )
    session.add(log)
    await session.flush()
    record = ScreeningRecord(
        search_log_id=log.id,
        task_id=task_id,
        source="arxiv",
        source_id="x1",
        title="T",
        fulltext_status="unavailable",
    )
    session.add(record)
    await session.flush()
    return record, reviewer_a, reviewer_b


async def test_latest_decision_is_the_newest_row(session: AsyncSession) -> None:
    record, reviewer_a, _ = await _seed(session)
    repo = ScreeningRepository(session)

    await repo.append_decision(
        record.id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.MAYBE,
        reviewer_id=reviewer_a,
    )
    await repo.append_decision(
        record.id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.INCLUDE,
        reviewer_id=reviewer_a,
    )
    await session.commit()

    latest = await repo.latest_decision(record.id, DecisionStage.TITLE_ABSTRACT, reviewer_a)
    assert latest is not None
    assert latest.verdict == ScreeningVerdict.INCLUDE.value


async def test_decisions_are_appended_not_overwritten(session: AsyncSession) -> None:
    """追加式：两次决策必须是两行（审计轨迹）。"""
    record, reviewer_a, _ = await _seed(session)
    repo = ScreeningRepository(session)

    await repo.append_decision(
        record.id, stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.MAYBE, reviewer_id=reviewer_a,
    )
    await repo.append_decision(
        record.id, stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.EXCLUDE,
        exclusion_reason="not relevant", reviewer_id=reviewer_a,
    )
    await session.commit()

    all_rows = await repo.list_decisions(record.id, DecisionStage.TITLE_ABSTRACT)
    assert len(all_rows) == 2


async def test_find_conflicts_marks_disagreeing_reviewers(session: AsyncSession) -> None:
    """双人独立筛选：结论不同的记录必须被识别为冲突。"""
    record, reviewer_a, reviewer_b = await _seed(session)
    repo = ScreeningRepository(session)

    await repo.append_decision(
        record.id, stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.INCLUDE, reviewer_id=reviewer_a,
    )
    await repo.append_decision(
        record.id, stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.EXCLUDE,
        exclusion_reason="off topic", reviewer_id=reviewer_b,
    )
    await session.commit()

    conflicts = await repo.find_conflicts(record.task_id, DecisionStage.TITLE_ABSTRACT)
    assert [c.record_id for c in conflicts] == [record.id]


async def test_append_decision_rejects_exclude_without_reason(session: AsyncSession) -> None:
    """排除必须给理由（服务层与数据层双重保证）。"""
    record, reviewer_a, _ = await _seed(session)
    repo = ScreeningRepository(session)

    with pytest.raises(ValueError, match="排除"):
        await repo.append_decision(
            record.id, stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE, reviewer_id=reviewer_a,
        )
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_repository.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db.repositories.screening'`

- [x] **Step 3: 写实现**

```python
# backend/app/db/repositories/screening.py
"""筛选数据访问：检索日志 / 候选记录 / 追加式决策。

约定（与既有 Repository 一致）：本层只 add/flush，不 commit；commit 由 service 层控制。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.screening import (
    DecisionStage,
    ScreeningDecision,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)
from app.db.repositories.base import BaseRepository


@dataclass(frozen=True)
class Conflict:
    """一条记录在某个阶段出现筛选者结论分歧。"""

    record_id: uuid.UUID
    verdicts: dict[str, str]  # reviewer_id(str) → verdict


class ScreeningRepository(BaseRepository[ScreeningRecord]):
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

    async def list_records(
        self, task_id: uuid.UUID, *, stage: DecisionStage | None = None, limit: int = 200
    ) -> list[ScreeningRecord]:
        stmt = select(ScreeningRecord).where(ScreeningRecord.task_id == task_id)
        if stage is not None:
            stmt = stmt.where(ScreeningRecord.current_stage == stage.value)
        stmt = stmt.order_by(ScreeningRecord.created_at.asc()).limit(max(1, min(limit, 1000)))
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
        """追加一条决策。排除必须给理由（与 DB CheckConstraint 双重保证）。"""
        if verdict is ScreeningVerdict.EXCLUDE and not (exclusion_reason or "").strip():
            raise ValueError("排除决策必须提供 exclusion_reason（PRISMA 2020 要求报告排除理由）")
        row = ScreeningDecision(
            record_id=record_id,
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

    async def list_decisions(
        self, record_id: uuid.UUID, stage: DecisionStage | None = None
    ) -> list[ScreeningDecision]:
        stmt = select(ScreeningDecision).where(ScreeningDecision.record_id == record_id)
        if stage is not None:
            stmt = stmt.where(ScreeningDecision.stage == stage.value)
        stmt = stmt.order_by(ScreeningDecision.created_at.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def latest_decision(
        self, record_id: uuid.UUID, stage: DecisionStage, reviewer_id: uuid.UUID
    ) -> ScreeningDecision | None:
        stmt = (
            select(ScreeningDecision)
            .where(
                ScreeningDecision.record_id == record_id,
                ScreeningDecision.stage == stage.value,
                ScreeningDecision.reviewer_id == reviewer_id,
            )
            .order_by(ScreeningDecision.created_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_conflicts(
        self, task_id: uuid.UUID, stage: DecisionStage
    ) -> list[Conflict]:
        """找出「同一记录同一阶段，不同筛选者给出不同结论」的记录。"""
        records = await self.list_records(task_id, limit=1000)
        conflicts: list[Conflict] = []
        for record in records:
            decisions = await self.list_decisions(record.id, stage)
            # 每位筛选者取最新一条
            latest: dict[str, str] = {}
            for decision in decisions:
                latest[str(decision.reviewer_id)] = decision.verdict
            if len(set(latest.values())) > 1:
                conflicts.append(Conflict(record_id=record.id, verdicts=latest))
        return conflicts
```

- [x] **Step 4: 跑测试确认通过**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_repository.py -q --no-cov`
Expected: PASS（5 passed）

- [x] **Step 5: 提交**

```bash
git add backend/app/db/repositories/screening.py backend/tests/test_screening_repository.py
git commit -m "feat(screening): add append-only screening repository"
```

---

## Task 3: 服务层（记录检索 + 落快照 + 派生状态）

**Files:**
- Create: `backend/app/knowledge/services/screening_service.py`
- Test: `backend/tests/test_screening_service.py`

- [x] **Step 1: 写失败测试**

```python
# backend/tests/test_screening_service.py
"""筛选服务测试：检索落库、去重快照、状态派生。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.screening import DecisionStage, ScreeningVerdict
from app.knowledge.services.screening_service import ScreeningService
from app.tools.literature.models import LiteratureRecord


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _record(source_id: str, title: str, source: str = "arxiv") -> LiteratureRecord:
    return LiteratureRecord(
        source_id=source_id,
        title=title,
        authors=["A"],
        abstract="a",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=f"https://arxiv.org/abs/{source_id}",
        pdf_url=f"https://arxiv.org/pdf/{source_id}",
        categories=["cs.CL"],
    )


async def test_record_search_persists_log_and_snapshot(session: AsyncSession) -> None:
    service = ScreeningService(session)
    task_id = uuid.uuid4()

    log = await service.record_search(
        task_id,
        query="rag evaluation",
        sources=["arxiv", "openalex"],
        records=[_record("a1", "T1"), _record("a2", "T2")],
    )
    await session.commit()

    assert log.hits_total == 2
    assert log.hits_deduped == 2
    records = await service.list_records(task_id)
    assert {r.source_id for r in records} == {"a1", "a2"}


async def test_record_search_dedupes_by_doi_or_title(session: AsyncSession) -> None:
    """跨源同一条文献只落一条（PRISMA「去重后记录数」）。"""
    service = ScreeningService(session)
    task_id = uuid.uuid4()

    log = await service.record_search(
        task_id,
        query="rag",
        sources=["arxiv", "openalex"],
        records=[
            _record("a1", "Same Title"),
            _record("o1", "Same Title", source="openalex"),  # 同标题 → 视为重复
        ],
        hits_by_source={"arxiv": 1, "openalex": 1},
    )
    await session.commit()

    assert log.hits_total == 2
    assert log.hits_deduped == 1
    assert log.hits_by_source == {"arxiv": 1, "openalex": 1}
    assert len(await service.list_records(task_id)) == 1


async def test_prisma_counts_identified_by_source_uses_raw_hits(session: AsyncSession) -> None:
    """PRISMA「识别」必须用**去重前**的各源命中数，否则各源合计与总数矛盾。"""
    service = ScreeningService(session)
    task_id = uuid.uuid4()

    await service.record_search(
        task_id,
        query="rag",
        sources=["arxiv", "openalex"],
        records=[_record("a1", "Same Title"), _record("o1", "Same Title", source="openalex")],
        hits_by_source={"arxiv": 10, "openalex": 5},
    )
    await session.commit()

    counts = await service.prisma_counts(task_id)

    assert counts["identified_by_source"] == {"arxiv": 10, "openalex": 5}
    assert sum(counts["identified_by_source"].values()) == counts["identified_total"]
    assert counts["records_screened"] == 1  # 去重后
    assert counts["duplicates_removed"] == 1


async def test_decide_advances_stage_and_derives_state(session: AsyncSession) -> None:
    service = ScreeningService(session)
    task_id = uuid.uuid4()
    reviewer = uuid.uuid4()
    await service.record_search(task_id, query="q" * 5, sources=["arxiv"], records=[_record("a1", "T1")])
    await session.commit()
    record = (await service.list_records(task_id))[0]

    await service.decide(
        record.id, stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.INCLUDE, reviewer_id=reviewer,
    )
    await session.commit()

    updated = await service.get_record(record.id)
    assert updated.current_stage == DecisionStage.FULLTEXT.value


async def test_excluding_at_title_abstract_ends_record(session: AsyncSession) -> None:
    service = ScreeningService(session)
    task_id = uuid.uuid4()
    reviewer = uuid.uuid4()
    await service.record_search(task_id, query="q" * 5, sources=["arxiv"], records=[_record("a1", "T1")])
    await session.commit()
    record = (await service.list_records(task_id))[0]

    await service.decide(
        record.id, stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.EXCLUDE, reviewer_id=reviewer,
        exclusion_reason="not relevant",
    )
    await session.commit()

    updated = await service.get_record(record.id)
    assert updated.current_stage == DecisionStage.TITLE_ABSTRACT.value  # 停在原阶段（已排除）


async def test_decide_requires_reason_for_exclude(session: AsyncSession) -> None:
    service = ScreeningService(session)
    task_id = uuid.uuid4()
    reviewer = uuid.uuid4()
    await service.record_search(task_id, query="q" * 5, sources=["arxiv"], records=[_record("a1", "T1")])
    await session.commit()
    record = (await service.list_records(task_id))[0]

    with pytest.raises(ValueError, match="排除"):
        await service.decide(
            record.id, stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE, reviewer_id=reviewer,
        )
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_service.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.knowledge.services.screening_service'`

- [x] **Step 3: 写实现**

```python
# backend/app/knowledge/services/screening_service.py
"""筛选服务（Phase 2 · PRISMA 深模块）。

职责：
1. `record_search`：把一次检索落成**可复现日志 + 结果快照**（跨源去重）；
2. `decide`：追加一条决策，并按结论推进 `current_stage`；
3. `prisma_counts`：由明细**派生** PRISMA 各阶段计数（不落库）。

去重口径（与 `source_registry` 一致）：DOI → source_id → 归一化题名。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.db.models.screening import (
    DecisionStage,
    FulltextStatus,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)
from app.db.repositories.screening import ScreeningRepository
from app.tools.literature.models import LiteratureRecord

#: 阶段推进顺序：标题摘要 → 全文 → 纳入。
_NEXT_STAGE: dict[DecisionStage, DecisionStage] = {
    DecisionStage.TITLE_ABSTRACT: DecisionStage.FULLTEXT,
    DecisionStage.FULLTEXT: DecisionStage.INCLUDED,
}


def _normalize_title(title: str) -> str:
    """题名归一化（用于去重）：NFKC + casefold + 折叠空白。"""
    import re
    import unicodedata

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", title).casefold()).strip()


class ScreeningService:
    """筛选工作台业务逻辑（HTTP 层只做校验与响应映射）。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ScreeningRepository(db)

    # ===== 检索落库 =====

    async def record_search(
        self,
        task_id: uuid.UUID,
        *,
        query: str,
        sources: list[str],
        records: list[LiteratureRecord],
        hits_by_source: dict[str, int] | None = None,
    ) -> SearchLog:
        """把一次检索落成日志 + 快照，并返回日志（含去重后计数）。

        **快照而非实时重查**：上游源内容会变，若筛选时重查则 PRISMA 数字无法复现。

        `hits_by_source` 是 PRISMA「识别」阶段的原始各源命中数（去重前）。
        未提供时按快照的来源分布推断（仅供单源场景，**多源请显式传入**，
        否则「各源合计」可能小于 `identified_total`）。
        """
        deduped = self._dedupe(records)
        if hits_by_source is None:
            hits_by_source = {}
            for item in records:
                source = _source_of(item)
                hits_by_source[source] = hits_by_source.get(source, 0) + 1
        log = await self._repo.add_search_log(
            SearchLog(
                task_id=task_id,
                query=query,
                sources=list(sources),
                hits_total=len(records),
                hits_by_source=dict(hits_by_source),
                hits_deduped=len(deduped),
                executed_at=datetime.now(UTC),
            )
        )
        for item in deduped:
            await self._repo.add_record(
                ScreeningRecord(
                    search_log_id=log.id,
                    task_id=task_id,
                    source=_source_of(item),
                    source_id=item.source_id,
                    title=item.title,
                    authors=list(item.authors),
                    abstract=item.abstract,
                    doi=None,
                    fulltext_status=FulltextStatus.UNAVAILABLE.value,
                    current_stage=DecisionStage.TITLE_ABSTRACT.value,
                )
            )
        return log

    @staticmethod
    def _dedupe(records: list[LiteratureRecord]) -> list[LiteratureRecord]:
        """跨源去重：DOI → source_id → 归一化题名。保留首次出现者。"""
        seen: set[str] = set()
        kept: list[LiteratureRecord] = []
        for item in records:
            key = f"id:{item.source_id}"
            title_key = f"title:{_normalize_title(item.title)}"
            if key in seen or title_key in seen:
                continue
            seen.add(key)
            seen.add(title_key)
            kept.append(item)
        return kept

    # ===== 查询 =====

    async def list_records(
        self, task_id: uuid.UUID, *, stage: DecisionStage | None = None
    ) -> list[ScreeningRecord]:
        return await self._repo.list_records(task_id, stage=stage)

    async def get_record(self, record_id: uuid.UUID) -> ScreeningRecord:
        record = await self._repo.get(record_id)
        if record is None:
            raise NotFoundException("筛选记录不存在")
        return record

    # ===== 决策 =====

    async def decide(
        self,
        record_id: uuid.UUID,
        *,
        stage: DecisionStage,
        verdict: ScreeningVerdict,
        reviewer_id: uuid.UUID,
        exclusion_reason: str | None = None,
        note: str = "",
    ) -> ScreeningRecord:
        """追加决策并按结论推进阶段。

        阶段推进规则（PRISMA）：
        - `include` → 进入下一阶段（标题摘要→全文→纳入；纳入阶段为终态）；
        - `maybe` → 停在当前阶段（待复核）；
        - `exclude` → 停在当前阶段（已排除，不再前进）。
        """
        record = await self.get_record(record_id)
        await self._repo.append_decision(
            record_id,
            stage=stage,
            verdict=verdict,
            reviewer_id=reviewer_id,
            exclusion_reason=exclusion_reason,
            note=note,
        )
        if verdict is ScreeningVerdict.INCLUDE:
            next_stage = _NEXT_STAGE.get(stage)
            if next_stage is not None:
                record.current_stage = next_stage.value
                await self._db.flush()
        return record

    # ===== PRISMA 计数（派生，不落库）=====

    async def prisma_counts(self, task_id: uuid.UUID) -> dict[str, Any]:
        """派生 PRISMA 2020 各阶段计数。

        全部由明细实时计算——不落汇总数字，避免与明细不一致。
        """
        logs = await self._repo.list_search_logs(task_id)
        records = await self._repo.list_records(task_id, limit=1000)

        # PRISMA「识别」阶段统计的是**各源原始命中数（去重前）**。
        # 因此 `search_log.hits_by_source` 必须在记录检索时就按源存好——
        # 不能事后从去重后的快照反推（快照已去重，反推会让「各源合计」
        # 小于 `identified_total`，流程图数字自相矛盾）。
        by_source: dict[str, int] = {}
        for log in logs:
            for source, hits in (log.hits_by_source or {}).items():
                by_source[source] = by_source.get(source, 0) + int(hits)

        title_decisions = 0
        title_excluded = 0
        fulltext_decisions = 0
        fulltext_excluded = 0
        included = 0
        exclusion_reasons: dict[str, int] = {}

        for record in records:
            for stage in (DecisionStage.TITLE_ABSTRACT, DecisionStage.FULLTEXT):
                decisions = await self._repo.list_decisions(record.id, stage)
                if not decisions:
                    continue
                latest = decisions[-1]
                if stage is DecisionStage.TITLE_ABSTRACT:
                    title_decisions += 1
                    if latest.verdict == ScreeningVerdict.EXCLUDE.value:
                        title_excluded += 1
                else:
                    fulltext_decisions += 1
                    if latest.verdict == ScreeningVerdict.EXCLUDE.value:
                        fulltext_excluded += 1
                if latest.verdict == ScreeningVerdict.EXCLUDE.value and latest.exclusion_reason:
                    key = latest.exclusion_reason
                    exclusion_reasons[key] = exclusion_reasons.get(key, 0) + 1
            if record.current_stage == DecisionStage.INCLUDED.value:
                included += 1

        return {
            "identified_by_source": by_source,
            "identified_total": sum(log.hits_total for log in logs),
            "duplicates_removed": sum(log.hits_total - log.hits_deduped for log in logs),
            "records_screened": len(records),
            "title_abstract": {
                "screened": title_decisions,
                "excluded": title_excluded,
            },
            "fulltext": {
                "assessed": fulltext_decisions,
                "excluded": fulltext_excluded,
            },
            "included": included,
            "exclusion_reasons": exclusion_reasons,
        }


def _source_of(record: LiteratureRecord) -> str:
    """从 `LiteratureRecord.source_id` 前缀推断来源（`openalex:W…` 等）。"""
    prefix, sep, _ = record.source_id.partition(":")
    if sep and prefix in {"openalex", "s2", "pubmed", "crossref"}:
        return prefix
    return "arxiv"
```

- [x] **Step 4: 跑测试确认通过**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_service.py -q --no-cov`
Expected: PASS（5 passed）

- [x] **Step 5: 提交**

```bash
git add backend/app/knowledge/services/screening_service.py backend/tests/test_screening_service.py
git commit -m "feat(screening): add screening service with dedupe and stage derivation"
```

---

## Task 4: PRISMA 流程图纯函数

**Files:**
- Create: `backend/app/tools/prisma/__init__.py`
- Create: `backend/app/tools/prisma/flow.py`
- Test: `backend/tests/test_prisma_flow.py`

- [x] **Step 1: 写失败测试**

```python
# backend/tests/test_prisma_flow.py
"""PRISMA 2020 流程图生成测试（纯函数，无 IO）。"""

from __future__ import annotations

from app.tools.prisma.flow import render_prisma_markdown, render_prisma_mermaid


def _counts() -> dict:
    return {
        "identified_by_source": {"arxiv": 40, "openalex": 20},
        "identified_total": 60,
        "duplicates_removed": 10,
        "records_screened": 50,
        "title_abstract": {"screened": 50, "excluded": 30},
        "fulltext": {"assessed": 20, "excluded": 5},
        "included": 15,
        "exclusion_reasons": {"not relevant": 25, "no fulltext": 5, "wrong population": 5},
    }


def test_markdown_contains_all_stage_counts() -> None:
    markdown = render_prisma_markdown(_counts())

    assert "60" in markdown  # identified_total
    assert "10" in markdown  # duplicates removed
    assert "50" in markdown  # records screened
    assert "30" in markdown  # title/abstract excluded
    assert "20" in markdown  # fulltext assessed
    assert "15" in markdown  # included


def test_markdown_lists_exclusion_reasons() -> None:
    markdown = render_prisma_markdown(_counts())

    assert "not relevant" in markdown
    assert "25" in markdown


def test_markdown_lists_sources() -> None:
    markdown = render_prisma_markdown(_counts())

    assert "arxiv" in markdown
    assert "openalex" in markdown


def test_mermaid_is_syntactically_balanced() -> None:
    mermaid = render_prisma_mermaid(_counts())

    assert mermaid.startswith("flowchart TD")
    assert mermaid.count("[") == mermaid.count("]")


def test_empty_counts_render_without_error() -> None:
    """零数据（刚建任务）也要能出图，不能抛异常。"""
    empty = {
        "identified_by_source": {},
        "identified_total": 0,
        "duplicates_removed": 0,
        "records_screened": 0,
        "title_abstract": {"screened": 0, "excluded": 0},
        "fulltext": {"assessed": 0, "excluded": 0},
        "included": 0,
        "exclusion_reasons": {},
    }

    assert "0" in render_prisma_markdown(empty)
    assert render_prisma_mermaid(empty).startswith("flowchart TD")
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_prisma_flow.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools.prisma'`

- [x] **Step 3: 写实现**

```python
# backend/app/tools/prisma/__init__.py
"""PRISMA 2020 报告工具（流程图 / 计数格式化）。"""
```

```python
# backend/app/tools/prisma/flow.py
"""PRISMA 2020 流程图生成（纯函数，无 IO、无网络）。

输入是 `ScreeningService.prisma_counts()` 的派生计数；输出 Markdown 表格
（可直接放进综述的「方法」章节）与 Mermaid 流程图（前端渲染）。
"""

from __future__ import annotations

from typing import Any


def _get(counts: dict[str, Any], *path: str, default: int = 0) -> int:
    """安全取值：任一层缺失按 0。"""
    node: Any = counts
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return int(node) if isinstance(node, int) else default


def render_prisma_markdown(counts: dict[str, Any]) -> str:
    """渲染 PRISMA 2020 计数表（Markdown）。"""
    by_source: dict[str, int] = counts.get("identified_by_source") or {}
    source_lines = "\n".join(
        f"| {source} | {hits} |" for source, hits in sorted(by_source.items())
    ) or "| （无） | 0 |"

    reasons: dict[str, int] = counts.get("exclusion_reasons") or {}
    reason_lines = "\n".join(
        f"| {reason} | {hits} |" for reason, hits in sorted(reasons.items())
    ) or "| （无） | 0 |"

    return f"""# PRISMA 2020 流程图数据

## 识别（Identification）

| 来源 | 命中记录数 |
| --- | --- |
{source_lines}

- 各源命中合计：**{_get(counts, "identified_total")}**
- 去重后记录数：**{_get(counts, "records_screened")}**
- 去除重复记录：**{_get(counts, "duplicates_removed")}**

## 筛选（Screening）

- 标题/摘要阶段筛查：**{_get(counts, "title_abstract", "screened")}**
- 标题/摘要阶段排除：**{_get(counts, "title_abstract", "excluded")}**
- 全文阶段评估：**{_get(counts, "fulltext", "assessed")}**
- 全文阶段排除：**{_get(counts, "fulltext", "excluded")}**

## 排除理由

| 理由 | 记录数 |
| --- | --- |
{reason_lines}

## 纳入（Included）

- 最终纳入研究：**{_get(counts, "included")}**
"""


def render_prisma_mermaid(counts: dict[str, Any]) -> str:
    """渲染 PRISMA 2020 流程图（Mermaid `flowchart TD`）。"""
    identified = _get(counts, "identified_total")
    screened = _get(counts, "records_screened")
    duplicates = _get(counts, "duplicates_removed")
    title_excluded = _get(counts, "title_abstract", "excluded")
    fulltext_assessed = _get(counts, "fulltext", "assessed")
    fulltext_excluded = _get(counts, "fulltext", "excluded")
    included = _get(counts, "included")

    return f"""flowchart TD
    A[识别：各源命中 n={identified}] --> B[去除重复 n={duplicates}]
    B --> C[标题/摘要筛查 n={screened}]
    C -->|排除 n={title_excluded}| X1[排除记录]
    C --> D[全文评估 n={fulltext_assessed}]
    D -->|排除 n={fulltext_excluded}| X2[排除记录]
    D --> E[纳入综述 n={included}]
"""
```

- [x] **Step 4: 跑测试确认通过**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_prisma_flow.py -q --no-cov`
Expected: PASS（5 passed）

- [x] **Step 5: 提交**

```bash
git add backend/app/tools/prisma/ backend/tests/test_prisma_flow.py
git commit -m "feat(prisma): add PRISMA 2020 flow chart renderers"
```

---

## Task 5: HTTP 端点

**Files:**
- Create: `backend/app/schemas/screening.py`
- Create: `backend/app/api/v1/endpoints/screening.py`
- Modify: `backend/app/api/v1/router.py`（**Lead 做**）
- Test: `backend/tests/test_screening_api.py`

- [x] **Step 1: 写失败测试**

```python
# backend/tests/test_screening_api.py
"""筛选 HTTP 端点测试。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.endpoints import screening as screening_endpoint
from app.db.base import Base
from app.db.session import get_db


class _User:
    id = uuid.uuid4()
    username = "tester"
    email = "t@example.com"
    is_active = True
    role = "user"


@pytest.fixture
def client() -> TestClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(screening_endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[screening_endpoint.get_current_active_user] = lambda: _User()

    from app.core.exceptions import AppException, NotFoundException

    @app.exception_handler(NotFoundException)
    async def _nf(_: Any, exc: NotFoundException) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": exc.message})

    @app.exception_handler(AppException)
    async def _app(_: Any, exc: AppException) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    import asyncio

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    with TestClient(app) as test_client:
        yield test_client


def test_record_search_creates_log(client: TestClient) -> None:
    response = client.post(
        "/api/v1/screening/search-logs",
        json={
            "task_id": str(uuid.uuid4()),
            "query": "rag evaluation",
            "sources": ["arxiv"],
            "records": [
                {
                    "source_id": "a1",
                    "title": "T1",
                    "authors": ["A"],
                    "abstract": "x",
                }
            ],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["hits_total"] == 1


def test_decide_appends_decision(client: TestClient) -> None:
    task_id = str(uuid.uuid4())
    created = client.post(
        "/api/v1/screening/search-logs",
        json={
            "task_id": task_id,
            "query": "q" * 5,
            "sources": ["arxiv"],
            "records": [{"source_id": "a1", "title": "T1", "authors": [], "abstract": ""}],
        },
    )
    assert created.status_code == 201

    listed = client.get(f"/api/v1/screening/records?task_id={task_id}")
    record_id = listed.json()["items"][0]["record_id"]

    response = client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["current_stage"] == "fulltext"


def test_exclude_without_reason_returns_400(client: TestClient) -> None:
    task_id = str(uuid.uuid4())
    client.post(
        "/api/v1/screening/search-logs",
        json={
            "task_id": task_id,
            "query": "q" * 5,
            "sources": ["arxiv"],
            "records": [{"source_id": "a1", "title": "T1", "authors": [], "abstract": ""}],
        },
    )
    record_id = client.get(f"/api/v1/screening/records?task_id={task_id}").json()["items"][0]["record_id"]

    response = client.post(
        f"/api/v1/screening/records/{record_id}/decisions",
        json={"stage": "title_abstract", "verdict": "exclude"},
    )

    assert response.status_code == 400
    assert "排除" in response.json()["detail"]


def test_prisma_counts_endpoint_returns_all_stages(client: TestClient) -> None:
    task_id = str(uuid.uuid4())
    client.post(
        "/api/v1/screening/search-logs",
        json={
            "task_id": task_id,
            "query": "q" * 5,
            "sources": ["arxiv", "openalex"],
            "hits_by_source": {"arxiv": 30, "openalex": 20},
            "records": [
                {"source_id": "a1", "title": "T1", "authors": [], "abstract": ""},
                {"source_id": "a2", "title": "T2", "authors": [], "abstract": ""},
            ],
        },
    )

    response = client.get(f"/api/v1/screening/prisma?task_id={task_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["records_screened"] == 2
    # 识别阶段用**去重前**的各源命中数，且各源合计与总数自洽
    assert body["identified_by_source"] == {"arxiv": 30, "openalex": 20}
    assert body["identified_total"] == 50
    assert sum(body["identified_by_source"].values()) == body["identified_total"]
    assert "title_abstract" in body
    assert "mermaid" in body
    assert body["mermaid"].startswith("flowchart TD")
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_api.py -q --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.v1.endpoints.screening'`

- [x] **Step 3: 写实现**

```python
# backend/app/schemas/screening.py
"""筛选工作台 HTTP 契约（Phase 2 · PRISMA）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LiteratureRecordIn(BaseModel):
    """检索结果（客户端提交的最小字段集）。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    doi: str | None = None


class SearchLogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID
    query: str = Field(min_length=2, max_length=2000)
    sources: list[str] = Field(min_length=1, max_length=10)
    hits_by_source: dict[str, int] = Field(default_factory=dict)
    """PRISMA「识别」阶段原始各源命中数（去重前）。留空时按 records 的来源分布推断。"""

    records: list[LiteratureRecordIn] = Field(default_factory=list, max_length=500)


class SearchLogOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_log_id: uuid.UUID
    task_id: uuid.UUID
    query: str
    sources: list[str]
    hits_total: int
    hits_by_source: dict[str, int] = Field(default_factory=dict)
    hits_deduped: int
    executed_at: datetime


class ScreeningRecordOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: uuid.UUID
    source: str
    source_id: str
    title: str
    authors: list[str]
    abstract: str
    fulltext_status: str
    current_stage: str


class ScreeningRecordListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScreeningRecordOut] = Field(default_factory=list)


class DecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(pattern="^(title_abstract|fulltext|included)$")
    verdict: str = Field(pattern="^(include|exclude|maybe)$")
    exclusion_reason: str | None = Field(default=None, max_length=256)
    note: str = Field(default="", max_length=2000)


class DecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: uuid.UUID
    current_stage: str


class PrismaOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identified_by_source: dict[str, int] = Field(default_factory=dict)
    identified_total: int
    duplicates_removed: int
    records_screened: int
    title_abstract: dict[str, int] = Field(default_factory=dict)
    fulltext: dict[str, int] = Field(default_factory=dict)
    included: int
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)
    markdown: str
    mermaid: str
```

```python
# backend/app/api/v1/endpoints/screening.py
"""PRISMA 筛选工作台 HTTP adapter（Phase 2）。

    POST /api/v1/screening/search-logs                     记录一次检索（落快照）
    GET  /api/v1/screening/records?task_id=…               列出候选记录
    POST /api/v1/screening/records/{id}/decisions          追加一条筛选决策
    GET  /api/v1/screening/prisma?task_id=…                PRISMA 计数 + 流程图

约定：全部要求 Bearer access token；业务在 `ScreeningService`，本层只做映射。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.exceptions import ValidationException
from app.db.models.screening import DecisionStage, ScreeningVerdict
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.services.screening_service import ScreeningService
from app.schemas.screening import (
    DecisionCreate,
    DecisionOut,
    PrismaOut,
    ScreeningRecordListOut,
    ScreeningRecordOut,
    SearchLogCreate,
    SearchLogOut,
)
from app.tools.literature.models import LiteratureRecord
from app.tools.prisma.flow import render_prisma_markdown, render_prisma_mermaid

router = APIRouter(prefix="/screening", tags=["screening"])

__all__ = ["get_current_active_user", "router"]


def get_screening_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScreeningService:
    return ScreeningService(db)


ServiceDep = Annotated[ScreeningService, Depends(get_screening_service)]
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


@router.post(
    "/search-logs",
    response_model=SearchLogOut,
    status_code=status.HTTP_201_CREATED,
    summary="记录一次检索（落快照）",
)
async def create_search_log(
    payload: SearchLogCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> SearchLogOut:
    """把一次检索落成可复现日志 + 结果快照（跨源去重）。"""
    records = [
        LiteratureRecord(
            source_id=item.source_id,
            title=item.title,
            authors=list(item.authors),
            abstract=item.abstract,
            published_at=datetime.now(UTC),
            source_url=f"https://example.org/{item.source_id}",
            pdf_url=f"https://example.org/{item.source_id}.pdf",
            categories=[],
        )
        for item in payload.records
    ]
    log = await service.record_search(
        payload.task_id,
        query=payload.query,
        sources=list(payload.sources),
        records=records,
        hits_by_source=dict(payload.hits_by_source) or None,
    )
    await service._db.commit()  # noqa: SLF001 - 事务边界在端点层（与既有端点一致）
    return SearchLogOut(
        search_log_id=log.id,
        task_id=log.task_id,
        query=log.query,
        sources=list(log.sources or []),
        hits_total=log.hits_total,
        hits_by_source=dict(log.hits_by_source or {}),
        hits_deduped=log.hits_deduped,
        executed_at=log.executed_at,
    )


@router.get("/records", response_model=ScreeningRecordListOut, summary="列出候选记录")
async def list_records(
    service: ServiceDep,
    current_user: CurrentUserDep,
    task_id: Annotated[uuid.UUID, Query()],
    stage: Annotated[str | None, Query()] = None,
) -> ScreeningRecordListOut:
    stage_enum = None
    if stage:
        try:
            stage_enum = DecisionStage(stage)
        except ValueError as exc:
            raise _bad_request(f"未知阶段：{stage}") from exc
    records = await service.list_records(task_id, stage=stage_enum)
    return ScreeningRecordListOut(
        items=[
            ScreeningRecordOut(
                record_id=row.id,
                source=row.source,
                source_id=row.source_id,
                title=row.title,
                authors=list(row.authors or []),
                abstract=row.abstract,
                fulltext_status=row.fulltext_status,
                current_stage=row.current_stage,
            )
            for row in records
        ]
    )


@router.post(
    "/records/{record_id}/decisions",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="追加筛选决策",
)
async def create_decision(
    record_id: uuid.UUID,
    payload: DecisionCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> DecisionOut:
    try:
        record = await service.decide(
            record_id,
            stage=DecisionStage(payload.stage),
            verdict=ScreeningVerdict(payload.verdict),
            reviewer_id=current_user.id,
            exclusion_reason=payload.exclusion_reason,
            note=payload.note,
        )
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except ValidationException as exc:
        raise _bad_request(exc.message) from exc
    await service._db.commit()  # noqa: SLF001
    return DecisionOut(record_id=record.id, current_stage=record.current_stage)


@router.get("/prisma", response_model=PrismaOut, summary="PRISMA 计数与流程图")
async def get_prisma(
    service: ServiceDep,
    current_user: CurrentUserDep,
    task_id: Annotated[uuid.UUID, Query()],
) -> PrismaOut:
    counts = await service.prisma_counts(task_id)
    return PrismaOut(
        **counts,
        markdown=render_prisma_markdown(counts),
        mermaid=render_prisma_mermaid(counts),
    )
```

- [x] **Step 4: 跑测试确认通过**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_screening_api.py -q --no-cov`
Expected: PASS（4 passed）

- [x] **Step 5: Lead 接线 + 提交**

Lead：在 `app/api/v1/router.py` 加 `screening.router`；跑全量门禁。

```bash
git add backend/app/schemas/screening.py backend/app/api/v1/endpoints/screening.py \
        backend/tests/test_screening_api.py backend/app/api/v1/router.py
git commit -m "feat(screening): add PRISMA screening HTTP endpoints"
```

---

## Task 6: 前端筛选工作台

**Files:**
- Create: `frontend/src/api/screening.ts` + `screening.test.ts`
- Create: `frontend/src/views/ScreeningView.vue` + `ScreeningView.test.ts`
- Modify: `frontend/src/api/types.ts`、`frontend/src/router/index.ts`、`frontend/src/layouts/AppLayout.vue`（**Lead 做后三个**）

- [x] **Step 1: 写失败测试**

```typescript
// frontend/src/api/screening.test.ts
import { describe, expect, it, vi } from "vitest";

import {
  createDecision,
  getPrismaCounts,
  listScreeningRecords,
  recordSearch,
} from "@/api/screening";

vi.mock("@/api/client", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

import api from "@/api/client";

describe("screening api", () => {
  it("recordSearch 提交检索并返回日志", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 201,
      data: {
        search_log_id: "log-1",
        task_id: "task-1",
        query: "rag",
        sources: ["arxiv"],
        hits_total: 2,
        hits_deduped: 1,
        executed_at: "2026-10-20T00:00:00Z",
      },
    });

    const result = await recordSearch({
      task_id: "task-1",
      query: "rag",
      sources: ["arxiv"],
      records: [{ source_id: "a1", title: "T1", authors: [], abstract: "" }],
    });

    expect(api.post).toHaveBeenCalledWith("/screening/search-logs", expect.any(Object));
    expect(result.hits_deduped).toBe(1);
  });

  it("listScreeningRecords 按 task_id 查询", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });

    await listScreeningRecords("task-1");

    expect(api.get).toHaveBeenCalledWith("/screening/records?task_id=task-1");
  });

  it("createDecision 提交阶段与结论", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 201,
      data: { record_id: "r1", current_stage: "fulltext" },
    });

    const result = await createDecision("r1", { stage: "title_abstract", verdict: "include" });

    expect(api.post).toHaveBeenCalledWith("/screening/records/r1/decisions", {
      stage: "title_abstract",
      verdict: "include",
    });
    expect(result.current_stage).toBe("fulltext");
  });

  it("createDecision 把 400 的 detail 作为可展示错误抛出", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 400,
      data: { detail: "排除决策必须提供 exclusion_reason" },
    });

    await expect(
      createDecision("r1", { stage: "title_abstract", verdict: "exclude" })
    ).rejects.toThrow("exclusion_reason");
  });

  it("getPrismaCounts 返回计数与流程图", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        identified_total: 2,
        duplicates_removed: 0,
        records_screened: 2,
        included: 0,
        identified_by_source: { arxiv: 2 },
        title_abstract: { screened: 0, excluded: 0 },
        fulltext: { assessed: 0, excluded: 0 },
        exclusion_reasons: {},
        markdown: "# PRISMA",
        mermaid: "flowchart TD",
      },
    });

    const result = await getPrismaCounts("task-1");

    expect(result.mermaid).toContain("flowchart");
  });
});
```

```typescript
// frontend/src/views/ScreeningView.test.ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import {
  createDecision,
  getPrismaCounts,
  listScreeningRecords,
  recordSearch,
} from "@/api/screening";
import ScreeningView from "@/views/ScreeningView.vue";

vi.mock("@/api/screening", () => ({
  recordSearch: vi.fn(),
  listScreeningRecords: vi.fn(),
  createDecision: vi.fn(),
  getPrismaCounts: vi.fn(),
}));

function recordRow(overrides = {}) {
  return {
    record_id: "r1",
    source: "arxiv",
    source_id: "a1",
    title: "RAG 综述",
    authors: ["张三"],
    abstract: "摘要",
    fulltext_status: "unavailable",
    current_stage: "title_abstract",
    ...overrides,
  };
}

describe("ScreeningView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listScreeningRecords).mockResolvedValue([recordRow()]);
    vi.mocked(getPrismaCounts).mockResolvedValue({
      identified_total: 1,
      duplicates_removed: 0,
      records_screened: 1,
      included: 0,
      identified_by_source: { arxiv: 1 },
      title_abstract: { screened: 0, excluded: 0 },
      fulltext: { assessed: 0, excluded: 0 },
      exclusion_reasons: {},
      markdown: "# PRISMA",
      mermaid: "flowchart TD",
    });
  });

  it("展示候选记录与当前阶段", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const row = wrapper.find('[data-test="screening-row-r1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("RAG 综述");
    expect(row.text()).toContain("标题/摘要");
  });

  it("纳入按钮调用 createDecision 并刷新", async () => {
    vi.mocked(createDecision).mockResolvedValue({ record_id: "r1", current_stage: "fulltext" });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "include",
    });
  });

  it("排除必须填写理由后才提交", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="screening-exclude-form-r1"]').exists()).toBe(true);

    await wrapper.find('[data-test="screening-exclude-reason-r1"]').setValue("不相关");
    await wrapper.find('[data-test="screening-exclude-submit-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "exclude",
      exclusion_reason: "不相关",
    });
  });

  it("展示 PRISMA 计数", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const prisma = wrapper.find('[data-test="screening-prisma"]');
    expect(prisma.exists()).toBe(true);
    expect(prisma.text()).toContain("1");
  });
});
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd frontend && pnpm test -- src/api/screening.test.ts src/views/ScreeningView.test.ts`
Expected: FAIL — 模块不存在

- [x] **Step 3: 写实现**

`frontend/src/api/screening.ts`：

```typescript
import api from "./client";
import type {
  PrismaCounts,
  ScreeningDecisionPayload,
  ScreeningRecord,
  SearchLogCreatePayload,
  SearchLogOut,
} from "./types";

// PRISMA 筛选工作台 API（Phase 2）
// Bearer 鉴权由 client.ts 的请求拦截器统一注入。

/** 记录一次检索（落快照，跨源去重在后端完成） */
export async function recordSearch(payload: SearchLogCreatePayload): Promise<SearchLogOut> {
  const response = await api.post<SearchLogOut>("/screening/search-logs", payload);
  return response.data;
}

/** 列出某个任务的候选记录 */
export function listScreeningRecords(taskId: string): Promise<ScreeningRecord[]> {
  return api
    .get<{ items: ScreeningRecord[] }>(`/screening/records?task_id=${encodeURIComponent(taskId)}`)
    .then((response) => response.data.items ?? []);
}

/**
 * 追加一条筛选决策。
 *
 * 后端对「排除但未给理由」固定返回 400，`detail` 可读；`client.ts` 的错误归一化
 * 只保留 `message`，会吞掉 `detail`，因此这里把 400 当正常响应解析后手工抛出。
 */
export async function createDecision(
  recordId: string,
  payload: ScreeningDecisionPayload
): Promise<{ record_id: string; current_stage: string }> {
  const response = await api.post<{ record_id: string; current_stage: string }>(
    `/screening/records/${encodeURIComponent(recordId)}/decisions`,
    payload,
    { validateStatus: (status) => status < 400 || status === 400 }
  );
  if (response.status === 400) {
    const detail = (response.data as { detail?: string } | undefined)?.detail;
    throw new Error(detail || "筛选决策提交失败（400）");
  }
  return response.data;
}

/** PRISMA 计数 + Markdown 表格 + Mermaid 流程图 */
export async function getPrismaCounts(taskId: string): Promise<PrismaCounts> {
  const response = await api.get<PrismaCounts>(
    `/screening/prisma?task_id=${encodeURIComponent(taskId)}`
  );
  return response.data;
}
```

`frontend/src/views/ScreeningView.vue`：

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Check, X } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import {
  createDecision,
  getPrismaCounts,
  listScreeningRecords,
} from "@/api/screening";
import type { PrismaCounts, ScreeningRecord } from "@/api/types";

// PRISMA 筛选工作台（Phase 2）：候选列表 + 逐条决策 + PRISMA 计数
const props = defineProps<{ taskId: string }>();

const records = ref<ScreeningRecord[]>([]);
const prisma = ref<PrismaCounts | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const success = ref<string | null>(null);
const busyId = ref<string | null>(null);
const excludeFormId = ref<string | null>(null);
const excludeReason = ref("");

const STAGE_LABELS: Record<string, string> = {
  title_abstract: "标题/摘要",
  fulltext: "全文",
  included: "已纳入",
};

const hint = computed(() => {
  if (!records.value.length) {
    return "还没有候选记录。先在任务里执行检索，结果会自动进入筛选工作台。";
  }
  return `${records.value.length} 条候选记录 · 纳入 ${prisma.value?.included ?? 0} 条`;
});

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    records.value = await listScreeningRecords(props.taskId);
    prisma.value = await getPrismaCounts(props.taskId);
  } catch (err) {
    error.value = (err as Error).message || "筛选数据加载失败";
  } finally {
    loading.value = false;
  }
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

/** 当前阶段：已纳入的记录不再可决策 */
function stageFor(record: ScreeningRecord): string {
  return record.current_stage === "fulltext" ? "fulltext" : "title_abstract";
}

async function handleInclude(record: ScreeningRecord) {
  busyId.value = record.record_id;
  error.value = null;
  success.value = null;
  try {
    await createDecision(record.record_id, {
      stage: stageFor(record),
      verdict: "include",
    });
    success.value = `已纳入「${record.title}」`;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "操作失败";
  } finally {
    busyId.value = null;
  }
}

function openExclude(record: ScreeningRecord) {
  excludeFormId.value = record.record_id;
  excludeReason.value = "";
  success.value = null;
}

async function submitExclude(record: ScreeningRecord) {
  // 排除必须给理由（PRISMA 2020 要求报告排除理由分类）
  if (!excludeReason.value.trim()) {
    error.value = "排除必须填写理由";
    return;
  }
  busyId.value = record.record_id;
  error.value = null;
  try {
    await createDecision(record.record_id, {
      stage: stageFor(record),
      verdict: "exclude",
      exclusion_reason: excludeReason.value.trim(),
    });
    success.value = `已排除「${record.title}」`;
    excludeFormId.value = null;
    await refresh();
  } catch (err) {
    error.value = (err as Error).message || "操作失败";
  } finally {
    busyId.value = null;
  }
}
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>PRISMA 筛选</h1>
      <p class="page__hint">{{ hint }}</p>
    </header>

    <ErrorBanner :message="error" testid="screening-error" />
    <p v-if="success" class="banner banner--ok" data-test="screening-success">{{ success }}</p>

    <div v-if="prisma" class="prisma" data-test="screening-prisma">
      <div class="prisma__item">
        <span class="prisma__label">识别</span>
        <span class="prisma__value">{{ prisma.identified_total }}</span>
      </div>
      <div class="prisma__item">
        <span class="prisma__label">去重后</span>
        <span class="prisma__value">{{ prisma.records_screened }}</span>
      </div>
      <div class="prisma__item">
        <span class="prisma__label">标题/摘要排除</span>
        <span class="prisma__value">{{ prisma.title_abstract.excluded }}</span>
      </div>
      <div class="prisma__item">
        <span class="prisma__label">全文排除</span>
        <span class="prisma__value">{{ prisma.fulltext.excluded }}</span>
      </div>
      <div class="prisma__item prisma__item--accent">
        <span class="prisma__label">纳入</span>
        <span class="prisma__value">{{ prisma.included }}</span>
      </div>
    </div>

    <p v-if="loading && !records.length" class="page__hint">加载中…</p>
    <p v-else-if="!records.length" class="page__hint" data-test="screening-empty">
      还没有候选记录。
    </p>
    <ul v-else class="records">
      <li
        v-for="record in records"
        :key="record.record_id"
        class="record"
        :data-test="`screening-row-${record.record_id}`"
      >
        <div class="record__head">
          <span class="record__title">{{ record.title }}</span>
          <span class="badge">{{ stageLabel(record.current_stage) }}</span>
        </div>
        <div class="record__meta">
          <span>{{ record.authors.join("；") || "（无作者）" }}</span>
          <code>{{ record.source }}:{{ record.source_id }}</code>
        </div>
        <p class="record__abstract">{{ record.abstract }}</p>

        <div v-if="record.current_stage !== 'included'" class="record__actions">
          <button
            class="btn btn--primary"
            type="button"
            :data-test="`screening-include-${record.record_id}`"
            :disabled="busyId === record.record_id"
            @click="handleInclude(record)"
          >
            <Check :size="15" />
            <span>纳入</span>
          </button>
          <button
            class="btn btn--ghost"
            type="button"
            :data-test="`screening-exclude-${record.record_id}`"
            :disabled="busyId === record.record_id"
            @click="openExclude(record)"
          >
            <X :size="15" />
            <span>排除</span>
          </button>
        </div>

        <div
          v-if="excludeFormId === record.record_id"
          class="exclude-form"
          :data-test="`screening-exclude-form-${record.record_id}`"
        >
          <input
            v-model="excludeReason"
            :data-test="`screening-exclude-reason-${record.record_id}`"
            type="text"
            placeholder="排除理由（PRISMA 要求，如：不相关 / 无全文 / 人群不符）"
          />
          <button
            class="btn btn--primary"
            type="button"
            :data-test="`screening-exclude-submit-${record.record_id}`"
            @click="submitExclude(record)"
          >
            确认排除
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.page__header { margin-bottom: 1rem; }

.prisma {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.prisma__item {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  min-width: 6rem;
}

.prisma__item--accent { background: #ecfdf5; }

.prisma__label { font-size: 0.75rem; color: #6b7280; }
.prisma__value { font-size: 1.1rem; font-weight: 600; }

.banner--ok {
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  background: #ecfdf5;
  color: #047857;
  font-size: 0.88rem;
  margin-bottom: 0.75rem;
}

.records { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }

.record {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.record__head { display: flex; align-items: center; gap: 0.5rem; }
.record__title { font-size: 0.92rem; font-weight: 500; }
.record__meta { display: flex; flex-wrap: wrap; gap: 0.5rem; font-size: 0.78rem; color: #6b7280; }
.record__abstract { font-size: 0.82rem; color: #374151; margin: 0; }
.record__actions { display: flex; gap: 0.4rem; }

.exclude-form { display: flex; gap: 0.4rem; flex-wrap: wrap; }

.exclude-form input {
  flex: 1 1 16rem;
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: 0.85rem;
}

.badge {
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.75rem;
  white-space: nowrap;
}

@media (max-width: 720px) {
  .record__head { flex-direction: column; align-items: flex-start; }
  .prisma__item { min-width: 0; flex: 1 1 6rem; }
}
</style>
```

同时在 `frontend/src/api/types.ts` 追加（**Lead 做**）：

```typescript
// ===== PRISMA 筛选契约（Phase 2）=====

export interface ScreeningRecord {
  record_id: string;
  source: string;
  source_id: string;
  title: string;
  authors: string[];
  abstract: string;
  fulltext_status: string;
  current_stage: string;
}

export interface SearchLogCreatePayload {
  task_id: string;
  query: string;
  sources: string[];
  /** PRISMA「识别」原始各源命中数（去重前）；多源时建议显式提供 */
  hits_by_source?: Record<string, number>;
  records: { source_id: string; title: string; authors: string[]; abstract: string; doi?: string | null }[];
}

export interface SearchLogOut {
  search_log_id: string;
  task_id: string;
  query: string;
  sources: string[];
  hits_total: number;
  hits_by_source: Record<string, number>;
  hits_deduped: number;
  executed_at: string;
}

export interface ScreeningDecisionPayload {
  stage: string;
  verdict: string;
  exclusion_reason?: string | null;
  note?: string;
}

export interface PrismaCounts {
  identified_by_source: Record<string, number>;
  identified_total: number;
  duplicates_removed: number;
  records_screened: number;
  title_abstract: Record<string, number>;
  fulltext: Record<string, number>;
  included: number;
  exclusion_reasons: Record<string, number>;
  markdown: string;
  mermaid: string;
}
```

- [x] **Step 4: 跑测试确认通过**

Run: `cd frontend && pnpm test -- src/api/screening.test.ts src/views/ScreeningView.test.ts`
Expected: PASS（9 passed）

- [x] **Step 5: Lead 接线 + 提交**

Lead：`types.ts` 追加类型、`router/index.ts` 加 `/tasks/:taskId/screening`、
`AppLayout.vue` 导航项。

```bash
git add frontend/src/api/screening.ts frontend/src/api/screening.test.ts \
        frontend/src/views/ScreeningView.vue frontend/src/views/ScreeningView.test.ts \
        frontend/src/api/types.ts frontend/src/router/index.ts frontend/src/layouts/AppLayout.vue
git commit -m "feat(frontend): add PRISMA screening workbench page"
```

---

## Task 7: 服务器端到端验收

- [x] **Step 1: 部署**

```powershell
cd 'E:\Multi-agent collaborative automation task assistant'
$b = Join-Path $env:TEMP 'maa.bundle'
git bundle create $b --all
scp $b ylin@10.0.10.2:~/maa.bundle
```

```bash
ssh ylin@10.0.10.2
cd ~/projects/multi-agent-assistant
git fetch ~/maa.bundle "+refs/heads/*:refs/remotes/win/*"
git merge --ff-only win/main
docker compose build backend nginx
docker compose up -d backend nginx
docker compose exec -T backend alembic upgrade head
docker compose exec -T backend alembic current   # 期望 b8e5f1c3d9a2 (head)
```

- [x] **Step 2: API 端到端（真实 PostgreSQL）**

写脚本 `C:\Users\Lenovo\AppData\Local\Temp\prisma_e2e.py`：

```python
"""PRISMA 筛选服务器端到端验收。"""
from __future__ import annotations

import pathlib
import time

import httpx

BASE = "http://10.0.10.2:48080/api/v1"
OUT = pathlib.Path(r"C:\Users\Lenovo\AppData\Local\Temp\prisma_e2e.md")
lines: list[str] = []

def emit(text: str = "") -> None:
    lines.append(text)

with httpx.Client(base_url=BASE, timeout=90.0) as client:
    token = client.post("/auth/login", json={"account": "demo", "password": os.environ["DEMO_PASSWORD"]}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    task = client.post(
        "/tasks",
        headers=headers,
        json={"topic": f"PRISMA 验收 {int(time.time())}", "keywords": ["prisma"],
              "citation_style": "gbt7714-2025-numeric", "offline_dataset": True},
    )
    emit(f"建任务: HTTP {task.status_code}")
    task_id = task.json()["task_id"]

    log = client.post(
        "/screening/search-logs",
        headers=headers,
        json={
            "task_id": task_id,
            "query": "retrieval augmented generation evaluation",
            "sources": ["arxiv", "openalex"],
            "records": [
                {"source_id": "a1", "title": "RAG Survey", "authors": ["A"], "abstract": "x"},
                {"source_id": "a2", "title": "Same Title", "authors": ["B"], "abstract": "y"},
                {"source_id": "o1", "title": "Same Title", "authors": ["B"], "abstract": "y"},
            ],
        },
    )
    emit(f"记录检索: HTTP {log.status_code} → hits_total={log.json().get('hits_total')}, hits_deduped={log.json().get('hits_deduped')}")

    listed = client.get(f"/screening/records?task_id={task_id}", headers=headers)
    items = listed.json()["items"]
    emit(f"候选记录: {len(items)} 条")

    if items:
        first = items[0]["record_id"]
        dec = client.post(
            f"/screening/records/{first}/decisions",
            headers=headers,
            json={"stage": "title_abstract", "verdict": "include"},
        )
        emit(f"纳入决策: HTTP {dec.status_code} → current_stage={dec.json().get('current_stage')}")

        bad = client.post(
            f"/screening/records/{first}/decisions",
            headers=headers,
            json={"stage": "title_abstract", "verdict": "exclude"},
        )
        emit(f"排除缺理由: HTTP {bad.status_code}（期望 400）")

        if len(items) > 1:
            second = items[1]["record_id"]
            exc = client.post(
                f"/screening/records/{second}/decisions",
                headers=headers,
                json={"stage": "title_abstract", "verdict": "exclude", "exclusion_reason": "not relevant"},
            )
            emit(f"排除带理由: HTTP {exc.status_code}")

    prisma = client.get(f"/screening/prisma?task_id={task_id}", headers=headers)
    emit("")
    emit(f"PRISMA: HTTP {prisma.status_code}")
    if prisma.status_code == 200:
        body = prisma.json()
        emit(f"  identified_total={body['identified_total']}")
        emit(f"  duplicates_removed={body['duplicates_removed']}")
        emit(f"  records_screened={body['records_screened']}")
        emit(f"  title_abstract={body['title_abstract']}")
        emit(f"  fulltext={body['fulltext']}")
        emit(f"  included={body['included']}")
        emit(f"  exclusion_reasons={body['exclusion_reasons']}")
        emit(f"  mermaid 首行: {body['mermaid'].splitlines()[0]}")
        emit(f"  markdown 含「纳入」: {'纳入' in body['markdown']}")

OUT.write_text("\n".join(lines), encoding="utf-8")
print("WROTE", OUT)
```

Run: `cd backend && .\.venv\Scripts\python.exe "$env:TEMP\prisma_e2e.py"`

Expected 输出要点：
- 记录检索 `hits_total=3, hits_deduped=2`（同标题跨源去重）
- 纳入决策 → `current_stage=fulltext`
- 排除缺理由 → `400`
- PRISMA 计数自洽：`records_screened=2`，`title_abstract.excluded=1`，
  `exclusion_reasons` 含 `not relevant: 1`，`mermaid` 以 `flowchart TD` 开头

- [x] **Step 3: 浏览器端到端**

```powershell
cd frontend
$env:E2E_BASE_URL='http://10.0.10.2:48080'
pnpm exec playwright test e2e/phase2-prisma.spec.ts --reporter=line
```

写 `frontend/e2e/phase2-prisma.spec.ts`：

```typescript
import { expect, test, type Page } from "@playwright/test";

// PRISMA 筛选工作台页面验收（Phase 2）
// 口令不入仓库（Issue #10）：运行时从环境变量取
const CREDENTIALS = { account: "demo", password: process.env.E2E_DEMO_PASSWORD ?? "" };

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill(CREDENTIALS.account);
  await page.locator("#password").fill(CREDENTIALS.password);
  await page.locator('form button[type="submit"]').click();
  // 登录端点有限流，被限流时等窗口恢复后重试
  const loggedIn = page.getByRole("link", { name: /任务/ });
  for (let i = 0; i < 8; i += 1) {
    const settled = await Promise.race([
      loggedIn.waitFor({ state: "visible", timeout: 10_000 }).then(() => "ok" as const).catch(() => null),
      page.locator("text=请求过于频繁").waitFor({ state: "visible", timeout: 10_000 }).then(() => "limited" as const).catch(() => null),
    ]);
    if (settled === "ok") return;
    await page.waitForTimeout(8_000);
    await page.locator('form button[type="submit"]').click().catch(() => undefined);
  }
  await expect(loggedIn).toBeVisible({ timeout: 30_000 });
}

test("PRISMA 筛选工作台：页面可达且无横向溢出", async ({ page }) => {
  await login(page);
  // 从任务列表进入第一个任务的筛选页
  const firstTask = page.locator('[data-test^="task-row-"]').first();
  await expect(firstTask).toBeVisible({ timeout: 30_000 });
  await firstTask.click();
  await page.getByRole("link", { name: "PRISMA 筛选" }).click();
  await expect(page.getByRole("heading", { name: "PRISMA 筛选" })).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
```

Expected: PASS

- [x] **Step 4: 更新文档并推送**

更新 `docs/superpowers/plans/2026-08-09-00-execution-notes.md`（新增 Phase 2 执行记录段）
与 `AGENTS.md` 快照，然后：

```powershell
git push origin main
git push backup main
```

---

## 验收清单

- [x] 检索落库后 `hits_total` / `hits_deduped` 正确，跨源同题去重
- [x] **`hits_by_source` 保存去重前各源命中数，且 `sum(hits_by_source) == hits_total`**
- [x] **PRISMA「识别」用去重前的各源命中数，各源合计与 `identified_total` 自洽**
- [x] 三阶段决策可追加，`include` 推进阶段、`exclude`/`maybe` 停留
- [x] 排除缺理由被拒（服务层 ValueError → 400；DB CheckConstraint 双保险）
- [x] PRISMA 计数由明细派生，且各阶段数字自洽（screened = included + excluded + pending）
- [x] 双人分歧可被 `find_conflicts` 识别
- [x] Markdown 表格与 Mermaid 流程图可生成，零数据不抛错
- [x] 前端工作台可纳入/排除（排除需填理由），展示 PRISMA 计数
- [x] 全量门禁：black/ruff/mypy/pytest ≥80% cov；前端 Vitest + `vue-tsc` + build
- [x] CI 6 作业全绿
