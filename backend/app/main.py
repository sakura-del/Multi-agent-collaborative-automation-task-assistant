"""FastAPI 应用入口。

阶段0完成的最小可运行骨架：
- 配置日志系统
- 挂载 CORS 中间件
- 请求 ID 注入中间件（贯穿调用链追踪）
- 全局异常处理中间件
- 注册 /api/v1 路由
- 预留 WebSocket 端点（阶段7填充）
- 应用生命周期钩子（启动/关闭时管理 engine 等资源）
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app import __version__
from app.api.middleware import AuthContextMiddleware
from app.api.v1.router import api_router
from app.core.config import get_settings, settings
from app.core.exceptions import AppException
from app.core.logging import bind_request_context, configure_logging, get_logger
from app.core.redis import RedisManager
from app.db.session import engine

logger = get_logger(__name__)

# ===== 统一错误响应映射（M0-2）=====

_HTTP_STATUS_ERROR_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}

_HTTP_STATUS_DEFAULT_MESSAGES: dict[int, str] = {
    400: "请求参数错误",
    401: "未认证或登录已失效",
    403: "没有权限执行该操作",
    404: "请求的资源不存在",
    405: "请求方法不被允许",
    409: "资源状态冲突",
    413: "请求内容过大",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后重试",
    500: "服务内部错误",
    502: "上游服务响应异常",
    503: "服务暂不可用，请稍后重试",
    504: "上游服务响应超时",
}

_STARLETTE_DEFAULT_PHRASES = frozenset({"not found", "method not allowed"})


def _extract_http_error_message(status_code: int, detail: Any) -> tuple[str, dict[str, Any]]:
    """从原生 HTTPException.detail 提取中文友好 message 与结构化 details。"""
    default_msg = _HTTP_STATUS_DEFAULT_MESSAGES.get(status_code, f"请求失败（HTTP {status_code}）")
    if isinstance(detail, str):
        text = detail.strip()
        if not text or text.lower() in _STARLETTE_DEFAULT_PHRASES:
            return default_msg, {}
        return text, {}
    if isinstance(detail, dict):
        encoded = jsonable_encoder(detail)
        for key in ("message", "msg", "detail", "error"):
            candidate = encoded.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip(), encoded
        return default_msg, encoded
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            for key in ("message", "msg", "detail"):
                candidate = first.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip(), {}
        elif isinstance(first, str) and first.strip():
            return first.strip(), {}
    return default_msg, {}


def _format_validation_error_message(errors: list[dict[str, Any]]) -> str:
    """将 FastAPI/Pydantic v2 首条校验错误格式化为中文友好提示。"""
    if not errors:
        return "请求参数校验失败"
    err = errors[0]
    raw_loc = err.get("loc") or ()
    parts = [
        str(part) for part in raw_loc if part not in ("body", "query", "path", "header", "cookie")
    ]
    field = ".".join(parts) if parts else ("请求体" if "body" in raw_loc else "")

    raw_msg = str(err.get("msg") or "").strip()
    if raw_msg.startswith("Value error, "):
        raw_msg = raw_msg[len("Value error, ") :].strip()
    if raw_msg and re.search(r"[\u4e00-\u9fff]", raw_msg):
        return raw_msg

    err_type = str(err.get("type") or "")
    raw_ctx = err.get("ctx")
    ctx: dict[str, Any] = raw_ctx if isinstance(raw_ctx, dict) else {}
    target = field or "请求参数"
    if err_type == "missing":
        return f"缺少必填参数 {target}"
    if err_type == "extra_forbidden":
        return f"包含不支持的多余字段：{target}"
    if err_type == "string_too_short":
        return f"参数 {target} 长度不能少于 {ctx.get('min_length', 1)} 个字符"
    if err_type == "string_too_long":
        return f"参数 {target} 长度不能超过 {ctx.get('max_length', '')} 个字符"
    if err_type == "too_short":
        return f"参数 {target} 至少需要 {ctx.get('min_length', 1)} 项"
    if err_type == "too_long":
        return f"参数 {target} 最多允许 {ctx.get('max_length', '')} 项"
    if err_type in ("greater_than_equal", "greater_than"):
        return f"参数 {target} 数值过小"
    if err_type in ("less_than_equal", "less_than"):
        return f"参数 {target} 数值过大"
    if err_type in ("literal_error", "enum"):
        return f"参数 {target} 取值不在允许范围内"
    if err_type == "uuid_parsing":
        return f"参数 {target} 的 UUID 格式不正确"
    if err_type in ("int_parsing", "float_parsing", "bool_parsing"):
        return f"参数 {target} 类型格式不正确"
    if err_type == "string_pattern_mismatch":
        return f"参数 {target} 格式不符合要求"
    if err_type == "json_invalid":
        return "请求体 JSON 格式不合法"
    if field and raw_msg:
        return f"参数 {field} 校验失败：{raw_msg}"
    return "请求参数校验失败"


# ===== 应用生命周期 =====


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动与关闭钩子。

    启动：初始化日志、显式初始化 RAG 组件（生产访问路径不允许隐式 Mock 回退），
    并从持久化的 ready 分块重建 BM25 内存索引（断点恢复）。
    关闭：释放数据库 engine 与 Redis 资源。
    """
    configure_logging()
    logger = get_logger(__name__)
    logger.info("应用启动", version=__version__, env=settings.app.env)

    # 平台模型凭据自检（Issue #19/#25）：忘配 key 时功能整体不可用，
    # 必须在启动日志里显式喊一声，而不是等第一个用户任务 401。
    from app.llm.health import platform_llm_status

    llm_status = platform_llm_status()
    if llm_status != "ok":
        logger.warning(
            "平台模型凭据未配置，平台模型功能不可用（BYOK 用户不受影响）",
            status=llm_status,
        )

    # 显式初始化知识库：嵌入/重排按配置选择真实或 Mock（环境变量 RAG_EMBEDDING_MOCK 控制）
    from app.db.session import AsyncSessionLocal
    from app.knowledge.services.document_service import KnowledgeDocumentService
    from app.knowledge.services.factory import get_index_manager, get_kb, get_storage, init_kb

    init_kb()
    async with AsyncSessionLocal() as session:
        service = KnowledgeDocumentService(
            session,
            storage=get_storage(),
            index_manager=get_index_manager(),
            kb=get_kb(),
        )
        try:
            await service.rebuild_bm25()
        except Exception as exc:  # 首次启动表未建或库为空时不阻塞启动
            logger.warning("bm25_startup_rebuild_skipped", error=str(exc))

    # ===== 引用样式注册表（Issue #4 M2）=====
    # 上传样式要能被**渲染期**解析，故渲染器与上传入口共用同一实例。
    # 启动时把库中已上传的样式重新登记，重启后自定义样式依然可用。
    from app.tools.csl.styles import StyleRegistry

    style_registry = StyleRegistry()
    app.state.style_registry = style_registry
    try:
        await _restore_custom_styles(style_registry)
    except Exception as exc:  # 测试库未建表 / 迁移未跑时不阻塞启动
        logger.warning("custom_style_restore_skipped", error=str(exc))

    # ===== 任务运行时（Plan 2 Task 4）=====
    from app.knowledge.services.search_provider import KnowledgeSearchProvider
    from app.orchestration.runtime import RuntimeContainer
    from app.orchestration.services.task_execution import TaskExecution

    # 文献任务需要 owner-scoped 证据检索：按调用开 session 的提供者（Plan 4 Task 8 实跑发现此前未接入）
    container = RuntimeContainer(
        knowledge_query=KnowledgeSearchProvider(), style_registry=style_registry
    )
    await container.start()
    # 配额服务（P1-D）：提交任务时预扣、完成时按实际用量退还差额。
    # 在 lifespan 里构造一次并复用（避免每个请求都建 Redis 连接）。
    from app.api.v1.endpoints.tasks import build_quota_service

    task_execution = TaskExecution(container, quota_service=build_quota_service())
    app.state.container = container
    app.state.runtime = task_execution
    try:
        await task_execution.recover()
    except Exception as exc:  # 测试库未建表时不阻塞启动
        logger.warning("task_runtime_recover_skipped", error=str(exc))

    # ===== 定时重跑（Issue #27）=====
    # 单 worker 部署，进程内一个 tick 循环足够；关闭开关时不启动（排期数据保留）。
    routine_scheduler = None
    if settings.routine.enabled:
        from app.db.session import AsyncSessionLocal
        from app.orchestration.services.routine_scheduler import RoutineScheduler

        routine_scheduler = RoutineScheduler(
            session_factory=AsyncSessionLocal,
            starter=task_execution.start_scheduled_refresh,
            config=settings.routine,
        )
        routine_scheduler.start()
    app.state.routine_scheduler = routine_scheduler

    yield

    logger.info("应用关闭，释放资源")
    if routine_scheduler is not None:
        await routine_scheduler.stop()
    await container.close()

    # 关闭兜底：容器只跟踪自己 `track()` 过的协程；工作流/通信层仍可能留下
    # 未被跟踪的子任务。`asyncio.run` 在 `_cancel_all_tasks` 上会**无限等待**
    # 这些任务（CI 实测卡 30 分钟、无栈），所以这里显式取消并给 5 秒上限。
    # 日志里带上任务名，下次再挂能直接定位是谁。
    #
    # ⚠️ 必须排除框架自身任务：`TestClient`/anyio 的 portal 用 `run_portal` 与
    # `_call_func` 两个任务驱动 lifespan，把它们一起取消会让 teardown 直接抛
    # `CancelledError`（本地实测 15 条用例连环 ERROR）。
    _INFRA_PREFIXES = ("anyio.", "starlette.", "uvicorn.", "asyncio.")
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and not task.get_name().startswith(_INFRA_PREFIXES)
    ]
    if pending:
        logger.warning(
            "lifespan_cancelling_pending_tasks",
            count=len(pending),
            names=[task.get_name() for task in pending],
        )
        for task in pending:
            task.cancel()
        await asyncio.wait(pending, timeout=5)

    await engine.dispose()
    await RedisManager.close()


async def _restore_custom_styles(registry: Any) -> None:
    """把库中已上传的自定义样式重新登记到共享注册表（Issue #4 M2）。

    上传样式落盘在 `artifact_root.parent/csl_styles/{user_id}/`，DB 行记录元数据。
    进程重启后必须重新登记，否则任务渲染会报「未知引用样式」。
    单个样式登记失败（文件被删/损坏）只跳过该条，不影响启动。
    """
    from sqlalchemy import select

    from app.db.models.citation_style import CitationStyle
    from app.db.session import AsyncSessionLocal

    storage_root = (Path(get_settings().app.artifact_root).parent / "csl_styles").resolve()
    restored = 0
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(CitationStyle))).scalars().all()
        for row in rows:
            if row.user_id is None:
                continue  # 预置样式由静态表提供，不落盘
            path = (storage_root / str(row.user_id) / row.filename).resolve()
            if not path.is_file():
                logger.warning("custom_style_file_missing", style_id=row.style_id, path=str(path))
                continue
            try:
                registry.register_custom(row.style_id, path)
                restored += 1
            except Exception as exc:
                logger.warning("custom_style_restore_failed", style_id=row.style_id, error=str(exc))
    if restored:
        logger.info("custom_styles_restored", count=restored)


# ===== 中间件 =====


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 trace_id，并写入响应头。

    所有日志可通过 trace_id 聚合一次完整的调用链，
    为多 Agent 系统的问题定位提供基础（阶段1任务7）。
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # 优先复用上游传入的 X-Request-ID，否则生成
        trace_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        bind_request_context(trace_id=trace_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        return response


# ===== 应用工厂 =====


def create_app() -> FastAPI:
    """构造 FastAPI 应用实例。"""
    app = FastAPI(
        title=settings.app.name,
        version=__version__,
        description="VeriPaper API",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS：阶段0允许前端开发服务器访问；Phase 3 起支持 Cloudflare Pages（正则放行预览域名）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allow_origins,
        # 空字符串会让 Starlette 编译出「匹配一切」的正则，必须转成 None
        allow_origin_regex=settings.cors.allow_origin_regex or None,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=settings.cors.allow_methods,
        allow_headers=settings.cors.allow_headers,
        # 跨域下载要读 Content-Disposition 取文件名
        expose_headers=settings.cors.expose_headers,
        # 缓存 OPTIONS 预检结果 24 小时，减少高延迟链路下的重复预检往返
        max_age=86400,
    )
    # 限流中间件（Phase 1 P1-D）。
    # ⚠️ Starlette 的 add_middleware 是**后注册者先执行**（洋葱模型外层先跑），
    # 限流需要 request.state.user_id（由 AuthContextMiddleware 注入），
    # 因此必须在 AuthContext **之前**注册，才能在链路上排在它之后执行。
    # 内部 fail-open：组件故障绝不拦业务请求。
    from app.api.rate_limit_middleware import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestIDMiddleware)
    # 鉴权上下文中间件：预解析 token 注入 request.state，不强制拦截
    app.add_middleware(AuthContextMiddleware)
    # HTTP 指标（Phase 2 可观测性）：最后注册 → 洋葱模型最外层 → 最先执行、最后记录，
    # 因此能覆盖整条链路耗时（含鉴权/限流），也不会被内层异常跳过。
    # 内部 fail-open：指标故障绝不影响响应。
    from app.api.metrics_middleware import MetricsMiddleware

    app.add_middleware(MetricsMiddleware)

    # 全局异常处理：把 AppException 转成统一 JSON 响应结构
    @app.exception_handler(AppException)
    async def handle_app_exception(_: Request, exc: AppException) -> JSONResponse:
        logger = get_logger(__name__)
        logger.warning(
            "业务异常",
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
        )
        # 可观测性：配额拒绝单独计数（与限流 429 区分——「用户额度用尽」和
        # 「客户端在刷」在运营上要采取完全不同的动作）
        if exc.error_code == "QUOTA_EXCEEDED":
            from app.core import metrics as metrics_module

            metrics_module.inc_counter("maa_quota_rejected")
        # 429（配额/限流）必须带 Retry-After，客户端才知道何时可重试
        headers: dict[str, str] = {}
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            headers["Retry-After"] = str(max(1, int(retry_after)))
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            },
            headers=headers or None,
        )

    # 兼容统一原生 HTTPException 与 Starlette 路由 404/405：保留原 detail 与 headers，
    # 同时补充 error_code / 中文 message / details，供新旧调用方一致消费。
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        encoded_detail = jsonable_encoder(exc.detail)
        message, details = _extract_http_error_message(exc.status_code, exc.detail)
        error_code = _HTTP_STATUS_ERROR_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
        raw_headers = getattr(exc, "headers", None)
        headers = dict(raw_headers) if raw_headers else None
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": error_code,
                "message": message,
                "details": details,
                "detail": encoded_detail,
            },
            headers=headers,
        )

    # 兼容统一 422 请求校验错误：先用 jsonable_encoder 处理（避免 Pydantic v2 ctx['error']
    # 中的 ValueError 实例不可 JSON 序列化导致 500），再提取首条中文 message。
    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(_: Request, exc: RequestValidationError) -> JSONResponse:
        encoded_errors = jsonable_encoder(exc.errors())
        error_list = encoded_errors if isinstance(encoded_errors, list) else []
        message = _format_validation_error_message(error_list)
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "VALIDATION_ERROR",
                "message": message,
                "details": {"errors": encoded_errors},
                "detail": encoded_errors,
            },
        )

    # 路由挂载
    app.include_router(api_router)

    # ===== 根路径存活探针别名（Issue #29）=====
    # `veripaper.me/api/health` 曾经被 Pages 的 SPA 兜底（`/* /index.html 200`）
    # 吞成 200 HTML：后端挂了外部探针照样绿。前端 `_redirects` 已把 `/api/*`
    # 反代到后端，这里再给一个**不带版本前缀**的稳定存活地址，
    # 让 `curl https://veripaper.me/api/health` 直接拿到 JSON。
    #
    # 与 `/api/v1/health` 的差别：只验进程存活，不查 DB/Redis/向量库；
    # 需要依赖级判定请用 `/api/v1/health/ready`（watchdog 用的就是它）。
    from app.api.v1.endpoints.health import health_check as _liveness_check

    app.add_api_route(
        "/api/health",
        _liveness_check,
        methods=["GET"],
        include_in_schema=False,
        tags=["health"],
    )

    # ===== 可观测性：Prometheus 抓取端点（Phase 2）=====
    # 为什么在根路径而不在 /api/v1：Prometheus 生态的默认约定就是 `/metrics`，
    # 换路径要在抓取配置里额外指定，没有必要。
    #
    # ⚠️ **刻意无鉴权**（Prometheus 抓取器不带业务 token），因此
    # `app/core/metrics.py` 的指标标签里绝不允许出现 user_id / 任务标题 / 真实路径
    # （只允许状态、节点类型、源名、路由模板这类低基数枚举）。
    # 生产部署应只在内网暴露本端点 —— 见 docs/runbooks/observability.md。
    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        from app.core import metrics as metrics_module

        return Response(
            content=metrics_module.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


app = create_app()
