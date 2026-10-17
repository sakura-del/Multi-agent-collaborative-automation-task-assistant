"""文献工具的结构化契约（Plan 3 Task 1）。

严格模型（`extra="forbid"`）：上游 Atom 字段与离线清单都必须先通过 schema 校验，
不允许把未知字段静默带入后续证据链。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class LiteratureSearch(BaseModel):
    """arXiv 检索请求。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=10, ge=1, le=50)
    date_from: date | None = None
    date_to: date | None = None


class LiteratureRecord(BaseModel):
    """归一化后的文献记录（arXiv 或离线清单）。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    published_at: datetime
    source_url: HttpUrl
    pdf_url: HttpUrl
    categories: list[str] = Field(default_factory=list)

    @field_validator("published_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        """统一为 UTC：naive 时间按 UTC 解释，带时区的时间转换为 UTC。"""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
