"""账号生命周期 HTTP Schema（Phase 1 P1-C / 计划 2026-10-20-00 §1.3）。

- 请求模型 ``extra="forbid"``：字段集合即契约，拼写错误不被静默忽略；
- 密码校验复用注册的强度规则（``min_length=8, max_length=128``）；
- 响应统一 ``message`` 单字段：``/password-reset/request`` 必须做到**恒定响应**，
  因此该响应里不得出现任何随账号是否存在而变化的字段（如 ``email_sent``）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

__all__ = [
    "EmailVerificationConfirm",
    "LifecycleMessageOut",
    "PasswordResetConfirm",
    "PasswordResetRequest",
]


class EmailVerificationConfirm(BaseModel):
    """邮箱验证确认请求。"""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=256, description="邮件里的一次性令牌")


class PasswordResetRequest(BaseModel):
    """密码重置申请请求（恒定响应，不泄漏账号是否存在）。"""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(description="注册邮箱")


class PasswordResetConfirm(BaseModel):
    """密码重置确认请求：密码规则与注册一致（≥8 位）。"""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=256, description="邮件里的一次性令牌")
    new_password: str = Field(min_length=8, max_length=128, description="新密码明文")


class LifecycleMessageOut(BaseModel):
    """账号生命周期操作的统一响应（只含人类可读消息，恒定性由端点保证）。"""

    model_config = ConfigDict(extra="forbid")

    message: str
