"""用户自带模型凭据的服务层（Issue #15 · BYOK P0）。

## 三条不变量

**① 明文只在「进入」时存在。**
`create` / `update` 接收明文，落库前立即加密；返回对象里没有明文，也没有密文。
调用方（端点）只拿得到 `api_key_hint`。

**② 默认凭据恒唯一。**
应用层「先清空再设置」，数据库再用部分唯一索引兜底。并发下两个请求同时设默认，
后者会收到 409 而不是留下两个默认。

**③ 越权一律 404。**
所有查询都以 `user_id` 过滤。不区分「不存在」与「不属于你」——
区分等于告诉攻击者「这个 ID 存在」（与登录不区分用户名/密码错误同理）。

## 为什么删除后要递补默认

否则用户删掉默认凭据后，建任务时会静默回落到平台模型，而用户以为还在用自己的
key —— 这属于「看起来成功、实际花了平台的钱」。递补规则取**最早创建**的一条，
可预测、不随删除顺序漂移。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.llm_credential import LLMCredential
from app.llm.providers import get_provider_spec, normalize_base_url
from app.schemas.llm_credential import LLMCredentialCreate, LLMCredentialUpdate
from app.security.credential_cipher import CredentialCipher, mask_secret

logger = get_logger(__name__)

#: 唯一约束名（用于把 IntegrityError 翻译成可读的 409）。
_UQ_NAME = "uq_llm_credential_user_name"
_UQ_DEFAULT = "uq_llm_credential_user_default"

#: 每用户凭据数量上限。不是产品限制，而是**防滥用**：
#: 凭据是用户可无限新增的行，没有上限时一个脚本就能把表填满。
#: 20 把足够覆盖「几家厂商 × 几个模型」的真实用法。
MAX_CREDENTIALS_PER_USER = 20


def _conflict_message(exc: IntegrityError) -> str:
    raw = str(getattr(exc, "orig", exc))
    if _UQ_NAME in raw:
        return "已存在同名凭据，请换一个名称"
    if _UQ_DEFAULT in raw:
        return "默认凭据设置冲突，请重试"
    return "凭据保存失败，请重试"


class LLMCredentialService:
    """凭据的增删改查 + 解密。"""

    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self._cipher_impl = cipher

    # ===== 内部工具 =====

    @property
    def cipher(self) -> CredentialCipher:
        if self._cipher_impl is None:
            self._cipher_impl = CredentialCipher.from_settings()
        return self._cipher_impl

    async def _get_default(self, session: AsyncSession, user_id: uuid.UUID) -> LLMCredential | None:
        result = await session.execute(
            select(LLMCredential).where(
                LLMCredential.user_id == user_id,
                LLMCredential.is_default.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def _clear_defaults(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        await session.execute(
            update(LLMCredential)
            .where(LLMCredential.user_id == user_id, LLMCredential.is_default.is_(True))
            .values(is_default=False)
        )

    @staticmethod
    def _resolve_base_url(provider: str, base_url: str | None) -> str:
        spec = get_provider_spec(provider)
        if spec.requires_base_url:
            return normalize_base_url(
                base_url or "",
                allow_insecure=settings.llm.byok_allow_insecure_base_url,
                allow_private=settings.llm.byok_allow_private_base_url,
            )
        if base_url:
            # 预置厂商的端点若可被用户覆盖，平台 key 就能被导向任意服务器
            raise ValidationException("预置厂商的端点由平台管理，请不要传 base_url")
        return spec.base_url or ""

    # ===== 读取 =====

    async def list(self, session: AsyncSession, user_id: uuid.UUID) -> list[LLMCredential]:
        result = await session.execute(
            select(LLMCredential).where(LLMCredential.user_id == user_id)
            # 排序必须带唯一列：created_at 在同一事务里可能完全相同（AGENTS §5 #14）
            .order_by(
                LLMCredential.is_default.desc(),
                LLMCredential.created_at.desc(),
                LLMCredential.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def get(
        self, session: AsyncSession, user_id: uuid.UUID, credential_id: uuid.UUID
    ) -> LLMCredential:
        result = await session.execute(
            select(LLMCredential).where(
                LLMCredential.id == credential_id,
                LLMCredential.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise NotFoundException("凭据不存在")
        return record

    # ===== 写入 =====

    async def create(
        self, session: AsyncSession, user_id: uuid.UUID, payload: LLMCredentialCreate
    ) -> LLMCredential:
        base_url = self._resolve_base_url(payload.provider, payload.base_url)
        cipher = self.cipher

        existing = await session.scalar(
            select(func.count(LLMCredential.id)).where(LLMCredential.user_id == user_id)
        )
        if int(existing or 0) >= MAX_CREDENTIALS_PER_USER:
            raise ValidationException(
                f"最多只能保存 {MAX_CREDENTIALS_PER_USER} 个模型凭据，请先删除不用的",
                details={"limit": MAX_CREDENTIALS_PER_USER},
            )

        record = LLMCredential(
            user_id=user_id,
            name=payload.name,
            provider=payload.provider,
            base_url=base_url,
            model=payload.model,
            api_key_ciphertext=cipher.encrypt(payload.api_key, user_id=user_id),
            api_key_hint=mask_secret(payload.api_key),
            key_fingerprint=cipher.fingerprint(payload.api_key),
            key_version=1,
            capabilities={},
            is_default=False,
            status="active",
        )

        # 首条自动成为默认，避免「添加了但任务还用平台模型」的困惑
        if payload.is_default or await self._get_default(session, user_id) is None:
            await self._clear_defaults(session, user_id)
            record.is_default = True

        session.add(record)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictException(_conflict_message(exc)) from exc

        await session.refresh(record)
        await session.commit()
        logger.info(
            "llm_credential_created",
            credential_id=str(record.id),
            user_id=str(user_id),
            provider=record.provider,
        )
        return record

    async def update(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        credential_id: uuid.UUID,
        payload: LLMCredentialUpdate,
    ) -> LLMCredential:
        record = await self.get(session, user_id, credential_id)
        data: dict[str, Any] = payload.model_dump(exclude_unset=True)

        if "name" in data and data["name"] is not None:
            record.name = data["name"]
        if "model" in data and data["model"] is not None:
            record.model = data["model"]
        if "status" in data and data["status"] is not None:
            record.status = data["status"]

        if data.get("api_key"):
            record.api_key_ciphertext = self.cipher.encrypt(data["api_key"], user_id=user_id)
            record.api_key_hint = mask_secret(data["api_key"])
            record.key_fingerprint = self.cipher.fingerprint(data["api_key"])
            record.key_version += 1
            # 换了 key，上一次的连通性结论就不作数了
            record.last_verified_at = None
            record.last_error = None

        if data.get("base_url"):
            # 只有 custom 能改端点；预置项走 provider 校验会直接 422
            record.base_url = self._resolve_base_url(record.provider, data["base_url"])

        if data.get("is_default") is True:
            await self._clear_defaults(session, user_id)
            record.is_default = True
        # is_default=False 刻意不处理：取消默认而不指定新的默认，会让任务静默回落到
        # 平台模型。要换默认就设另一条为默认。

        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictException(_conflict_message(exc)) from exc

        await session.refresh(record)
        await session.commit()
        logger.info(
            "llm_credential_updated",
            credential_id=str(record.id),
            user_id=str(user_id),
            key_version=record.key_version,
        )
        return record

    async def delete(
        self, session: AsyncSession, user_id: uuid.UUID, credential_id: uuid.UUID
    ) -> None:
        record = await self.get(session, user_id, credential_id)
        was_default = record.is_default

        await session.delete(record)
        await session.flush()

        if was_default:
            result = await session.execute(
                select(LLMCredential)
                .where(LLMCredential.user_id == user_id)
                .order_by(LLMCredential.created_at.asc(), LLMCredential.id.asc())
                .limit(1)
            )
            successor = result.scalar_one_or_none()
            if successor is not None:
                successor.is_default = True

        await session.commit()
        logger.info(
            "llm_credential_deleted",
            credential_id=str(credential_id),
            user_id=str(user_id),
        )

    # ===== 供运行时使用 =====

    def decrypt_api_key(self, record: LLMCredential) -> str:
        """还原明文（**只给发往厂商的调用链用，不要放进任何响应**）。"""
        return self.cipher.decrypt(record.api_key_ciphertext, user_id=record.user_id)
