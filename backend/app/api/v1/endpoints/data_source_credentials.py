"""用户自带数据源凭据端点（Issue #67 · BYOK）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/data-source-credentials` | 所有支持的数据源的配置状态（只回掩码） |
| `PUT` | `/data-source-credentials/{provider}` | 保存/覆盖该数据源的 Key（明文只进不出） |
| `DELETE` | `/data-source-credentials/{provider}` | 删除该数据源的 Key（幂等 204） |

## 为什么是 PUT 而不是 POST

一个用户对同一个数据源只有「一把当前生效的 Key」——保存就是覆盖。
用 PUT 表达幂等语义：重试同一个请求不会造出第二条记录。

## 为什么全部强制 JWT

与 `llm_credentials` 同因：数据源 Key 能花用户自己的钱（万方按次计费）。
不接受开放 API Key 鉴权，避免「只读 Key 泄露配置、可写 Key 接管账号」。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.models.data_source_credential import DataSourceCredential
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.data_source_credential import (
    DataSourceCredentialOut,
    DataSourceCredentialUpsert,
)
from app.security.audit import record_audit
from app.security.data_source_credential_service import (
    SUPPORTED_PROVIDERS,
    DataSourceCredentialService,
    provider_status,
)

router = APIRouter(prefix="/data-source-credentials", tags=["data-source-credentials"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _service() -> DataSourceCredentialService:
    return DataSourceCredentialService()


def _to_out(status: dict) -> DataSourceCredentialOut:
    """显式构造响应：不依赖 ORM / dict 序列化，杜绝「新增一列就意外泄露」。"""
    return DataSourceCredentialOut(**status)


@router.get("", response_model=list[DataSourceCredentialOut], summary="数据源凭据状态")
async def list_data_source_credentials(
    current_user: CurrentUserDep, db: DbDep
) -> list[DataSourceCredentialOut]:
    """返回所有支持的数据源；未配置的也会出现（`configured=false`），前端好渲染。"""
    records = await _service().list(db, current_user.id)
    by_provider: dict[str, DataSourceCredential] = {record.provider: record for record in records}
    return [
        _to_out(provider_status(provider, by_provider.get(provider)))
        for provider in SUPPORTED_PROVIDERS
    ]


@router.put(
    "/{provider}",
    response_model=DataSourceCredentialOut,
    summary="保存数据源 Key",
)
async def upsert_data_source_credential(
    provider: str,
    payload: DataSourceCredentialUpsert,
    current_user: CurrentUserDep,
    db: DbDep,
    request: Request,
) -> DataSourceCredentialOut:
    """保存/覆盖 Key；响应只有脱敏串，明文与密文都不回传。"""
    actor_id, actor_name = current_user.id, current_user.username
    record = await _service().upsert(db, current_user.id, provider, payload.api_key)
    await record_audit(
        db,
        actor_id=actor_id,
        actor_name=actor_name,
        action="data_source_credential.upsert",
        target_type="data_source_credential",
        target_id=record.id,
        # 只记 provider 与 key 的尾部掩码，绝不记原文
        metadata={"provider": record.provider, "key_mask": record.key_mask},
        request=request,
    )
    return _to_out(provider_status(record.provider, record))


@router.delete("/{provider}", status_code=204, summary="删除数据源 Key")
async def delete_data_source_credential(
    provider: str,
    current_user: CurrentUserDep,
    db: DbDep,
    request: Request,
) -> Response:
    """幂等删除：不存在也返回 204（避免前端重复点击报错）。"""
    actor_id, actor_name = current_user.id, current_user.username
    canonical = _service().require_provider(provider)
    await _service().delete(db, current_user.id, canonical)
    await record_audit(
        db,
        actor_id=actor_id,
        actor_name=actor_name,
        action="data_source_credential.delete",
        target_type="data_source_credential",
        target_id=None,
        metadata={"provider": canonical},
        request=request,
    )
    return Response(status_code=204)
