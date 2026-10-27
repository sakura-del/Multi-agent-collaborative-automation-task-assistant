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

from typing import ClassVar

import pytest

from app.core.exceptions import LLMException
from app.llm.adapters import GLMAdapter, OpenAIAdapter, QwenAdapter
from app.llm.factory import LLMManager, create_llm, register_adapter
from app.llm.models import Message, MessageRole
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
            # ClassVar 明确表示这是类变量（测试夹具配置），而非实例的可变默认值
            fallback_chain: ClassVar[list] = [
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
            manager._instances[key] = MockLLM(responses=[], raise_on_call=timeout_exc)

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


class TestDeepSeekFallback:
    """主模型持续 5xx 时的保底通道（Plan 5 答辩保障）。

    实跑背景：model.rhzy.ai 端点返回 503，导致规划直接失败转人工；
    配置 fallback_chain 首项为 deepseek 后应能自动接管。
    """

    def test_deepseek_registered_as_openai_compatible(self) -> None:
        from app.llm.adapters import OpenAIAdapter
        from app.llm.factory import _ADAPTER_REGISTRY

        assert "deepseek" in _ADAPTER_REGISTRY
        assert _ADAPTER_REGISTRY["deepseek"] is OpenAIAdapter

    def test_config_has_deepseek_credentials_fields(self) -> None:
        from app.core.config import LLMConfig

        cfg = LLMConfig()
        assert cfg.deepseek_api_key is None
        assert cfg.deepseek_base_url == "https://api.deepseek.com/v1"

    def test_fallback_chain_first_entry_is_usable(self) -> None:
        """降级链首项必须是一个**实测可用**的模型，且链条非空。

        ## 为什么不再写死 deepseek（2026-10-27 改）

        原断言是「首项必须是 deepseek/deepseek-chat」。切到
        `workbuddy-ai` 主模型后首项改成同端点同族的 `-sg` 变体
        （与主模型共用 key，最不容易因 key 失效而整链失效），
        deepseek 退到第二位作兜底。

        **测试意图从来不是「首项必须是 deepseek」**，而是「首项得是真能用的东西」。
        写死具体厂商会让每次换模型都得改测试，而真正该守住的性质是：
        链条非空、每项都有 provider 与 model、**不含已知的占位 key 厂商**
        （glm/qwen 的 key 在 .env 里仍是 `your-xxx` 占位符，实测 401）。
        """
        from pathlib import Path

        import yaml

        path = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        chain = data["llm"]["fallback_chain"]

        assert chain, "降级链不应为空（否则主模型失败即整任务失败）"
        for item in chain:
            assert item.get("provider"), f"降级链项缺 provider: {item}"
            assert item.get("model"), f"降级链项缺 model: {item}"

        # 占位 key 的厂商不该出现在降级链里：每项会先浪费一次 401 才轮到下一个
        placeholders = {"glm", "qwen"}
        providers = {item["provider"] for item in chain}
        assert not (providers & placeholders), (
            f"降级链含占位 key 厂商 {providers & placeholders}；"
            "这些 key 在 .env 里仍是你 your-xxx 占位符，实测 401，配真 key 后再加回来"
        )

    def test_manager_initializes_deepseek_base_url(self) -> None:
        from app.core.config import LLMConfig
        from app.llm.factory import LLMManager

        cfg = LLMConfig(deepseek_api_key="sk-test", deepseek_base_url="https://api.deepseek.com/v1")
        manager = LLMManager(cfg)

        assert manager._api_keys["deepseek"] == "sk-test"
        assert manager._base_urls["deepseek"] == "https://api.deepseek.com/v1"
