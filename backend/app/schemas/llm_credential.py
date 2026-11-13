"""BYOK 凭据的 Pydantic 契约（Issue #15）。

## 响应模型里为什么没有 `api_key`

不是「默认 None 不序列化」这种依赖配置的保证，而是**字段根本不存在**：
类型层面就不可能出现明文。前端只能拿到 `masked_key`（如 `sk-…abcd`）。

## 校验分层

| 层 | 负责 | 例子 |
| --- | --- | --- |
| Pydantic | 语法 | 名称非空、key 不含空白、长度上限 |
| Service | 策略 | 预置厂商禁止自带 base_url、默认凭据唯一 |
| `providers.normalize_base_url` | 安全 | https、端口、私网 IP、单标签主机名 |

分开的原因：安全策略要读运行时配置（`byok_allow_insecure_base_url`），
塞进 schema 会让「同一份请求在测试与生产校验结果不同」变得难以解释。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderKey = Literal[
    "openai",
    "deepseek",
    "glm",
    "qwen",
    "siliconflow",
    "moonshot",
    "custom",
]


class LLMCredentialCreate(BaseModel):
    """新增一把自带凭据。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, description="用户可见名称，同用户内唯一")
    provider: ProviderKey = Field(description="厂商键")
    model: str = Field(min_length=1, max_length=160, description="模型名，如 deepseek-chat")
    api_key: str = Field(min_length=8, max_length=512, description="厂商 API Key（只进不出）")
    base_url: str | None = Field(
        default=None, max_length=512, description="仅 custom 需要；预置厂商由服务端决定"
    )
    is_default: bool = Field(default=False, description="是否设为默认；首条会自动成为默认")

    @field_validator("name", "model", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _clean_key(cls, value: Any) -> Any:
        """去掉首尾空白（粘贴常带换行），但**拒绝内部空白**。

        Key 内部的空白几乎一定是复制时混入了别的内容；静默剔除会造出
        「保存成功、调用 401」的假成功。
        """
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if any(ch.isspace() for ch in stripped):
            raise ValueError("API Key 不能包含空白字符，请检查是否复制了多余内容")
        return stripped


class LLMCredentialUpdate(BaseModel):
    """局部更新；字段为 `None` 表示不改。

    `provider` 刻意不可改：换厂商意味着 `base_url` 语义整体变化，
    让用户新建一条比「原地变身」更不容易出错（历史任务快照也不会失真）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    base_url: str | None = Field(default=None, max_length=512)
    is_default: bool | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("name", "model", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _clean_key(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if any(ch.isspace() for ch in stripped):
            raise ValueError("API Key 不能包含空白字符，请检查是否复制了多余内容")
        return stripped


class LLMCredentialOut(BaseModel):
    """凭据元数据（**绝不含明文或密文**）。"""

    model_config = ConfigDict(extra="forbid")

    credential_id: uuid.UUID
    name: str
    provider: str
    provider_label: str
    model: str
    base_url: str
    masked_key: str = Field(description="脱敏回显，如 `sk-…abcd`")
    key_version: int
    is_default: bool
    status: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    last_verified_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProviderOut(BaseModel):
    """厂商目录项（前端下拉框数据源）。"""

    model_config = ConfigDict(extra="forbid")

    provider: str
    label: str
    base_url: str | None
    requires_base_url: bool
