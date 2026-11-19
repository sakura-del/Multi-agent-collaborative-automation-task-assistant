"""路由委派环回归（2026-11-19 生产 RecursionError）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。

事故链：extract 节点路由到 BYOKClient（包装类，内部持有 BaseLLM adapter）
→ BYOKClient.chat 调 adapter.chat → adapter 处于同一 node_type scope
→ BaseLLM.chat 再次命中路由 → 又委派回同一个 BYOKClient → 无限递归
（生产实证：同一毫秒刷出几十条 llm_routed，栈深耗尽）。

修复：委派调用包 `suppress_routing()`——委派目标内部不再二次路由。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, Usage
from app.llm.router import build_router_from_settings, node_type_scope
from app.llm.scope import (
    bind_user_routes,
    routing_suppressed,
    suppress_routing,
)


class _EchoClient(BaseLLM):
    """记录 chat 调用次数的最小客户端。"""

    def __init__(self, label: str) -> None:
        super().__init__(label, provider="fake", max_retries=1)
        self.label = label
        self.calls = 0

    def _create_client(self) -> Any:
        return None

    async def _raw_chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self.label, model=self.model, usage=Usage())

    async def stream_chat(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield ""  # pragma: no cover

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]  # pragma: no cover


class TestRoutingSuppression:
    def test_suppress_flag_scoped(self) -> None:
        assert routing_suppressed() is False
        with suppress_routing():
            assert routing_suppressed() is True
        assert routing_suppressed() is False

    def test_router_returns_none_when_suppressed(self) -> None:

        router = build_router_from_settings()
        with suppress_routing():
            assert router.resolve("extract") is None


class TestDelegationNoLoop:
    async def test_wrapped_target_does_not_recurse(self) -> None:
        """包装类目标内部再调 BaseLLM.chat：屏蔽后只委派一层，不无限递归。

        复现链（修复前）：outer.chat(scope=extract) → resolve→inner（包装类）
        → inner.chat → adapter.chat（BaseLLM，scope 仍在）→ resolve→inner → …
        """
        adapter = _EchoClient("adapter")
        delegations = {"count": 0}

        class Wrapper:
            """模拟 BYOKClient：包装类，内部持有 BaseLLM adapter。"""

            model = "wrapped"

            async def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
                delegations["count"] += 1
                if delegations["count"] > 3:
                    raise RuntimeError("delegation loop (should not happen)")
                return await adapter.chat(*args, **kwargs)

        wrapper = Wrapper()
        # 用户路由（与生产 30f6593c 场景一致）：resolve 命中 → 委派 wrapper
        with node_type_scope("extract"):
            bind_user_routes({"extract": wrapper})
            response = await adapter.chat([Message(role="user", content="hi")])

        assert response.content == "adapter"
        assert delegations["count"] == 1, "委派必须只有一层"
        assert adapter.calls == 1


