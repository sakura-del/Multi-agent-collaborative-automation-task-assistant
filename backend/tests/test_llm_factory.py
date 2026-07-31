"""LLM 热切换与降级机制测试。

覆盖：
- create_llm 工厂函数
- register_adapter 自定义适配器注册
- LLMManager 配置加载与实例缓存
- 运行时切换主模型
- 自动降级：主模型失败 → 备用模型成功
- 不可恢复异常不降级
- 全部模型失败抛 LLMException
- 流式调用不降级
"""

from __future__ import annotations

import pytest

from app.core.exceptions import LLMException
from app.llm.adapters import GLMAdapter, OpenAIAdapter, QwenAdapter
from app.llm.base import BaseLLM
from app.llm.factory import LLMManager, create_llm, register_adapter
from app.llm.models import LLMResponse, Message, MessageRole, Usage
from tests.llm.mock_llm import MockLLM, make_text_response


class TestCreateLLM:
    """工厂函数测试。"""

    def test_create_openai(self):
        llm = create_llm("openai", "gpt-4o", api_key="sk-x")
        assert isinstance(llm, OpenAIAdapter)

    def test_create_glm(self):
        llm = create_llm("glm", "glm-4", api_key="glm-x")
        assert isinstance(llm, GLMAdapter)

    def test_create_qwen(self):
        llm = create_llm("qwen", "qwen-turbo", api_key="qwen-x")
        assert isinstance(llm, QwenAdapter)

    def test_create_unknown_provider_raises(self):
        with pytest.raises(LLMException):
            create_llm("unknown", "x")


class TestRegisterAdapter:
    """自定义适配器注册测试。"""

    def test_register_custom_adapter(self):
        """register_adapter 扩展厂商注册表。"""

        class CustomAdapter(MockLLM):
            def __init__(self, model: str = "custom", **kwargs):
                super().__init__(model=model, provider="custom", **kwargs)

        register_adapter("custom", CustomAdapter)
        llm = create_llm("custom", "custom-1.0", api_key="x")
        assert isinstance(llm, CustomAdapter)


class TestLLMManagerConfig:
    """LLMManager 配置加载测试。"""

    def test_apply_config_sets_primary_and_fallback(self):
        """_apply_config 正确读取主模型与降级链。"""

        class _MockConfig:
            default_provider = "openai"
            default_model = "gpt-4o-mini"
            request_timeout = 30
            max_retries = 3
            openai_api_key = "sk-1"
            openai_base_url = "https://api.openai.com/v1"
            glm_api_key = "glm-1"
            glm_base_url = "https://open.bigmodel.cn/api/paas/v4"
            qwen_api_key = "qwen-1"
            qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            fallback_chain = [
                type("Item", (), {"provider": "glm", "model": "glm-4-flash"})(),
                type("Item", (), {"provider": "qwen", "model": "qwen-turbo"})(),
            ]

        manager = LLMManager(_MockConfig())
        assert manager._primary == ("openai", "gpt-4o-mini")
        # 主模型应从 fallback_chain 中移除
        assert ("openai", "gpt-4o-mini") not in manager._fallback_chain
        assert ("glm", "glm-4-flash") in manager._fallback_chain

    def test_empty_config_defaults(self):
        """无配置时主模型为 None。"""
        manager = LLMManager(None)
        assert manager._primary is None

    def test_create_default_initializes_primary(self):
        """create_default 注入 Key 并预创建主模型。"""
        manager = LLMManager(None)
        manager._primary = ("openai", "gpt-4o-mini")
        manager.create_default(openai_api_key="sk-test")
        # 主模型实例已创建
        assert ("openai", "gpt-4o-mini") in manager._instances


class TestLLMManagerSwitchPrimary:
    """运行时切换主模型测试。"""

    def test_switch_primary(self):
        manager = LLMManager(None)
        manager._primary = ("openai", "gpt-4o")
        manager._fallback_chain = [("glm", "glm-4"), ("openai", "gpt-4o")]
        manager.switch_primary("glm", "glm-4")
        assert manager._primary == ("glm", "glm-4")
        # 切换后从降级链移除新主模型
        assert ("glm", "glm-4") not in manager._fallback_chain

    def test_get_primary_no_config_raises(self):
        manager = LLMManager(None)
        with pytest.raises(LLMException):
            manager.get_primary()


class TestLLMManagerChat:
    """LLMManager.chat 自动降级测试。"""

    @pytest.mark.asyncio
    async def test_primary_success_no_fallback(self):
        """主模型成功时不触发降级。"""
        manager = LLMManager(None)
        manager._primary = ("mock", "mock-1")
        # 直接注入 MockLLM 实例
        mock = MockLLM(responses=[make_text_response("ok")])
        manager._instances[("mock", "mock-1")] = mock
        result = await manager.chat([Message(role=MessageRole.USER, content="q")])
        assert result.content == "ok"
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_on_recoverable_error(self):
        """主模型可恢复异常时降级到备用模型。"""
        manager = LLMManager(None)
        manager._primary = ("mock", "primary")
        manager._fallback_chain = [("mock", "backup")]

        # 主模型抛超时异常，备用模型成功
        primary = MockLLM(
            responses=[],
            raise_on_call=type("APITimeoutError", (Exception,), {})("timeout"),
        )
        backup = MockLLM(responses=[make_text_response("from backup")])
        manager._instances[("mock", "primary")] = primary
        manager._instances[("mock", "backup")] = backup

        result = await manager.chat([Message(role=MessageRole.USER, content="q")])
        assert result.content == "from backup"
        # 主模型与备用模型各调用1次
        assert primary.call_count == 1
        assert backup.call_count == 1

    @pytest.mark.asyncio
    async def test_no_fallback_on_non_recoverable_error(self):
        """不可恢复异常（鉴权失败）不降级，直接抛出。"""
        manager = LLMManager(None)
        manager._primary = ("mock", "primary")
        manager._fallback_chain = [("mock", "backup")]

        primary = MockLLM(
            responses=[],
            raise_on_call=type("AuthenticationError", (Exception,), {})("401"),
        )
        backup = MockLLM(responses=[make_text_response("backup ok")])
        manager._instances[("mock", "primary")] = primary
        manager._instances[("mock", "backup")] = backup

        with pytest.raises(Exception) as exc_info:
            await manager.chat([Message(role=MessageRole.USER, content="q")])
        assert "401" in str(exc_info.value)
        # 备用模型未被调用
        assert backup.call_count == 0

    @pytest.mark.asyncio
    async def test_all_models_fail_raises(self):
        """所有模型均失败时抛 LLMException。"""
        manager = LLMManager(None)
        manager._primary = ("mock", "p")
        manager._fallback_chain = [("mock", "b1"), ("mock", "b2")]

        timeout_exc = type("APITimeoutError", (Exception,), {})("timeout")
        for key in [("mock", "p"), ("mock", "b1"), ("mock", "b2")]:
            manager._instances[key] = MockLLM(
                responses=[], raise_on_call=timeout_exc
            )

        with pytest.raises(LLMException) as exc_info:
            await manager.chat([Message(role=MessageRole.USER, content="q")])
        assert "所有 LLM 模型均调用失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_chat_without_primary_raises(self):
        """未配置主模型时 chat 抛异常。"""
        manager = LLMManager(None)
        with pytest.raises(LLMException):
            await manager.chat([Message(role=MessageRole.USER, content="q")])

    @pytest.mark.asyncio
    async def test_instance_cached(self):
        """同 key 复用实例（缓存机制）。"""
        manager = LLMManager(None)
        manager._primary = ("openai", "gpt-4o")
        manager._api_keys["openai"] = "sk-test"
        manager._base_urls["openai"] = "https://api.openai.com/v1"
        llm1 = manager._get_or_create("openai", "gpt-4o")
        llm2 = manager._get_or_create("openai", "gpt-4o")
        assert llm1 is llm2  # 同一实例


class TestLLMManagerStream:
    """流式调用测试（不降级）。"""

    @pytest.mark.asyncio
    async def test_stream_chat_uses_primary_only(self):
        manager = LLMManager(None)
        manager._primary = ("mock", "p")
        mock = MockLLM(stream_chunks=["chunk1", "chunk2", "chunk3"])
        manager._instances[("mock", "p")] = mock

        chunks = []
        async for c in manager.stream_chat([Message(role=MessageRole.USER, content="q")]):
            chunks.append(c)
        assert chunks == ["chunk1", "chunk2", "chunk3"]
