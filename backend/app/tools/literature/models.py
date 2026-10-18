"""文献工具的结构化契约（Plan 3 Task 1）。

严格模型（`extra="forbid"`）：上游 Atom 字段与离线清单都必须先通过 schema 校验，
不允许把未知字段静默带入后续证据链。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

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


class Artifact(BaseModel):
    """服务端生成的导出产物元数据（Plan 3 Task 5）。

    `artifact_id` 由服务端 uuid4 生成，客户端只能用它做查找；
    真实路径固定为 `<artifact_root>/<user_id>/<task_id>/<artifact_id><suffix>`。
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    kind: Literal["markdown", "docx"]
    filename: str = Field(min_length=1, max_length=128)
    download_name: str = Field(min_length=1, max_length=200)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


# ===== 引用真实性校验契约（Issue #3，Lead 冻结：实现方不得改字段名/语义）=====


class MetadataRecord(BaseModel):
    """外部元数据源（CrossRef / OpenAlex）归一化后的单条文献元数据。

    只取元数据，不取全文（版权约束）。
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["crossref", "openalex"]
    doi: str | None = None
    """归一化 DOI：小写、去掉 `https://doi.org/` 前缀；无 DOI 时为 None。"""

    title: str = Field(min_length=1, max_length=1000)
    authors: list[str] = Field(default_factory=list)
    """作者姓名原样保留（如 `"Zhang, Wei"` 或 `"Wei Zhang"`），比对时再归一化。"""

    year: int | None = Field(default=None, ge=1000, le=2999)
    container_title: str | None = None
    """期刊 / 会议 / 预印本平台名。"""

    cited_by_count: int | None = Field(default=None, ge=0)
    is_open_access: bool | None = None
    source_url: HttpUrl | None = None


class ReferenceCandidate(BaseModel):
    """待核验的本地参考文献（来自检索节点 artifacts 或 bibliography 条目）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    """稳定标识：有 DOI 用归一化 DOI，否则用标题归一化值（由调用方构造）。"""

    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1000, le=2999)
    doi: str | None = None
    source_url: str | None = None


class ReferenceVerdict(StrEnum):
    """核验结论四态；前三态是判定，`UNVERIFIED` 是外部源不可用时的降级态。"""

    VERIFIED = "verified"
    """DOI/标题可解析，且标题/作者/年份与外部元数据吻合。"""

    METADATA_MISMATCH = "metadata_mismatch"
    """文献存在，但标题相似度不足、作者或年份偏差超阈值。"""

    NOT_FOUND = "not_found"
    """DOI 不存在（或无效），且标题检索在外部源无命中。"""

    UNVERIFIED = "unverified"
    """外部源超时/限流/不可用导致无法判定：不得据此判失败或阻塞任务。"""


class ReferenceVerification(BaseModel):
    """单条参考文献的核验结论（Issue #3 的核心输出契约）。"""

    model_config = ConfigDict(extra="forbid")

    reference_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    verdict: ReferenceVerdict
    reason: str = ""
    """人类可读判定理由（进入校验报告 feedback 与导出报告）。"""

    similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    matched: MetadataRecord | None = None
    cached: bool = False
    """是否来自核验缓存（缓存命中不得再次外呼）。"""


class ReferenceVerificationSummary(BaseModel):
    """核验统计汇总：供校验报告与导出报告展示。"""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(default=0, ge=0)
    verified: int = Field(default=0, ge=0)
    metadata_mismatch: int = Field(default=0, ge=0)
    not_found: int = Field(default=0, ge=0)
    unverified: int = Field(default=0, ge=0)
    cached_hits: int = Field(default=0, ge=0)
    dropped: int = Field(default=0, ge=0)
    """因「拦截未核验引用」开关被剔出参考文献列表的条数。"""

    intercepted: bool = False
    """拦截开关是否生效。"""
