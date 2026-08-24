"""健康检查端点：存活探针 + 真实就绪探测（DB / Redis / Chroma）。

用途：
- /health        存活探针（liveness）：应用进程存活即 200
- /health/ready  就绪探针（readiness）：真实探测三项依赖
  - database：执行 SELECT 1
  - redis：RedisManager.ping()
  - chroma：Chroma 服务 heartbeat 端点
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
    """Chroma 探测：调用 Chroma 服务 heartbeat 端点。"""
    url = f"http://{settings.rag.chroma_host}:{settings.rag.chroma_port}/api/v1/heartbeat"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("readiness_chroma_failed", url=url, error=str(exc))
        return False


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
    """就绪检查：并行探测 database / redis / chroma。

    全部通过返回 200 与 status=ready；任一失败返回 503 与 status=not_ready，
    失败的依赖在 checks 中标记为 failed。
    """
    db_ok, redis_ok, chroma_ok = await asyncio.gather(
        probe_database(), probe_redis(), probe_chroma()
    )
    checks: dict[str, str] = {
        "database": "ok" if db_ok else "failed",
        "redis": "ok" if redis_ok else "failed",
        "chroma": "ok" if chroma_ok else "failed",
    }
    ready = db_ok and redis_ok and chroma_ok
    if not ready:
        logger.info("readiness_not_ready", **checks)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks},
        )
    return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})
