"""工具声明与网关异常形状。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.core.exceptions import AppException
from app.tools.agent.context import ToolContext


class ConfirmationRequiredException(AppException):
    """写工具未确认：返回 409 + 工具名与影响说明，前端据此弹确认卡。"""

    status_code = 409
    default_message = "该操作会修改数据，需要确认"
    error_code = "TOOL_CONFIRMATION_REQUIRED"

    def __init__(
        self, message: str | None = None, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details)


class ToolValidationException(AppException):
    """工具参数不合法：422 + 结构化字段错误。"""

    status_code = 422
    default_message = "工具参数不合法"
    error_code = "TOOL_ARGUMENTS_INVALID"


@dataclass(frozen=True)
class ToolSpec:
    """单个工具：名称 / 领域 / JSON Schema / 处理函数 / 是否写操作。"""

    name: str
    domain: str
    summary: str
    args_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
    write: bool = False

    @property
    def parameters(self) -> dict[str, Any]:
        """给模型看的 JSON Schema（Pydantic 生成，永远与校验一致）。"""
        return self.args_model.model_json_schema()

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "summary": self.summary,
            "write": self.write,
            "parameters": self.parameters,
        }
