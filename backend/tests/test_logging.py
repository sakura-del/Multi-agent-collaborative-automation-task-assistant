"""logging 模块测试：结构化日志与上下文绑定。

阶段1验收：核心模块覆盖率 ≥70%。
"""

from __future__ import annotations

import structlog

from app.core.logging import bind_request_context, configure_logging, get_logger


class TestLoggingSystem:
    """日志系统配置与上下文绑定。"""

    def test_configure_logging_does_not_raise(self) -> None:
        """configure_logging 应能正常初始化。"""
        configure_logging()  # 不抛异常即通过

    def test_get_logger_returns_bound_logger(self) -> None:
        """get_logger 应返回 BoundLogger。"""
        logger = get_logger("test.module")
        assert logger is not None

    def test_bind_request_context_sets_trace_id(self) -> None:
        """bind_request_context 应把 trace_id 写入 contextvars。"""
        bind_request_context(trace_id="trace-abc-123")
        # 通过 contextvars 读取验证
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("trace_id") == "trace-abc-123"

    def test_bind_request_context_with_all_fields(self) -> None:
        """绑定多个上下文字段。"""
        bind_request_context(
            trace_id="t1",
            task_id="task-1",
            agent_id="agent-1",
            user_id="user-1",
        )
        ctx = structlog.contextvars.get_contextvars()
        assert ctx["trace_id"] == "t1"
        assert ctx["task_id"] == "task-1"
        assert ctx["agent_id"] == "agent-1"
        assert ctx["user_id"] == "user-1"

    def test_bind_request_context_clears_previous(self) -> None:
        """再次绑定应清除上一次的上下文。"""
        bind_request_context(trace_id="first", task_id="t1")
        bind_request_context(trace_id="second")
        ctx = structlog.contextvars.get_contextvars()
        # 第二次绑定未传 task_id，应不存在
        assert ctx.get("trace_id") == "second"
        assert "task_id" not in ctx
