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

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

    yield

    logger.info("应用关闭，释放资源")
    await container.close()
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
        description="基于多智能体协作的自动化任务执行系统",
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

    # 路由挂载
    app.include_router(api_router)

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
