"""API v1 路由聚合。

所有 v1 版本端点在此挂载到统一前缀 `/api/v1`。
新增业务模块时，按 `include_router` 方式扩展。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, ws
from app.knowledge.api.endpoints import router as knowledge_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(ws.router)
api_router.include_router(knowledge_router)
