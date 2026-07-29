"""健康检查端点。

用途：
- Docker Compose / k8s 健康探针
- 阶段1验收标准：`/health` 返回 200
- 阶段0仅返回应用存活状态；后续阶段可扩展 DB / Redis / LLM 等依赖检查
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app import __version__
from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
async def health_check() -> Dict[str, Any]:
    """返回应用基础信息，供探针与监控使用。"""
    return {
        "status": "ok",
        "app": settings.app.name,
        "version": __version__,
        "env": settings.app.env,
    }


@router.get("/health/ready", summary="就绪检查")
async def readiness_check() -> Dict[str, Any]:
    """就绪检查：阶段0仅返回静态就绪状态。

    后续阶段会补充：
    - 数据库连接 ping
    - Redis 连接 ping
    - Chroma 服务可达性
    - LLM 配额余量
    """
    return {
        "status": "ready",
        "checks": {
            # 阶段0：占位，阶段1开始接入实际依赖
            "database": "skipped",
            "redis": "skipped",
            "chroma": "skipped",
        },
    }
