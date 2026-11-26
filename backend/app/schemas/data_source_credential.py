"""数据源凭据（BYOK）的 Pydantic 契约（Issue #67）。

## 响应模型里为什么没有 `api_key`

与 `llm_credential` 同一原则：字段根本不存在，类型层面就不可能序列化出明文。
前端只能拿到 `key_mask`（如 `387041****02cc`）。

## 校验分层

| 层 | 负责 | 例子 |
| --- | --- | --- |
| Pydantic | 语法 | Key 非空、长度上限、不含内部空白 |
| Service | 策略 | provider 白名单、加密与脱敏 |

不把 provider 白名单写成 `Literal`：新增数据源时不想让前端旧包直接 422，
而是拿到可读的「暂不支持该数据源」——`Literal` 会先被 FastAPI 翻译成 422。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Key 长度边界（万方 App Key 约 20~40 字符，留足余量）
MIN_API_KEY_LENGTH = 8
MAX_API_KEY_LENGTH = 512


class DataSourceCredentialUpsert(BaseModel):
    """保存/覆盖某个数据源的 Key。"""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(
        min_length=MIN_API_KEY_LENGTH,
        max_length=MAX_API_KEY_LENGTH,
        description="数据源 App Key（只进不出，响应只回脱敏串）",
    )

    @field_validator("api_key", mode="before")
    @classmethod
    def _clean_key(cls, value: Any) -> Any:
        """去掉首尾空白（粘贴常带换行），但拒绝内部空白。

        Key 内部的空白几乎一定是复制时混入了别的内容；静默剔除会造出
        「保存成功、调用 401」的假成功（与 LLM BYOK 同一教训）。
        """
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if any(ch.isspace() for ch in stripped):
            raise ValueError("API Key 不能包含空白字符，请检查是否复制了多余内容")
        return stripped


class DataSourceCredentialOut(BaseModel):
    """某个数据源的配置状态（**绝不含明文或密文**）。"""

    model_config = ConfigDict(extra="forbid")

    provider: str
    configured: bool
    key_mask: str | None = Field(default=None, description="脱敏回显，如 `387041****02cc`")
    status: str | None = None
    updated_at: datetime | None = None
