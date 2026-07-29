"""全局自定义异常与错误码定义。

设计原则：
- 业务层抛出领域异常，API 层统一捕获并转换为 HTTP 响应
- 所有异常携带可读 message 与可选 details，便于日志追踪
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class AppException(Exception):
    """应用异常基类。子类定义 status_code 与 default_message。"""

    status_code: int = 500
    default_message: str = "服务内部错误"
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Optional[Dict[str, Any]] = None,
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
