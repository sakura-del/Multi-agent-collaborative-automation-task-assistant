"""节点级模型路由（混合处理，不单靠一个 API）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。

覆盖：
1. 路由表解析：命中/未命中/目标缺失；
2. 客户端按配置构建（provider/model/base_url）；
3. BaseLLM.chat 的委派：命中路由时转给目标客户端，未命中走自身；
4. 委派不自递归（目标客户端再进入时 target is self）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.llm import router as router_mod
from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, Usage
from app.llm.router import ModelRouter, node_type_scope


class _Recorder(BaseLLM):
    """记录 chat 调用的假客户端。"""

    def __init__(self, tag: str) -> None:
        super().__init__(f"model-{tag}", provider="fake", max_retries=1)
        self.tag = tag
        self.calls = 0

    def _create_client(self) -> Any:  # pragma: no cover
        return None

    async def _raw_chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=f"from-{self.tag}", model=self.model, usage=Usage())

    async def stream_chat(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield ""  # pragma: no cover

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class TestRouterResolution:
    def test_hit_builds_client_from_targets(self) -> None:
        router = ModelRouter(
            routing={"extract": "local"},
            targets={
                "local": {
                    "provider": "custom",
                    "model": "qwen3.8-27b-uncensored-fp8",
                    "base_url": "https://model.rhdata.cn/v1",
                    "api_key": "sk-test",
                }
            },
        )
        client = router.resolve("extract")
        assert client is not None
        assert client.model == "qwen3.8-27b-uncensored-fp8"

    def test_unrouted_node_returns_none(self) -> None:
        router = ModelRouter(routing={"extract": "local"}, targets={})
        assert router.resolve("write") is None

    def test_missing_target_degrades_to_none(self) -> None:
        router = ModelRouter(routing={"extract": "ghost"}, targets={})
        assert router.resolve("extract") is None

    def test_target_without_model_degrades(self) -> None:
        router = ModelRouter(routing={"extract": "x"}, targets={"x": {"provider": "custom"}})
        assert router.resolve("extract") is None


class TestBaseLLMDelegation:
    @pytest.mark.asyncio
    async def test_chat_delegates_when_routed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        default_client = _Recorder("default")
        routed_client = _Recorder("local")
        router = ModelRouter(routing={"extract": "local"}, targets={})
        router._clients["local"] = routed_client
        monkeypatch.setattr(router_mod, "_router", router)
        monkeypatch.setattr("app.llm.base.get_model_router", lambda: router)

        with node_type_scope("extract"):
            response = await default_client.chat([Message(role="user", content="hi")])

        assert response.content == "from-local"
        assert routed_client.calls == 1
        assert default_client.calls == 0

    @pytest.mark.asyncio
    async def test_chat_uses_self_when_not_routed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        default_client = _Recorder("default")
        router = ModelRouter(routing={"extract": "local"}, targets={})
        monkeypatch.setattr("app.llm.base.get_model_router", lambda: router)

        response = await default_client.chat([Message(role="user", content="hi")])
        assert response.content == "from-default"

    @pytest.mark.asyncio
    async def test_scope_resets_after_exit(self) -> None:
        from app.llm.router import current_node_type

        with node_type_scope("write"):
            assert current_node_type() == "write"
        assert current_node_type() == ""
