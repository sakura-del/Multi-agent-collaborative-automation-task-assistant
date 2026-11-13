"""用户自带模型凭据端点（Issue #15 · BYOK P0）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/llm-credentials/providers` | 厂商目录（前端下拉框） |
| `GET` | `/llm-credentials` | 列表（只回显脱敏串） |
| `POST` | `/llm-credentials` | 新增（明文只进不出） |
| `GET` | `/llm-credentials/{id}` | 详情 |
| `PATCH` | `/llm-credentials/{id}` | 改名/换模型/换 key/设默认 |
| `DELETE` | `/llm-credentials/{id}` | 删除（物理删除密文） |

## 为什么全部强制 JWT

凭据是「能花用户自己钱」的东西，不接受开放 API Key 鉴权：
一个泄露的只读 Key 若能读凭据列表，至少泄露了「用户在用什么厂商/模型」；
若能改，等于接管用户的付费账号。需要脚本管理时再单独设计 scope。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.models.llm_credential import LLMCredential
from app.db.models.user import User
from app.db.session import get_db
from app.llm.providers import PRESET_PROVIDERS, PROVIDER_ORDER, get_provider_spec
from app.schemas.llm_credential import (
    LLMCredentialCreate,
    LLMCredentialOut,
    LLMCredentialUpdate,
    ProviderOut,
)
from app.security.llm_credential_service import LLMCredentialService

router = APIRouter(prefix="/llm-credentials", tags=["llm-credentials"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _to_out(record: LLMCredential) -> LLMCredentialOut:
    """显式构造响应：不依赖 ORM 序列化，杜绝「新增一列就意外泄露」。"""
    return LLMCredentialOut(
        credential_id=record.id,
        name=record.name,
        provider=record.provider,
        provider_label=get_provider_spec(record.provider).label,
        model=record.model,
        base_url=record.base_url,
        masked_key=record.api_key_hint,
        key_version=record.key_version,
        is_default=record.is_default,
        status=record.status,
        capabilities=dict(record.capabilities or {}),
        last_verified_at=record.last_verified_at,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _service() -> LLMCredentialService:
    return LLMCredentialService()


@router.get("/providers", response_model=list[ProviderOut], summary="模型厂商目录")
async def list_providers(current_user: CurrentUserDep) -> list[ProviderOut]:
    """返回预置厂商与「自定义」选项。**不含任何密钥或平台配置。**"""
    return [
        ProviderOut(
            provider=key,
            label=PRESET_PROVIDERS[key].label,
            base_url=PRESET_PROVIDERS[key].base_url,
            requires_base_url=PRESET_PROVIDERS[key].requires_base_url,
        )
        for key in PROVIDER_ORDER
    ]


@router.get("", response_model=list[LLMCredentialOut], summary="我的模型凭据")
async def list_credentials(current_user: CurrentUserDep, db: DbDep) -> list[LLMCredentialOut]:
    records = await _service().list(db, current_user.id)
    return [_to_out(record) for record in records]


@router.post("", response_model=LLMCredentialOut, status_code=201, summary="新增模型凭据")
async def create_credential(
    payload: LLMCredentialCreate, current_user: CurrentUserDep, db: DbDep
) -> LLMCredentialOut:
    record = await _service().create(db, current_user.id, payload)
    return _to_out(record)


@router.get("/{credential_id}", response_model=LLMCredentialOut, summary="凭据详情")
async def get_credential(
    credential_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> LLMCredentialOut:
    record = await _service().get(db, current_user.id, credential_id)
    return _to_out(record)


@router.patch("/{credential_id}", response_model=LLMCredentialOut, summary="更新凭据")
async def update_credential(
    credential_id: UUID,
    payload: LLMCredentialUpdate,
    current_user: CurrentUserDep,
    db: DbDep,
) -> LLMCredentialOut:
    record = await _service().update(db, current_user.id, credential_id, payload)
    return _to_out(record)


@router.delete("/{credential_id}", status_code=204, summary="删除凭据")
async def delete_credential(
    credential_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> Response:
    await _service().delete(db, current_user.id, credential_id)
    return Response(status_code=204)
