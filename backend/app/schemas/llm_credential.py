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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProviderKey = Literal[
    "openai",
    "deepseek",
    "glm",
    "qwen",
    "siliconflow",
    "moonshot",
    "custom",
]

#: 容量字段的取值上界。不是产品限制，而是防止把「1M」误填成 1000000000
#: 之后请求体里出现荒唐的 max_tokens。
MAX_CONTEXT_WINDOW = 10_000_000
MAX_OUTPUT_TOKENS = 1_000_000


def _clean_optional_int(value: Any) -> Any:
    """空串按「未填」处理：前端清空输入框时拿到的是 `""` 而不是 None。"""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return int(stripped)
    return value


class ReasoningTier(BaseModel):
    """推理等级档位 → API 参数值（参照 DeepSeek Harness 模型目录的设计）。

    例：{"label": "low", "value": "low"} / {"label": "max", "value": "max"}——
    前端下拉展示 label，调用时把 value 作为推理档位参数下发给模型。
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=24)
    value: str = Field(min_length=1, max_length=64)


class ModelCapabilities(BaseModel):
    """模型能力声明（凭据级，用户在「我的模型」勾选）。

    ## 为什么要声明而不是自动探测

    中转站的 /models 通常只给 id，不给多模态/推理能力；探测要花钱且不可靠。
    由用户按厂商文档勾选是唯一诚实做法——勾错只会得到可读的调用错误，
    而「猜能力」会让 UI 出现假的「支持图片」。

    - ``text``：文本输入（默认 True，几乎所有模型都支持）；
    - ``image``：图片输入（视觉模型才勾，勾了才允许在聊天里传图）；
    - ``reasoning_tiers``：可用的推理等级档位；空 = 不支持推理参数。
    """

    model_config = ConfigDict(extra="forbid")

    text: bool = True
    image: bool = False
    reasoning_tiers: list[ReasoningTier] = Field(default_factory=list, max_length=4)


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
    context_window: int | None = Field(
        default=None,
        ge=256,
        le=MAX_CONTEXT_WINDOW,
        description="模型上下文窗口（token）。只用于展示与校验，不参与请求",
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_OUTPUT_TOKENS,
        description="单次输出上限（token）。非空时作为 max_tokens 真正下发给厂商",
    )
    is_default: bool = Field(default=False, description="是否设为默认；首条会自动成为默认")
    capabilities: ModelCapabilities = Field(
        default_factory=ModelCapabilities, description="模型能力声明（文本/图片/推理档位）"
    )

    @field_validator("name", "model", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("context_window", "max_output_tokens", mode="before")
    @classmethod
    def _blank_as_none(cls, value: Any) -> Any:
        return _clean_optional_int(value)

    @model_validator(mode="after")
    def _output_fits_context(self) -> LLMCredentialCreate:
        if (
            self.context_window is not None
            and self.max_output_tokens is not None
            and self.max_output_tokens > self.context_window
        ):
            raise ValueError("最大输出不能大于上下文窗口")
        return self

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
    context_window: int | None = Field(
        default=None, ge=256, le=MAX_CONTEXT_WINDOW, description="传 null 表示清除"
    )
    max_output_tokens: int | None = Field(
        default=None, ge=1, le=MAX_OUTPUT_TOKENS, description="传 null 表示清除"
    )
    is_default: bool | None = None
    status: Literal["active", "disabled"] | None = None
    capabilities: ModelCapabilities | None = Field(
        default=None, description="整体替换能力声明；None = 不改"
    )

    @field_validator("name", "model", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("context_window", "max_output_tokens", mode="before")
    @classmethod
    def _blank_as_none(cls, value: Any) -> Any:
        return _clean_optional_int(value)

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
    context_window: int | None = None
    max_output_tokens: int | None = None
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


class LLMProbeRequest(BaseModel):
    """测试连接请求（Issue #15 P1）。

    两种用法：
    - `credential_id`：测已保存的凭据（复用库里的 key，前端列表页/编辑页用）；
    - `provider + api_key (+ base_url)`：测还没保存的配置（新建表单用）。
    """

    model_config = ConfigDict(extra="forbid")

    credential_id: uuid.UUID | None = None
    provider: ProviderKey | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    base_url: str | None = Field(default=None, max_length=512)

    @field_validator("api_key", mode="before")
    @classmethod
    def _clean_key(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if any(ch.isspace() for ch in stripped):
            raise ValueError("API Key 不能包含空白字符，请检查是否复制了多余内容")
        return stripped

    @model_validator(mode="after")
    def _check_source(self) -> LLMProbeRequest:
        if self.credential_id is not None:
            if self.api_key:
                raise ValueError("credential_id 与 api_key 只能二选一")
            return self
        if self.provider is None:
            raise ValueError("未提供 credential_id 时必须指定 provider")
        if not self.api_key:
            raise ValueError("未提供 credential_id 时必须填写 API Key")
        return self


class LLMProbeOut(BaseModel):
    """测试连接结果。**不含密钥**，只回可读状态与模型列表。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    latency_ms: int
    models: list[str] = Field(default_factory=list)
    error: str | None = None
    credential_status: str | None = None
    """测已保存凭据时回传最新状态：active / invalid。未保存配置为 None。"""
