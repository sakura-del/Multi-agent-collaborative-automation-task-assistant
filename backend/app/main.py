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
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import bind_request_context, configure_logging, get_logger
from app.db.session import engine


# ===== 应用生命周期 =====


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动与关闭钩子。

    启动：初始化日志、预热连接池（engine 懒加载，此处仅记录日志）。
    关闭：释放数据库 engine 等资源。
    """
    configure_logging()
    logger = get_logger(__name__)
    logger.info("应用启动", version=__version__, env=settings.app.env)

    yield

    logger.info("应用关闭，释放资源")
    await engine.dispose()


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
