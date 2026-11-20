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

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import settings
from app.core.exceptions import ForbiddenException
from app.db.models.llm_credential import LLMCredential
from app.db.models.user import User
from app.db.session import get_db
from app.llm.probe import fetch_models
from app.llm.providers import PRESET_PROVIDERS, PROVIDER_ORDER, get_provider_spec
from app.schemas.llm_credential import (
    LLMCredentialCreate,
    LLMCredentialOut,
    LLMCredentialUpdate,
    LLMProbeOut,
    LLMProbeRequest,
    ProviderOut,
)
from app.security.audit import record_audit
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
        context_window=record.context_window,
        max_output_tokens=record.max_output_tokens,
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


async def _probe(payload: LLMProbeRequest, current_user: User, db: AsyncSession) -> LLMProbeOut:
    """共用的探活逻辑：已保存凭据复用库里的 key，未保存配置直接测。

    失败会写回凭据（计数 + 可读错误）；成功清零并恢复 active。
    未保存配置失败不落库（用户可能只是在试）。
    """
    service = _service()
    record: LLMCredential | None = None

    if payload.credential_id is not None:
        record = await service.get(db, current_user.id, payload.credential_id)
        if record.status == "disabled":
            raise ForbiddenException("该凭据已停用，请先启用再测试")
        base_url = record.base_url
        api_key = service.decrypt_api_key(record)
    else:
        # provider 已由 schema 校验非空；这里再取一次保证类型收敛
        provider = payload.provider or "custom"
        base_url = service.resolve_base_url(provider, payload.base_url)
        api_key = payload.api_key or ""

    result = await fetch_models(
        base_url=base_url,
        api_key=api_key,
        allow_private=settings.llm.byok_allow_private_base_url,
    )

    credential_status: str | None = None
    if record is not None:
        if result.ok:
            record = await service.record_probe_success(db, record)
        else:
            record = await service.record_failure(
                db,
                user_id=current_user.id,
                credential_id=record.id,
                error=result.error or "测试失败",
            )
        credential_status = record.status

    return LLMProbeOut(
        ok=result.ok,
        latency_ms=result.latency_ms,
        models=result.models,
        error=result.error,
        credential_status=credential_status,
    )


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


@router.post("/probe", response_model=LLMProbeOut, summary="测试连接并拉取模型列表")
async def probe_credential(
    payload: LLMProbeRequest, current_user: CurrentUserDep, db: DbDep
) -> LLMProbeOut:
    """测已保存凭据或未保存配置；成功会返回厂商的模型 id 列表。

    这是**唯一**会用用户 key 主动外呼的端点，因此必须走 pin 传输层
    （`fetch_models` 内部完成），不能裸 `httpx`。
    """
    return await _probe(payload, current_user, db)


@router.post("/{credential_id}/test", response_model=LLMProbeOut, summary="测试已保存凭据")
async def test_credential(
    credential_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> LLMProbeOut:
    return await _probe(LLMProbeRequest(credential_id=credential_id), current_user, db)


@router.get("", response_model=list[LLMCredentialOut], summary="我的模型凭据")
async def list_credentials(current_user: CurrentUserDep, db: DbDep) -> list[LLMCredentialOut]:
    records = await _service().list(db, current_user.id)
    return [_to_out(record) for record in records]


@router.post("", response_model=LLMCredentialOut, status_code=201, summary="新增模型凭据")
async def create_credential(
    payload: LLMCredentialCreate,
    current_user: CurrentUserDep,
    db: DbDep,
    request: Request,
) -> LLMCredentialOut:
    record = await _service().create(db, current_user.id, payload)
    await record_audit(
        db,
        actor=current_user,
        action="credential.create",
        target_type="credential",
        target_id=record.id,
        metadata={"provider": record.provider, "model": record.model, "name": record.name},
        request=request,
    )
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
    request: Request,
) -> LLMCredentialOut:
    record = await _service().update(db, current_user.id, credential_id, payload)
    await record_audit(
        db,
        actor=current_user,
        action="credential.update",
        target_type="credential",
        target_id=credential_id,
        # 只记改了哪些字段名；key 轮换标记出来，但绝不记值
        metadata={
            "fields": sorted(payload.model_fields_set),
            "key_rotated": "api_key" in payload.model_fields_set,
        },
        request=request,
    )
    return _to_out(record)


@router.delete("/{credential_id}", status_code=204, summary="删除凭据")
async def delete_credential(
    credential_id: UUID, current_user: CurrentUserDep, db: DbDep, request: Request
) -> Response:
    await _service().delete(db, current_user.id, credential_id)
    await record_audit(
        db,
        actor=current_user,
        action="credential.delete",
        target_type="credential",
        target_id=credential_id,
        request=request,
    )
    return Response(status_code=204)
