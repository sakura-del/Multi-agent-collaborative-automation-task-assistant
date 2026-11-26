"""DATA_SOURCE_CREDENTIAL：用户自带的文献数据源凭据（Issue #67 · BYOK）。

## 与 `llm_credential` 的关系

同属「用户自带 Key」：明文只进不出、AES-256-GCM 加密（复用
`app.security.credential_cipher.CredentialCipher`，AAD 绑定 user_id）。
区别在生命周期：

- 模型凭据是**多把**（不同厂商/模型各一条，还有默认凭据的概念）；
- 数据源凭据是**每 provider 至多一条**：同一用户再次填写同一个 provider 的 Key
  就是覆盖（`upsert`），不需要「哪条是默认」这种语义。

## 为什么单独建表而不是塞进 `llm_credential`

两者用途不同：一个用于调 LLM（花 token 钱），一个用于调第三方文献接口
（花订阅额度）。混在一张表里会让「删掉模型凭据」意外停掉文献检索，
也会让两边的 provider 目录互相污染。

## 明文绝不落库

只存 `api_key_ciphertext`（v1:base64）与 `key_mask`（脱敏回显）。
`tests/test_data_source_credentials.py` 会断言表里没有明文列。
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: 支持的数据源 provider。新增源时同步：
#: - `app.security.data_source_credential_service.SUPPORTED_PROVIDERS`
#: - 前端设置页 / 建任务表单的枚举
DATA_SOURCE_PROVIDERS: tuple[str, ...] = ("wanfang",)


class DataSourceCredential(Base):
    """用户自带的文献数据源 Key（加密存储，响应只回脱敏串）。"""

    __tablename__ = "data_source_credential"

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_data_source_credential_user_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="凭据ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属用户ID",
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, comment="数据源键：wanfang")
    api_key_ciphertext: Mapped[str] = mapped_column(
        Text, nullable=False, comment="AES-256-GCM 密文（v1:base64）"
    )
    key_mask: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="脱敏回显（如 387041****02cc），不含完整 Key"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False, comment="active/disabled"
    )

    # Base 已提供 created_at / updated_at
