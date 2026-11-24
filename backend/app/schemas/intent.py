"""意图分类的请求/响应契约（端点与工具网关共用）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
