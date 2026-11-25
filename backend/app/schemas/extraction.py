"""自定义论文抽取表 HTTP Schema（Issue #36）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.orchestration.contracts import EvidenceLocator

ExtractionSourceType = Literal["knowledge_doc", "screening_record"]
ExtractionCellStatus = Literal["pending", "extracted", "not_reported", "error"]
ExtractionTableStatus = Literal["draft", "running", "completed", "failed"]


class ExtractionColumnSpec(BaseModel):
    """自定义抽取列定义。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    label: str = Field(min_length=1, max_length=48)
    prompt: str = Field(min_length=2, max_length=400)


class ExtractionSourceRef(BaseModel):
    """待抽取论文来源引用（仅允许 knowledge_doc / screening_record）。"""

    model_config = ConfigDict(extra="forbid")

    source_type: ExtractionSourceType
    source_id: UUID


class ExtractionTableCreate(BaseModel):
    """创建抽取表请求。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    task_id: UUID | None = None
    columns: list[ExtractionColumnSpec] = Field(min_length=1, max_length=12)
    sources: list[ExtractionSourceRef] = Field(min_length=1, max_length=50)
    llm_credential_id: UUID | None = None
    auto_run: bool = True

    @field_validator("columns")
    @classmethod
    def _validate_unique_column_keys(
        cls, value: list[ExtractionColumnSpec]
    ) -> list[ExtractionColumnSpec]:
        seen: set[str] = set()
        for col in value:
            if col.key in seen:
                raise ValueError(f"列标识重复：{col.key}")
            seen.add(col.key)
        return value


class ExtractionColumnAddRequest(BaseModel):
    """向既有抽取表追加自定义列。"""

    model_config = ConfigDict(extra="forbid")

    column: ExtractionColumnSpec
    auto_run: bool = False
    llm_credential_id: UUID | None = None


class ExtractionRunRequest(BaseModel):
    """重跑整表或仅重跑指定列/指定论文。"""

    model_config = ConfigDict(extra="forbid")

    column_keys: list[str] = Field(default_factory=list, max_length=12)
    source_ids: list[UUID] = Field(default_factory=list, max_length=50)
    llm_credential_id: UUID | None = None


class ExtractionCellOut(BaseModel):
    """单个单元格抽取结果。"""

    model_config = ConfigDict(extra="forbid")

    source_type: ExtractionSourceType
    source_id: str
    column_key: str
    status: ExtractionCellStatus
    value: str | None = None
    quote: str | None = None
    passage: str | None = None
    locator: EvidenceLocator | None = None
    evidence_scope: Literal["full_text", "abstract_only"] = "full_text"
    confidence: float | None = None


class ExtractionSourceOut(BaseModel):
    """抽取表行源展示元数据。"""

    model_config = ConfigDict(extra="forbid")

    source_type: ExtractionSourceType
    source_id: str
    title: str
    year: int | None = None
    source_url: str | None = None


class ExtractionTableSummaryOut(BaseModel):
    """抽取表列表摘要。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    task_id: UUID | None = None
    title: str
    status: ExtractionTableStatus
    error: str | None = None
    column_count: int
    source_count: int
    completed_cells: int = 0
    failed_cells: int = 0
    total_cells: int = 0
    created_at: datetime
    updated_at: datetime


class ExtractionTableListOut(BaseModel):
    """抽取表列表响应。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ExtractionTableSummaryOut]


class ExtractionTableOut(BaseModel):
    """抽取表完整详情（含所有单元格）。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    task_id: UUID | None = None
    title: str
    status: ExtractionTableStatus
    error: str | None = None
    columns: list[ExtractionColumnSpec]
    sources: list[ExtractionSourceOut]
    cells: list[ExtractionCellOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
