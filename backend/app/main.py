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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app import __version__
from app.api.middleware import AuthContextMiddleware
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import bind_request_context, configure_logging, get_logger
from app.core.redis import RedisManager
from app.db.session import engine

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

    # ===== 任务运行时（Plan 2 Task 4）=====
    from app.knowledge.services.search_provider import KnowledgeSearchProvider
    from app.orchestration.runtime import RuntimeContainer
    from app.orchestration.services.task_execution import TaskExecution

    # 文献任务需要 owner-scoped 证据检索：按调用开 session 的提供者（Plan 4 Task 8 实跑发现此前未接入）
    container = RuntimeContainer(knowledge_query=KnowledgeSearchProvider())
    await container.start()
    task_execution = TaskExecution(container)
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

    # CORS：阶段0允许前端开发服务器访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allow_origins,
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=settings.cors.allow_methods,
        allow_headers=settings.cors.allow_headers,
    )
    app.add_middleware(RequestIDMiddleware)
    # 鉴权上下文中间件：预解析 token 注入 request.state，不强制拦截
    app.add_middleware(AuthContextMiddleware)

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
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    # 路由挂载
    app.include_router(api_router)

    return app


app = create_app()
