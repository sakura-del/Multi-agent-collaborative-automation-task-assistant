"""意图分类的请求/响应契约（端点与工具网关共用）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.orchestration.contracts import WorkflowPreset


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=500)


class IntentResponse(BaseModel):
    intent: Literal["literature_review", "other"]
    topic: str
    keywords: list[str] = Field(default_factory=list, max_length=6)
    source: Literal["llm", "fallback"]
    workflow_preset: WorkflowPreset = "literature_review"


class KeywordSuggestRequest(BaseModel):
    """AI 拆分学术检索关键词请求契约。"""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=5, max_length=200)
    output_language: Literal["zh", "en"] = "zh"
    existing_keywords: list[str] = Field(default_factory=list, max_length=10)
    max_keywords: int = Field(default=8, ge=3, le=10)

    @field_validator("topic", mode="before")
    @classmethod
    def _strip_topic(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class KeywordSuggestion(BaseModel):
    """单个候选关键词芯片。"""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, max_length=40)
    lang: Literal["zh", "en"] = "zh"


class KeywordSuggestResponse(BaseModel):
    """AI 拆词响应（含模型直出或确定性兜底来源标记）。"""

    model_config = ConfigDict(extra="forbid")

    suggestions: list[KeywordSuggestion] = Field(default_factory=list)
    source: Literal["llm", "fallback"]
