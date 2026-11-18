"""模型能力元数据（Issue #22 延伸）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。
"""

from __future__ import annotations

from app.llm.model_profiles import ModelProfile, apply_model_profile, profile_for


class TestProfileLookup:
    def test_exact_match_local_qwen(self) -> None:
        profile = profile_for("qwen3.8-27b-uncensored-fp8")
        assert profile.disable_thinking is True
        assert profile.min_max_tokens_with_thinking >= 2000

    def test_provider_prefix_stripped(self) -> None:
        # /v1/models 里无 qwen/ 前缀，但用户/文档常带前缀——两种都要认
        assert profile_for("qwen/qwen3.8-27b-uncensored-fp8").disable_thinking is True

    def test_free_pool_marked_low_concurrency(self) -> None:
        profile = profile_for("qiniu/conatus-free")
        assert profile.concurrency_friendly is False
        assert profile.recommended_max_concurrency == 1

    def test_deepseek_supports_cache(self) -> None:
        assert profile_for("deepseek-flash").supports_prefix_cache is True

    def test_unknown_model_uses_conservative_default(self) -> None:
        assert profile_for("some-unknown-model") == ModelProfile()

    def test_empty_model_returns_default(self) -> None:
        assert profile_for("") == ModelProfile()


class TestApplyProfile:
    def test_thinking_disabled_for_local_qwen(self) -> None:
        payload: dict = {}
        apply_model_profile(payload, "qwen3.8-27b-uncensored-fp8")
        assert payload["chat_template_kwargs"]["enable_thinking"] is False

    def test_caller_override_wins(self) -> None:
        payload = {"chat_template_kwargs": {"enable_thinking": True}}
        apply_model_profile(payload, "qwen3.8-27b-uncensored-fp8")
        assert payload["chat_template_kwargs"]["enable_thinking"] is True

    def test_no_injection_for_normal_models(self) -> None:
        payload: dict = {}
        apply_model_profile(payload, "deepseek-flash")
        assert "chat_template_kwargs" not in payload
