"""结构化日志系统。

基于 structlog 实现：
- 支持 JSON / console 两种输出格式
- 日志分级：DEBUG / INFO / WARNING / ERROR
- 通过 `trace_id` / `task_id` / `agent_id` 关联完整调用链（阶段1任务7）
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """初始化全局日志系统。在 FastAPI 启动时调用一次即可。"""
    level = getattr(logging, settings.app.log_level.upper(), logging.DEBUG)

    # 标准 logging 配置（structlog 的输出会经过 stdlib 处理器）
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # 共享处理器链：contextvars 注入 → 添加调用链字段 → 序列化
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any
    if settings.app.log_format == "json":
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        # 使用 stdlib LoggerFactory：logger 具备 name 属性，
        # 与 add_logger_name 处理器兼容，且能复用 logging.basicConfig 的输出配置
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取一个绑定 logger。建议传入模块名：`get_logger(__name__)`。"""
    return structlog.get_logger(name)


def bind_request_context(
    trace_id: str,
    task_id: str | None = None,
    agent_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """绑定单次请求/任务的上下文到 contextvars。

    后续所有日志输出会自动带上这些字段，便于跨模块按 trace_id 聚合查询。
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    if task_id:
        structlog.contextvars.bind_contextvars(task_id=task_id)
    if agent_id:
        structlog.contextvars.bind_contextvars(agent_id=agent_id)
    if user_id:
        structlog.contextvars.bind_contextvars(user_id=user_id)
