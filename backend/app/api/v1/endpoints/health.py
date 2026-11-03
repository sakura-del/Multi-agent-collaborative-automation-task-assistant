"""健康检查端点：存活探针 + 真实就绪探测（DB / Redis / 向量库）。

用途：
- /health        存活探针（liveness）：应用进程存活即 200
- /health/ready  就绪探针（readiness）：真实探测三项依赖
  - database：执行 SELECT 1
  - redis：RedisManager.ping()
  - vector：按**当前后端**探测（chroma → heartbeat；pgvector → 池上 SELECT 1）
  任一必需依赖失败 → 503 且对应依赖标记 failed；全部通过 → 200 ready。
  LLM 可用性不纳入容器就绪（配额/网络抖动不应让容器被判未就绪）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisManager
from app.db.session import engine

router = APIRouter(tags=["health"])

logger = get_logger(__name__)

# 各依赖探测的超时（秒）：避免单点故障拖慢就绪检查
_PROBE_TIMEOUT = 3.0


# ===== 依赖探测函数（可被测试 monkeypatch 替换）=====


async def probe_database() -> bool:
    """数据库探测：执行 SELECT 1，失败返回 False。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("readiness_db_failed", error=str(exc))
        return False


async def probe_redis() -> bool:
    """Redis 探测：PING，失败返回 False。"""
    try:
        return bool(await asyncio.wait_for(RedisManager.ping(), timeout=_PROBE_TIMEOUT))
    except Exception as exc:
        logger.warning("readiness_redis_failed", error=str(exc))
        return False


async def probe_chroma() -> bool:
    """Chroma 探测：调用 Chroma 服务 heartbeat 端点。

    路径随服务端版本变化：0.4.x 为 `/api/v1/heartbeat`，1.x 起为 `/api/v2/heartbeat`。
    本仓库两端已统一到 1.5.9（见 docker-compose.yml 与 pyproject.toml），故用 v2；
    同时保留 v1 回退，避免滚动升级期间误报不健康。
    """
    base = f"http://{settings.rag.chroma_host}:{settings.rag.chroma_port}"
    paths = ("/api/v2/heartbeat", "/api/v1/heartbeat")
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
        for path in paths:
            try:
                resp = await client.get(f"{base}{path}")
                if resp.status_code == 200:
                    return True
            except Exception as exc:
                logger.warning("readiness_chroma_failed", url=f"{base}{path}", error=str(exc))
    return False


async def probe_pgvector() -> bool:
    """pgvector 探测：复用已建连接池执行 `SELECT 1`。

    刻意不查向量表：就绪探针回答的是「依赖通不通」，
    表没建/维度不对属于配置错误，会在首次检索时给出可操作的异常，
    不该把「表还没迁移」误报成「数据库不可达」。
    """
    try:
        from app.knowledge.services.factory import get_vector_store

        store = get_vector_store()
        ping = getattr(store._backend, "ping", None)
        if not callable(ping):
            logger.warning("readiness_pgvector_no_ping", backend=store.backend_name)
            return False
        # 同步驱动：放到线程里跑，避免阻塞事件循环（探针与真实请求共用同一约束）
        return bool(await asyncio.wait_for(asyncio.to_thread(ping), timeout=_PROBE_TIMEOUT))
    except Exception as exc:
        logger.warning("readiness_pgvector_failed", error=str(exc))
        return False


async def probe_vector() -> bool:
    """向量库探测：按当前后端分派（未知后端按 chroma 处理，保持向后兼容）。"""
    backend = (getattr(settings.rag, "vector_backend", "") or "chroma").strip().lower()
    if backend == "pgvector":
        return await probe_pgvector()
    return await probe_chroma()


# ===== 端点 =====


@router.get("/health", summary="健康检查（存活探针）")
async def health_check() -> dict[str, Any]:
    """返回应用基础信息，供探针与监控使用。"""
    return {
        "status": "ok",
        "app": settings.app.name,
        "version": __version__,
        "env": settings.app.env,
    }


@router.get("/health/ready", summary="就绪检查（真实依赖探测）")
async def readiness_check() -> JSONResponse:
    """就绪检查：并行探测 database / redis / 向量库。

    全部通过返回 200 与 status=ready；任一失败返回 503 与 status=not_ready，
    失败的依赖在 checks 中标记为 failed。

    `checks.vector` 是稳定键（与后端无关）；chroma 模式下额外保留 `checks.chroma`
    别名，避免既有部署脚本/看板在切换期立刻失效。
    """
    db_ok, redis_ok, vector_ok = await asyncio.gather(
        probe_database(), probe_redis(), probe_vector()
    )
    checks: dict[str, str] = {
        "database": "ok" if db_ok else "failed",
        "redis": "ok" if redis_ok else "failed",
        "vector": "ok" if vector_ok else "failed",
    }
    backend = (getattr(settings.rag, "vector_backend", "") or "chroma").strip().lower()
    if backend != "pgvector":
        checks["chroma"] = checks["vector"]

    ready = db_ok and redis_ok and vector_ok
    if not ready:
        logger.info("readiness_not_ready", **checks)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks},
        )
    return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})
