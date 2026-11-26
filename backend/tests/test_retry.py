"""自动重试机制单元测试：退避、策略切换、耗尽触发人工干预。"""

import asyncio

import pytest

from app.orchestration.retry import RetryExecutor, RetryPolicy
from app.orchestration.task_state import SubtaskStatus, TaskStateMachine


class TestRetryPolicy:
    """重试策略配置测试。"""

    def test_default_backoff_sequence(self) -> None:
        """默认退避序列为 1s/3s/9s。"""
        policy = RetryPolicy()
        assert policy.get_backoff(0) == 1.0
        assert policy.get_backoff(1) == 3.0
        assert policy.get_backoff(2) == 9.0

    def test_default_max_retries(self) -> None:
        """默认最多重试 3 次。"""
        assert RetryPolicy().max_retries == 3

    def test_strategy_changes_per_attempt(self) -> None:
        """每次重试策略不同。"""
        policy = RetryPolicy()
        s0 = policy.get_strategy(0)
        s1 = policy.get_strategy(1)
        s2 = policy.get_strategy(2)
        assert s0["strategy"] != s1["strategy"]
        assert s1["strategy"] != s2["strategy"]

    def test_backoff_out_of_range_returns_last(self) -> None:
        """超出范围的退避时间用最后一个值。"""
        policy = RetryPolicy()
        assert policy.get_backoff(99) == 9.0


@pytest.mark.asyncio
class TestRetryExecutor:
    """重试执行器测试。"""

    async def test_success_on_first_attempt(self) -> None:
        """首次成功不重试。"""
        call_count = 0

        async def func() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=3))
        result = await executor.execute(func)
        assert result.success
        assert result.attempts == 1
        assert call_count == 1

    async def test_success_after_retry(self) -> None:
        """首次失败，第二次成功。"""
        call_count = 0

        async def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("首次失败")
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=3))
        result = await executor.execute(func)
        assert result.success
        assert result.attempts == 2
        assert call_count == 2

    async def test_retry_exhausted_triggers_waiting_human(self) -> None:
        """重试耗尽后状态转为 WAITING_HUMAN。"""
        call_count = 0

        async def func() -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"第{call_count}次失败")

        sm = TaskStateMachine(node_id="n1")
        executor = RetryExecutor(RetryPolicy(max_retries=2, backoff_times=[0.01, 0.01]))
        result = await executor.execute(func, state_machine=sm)

        assert not result.success
        assert result.attempts == 3  # 首次 + 2 次重试
        assert result.final_status is SubtaskStatus.WAITING_HUMAN
        assert sm.status is SubtaskStatus.WAITING_HUMAN

    async def test_retry_injects_strategy_params(self) -> None:
        """重试时注入策略参数到 kwargs。"""
        received_temps: list[float | None] = []

        async def func(**kwargs: object) -> str:
            received_temps.append(kwargs.get("temperature"))  # type: ignore[arg-type]
            if len(received_temps) == 1:
                raise RuntimeError("首次失败")
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=3))
        await executor.execute(func)
        # 首次无 temperature 注入，重试有
        assert received_temps[0] is None
        assert received_temps[1] is not None

    async def test_retry_respects_max_retries(self) -> None:
        """重试次数不超过 max_retries。"""
        call_count = 0

        async def func() -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("总是失败")

        executor = RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01]))
        result = await executor.execute(func)
        assert call_count == 2  # 首次 + 1 次重试
        assert not result.success

    async def test_backoff_delay_applied(self) -> None:
        """重试前有退避等待。"""
        call_times: list[float] = []

        async def func() -> str:
            call_times.append(asyncio.get_event_loop().time())
            if len(call_times) == 1:
                raise RuntimeError("首次失败")
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.1]))
        await executor.execute(func)
        # 第二次调用与第一次间隔至少 0.1s
        assert call_times[1] - call_times[0] >= 0.09

    async def test_on_transition_metadata_signatures(self) -> None:
        """on_transition 应根据回调签名精确适配 2 参、位置参、KEYWORD_ONLY、**kwargs 与 *args。"""
        from typing import Any

        async def always_fail_then_succeed_factory() -> Any:
            calls = 0

            async def _fn() -> str:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("首次失败触发重试")
                return "ok"

            return _fn

        # 1. 仅 2 参：不传递 metadata，不抛 TypeError
        seen_2arg: list[tuple[SubtaskStatus, SubtaskStatus]] = []

        async def cb_two(old: SubtaskStatus, new: SubtaskStatus) -> None:
            seen_2arg.append((old, new))

        res_2 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            await always_fail_then_succeed_factory(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_two,
        )
        assert res_2.success
        assert (SubtaskStatus.FAILED, SubtaskStatus.RETRYING) in seen_2arg

        # 2. 第 3 个位置参数名非 metadata（POSITIONAL_OR_KEYWORD）：通过位置参数传入 retry_meta
        seen_pos_meta: list[Any] = []

        async def cb_pos(old: SubtaskStatus, new: SubtaskStatus, meta: Any = None) -> None:
            if new is SubtaskStatus.RETRYING:
                seen_pos_meta.append(meta)

        res_pos = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            await always_fail_then_succeed_factory(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_pos,
        )
        assert res_pos.success
        assert len(seen_pos_meta) == 1
        assert isinstance(seen_pos_meta[0], dict)
        assert seen_pos_meta[0].get("stage") == "node_retry"

        # 3. KEYWORD_ONLY `*, metadata=None`：必须走关键字传递，修复前会触发 TypeError
        seen_kw_only: list[Any] = []

        async def cb_kw_only(
            old: SubtaskStatus, new: SubtaskStatus, *, metadata: Any = None
        ) -> None:
            if new is SubtaskStatus.RETRYING:
                seen_kw_only.append(metadata)

        res_kw = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            await always_fail_then_succeed_factory(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_kw_only,
        )
        assert res_kw.success
        assert len(seen_kw_only) == 1
        assert isinstance(seen_kw_only[0], dict)
        assert seen_kw_only[0].get("stage") == "node_retry"

        # 4. VAR_KEYWORD `**kwargs`：必须收到 kwargs["metadata"]
        seen_var_kw: list[Any] = []

        async def cb_var_kw(old: SubtaskStatus, new: SubtaskStatus, **kwargs: Any) -> None:
            if new is SubtaskStatus.RETRYING:
                seen_var_kw.append(kwargs.get("metadata"))

        res_vkw = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            await always_fail_then_succeed_factory(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_var_kw,
        )
        assert res_vkw.success
        assert len(seen_var_kw) == 1
        assert isinstance(seen_var_kw[0], dict)
        assert seen_var_kw[0].get("stage") == "node_retry"

        # 5. VAR_POSITIONAL `*args`：必须收到 args[0]
        seen_var_pos: list[Any] = []

        async def cb_var_pos(old: SubtaskStatus, new: SubtaskStatus, *args: Any) -> None:
            if new is SubtaskStatus.RETRYING:
                seen_var_pos.append(args[0] if args else None)

        res_vp = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            await always_fail_then_succeed_factory(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_var_pos,
        )
        assert res_vp.success
        assert len(seen_var_pos) == 1
        assert isinstance(seen_var_pos[0], dict)
        assert seen_var_pos[0].get("stage") == "node_retry"

    async def test_on_transition_metadata_edge_cases(self) -> None:
        """非重试转换传 metadata=None 不崩溃，且排除前 2 槽名碰撞与第 4 槽 POSITIONAL_ONLY 错位（C4）。"""
        from functools import partial
        from typing import Any

        def make_fail_once() -> Any:
            calls = 0

            async def _fn() -> str:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("首次失败触发重试")
                return "ok"

            return _fn

        # 1. POSITIONAL_ONLY 第 3 参（无默认值，名 meta）：非重试转换传 None，RETRYING 传 dict
        seen_pos_only_meta: list[tuple[SubtaskStatus, SubtaskStatus, Any]] = []

        async def cb_pos_only_meta(old: SubtaskStatus, new: SubtaskStatus, meta: Any, /) -> None:
            seen_pos_only_meta.append((old, new, meta))

        res_1 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_pos_only_meta,
        )
        assert res_1.success
        assert any(
            new is SubtaskStatus.READY and meta is None for _, new, meta in seen_pos_only_meta
        )
        assert any(
            new is SubtaskStatus.RETRYING
            and isinstance(meta, dict)
            and meta.get("stage") == "node_retry"
            for _, new, meta in seen_pos_only_meta
        )

        # 2. POSITIONAL_ONLY 第 3 参（无默认值，名 metadata）
        seen_pos_only_metadata: list[tuple[SubtaskStatus, SubtaskStatus, Any]] = []

        async def cb_pos_only_metadata(
            old: SubtaskStatus, new: SubtaskStatus, metadata: Any, /
        ) -> None:
            seen_pos_only_metadata.append((old, new, metadata))

        res_2 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_pos_only_metadata,
        )
        assert res_2.success
        assert any(
            new is SubtaskStatus.READY and meta is None for _, new, meta in seen_pos_only_metadata
        )
        assert any(
            new is SubtaskStatus.RETRYING and isinstance(meta, dict)
            for _, new, meta in seen_pos_only_metadata
        )

        # 3. KEYWORD_ONLY 无默认值 `*, metadata`：非重试转换必须传 metadata=None
        seen_kw_required: list[tuple[SubtaskStatus, SubtaskStatus, Any]] = []

        async def cb_kw_required(old: SubtaskStatus, new: SubtaskStatus, *, metadata: Any) -> None:
            seen_kw_required.append((old, new, metadata))

        res_3 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_kw_required,
        )
        assert res_3.success
        assert any(new is SubtaskStatus.READY and meta is None for _, new, meta in seen_kw_required)
        assert any(
            new is SubtaskStatus.RETRYING and isinstance(meta, dict)
            for _, new, meta in seen_kw_required
        )

        # 4. 前 2 槽名碰撞 `cb(old, metadata, new=None)` 与 `cb(old, metadata, **kwargs)`：不得报 multiple values
        seen_2nd_collision: list[tuple[SubtaskStatus, SubtaskStatus, Any]] = []

        async def cb_2nd_collision(
            old: SubtaskStatus, metadata: SubtaskStatus, new: Any = None
        ) -> None:
            seen_2nd_collision.append((old, metadata, new))

        res_4 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_2nd_collision,
        )
        assert res_4.success
        assert all(third is None for _, _, third in seen_2nd_collision)

        seen_2nd_kw: list[tuple[SubtaskStatus, SubtaskStatus, dict[str, Any]]] = []

        async def cb_2nd_kw(old: SubtaskStatus, metadata: SubtaskStatus, **kwargs: Any) -> None:
            seen_2nd_kw.append((old, metadata, kwargs))

        res_4b = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_2nd_kw,
        )
        assert res_4b.success
        assert all(kw == {} for _, _, kw in seen_2nd_kw)

        # 5. 第 4 槽 POSITIONAL_ONLY 且第 3 槽有默认值：不得把 metadata 错位传给 other
        seen_pos_only_4th: list[tuple[Any, Any]] = []

        async def cb_pos_only_4th(
            old: SubtaskStatus,
            new: SubtaskStatus,
            other: Any = "x",
            metadata: Any = None,
            /,
        ) -> None:
            seen_pos_only_4th.append((other, metadata))

        res_5 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=cb_pos_only_4th,
        )
        assert res_5.success
        assert all(other == "x" and meta is None for other, meta in seen_pos_only_4th)

        # 6. functools.partial 与可调用类实例（__call__）
        seen_partial: list[tuple[str, SubtaskStatus, Any]] = []

        async def cb_with_ctx(
            ctx: str, old: SubtaskStatus, new: SubtaskStatus, *, metadata: Any
        ) -> None:
            if new is SubtaskStatus.RETRYING:
                seen_partial.append((ctx, new, metadata))

        res_6 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=partial(cb_with_ctx, "CTX"),
        )
        assert res_6.success
        assert len(seen_partial) == 1
        assert seen_partial[0][0] == "CTX"
        assert isinstance(seen_partial[0][2], dict)

        class CallableObsKw:
            def __init__(self) -> None:
                self.items: list[tuple[SubtaskStatus, Any]] = []

            async def __call__(
                self, old: SubtaskStatus, new: SubtaskStatus, *, metadata: Any
            ) -> None:
                self.items.append((new, metadata))

        obs_kw = CallableObsKw()
        res_7 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=obs_kw,
        )
        assert res_7.success
        assert any(new is SubtaskStatus.READY and meta is None for new, meta in obs_kw.items)
        assert any(
            new is SubtaskStatus.RETRYING and isinstance(meta, dict) for new, meta in obs_kw.items
        )

        class CallableObsPosOnly:
            def __init__(self) -> None:
                self.items: list[tuple[SubtaskStatus, Any]] = []

            async def __call__(self, old: SubtaskStatus, new: SubtaskStatus, meta: Any, /) -> None:
                self.items.append((new, meta))

        obs_pos = CallableObsPosOnly()
        res_8 = await RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01])).execute(
            make_fail_once(),
            state_machine=TaskStateMachine(node_id="n1"),
            on_transition=obs_pos,
        )
        assert res_8.success
        assert any(new is SubtaskStatus.READY and meta is None for new, meta in obs_pos.items)
        assert any(
            new is SubtaskStatus.RETRYING and isinstance(meta, dict) for new, meta in obs_pos.items
        )
