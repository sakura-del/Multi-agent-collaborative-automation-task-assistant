"""用户自带的文献数据源凭据服务（Issue #67 · BYOK）。

## 三条不变量

**① 明文只在「进入」时存在。**
`upsert` 接收明文，落库前立即加密；返回对象里没有明文，也没有密文。
调用方（端点）只拿得到 `key_mask`。

**② 每用户每数据源至多一条。**
同一 provider 再次保存就是覆盖（换 Key 语义），数据库唯一约束兜底并发。

**③ 越权一律当「未配置」。**
运行时 `get_decrypted_key` 以 user_id 过滤；别人的 Key 在这里等于不存在。

## 为什么失败不抛给任务

`get_decrypted_key` 由任务执行链路调用。用户没配置、key 被停用、甚至历史密文
因主密钥轮换解不开 —— 这些都不该让整个综述任务失败：跳过万方、继续用免费源
（arXiv/OpenAlex）是更合理的产品行为。是否「静默」由调用方决定：本服务只负责
返回 None 或抛出解密异常，降级与 warning 在 `TaskExecution` 里。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, ValidationException
from app.core.logging import get_logger
from app.db.models.data_source_credential import DATA_SOURCE_PROVIDERS, DataSourceCredential
from app.security.credential_cipher import CredentialCipher

logger = get_logger(__name__)

#: 支持的数据源（与模型文件里的常量保持一致，端点用它填充未配置项）
SUPPORTED_PROVIDERS: tuple[str, ...] = DATA_SOURCE_PROVIDERS

_UQ_NAME = "uq_data_source_credential_user_provider"


def mask_key(raw: str) -> str:
    """生成可回显的脱敏串（**永远不返回完整 Key**）。

    保留前 6 位与后 4 位，既让用户能辨认是哪把 Key，又不足以还原；
    长度 ≤ 10 时前 6 与后 4 会重叠，直接给全掩码。
    """
    value = (raw or "").strip()
    if len(value) <= 10:
        return "****"
    return f"{value[:6]}****{value[-4:]}"


class DataSourceCredentialService:
    """数据源凭据的 upsert / 读取 / 解密 / 删除。"""

    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self._cipher_impl = cipher

    @property
    def cipher(self) -> CredentialCipher:
        if self._cipher_impl is None:
            self._cipher_impl = CredentialCipher.from_settings()
        return self._cipher_impl

    # ===== 内部工具 =====

    @staticmethod
    def require_provider(provider: str) -> str:
        """归一 provider 并校验白名单；未知 provider → 可读的 ValidationException。"""
        canonical = str(provider or "").strip().lower()
        if canonical not in SUPPORTED_PROVIDERS:
            options = "、".join(SUPPORTED_PROVIDERS)
            raise ValidationException(f"暂不支持的数据源：{provider}（可选：{options}）")
        return canonical

    async def get(
        self, session: AsyncSession, user_id: uuid.UUID, provider: str
    ) -> DataSourceCredential | None:
        canonical = self.require_provider(provider)
        result = await session.execute(
            select(DataSourceCredential).where(
                DataSourceCredential.user_id == user_id,
                DataSourceCredential.provider == canonical,
            )
        )
        return result.scalar_one_or_none()

    # ===== 读取 =====

    async def list(self, session: AsyncSession, user_id: uuid.UUID) -> list[DataSourceCredential]:
        result = await session.execute(
            select(DataSourceCredential).where(DataSourceCredential.user_id == user_id)
            # 排序带唯一列：created_at 同事务可能相同（AGENTS §5 #14）
            .order_by(DataSourceCredential.provider.asc(), DataSourceCredential.id.asc())
        )
        return list(result.scalars().all())

    # ===== 写入 =====

    async def upsert(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        provider: str,
        api_key: str,
    ) -> DataSourceCredential:
        """保存或覆盖某数据源 Key（加密后落库，返回脱敏元数据）。"""
        canonical = self.require_provider(provider)
        key = (api_key or "").strip()
        if not key:
            raise ValidationException("API Key 不能为空")

        record = await self.get(session, user_id, canonical)
        if record is None:
            record = DataSourceCredential(
                user_id=user_id,
                provider=canonical,
                api_key_ciphertext=self.cipher.encrypt(key, user_id=user_id),
                key_mask=mask_key(key),
                status="active",
            )
            session.add(record)
        else:
            # 唯一约束保证每 provider 一条；再次保存即换 Key 语义
            record.api_key_ciphertext = self.cipher.encrypt(key, user_id=user_id)
            record.key_mask = mask_key(key)
            record.status = "active"

        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictException("数据源凭据保存冲突，请重试") from exc

        await session.refresh(record)
        await session.commit()
        logger.info(
            "data_source_credential_saved",
            provider=canonical,
            user_id=str(user_id),
        )
        return record

    async def delete(self, session: AsyncSession, user_id: uuid.UUID, provider: str) -> bool:
        """删除某数据源 Key；不存在返回 False（端点按幂等 204 处理）。"""
        record = await self.get(session, user_id, provider)
        if record is None:
            return False
        await session.delete(record)
        await session.flush()
        await session.commit()
        logger.info(
            "data_source_credential_deleted",
            provider=record.provider,
            user_id=str(user_id),
        )
        return True

    # ===== 供运行时使用 =====

    async def get_decrypted_key(
        self, session: AsyncSession, user_id: uuid.UUID, provider: str
    ) -> str | None:
        """读取并解密**属于该用户**的 Key；未配置 / 已停用返回 None。

        解密失败（主密钥轮换、密文损坏）抛 `CredentialDecryptError`：
        调用方按「该源不可用」降级，但日志里能区分「没配」与「配坏了」。
        """
        record = await self.get(session, user_id, provider)
        if record is None or record.status != "active":
            return None
        return self.cipher.decrypt(record.api_key_ciphertext, user_id=user_id)


def provider_status(provider: str, record: DataSourceCredential | None) -> dict[str, Any]:
    """把「可能不存在」的记录投影成前端要的配置状态（不暴露任何密文）。"""
    if record is None:
        return {
            "provider": provider,
            "configured": False,
            "key_mask": None,
            "status": None,
            "updated_at": None,
        }
    return {
        "provider": provider,
        "configured": True,
        "key_mask": record.key_mask,
        "status": record.status,
        "updated_at": record.updated_at,
    }
