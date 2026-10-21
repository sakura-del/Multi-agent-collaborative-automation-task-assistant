"""筛选工作台 HTTP 契约（Phase 2 · PRISMA）。

约定与既有端点一致：`extra="forbid"`（多余字段直接 422，避免前端拼错字段被静默忽略），
枚举字段用真实枚举类型（Pydantic 自动校验，OpenAPI 里也能看到合法取值）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.screening import DecisionStage, ScreeningVerdict

#: 命中数必须非负：负数会让 PRISMA 流程图的数字自相矛盾。
_NonNegativeInt = Annotated[int, Field(ge=0)]


class LiteratureRecordIn(BaseModel):
    """检索结果（客户端提交的最小字段集）。

    `doi` **必须**保留并参与落库——DOI 是跨源去重的最高优先级键。
    本类不暴露 `source_url`/`pdf_url`：上层来源的 URL 形式各异，
    对外契约只收「标识 + 内容」，URL 由适配层按来源规范生成。
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list, max_length=50)
    abstract: str = Field(default="", max_length=20_000)
    doi: str | None = Field(default=None, max_length=256)


class SearchLogCreate(BaseModel):
    """记录一次检索。

    `sources` 是本次检索的库；`hits_by_source` 是**去重前**各库命中数
    （PRISMA「识别」阶段依据）。`hits_total` 可显式给出——上游分页/限流
    只回传部分样本时，识别数字不能退化成样本条数。
    """

    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID
    query: str = Field(min_length=2, max_length=2000)
    sources: list[str] = Field(min_length=1, max_length=10)
    hits_by_source: dict[str, _NonNegativeInt] = Field(default_factory=dict)
    hits_total: _NonNegativeInt | None = None
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
    doi: str | None = None
    fulltext_status: str
    current_stage: str


class ScreeningRecordListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScreeningRecordOut] = Field(default_factory=list)


class DecisionCreate(BaseModel):
    """追加一条筛选决策。

    `stage`/`verdict` 用枚举：非法取值由 Pydantic 直接转 422，
    不必在端点里手写字符串正则。
    """

    model_config = ConfigDict(extra="forbid")

    stage: DecisionStage
    verdict: ScreeningVerdict
    exclusion_reason: str | None = Field(default=None, max_length=256)
    note: str = Field(default="", max_length=2000)


class DecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: uuid.UUID
    current_stage: str


class PrismaOut(BaseModel):
    """PRISMA 2020 计数 + 可直接进论文的渲染结果（Markdown / Mermaid）。"""

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
