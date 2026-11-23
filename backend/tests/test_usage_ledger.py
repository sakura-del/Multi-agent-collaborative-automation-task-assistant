"""任务级用量账本（Issue #16 修复）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。

覆盖：
1. add/take 的累加与清零语义；
2. record_usage 在无账本时静默跳过；
3. 作用域隔离（任务之间不串账）。
"""

from __future__ import annotations

from app.llm.models import Usage
from app.llm.usage_ledger import UsageLedger, current_ledger, record_usage, use_usage_ledger


class TestUsageLedger:
    def test_add_accumulates_all_fields(self) -> None:
        ledger = UsageLedger()
        ledger.add(Usage(prompt_tokens=100, completion_tokens=50, cache_hit_tokens=60))
        ledger.add(Usage(prompt_tokens=10, completion_tokens=5, cache_miss_tokens=10))
        assert ledger.calls == 2
        assert ledger.prompt_tokens == 110
        assert ledger.completion_tokens == 55
        assert ledger.cache_hit_tokens == 60
        assert ledger.cache_miss_tokens == 10
        assert ledger.total_tokens == 165

    def test_take_returns_delta_and_clears(self) -> None:
        ledger = UsageLedger()
        ledger.add(Usage(prompt_tokens=100, completion_tokens=50))
        first = ledger.take()
        assert first.prompt_tokens == 100 and first.completion_tokens == 50
        # 第二次 take 应该是 0（清零）
        second = ledger.take()
        assert second.total_tokens == 0
        # 清零后继续累加
        ledger.add(Usage(prompt_tokens=7))
        assert ledger.take().prompt_tokens == 7

    def test_add_ignores_negative(self) -> None:
        ledger = UsageLedger()
        ledger.add(Usage(prompt_tokens=-5, completion_tokens=-1))
        assert ledger.total_tokens == 0


class TestRecordUsageScope:
    def test_noop_without_ledger(self) -> None:
        record_usage(Usage(prompt_tokens=100))  # 不应抛异常

    def test_records_into_active_ledger(self) -> None:
        with use_usage_ledger() as ledger:
            record_usage(Usage(prompt_tokens=42, completion_tokens=8))
            assert ledger.prompt_tokens == 42
            assert current_ledger() is ledger
        assert current_ledger() is None

    def test_scopes_are_isolated(self) -> None:
        with use_usage_ledger() as outer:
            record_usage(Usage(prompt_tokens=10))
            with use_usage_ledger() as inner:
                record_usage(Usage(prompt_tokens=99))
                assert inner.prompt_tokens == 99
            # 内层结束后外层不受影响
            assert outer.prompt_tokens == 10
