"""LLM usage 缓存字段解析（Issue #22 埋点）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm.adapters.openai import _parse_cache_usage
from app.llm.models import Usage


class TestUsageModel:
    def test_defaults_are_zero_and_rate_is_zero(self) -> None:
        usage = Usage()
        assert usage.cache_hit_tokens == 0
        assert usage.cache_miss_tokens == 0
        assert usage.cache_hit_rate == 0.0

    def test_total_tokens_unchanged(self) -> None:
        usage = Usage(prompt_tokens=10, completion_tokens=5)
        assert usage.total_tokens == 15

    def test_hit_rate_computed_from_observed(self) -> None:
        usage = Usage(prompt_tokens=1000, completion_tokens=100, cache_hit_tokens=750)
        assert usage.cache_hit_rate == 0.75


class TestParseCacheUsage:
    def test_deepseek_style_object(self) -> None:
        raw = SimpleNamespace(
            prompt_tokens=1000,
            prompt_cache_hit_tokens=640,
            prompt_cache_miss_tokens=360,
        )
        assert _parse_cache_usage(raw) == {"cache_hit_tokens": 640, "cache_miss_tokens": 360}

    def test_deepseek_style_dict(self) -> None:
        raw = {"prompt_tokens": 100, "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36}
        assert _parse_cache_usage(raw) == {"cache_hit_tokens": 64, "cache_miss_tokens": 36}

    def test_openai_nested_details(self) -> None:
        raw = SimpleNamespace(
            prompt_tokens=1000,
            prompt_cache_hit_tokens=None,
            prompt_cache_miss_tokens=None,
            prompt_tokens_details=SimpleNamespace(cached_tokens=512),
        )
        assert _parse_cache_usage(raw) == {"cache_hit_tokens": 512, "cache_miss_tokens": 488}

    def test_vendor_without_cache_fields_is_tolerated(self) -> None:
        raw = SimpleNamespace(prompt_tokens=42)
        assert _parse_cache_usage(raw) == {"cache_hit_tokens": 0, "cache_miss_tokens": 0}

    def test_garbage_values_do_not_raise(self) -> None:
        raw = {"prompt_cache_hit_tokens": "n/a", "prompt_cache_miss_tokens": -5}
        assert _parse_cache_usage(raw) == {"cache_hit_tokens": 0, "cache_miss_tokens": 0}
