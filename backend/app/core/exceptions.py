"""全局自定义异常与错误码定义。

设计原则：
- 业务层抛出领域异常，API 层统一捕获并转换为 HTTP 响应
- 所有异常携带可读 message 与可选 details，便于日志追踪
"""

from __future__ import annotations

from typing import Any


class AppException(Exception):
    """应用异常基类。子类定义 status_code 与 default_message。"""

    status_code: int = 500
    default_message: str = "服务内部错误"
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class NotFoundException(AppException):
    status_code = 404
    default_message = "资源不存在"
    error_code = "NOT_FOUND"


class UnauthorizedException(AppException):
    status_code = 401
    default_message = "未授权访问"
    error_code = "UNAUTHORIZED"


class ForbiddenException(AppException):
    status_code = 403
    default_message = "无权访问该资源"
    error_code = "FORBIDDEN"


class EmailNotVerifiedException(ForbiddenException):
    """登录被邮箱验证门禁拦截（2026-11-13）。

    403 而非 401：账号密码本身是对的，是「身份已确认但被规则禁止」；
    前端据此展示「查收验证邮件 + 重发」而不是引导重新输入密码。
    """

    default_message = "邮箱尚未验证，请查收验证邮件后再登录"
    error_code = "EMAIL_NOT_VERIFIED"


class ValidationException(AppException):
    status_code = 422
    default_message = "请求参数校验失败"
    error_code = "VALIDATION_ERROR"


class LLMException(AppException):
    status_code = 502
    default_message = "LLM 调用失败"
    error_code = "LLM_ERROR"


class DAGCycleError(AppException):
    """DAG 出现循环依赖（阶段4使用）"""

    status_code = 400
    default_message = "任务依赖图存在循环依赖"
    error_code = "DAG_CYCLE"


class UpstreamError(AppException):
    """上游服务返回不可重试的错误（4xx / 重定向异常 / 响应格式非法）。"""

    status_code = 502
    default_message = "上游服务返回错误"
    error_code = "UPSTREAM_ERROR"


class UpstreamTemporaryError(UpstreamError):
    """上游服务暂时不可用（超时 / 连接失败 / 5xx），允许离线回退或重试。"""

    status_code = 503
    default_message = "上游服务暂时不可用"
    error_code = "UPSTREAM_TEMPORARY"


class EvidenceCitationError(AppException):
    """引用标记无法解析到提供的证据（臆造 [E9] 等），持久化前必须拒绝。"""

    status_code = 422
    default_message = "引用标记超出可用证据范围"
    error_code = "EVIDENCE_CITATION"


class HumanActionRequiredError(AppException):
    """自动修复耗尽，需要人工介入（任务转入 WAITING_HUMAN）。"""

    status_code = 409
    default_message = "需要人工介入"
    error_code = "HUMAN_ACTION_REQUIRED"


class RateLimitedException(AppException):
    """请求被限流/临时锁定（429）。

    与 `app/api/rate_limit_middleware.py` 直接返回的 429 语义一致：都带 `Retry-After`。
    这里做成异常，是为了让**端点内部**的判定（如登录失败退避）走统一的异常处理器，
    而不是每个端点各自拼 JSONResponse。
    """

    status_code = 429
    default_message = "请求过于频繁，请稍后重试"
    error_code = "RATE_LIMITED"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 60,
        details: dict[str, Any] | None = None,
    ) -> None:
        retry = max(1, int(retry_after))
        self.retry_after = retry
        merged = {"retry_after": retry}
        if details:
            merged.update(details)
        super().__init__(message or self.default_message, details=merged)
