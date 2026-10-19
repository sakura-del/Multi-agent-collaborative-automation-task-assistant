"""任务级引用核验开关（Issue #3 遗留项 / 计划 §1.1）。

背景：`offline_dataset=true` 的演示任务默认**零外呼**（R6：避免 CrossRef/OpenAlex 429 拖垮任务），
核验器被摘掉 → 产物里没有 `stats.reference_verification`，前端只能显示空态。
但「引用可核验」是本产品的核心卖点，演示时需要有办法**显式打开**核验。

本文件锁定开关的三态语义与优先级：
- `None`（默认）：沿用既有 `_offline_without_verification()` 逻辑 → 行为**完全不变**（R6 不回归）
- `True`：本任务强制核验，**即使离线**（核验结论有 7 天缓存兜底）
- `False`：本任务强制跳过核验
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.orchestration.contracts import LiteratureReviewRequest
from app.orchestration.services.task_execution import TaskExecution
from app.schemas.task import TaskCreate

SENTINEL_VERIFIER = object()


class _Task:
    """最小任务替身：`_offline_without_verification` 只看 config。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self.id = uuid4()
        self.config = config
        self.task_type = "literature_review"


def _execution_with_verifier(verifier: Any = SENTINEL_VERIFIER) -> TaskExecution:
    """构造只用于判定逻辑的 TaskExecution（不启动容器、不碰数据库）。"""

    class _Container:
        reference_verifier = verifier

    execution = TaskExecution.__new__(TaskExecution)
    execution.container = _Container()  # type: ignore[assignment]
    return execution


class TestContractField:
    def test_default_is_none(self) -> None:
        """默认 None：不改变既有行为。"""
        request = LiteratureReviewRequest(topic="检索增强生成评估", keywords=["RAG"])
        assert request.verify_references is None

    def test_accepts_true_and_false(self) -> None:
        for value in (True, False):
            request = LiteratureReviewRequest(
                topic="检索增强生成评估", keywords=["RAG"], verify_references=value
            )
            assert request.verify_references is value

    def test_round_trips_through_model_dump(self) -> None:
        """落库靠 model_dump：开关必须进 config，重试/恢复路径才一致。"""
        request = LiteratureReviewRequest(
            topic="检索增强生成评估", keywords=["RAG"], verify_references=True
        )
        assert request.model_dump(mode="json")["verify_references"] is True


class TestHttpSchema:
    def test_task_create_accepts_switch(self) -> None:
        payload = TaskCreate(
            topic="检索增强生成评估方法综述",
            keywords=["RAG"],
            verify_references=True,
        )
        assert payload.verify_references is True
        assert payload.to_contract().verify_references is True

    def test_task_create_defaults_to_none(self) -> None:
        payload = TaskCreate(topic="检索增强生成评估方法综述", keywords=["RAG"])
        assert payload.verify_references is None

    def test_unknown_field_still_rejected(self) -> None:
        """extra="forbid" 不得被新字段破坏。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TaskCreate(
                topic="检索增强生成评估方法综述",
                keywords=["RAG"],
                verify_refrence=True,  # 拼写错误必须报错
            )


class TestVerifierSelection:
    def test_default_offline_task_skips_verifier(self, monkeypatch) -> None:
        """守卫 R6：离线任务 + 默认配置 → 不注入核验器（零外呼）。"""
        execution = _execution_with_verifier()
        task = _Task({"offline_dataset": True})

        assert execution._reference_verifier_for_task(task) is None

    def test_explicit_true_forces_verification_on_offline_task(self) -> None:
        """Issue #3 演示路径：显式打开时离线任务也做核验。"""
        execution = _execution_with_verifier()
        task = _Task({"offline_dataset": True, "verify_references": True})

        assert execution._reference_verifier_for_task(task) is SENTINEL_VERIFIER

    def test_explicit_false_skips_even_online_task(self) -> None:
        """显式关闭：即使在线任务也不核验。"""
        execution = _execution_with_verifier()
        task = _Task({"offline_dataset": False, "verify_references": False})

        assert execution._reference_verifier_for_task(task) is None

    def test_none_keeps_online_task_verified(self) -> None:
        """None + 在线任务 → 沿用既有逻辑（核验）。"""
        execution = _execution_with_verifier()
        task = _Task({"offline_dataset": False})

        assert execution._reference_verifier_for_task(task) is SENTINEL_VERIFIER

    def test_none_key_absent_behaves_like_none(self) -> None:
        """历史任务 config 没有该键 → 与 None 同语义。"""
        execution = _execution_with_verifier()

        assert execution._reference_verifier_for_task(_Task({"offline_dataset": True})) is None


class TestReviewerPathHonoursSwitch:
    def test_reviewer_keeps_verifier_when_switch_true(self) -> None:
        """复盘路径同样要看开关：显式打开时不得摘掉 reviewer 的核验器。"""
        execution = _execution_with_verifier()

        class _Reviewer:
            reference_verifier = SENTINEL_VERIFIER

        reviewer = _Reviewer()
        execution._apply_reference_verification(
            reviewer, _Task({"offline_dataset": True, "verify_references": True})
        )

        assert reviewer.reference_verifier is SENTINEL_VERIFIER

    def test_reviewer_loses_verifier_by_default_offline(self) -> None:
        """守卫 R6：默认离线仍摘掉 reviewer 核验器。"""
        execution = _execution_with_verifier()

        class _Reviewer:
            reference_verifier = SENTINEL_VERIFIER

        reviewer = _Reviewer()
        execution._apply_reference_verification(reviewer, _Task({"offline_dataset": True}))

        assert reviewer.reference_verifier is None
