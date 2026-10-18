"""实验输入的 Pydantic 契约（Plan 5 Task 5）。

三类不可变输入：
- `ExperimentConfig`：四组实验画像（baseline_a/b/c/full）
- `EvaluationTopic`：15 个冻结课题（3 领域 × 5）
- `HumanRating`：盲评表格行（1–5 整数分）

所有模型 `extra="forbid"`：拼错字段名必须直接失败，避免"看起来跑通但配置没生效"。
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 固定实验参数（roadmap §3：模型 / 温度 / 语料与 Prompt 版本 / 种子）
FROZEN_MODEL = "gpt-6-luna"
FROZEN_PROVIDER = "openai"
FROZEN_TEMPERATURE = 0.2
FROZEN_MAX_OUTPUT_TOKENS = 2048
FROZEN_SEED = 20261017
FROZEN_PROMPT_VERSION = "lit-v1"
FROZEN_CORPUS_VERSION = "corpus-10-v1"

# 允许的实验画像名称（与 roadmap §3 的四组一一对应）
ALLOWED_PROFILE_NAMES = {
    "single_agent_no_rag",
    "multi_agent_no_rag",
    "multi_agent_vector_rag",
    "multi_agent_hybrid_rerank",
}

TOPIC_ID_RE = re.compile(r"^[a-z]{2,4}-[a-z0-9-]{2,40}$")


class TopicDomain(StrEnum):
    """三个实验领域（roadmap §3：15 课题 × 3 领域）。"""

    AI = "ai"
    SOFTWARE_ENGINEERING = "software_engineering"
    EDUCATION_TECHNOLOGY = "education_technology"


class AgentMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


class RetrievalMode(StrEnum):
    NONE = "none"
    VECTOR = "vector"
    HYBRID_RERANK = "hybrid_rerank"


class ExperimentConfig(BaseModel):
    """单组实验画像；除画像开关外，所有参数必须一致（可复现性前提）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="画像名，必须属于四组之一")
    agent_mode: AgentMode
    retrieval_mode: RetrievalMode
    validation_enabled: bool
    review_enabled: bool

    # 固定参数：每组都写死同样的值，校验时互相比较
    provider: str = FROZEN_PROVIDER
    model: str = FROZEN_MODEL
    temperature: float = FROZEN_TEMPERATURE
    max_output_tokens: int = FROZEN_MAX_OUTPUT_TOKENS
    prompt_version: str = FROZEN_PROMPT_VERSION
    corpus_version: str = FROZEN_CORPUS_VERSION
    seed: int = FROZEN_SEED
    runs_per_topic: int = Field(default=2, ge=1, le=10)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if value not in ALLOWED_PROFILE_NAMES:
            raise ValueError(f"未知实验画像: {value}（允许: {sorted(ALLOWED_PROFILE_NAMES)}）")
        return value

    @field_validator("temperature")
    @classmethod
    def _check_temperature(cls, value: float) -> float:
        if abs(value - FROZEN_TEMPERATURE) > 1e-9:
            raise ValueError(f"温度必须固定为 {FROZEN_TEMPERATURE}（当前 {value}）")
        return value

    @model_validator(mode="after")
    def _check_frozen_parameters(self) -> ExperimentConfig:
        expected = {
            "provider": FROZEN_PROVIDER,
            "model": FROZEN_MODEL,
            "max_output_tokens": FROZEN_MAX_OUTPUT_TOKENS,
            "prompt_version": FROZEN_PROMPT_VERSION,
            "corpus_version": FROZEN_CORPUS_VERSION,
            "seed": FROZEN_SEED,
        }
        for field, want in expected.items():
            got = getattr(self, field)
            if got != want:
                raise ValueError(f"固定参数 {field} 必须为 {want!r}（当前 {got!r}）")
        return self

    @model_validator(mode="after")
    def _check_switch_consistency(self) -> ExperimentConfig:
        """画像开关必须与名称语义一致，避免"名字叫 baseline 但开了 RAG"。"""
        expected: dict[str, dict[str, Any]] = {
            "single_agent_no_rag": {
                "agent_mode": AgentMode.SINGLE,
                "retrieval_mode": RetrievalMode.NONE,
                "validation_enabled": False,
                "review_enabled": False,
            },
            "multi_agent_no_rag": {
                "agent_mode": AgentMode.MULTI,
                "retrieval_mode": RetrievalMode.NONE,
                "validation_enabled": True,
                "review_enabled": True,
            },
            "multi_agent_vector_rag": {
                "agent_mode": AgentMode.MULTI,
                "retrieval_mode": RetrievalMode.VECTOR,
                "validation_enabled": True,
                "review_enabled": True,
            },
            "multi_agent_hybrid_rerank": {
                "agent_mode": AgentMode.MULTI,
                "retrieval_mode": RetrievalMode.HYBRID_RERANK,
                "validation_enabled": True,
                "review_enabled": True,
            },
        }[self.name]
        for field, want in expected.items():
            if getattr(self, field) != want:
                raise ValueError(
                    f"画像 {self.name} 的 {field} 应为 {want}（当前 {getattr(self, field)}）"
                )
        return self


class EvaluationTopic(BaseModel):
    """单个冻结课题：中文主题 + 英文检索式 + 领域 + 语料清单。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    topic_id: str = Field(description="形如 ai-rag-eval 的稳定 ID")
    domain: TopicDomain
    topic_zh: str = Field(min_length=2, max_length=200)
    query_en: str = Field(min_length=3, max_length=300)
    corpus_manifest: str = Field(description="相对 backend/ 的语料清单路径")

    @field_validator("topic_id")
    @classmethod
    def _check_topic_id(cls, value: str) -> str:
        if not TOPIC_ID_RE.match(value):
            raise ValueError(f"topic_id 格式非法: {value}（应形如 ai-rag-eval）")
        return value


class HumanRating(BaseModel):
    """盲评表格的一行：1–5 整数分，输出 ID 不暴露实验分组。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anonymous_output_id: str = Field(min_length=4, max_length=64)
    evaluator_id: str = Field(min_length=1, max_length=64)
    accuracy: int = Field(ge=1, le=5)
    coherence: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    citation_quality: int = Field(ge=1, le=5)
    notes: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _check_anonymous_id(self) -> HumanRating:
        """匿名 ID 不得包含画像名/分组关键词，否则盲评失效。"""
        lowered = self.anonymous_output_id.lower()
        leaks = [
            token
            for token in ("baseline", "single_agent", "multi_agent", "hybrid", "vector_rag", "full")
            if token in lowered
        ]
        if leaks:
            raise ValueError(f"anonymous_output_id 泄漏分组信息: {leaks}")
        return self

    @property
    def mean_score(self) -> float:
        return (self.accuracy + self.coherence + self.completeness + self.citation_quality) / 4.0


# ===== 加载与校验辅助 =====


def load_experiment_config(path: Path) -> ExperimentConfig:
    """从 YAML 加载单组画像（严格模式：多余字段即失败）。"""
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"配置文件必须是映射: {path}")
    return ExperimentConfig.model_validate(payload)


def load_topics(path: Path) -> list[EvaluationTopic]:
    """从 JSON 加载课题列表，拒绝重复 topic_id。"""
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("topics") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise ValueError(f"课题清单为空或格式非法: {path}")
    topics = [EvaluationTopic.model_validate(item) for item in items]

    seen: set[str] = set()
    duplicates: list[str] = []
    for topic in topics:
        if topic.topic_id in seen:
            duplicates.append(topic.topic_id)
        seen.add(topic.topic_id)
    if duplicates:
        raise ValueError(f"存在重复 topic_id: {sorted(set(duplicates))}")
    return topics


def load_human_ratings(path: Path) -> list[HumanRating]:
    """从 CSV 加载盲评记录（表头必须完全一致）。"""
    import csv

    expected_columns = [
        "anonymous_output_id",
        "evaluator_id",
        "accuracy",
        "coherence",
        "completeness",
        "citation_quality",
        "notes",
    ]
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_columns:
            raise ValueError(f"评分表表头不符: {reader.fieldnames} != {expected_columns}")
        return [HumanRating.model_validate(row) for row in reader]
