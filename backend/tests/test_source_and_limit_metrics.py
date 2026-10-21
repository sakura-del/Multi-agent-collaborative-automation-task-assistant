"""检索源失败 + 限流/配额拒绝指标测试（Phase 2 · 可观测性 Task 4）。

## 为什么这三个指标要分开

运营排障时它们指向完全不同的动作：

| 指标 | 含义 | 该做什么 |
| --- | --- | --- |
| `maa_literature_source_errors` | 上游检索源故障 | 换源 / 降级 / 联系源方 |
| `maa_rate_limit_rejected` | 客户端在刷 | 查是谁、要不要提额 |
| `maa_quota_rejected` | 用户额度用尽 | 转化付费 / 调整配额策略 |

混在一起就分不清是"我们的依赖坏了"还是"用户在刷"还是"用户没钱了"。
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.core import metrics


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    metrics.reset_for_tests()


def _value(name: str, **labels: str) -> float:
    labels_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    target = f"{name}_total{{{labels_str}}}" if labels_str else f"{name}_total"
    for line in metrics.render().splitlines():
        if line.startswith("#"):
            continue
        if line.startswith(target + " "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


class _FailingSource:
    """总是失败的检索源（模拟 arXiv 5xx / OpenAlex 限流）。"""

    async def search(self, search: Any) -> list[Any]:
        raise RuntimeError("上游 503")


class _OkSource:
    def __init__(self, records: list[Any]) -> None:
        self._records = records

    async def search(self, search: Any) -> list[Any]:
        return list(self._records)


class TestSourceErrorMetrics:
    @pytest.mark.asyncio
    async def test_single_source_failure_is_recorded(self) -> None:
        """单源（也是唯一源）失败：**先打点再上抛**。

        `MultiSourceLiteratureSource.search` 在「全部源都失败」时会向上抛
        （正确设计：不该把"全部挂了"静默成"无命中"）。测试要接住异常，
        然后断言指标已记录 —— 打点必须在抛之前完成。
        """
        from app.tools.literature.models import LiteratureSearch
        from app.tools.literature.source_registry import MultiSourceLiteratureSource

        registry = MultiSourceLiteratureSource(sources=[_FailingSource()])
        with pytest.raises(RuntimeError):
            await registry.search(LiteratureSearch(query="rag", max_results=5))

        assert _value("maa_literature_source_errors", source="_FailingSource") == 1.0

    @pytest.mark.asyncio
    async def test_partial_failure_records_only_failed_source(self) -> None:
        """单源失败不影响其他源（既有隔离语义），指标也应只记失败的那个。"""
        from app.tools.literature.models import LiteratureSearch
        from app.tools.literature.source_registry import MultiSourceLiteratureSource

        class _Record:
            source_id = "a1"
            title = "T"
            authors: ClassVar[list[str]] = []
            abstract = ""
            published_at = None
            source_url = "https://example.com/a"
            pdf_url = "https://example.com/a.pdf"
            categories: ClassVar[list[str]] = []
            doi = None

        registry = MultiSourceLiteratureSource(sources=[_FailingSource(), _OkSource([_Record()])])
        await registry.search(LiteratureSearch(query="rag", max_results=5))

        assert _value("maa_literature_source_errors", source="_FailingSource") == 1.0
        assert _value("maa_literature_source_errors", source="_OkSource") == 0.0

    @pytest.mark.asyncio
    async def test_metrics_failure_does_not_break_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """指标故障不能让检索崩（fail-open）。

        场景：源全部失败 + 打点也炸 → 仍只抛原本的检索异常，
        不会变成"指标异常把业务异常盖掉"。
        """
        from app.tools.literature.models import LiteratureSearch
        from app.tools.literature.source_registry import MultiSourceLiteratureSource

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("指标炸了")

        monkeypatch.setattr(metrics, "_inc_raw", boom)

        registry = MultiSourceLiteratureSource(sources=[_FailingSource()])
        with pytest.raises(RuntimeError, match="503"):
            await registry.search(LiteratureSearch(query="rag", max_results=5))


class TestRateLimitMetrics:
    def test_rejected_request_increments_counter(self) -> None:
        """限流 429 必须打点（否则"谁在刷"完全看不见）。"""
        from app.core import metrics as m

        # 直接验证打点契约（中间件集成断言在 test_rate_limit_middleware 里更重，
        # 这里保证 counter 名字与标签组合可用）
        m.inc_counter("maa_rate_limit_rejected", route="task_create")

        assert _value("maa_rate_limit_rejected", route="task_create") == 1.0

    def test_route_label_is_low_cardinality(self) -> None:
        """route 标签是**限流规则名**（task_create 等），不是真实路径。"""
        metrics.inc_counter("maa_rate_limit_rejected", route="task_create")
        text = metrics.render()

        assert 'route="task_create"' in text
        assert "/api/v1/tasks/" not in text


class TestQuotaMetrics:
    def test_quota_rejected_increments_counter(self) -> None:
        from app.core.quota import QuotaExceededError

        # 模拟异常处理层的打点契约
        metrics.inc_counter("maa_quota_rejected")

        assert _value("maa_quota_rejected") == 1.0
        assert QuotaExceededError.status_code == 429

    def test_quota_and_rate_limit_are_distinct(self) -> None:
        """两个 429 必须分开计数（用户没钱 vs 用户在刷，处理动作完全不同）。"""
        metrics.inc_counter("maa_quota_rejected")
        metrics.inc_counter("maa_rate_limit_rejected", route="task_create")

        assert _value("maa_quota_rejected") == 1.0
        assert _value("maa_rate_limit_rejected", route="task_create") == 1.0
