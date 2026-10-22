"""实验画像开关契约测试（2026-10-22 实验矩阵 Task 1）。

## 本文件锁定

1. `LiteratureReviewRequest` 新增三个画像开关，**默认全开**（不改变既有行为）；
2. 非法取值被拒（Pydantic 校验）；
3. `TaskCreate`（HTTP 层）能透传三个开关到契约。

## 为什么默认必须全开

网页用户不传这些字段时应得到**完整能力**（检索 + 校验 + 复盘），
与改造前一致。若默认关掉任何一个，既有 400+ 测试与真实用户行为都会静默降级。

## 为什么开关放在请求契约而不是全局配置

120 次运行要跑四种画像。若用全局配置，每次切换都要重启进程，
且并发跑不同画像会互相污染。放契约里，每个 run 的 `config.json` 天然记录了
开关取值 —— 可复现、可并发、可追溯。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.orchestration.contracts import LiteratureReviewRequest


def _minimal(**overrides: object) -> LiteratureReviewRequest:
    payload: dict[str, object] = {
        "topic": "检索增强生成综述",
        "keywords": ["RAG"],
    }
    payload.update(overrides)
    return LiteratureReviewRequest(**payload)


class TestDefaultsAreFullyEnabled:
    def test_retrieval_mode_defaults_to_hybrid_rerank(self) -> None:
        """默认最强检索：网页用户不传就该得到完整能力。"""
        request = _minimal()

        assert request.retrieval_mode == "hybrid_rerank"

    def test_validation_enabled_by_default(self) -> None:
        assert _minimal().validation_enabled is True

    def test_review_enabled_by_default(self) -> None:
        assert _minimal().review_enabled is True

    def test_defaults_preserve_existing_behaviour(self) -> None:
        """既有调用方（不传新字段）行为不变 —— 这是兼容性的硬要求。"""
        request = _minimal()

        assert request.retrieval_mode == "hybrid_rerank"
        assert request.validation_enabled is True
        assert request.review_enabled is True


class TestExplicitValues:
    @pytest.mark.parametrize("mode", ["none", "vector", "hybrid_rerank"])
    def test_valid_retrieval_modes(self, mode: str) -> None:
        request = _minimal(retrieval_mode=mode)

        assert request.retrieval_mode == mode

    def test_can_disable_validation(self) -> None:
        request = _minimal(validation_enabled=False)

        assert request.validation_enabled is False

    def test_can_disable_review(self) -> None:
        request = _minimal(review_enabled=False)

        assert request.review_enabled is False

    def test_invalid_retrieval_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _minimal(retrieval_mode="semantic")  # 不是合法值

    def test_extra_field_still_rejected(self) -> None:
        """契约保持 `extra="forbid"`（不能因为加字段就放松）。"""
        with pytest.raises(ValidationError):
            _minimal(nonexistent_switch=True)


class TestTaskCreatePassthrough:
    """HTTP 层 `TaskCreate` 必须能透传三个开关。"""

    def test_task_create_defaults(self) -> None:
        from app.schemas.task import TaskCreate

        payload = TaskCreate(topic="检索增强生成综述", keywords=["RAG"])

        assert payload.retrieval_mode == "hybrid_rerank"
        assert payload.validation_enabled is True
        assert payload.review_enabled is True

    def test_task_create_to_contract_passes_switches(self) -> None:
        from app.schemas.task import TaskCreate

        payload = TaskCreate(
            topic="检索增强生成综述",
            keywords=["RAG"],
            retrieval_mode="none",
            validation_enabled=False,
            review_enabled=False,
        )

        contract = payload.to_contract()

        assert contract.retrieval_mode == "none"
        assert contract.validation_enabled is False
        assert contract.review_enabled is False

    def test_task_create_rejects_invalid_mode(self) -> None:
        from app.schemas.task import TaskCreate

        with pytest.raises(ValidationError):
            TaskCreate(topic="检索增强生成综述", keywords=["RAG"], retrieval_mode="bad")
