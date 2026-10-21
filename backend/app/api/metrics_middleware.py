"""HTTP 指标中间件（Phase 2 · 可观测性 Task 2）。

## 只有一件事最难：基数控制

Prometheus 的标签每种组合都是一条独立时间序列。用 `request.url.path` 作标签的
后果是灾难性的：

- `/api/v1/tasks/<uuid1>`、`<uuid2>` … 每个任务一条序列 → 几百任务就几千条，
  抓取变慢、内存涨、图全是长尾；
- 更糟的是**外部扫描**：随便扫 `/foo1`、`/foo2` … 就能无限注入序列，
  这是 Prometheus 侧的真实攻击面。

所以标签**必须**用路由模板（`request.scope["route"].path`）；
没匹配到路由时用 `__unmatched__` 常量（不能回退到原始路径）。

## 其他两个必要决定

- **探针/抓取端点不记录**：`/metrics`、`/api/v1/health*`、`/api/v1/ws` 是高频调用，
  记录它们会把真实业务数据淹没（Prometheus 自己是按固定间隔抓的）。
- **fail-open**：中间件内部任何异常都不能影响响应（与限流/配额同一原则）。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core import metrics
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 未匹配到路由时的标签值（**不要**回退到真实路径，见模块 docstring）
UNMATCHED_ROUTE = "__unmatched__"

#: 不记录指标的路径前缀（探针 / 抓取 / 长连接）。
#: 注意这里用前缀匹配：`/api/v1/health` 覆盖 `/health` 与 `/health/ready`。
_SKIP_PREFIXES: tuple[str, ...] = (
    "/metrics",
    "/api/v1/health",
    "/api/v1/ws",
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录 HTTP 请求数与耗时分布（路由模板作为标签）。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path.startswith(_SKIP_PREFIXES):
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500  # 未走到响应就抛异常时，按 500 记（不能丢这条）
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            # 用 finally：异常路径（业务 500）也必须留下记录，
            # 否则「错误率」这个指标永远为 0 —— 那比没有指标更危险。
            elapsed = time.perf_counter() - started
            self._record(request, status_code=status_code, elapsed=elapsed)

    @staticmethod
    def _record(request: Request, *, status_code: int, elapsed: float) -> None:
        """打点（内部已 fail-open，这里再包一层防御：中间件绝不能抛）。"""
        try:
            route = request.scope.get("route")
            template = getattr(route, "path", None) or UNMATCHED_ROUTE
            metrics.inc_counter(
                "maa_http_requests",
                method=request.method,
                route=template,
                status=str(status_code),
            )
            metrics.observe_histogram(
                "maa_http_request_duration_seconds",
                elapsed,
                method=request.method,
                route=template,
            )
        except Exception as exc:  # pragma: no cover - 双保险
            logger.debug("http_metrics_record_failed", error=str(exc))
