"""API Key 管理端点（Phase 2 · 开放 API Task 4）。

## 三个端点，全部**强制 JWT only**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/api-keys` | 生成（明文只返回一次） |
| `GET` | `/api/v1/api-keys` | 列表（只返回前缀） |
| `DELETE` | `/api/v1/api-keys/{id}` | 吊销（软删除） |

**为什么拒绝 Key 鉴权**：一个 Key 能生成新 Key 等于权限提升 ——
拿到只读 Key 的人可以自建一个全权 Key，scope 形同虚设。
所以这里用 `get_current_active_user`（只认 Bearer），不用双通道依赖。

## 为什么明文只在 201 响应里

与密码同一原则：数据库只有哈希。用户关掉弹窗后只能重建 ——
这是**正确的取舍**（Key 泄漏的代价远高于重建一次的不便）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.models.api_key import ApiKey
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.security.api_key_service import ApiKeyService

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _service(db: AsyncSession) -> ApiKeyService:
    """用请求级 session 适配服务层需要的 `session_factory`。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():  # type: ignore[no-untyped-def]
        yield db

    return ApiKeyService(_factory)  # type: ignore[arg-type]


def _to_out(record: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        key_id=record.id,
        name=record.name,
        prefix=record.prefix,
        scopes=list(record.scopes or []),
        last_used_at=record.last_used_at,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
        created_at=record.created_at,
    )


@router.post("", response_model=ApiKeyCreated, status_code=201, summary="生成 API Key")
async def create_api_key(
    payload: ApiKeyCreate, current_user: CurrentUserDep, db: DbDep
) -> ApiKeyCreated:
    """生成 Key。

    **响应里的 `key` 字段只出现这一次**，请立即保存。
    """
    service = _service(db)
    try:
        record, plaintext = await service.create(
            current_user.id,
            name=payload.name,
            scopes=list(payload.scopes) if payload.scopes is not None else None,
            expires_in_days=payload.expires_in_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    base = _to_out(record)
    return ApiKeyCreated(**base.model_dump(), key=plaintext)


@router.get("", response_model=list[ApiKeyOut], summary="列出我的 API Key")
async def list_api_keys(
    current_user: CurrentUserDep,
    db: DbDep,
    include_revoked: bool = False,
) -> list[ApiKeyOut]:
    """列出我的 Key（默认不含已吊销；`include_revoked=true` 给审计视角）。

    **绝不返回明文** —— 响应模型 `ApiKeyOut` 里根本没有 `key` 字段。
    """
    service = _service(db)
    records = await service.list_for_user(current_user.id, include_revoked=include_revoked)
    return [_to_out(record) for record in records]


@router.delete("/{key_id}", status_code=204, summary="吊销 API Key")
async def revoke_api_key(key_id: UUID, current_user: CurrentUserDep, db: DbDep) -> None:
    """吊销 Key（软删除，保留审计）。重复吊销幂等。"""
    service = _service(db)
    try:
        await service.revoke(current_user.id, key_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
