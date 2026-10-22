"""实验 CLI live 适配器装配测试（2026-10-22 实验矩阵 Task 3）。

## 本文件锁定

1. `--adapter live` 能装配出 `TaskExecutionExecutor`（不再是「未实现」占位）；
2. 装配失败时给**可读错误**（缺用户 / 缺语料 / 缺模型密钥）；
3. `--adapter fixed` 行为不变（CI 依赖它）。

## 为什么装配要可注入

装配需要真实数据库 + 模型客户端 + 演示用户，单测里都不该真连。
所以 `build_live_executor(...)` 接受可选依赖，测试注入替身；
CLI 走默认（真实依赖）。

## 为什么缺依赖必须报可读错误

实验要跑几小时。若启动 3 秒后抛个 `AttributeError: 'NoneType'`，
用户不知道该补什么。每条错误都要说清「缺什么、怎么补」。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from evaluation.cli import build_live_executor
from evaluation.runner import TaskExecutionExecutor


class _FakeExecution:
    """最小 TaskExecution 替身。"""

    async def submit(self, user_id: Any, request: Any) -> uuid.UUID:
        return uuid.uuid4()


class _FakeUser:
    def __init__(self, user_id: uuid.UUID | None = None) -> None:
        self.id = user_id or uuid.uuid4()


class TestBuildLiveExecutor:
    def test_builds_task_execution_executor(self) -> None:
        """注入依赖时应装配出真实执行器。"""
        user_id = uuid.uuid4()
        executor = build_live_executor(
            execution=_FakeExecution(),
            user_id=user_id,
        )

        assert isinstance(executor, TaskExecutionExecutor)
        assert executor.user_id == user_id

    def test_poll_interval_configurable(self) -> None:
        executor = build_live_executor(
            execution=_FakeExecution(),
            user_id=uuid.uuid4(),
            poll_interval=0.2,
            timeout=60.0,
        )

        assert executor.poll_interval == 0.2
        assert executor.timeout == 60.0

    def test_missing_execution_raises_readable_error(self) -> None:
        """缺执行器时要说明缺什么，而不是 AttributeError。"""
        with pytest.raises(ValueError, match="TaskExecution"):
            build_live_executor(execution=None, user_id=uuid.uuid4())

    def test_missing_user_raises_readable_error(self) -> None:
        with pytest.raises(ValueError, match="演示用户"):
            build_live_executor(execution=_FakeExecution(), user_id=None)


class TestResolveDemoUser:
    def test_returns_user_id_when_found(self) -> None:
        from evaluation.cli import resolve_demo_user

        user_id = uuid.uuid4()

        async def _lookup(username: str) -> _FakeUser | None:
            return _FakeUser(user_id) if username == "demo" else None

        resolved = resolve_demo_user(username="demo", lookup=_lookup)

        assert resolved == user_id

    def test_missing_user_error_mentions_how_to_create(self) -> None:
        from evaluation.cli import resolve_demo_user

        async def _lookup(username: str) -> None:
            return None

        with pytest.raises(ValueError) as exc_info:
            resolve_demo_user(username="ghost", lookup=_lookup)

        message = str(exc_info.value)
        assert "ghost" in message
        assert "注册" in message or "创建" in message or "register" in message.lower()


class TestFixedAdapterUnchanged:
    def test_fixed_adapter_still_available(self) -> None:
        """CI 依赖 fixed 适配器，不能因为加 live 就改坏它。"""
        from evaluation.cli import build_executor

        executor = build_executor("fixed")

        assert executor.__class__.__name__ == "FixedAdapterExecutor"

    def test_unknown_adapter_still_errors(self) -> None:
        from evaluation.cli import build_executor

        with pytest.raises(SystemExit, match="未知适配器"):
            build_executor("nonexistent")
