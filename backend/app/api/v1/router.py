"""API v1 路由聚合。

所有 v1 版本端点在此挂载到统一前缀 `/api/v1`。
新增业务模块时，按 `include_router` 方式扩展。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    # 别名导入：`annotations` 与 `from __future__ import annotations` 的
    # `__future__._Feature` 同名，直接 import 会让 mypy 把该名字推断成
    # `_Feature`（`"_Feature" has no attribute "router"`）。
    annotations as annotation_endpoints,
)
from app.api.v1.endpoints import (
    auth,
    auth_lifecycle,
    citation_styles,
    health,
    observability,
    references,
    screening,
    task_ws,
    tasks,
    templates,
    workspaces,
    ws,
)
from app.knowledge.api.endpoints import router as knowledge_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(auth_lifecycle.router)
api_router.include_router(ws.router)
api_router.include_router(knowledge_router)
api_router.include_router(citation_styles.router)
api_router.include_router(templates.router)
api_router.include_router(references.router)
api_router.include_router(screening.router)
api_router.include_router(tasks.router)
api_router.include_router(task_ws.router)
# 可观测性看板（Phase 2）：owner-scoped 的任务健康与成本概览
api_router.include_router(observability.router)
# 团队工作区 + 席位（Phase 2）
api_router.include_router(workspaces.router)
# 导师批注（Phase 2）：挂在 /tasks/{task_id}/annotations 下
api_router.include_router(annotation_endpoints.router)
