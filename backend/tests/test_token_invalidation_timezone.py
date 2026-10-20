"""强制下线时区缺陷回归（Phase 1 P1-C 暴露的真实缺陷）。

## 缺陷

`user.token_invalid_before` 是 `DateTime(timezone=True)`，但 **SQLite 不保留 tzinfo**：
读回来是 naive datetime。`deps.py` 直接调用 `naive.timestamp()`——Python 把 naive 按
**本地时区**解释。本机 UTC+8 下，写进去的 `now`（UTC）读回后 `.timestamp()`
比真实 epoch **少 28800 秒**，于是「重置密码后旧 token 应立即失效」被绕过
（`iat` 远大于这个偏小的时间戳）。

这是生产也存在的隐性安全缺陷：行为随部署时区变化（UTC 容器恰好正确，UTC+8 失效）。

## 正确语义

数据库读回的 naive datetime **一律按 UTC 解释**（因为写入端用的是 UTC），
与运行环境时区无关。本文件锁定该语义，并覆盖两个消费点（access / refresh）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import utc_timestamp


class TestUtcTimestamp:
    def test_naive_is_interpreted_as_utc(self) -> None:
        """naive datetime 必须按 UTC 解释，而不是本地时区。"""
        naive = datetime(2026, 10, 20, 12, 0, 0)
        aware = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)

        assert utc_timestamp(naive) == aware.timestamp()

    def test_aware_is_converted_normally(self) -> None:
        aware = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
        assert utc_timestamp(aware) == aware.timestamp()

    def test_result_is_independent_of_local_timezone(self) -> None:
        """同一 naive 值在任何时区下都必须得到同一 epoch（防回归到本地时区解释）。"""
        naive = datetime(2026, 10, 20, 0, 0, 0)
        value = utc_timestamp(naive)

        assert value == datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC).timestamp()
        # 关键：不能等于按本地时区解释的结果（除非恰好在 UTC 时区）
        offset = datetime.now().astimezone().utcoffset()
        if offset is not None and offset != timedelta(0):
            assert value != naive.timestamp(), "仍按本地时区解释（缺陷未修复）"

    def test_offset_aware_is_normalized(self) -> None:
        """带非 UTC 偏移的 aware 值按其偏移换算（语义不变）。"""
        beijing = datetime(2026, 10, 20, 20, 0, 0, tzinfo=UTC).astimezone(
            __import__("datetime").timezone(timedelta(hours=8))
        )
        assert utc_timestamp(beijing) == datetime(2026, 10, 20, 20, 0, 0, tzinfo=UTC).timestamp()


@pytest.mark.parametrize("naive", [True, False])
def test_invalidation_comparison_is_timezone_independent(naive: bool) -> None:
    """模拟 deps 的比较：签发于 invalid_before 之前 → 必须判定失效。"""
    now = datetime.now(UTC)
    issued_at = now - timedelta(minutes=5)
    invalid_before = now
    stored = invalid_before.replace(tzinfo=None) if naive else invalid_before

    # 旧 token 的 iat 早于失效时刻 → 应判失效
    assert issued_at.timestamp() < utc_timestamp(stored)
