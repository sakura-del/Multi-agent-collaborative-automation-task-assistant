"""任务创建草稿 HTTP 契约（Issue #68）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DRAFT_PAYLOAD_BYTES = 64 * 1024  # 64 KB 硬上限，防大包滥用


class TaskDraftPayload(BaseModel):
    """表单草稿白名单字段（允许部分填写，不强制必填）。"""

    model_config = ConfigDict(extra="forbid")

    workflow_preset: str | None = Field(default=None, max_length=32)
    topic: str = Field(default="", max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    max_documents: int | None = Field(default=None, ge=1, le=50)
    date_from: str | None = Field(default=None, max_length=16)
    date_to: str | None = Field(default=None, max_length=16)
    output_language: Literal["zh", "en"] | None = None
    citation_style: str | None = Field(default=None, max_length=64)
    offline_dataset: bool | None = None
    reference_library: bool | None = None
    verify_references: bool | None = None
    document_ids: list[str] = Field(default_factory=list, max_length=50)
    literature_sources: list[str] = Field(default_factory=list, max_length=6)
    model_source: Literal["platform", "byok"] | None = None
    llm_credential_id: str | None = Field(default=None, max_length=64)
    draft_text: str | None = Field(default=None, max_length=30000)
    seed_dois: list[str] = Field(default_factory=list, max_length=5)
    snowball_direction: Literal["both", "backward", "forward"] | None = None

    @model_validator(mode="after")
    def _check_total_size(self) -> TaskDraftPayload:
        raw = json.dumps(self.model_dump(exclude_none=True), ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_DRAFT_PAYLOAD_BYTES:
            raise ValueError("草稿体积超过 64 KB 上限")
        return self


class TaskDraftPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: TaskDraftPayload
    #: 客户端已知的云端版本号；None 表示强制覆盖（用户点击「用本地覆盖」时使用）
    base_revision: int | None = Field(default=None, ge=0)


class TaskDraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    revision: int
    updated_at: datetime
