"""RuntimeContainer 依赖注入测试。

回归背景（两次实跑发现）：
1. Plan 3 Exit Gate：容器虽然在 start() 中初始化了 LLMManager，但默认 Agent 工厂
   一直是 `ExecutorAgent()` 这类无 LLM 实例，导致真实任务**静默降级**
   （0 次 LLM 调用、输出「[降级输出] …」）。
2. Plan 5 Task 8：修完上一条后注入的是 `manager.get_primary()`——单个适配器实例，
   主模型 503 时**不会**触发 fallback_chain，任务直接转人工。

这里锁定：容器给 Agent 的必须是**带降级能力**的客户端（`FallbackChatClient`）。
"""

from __future__ import annotations


class _StubManager:
    """最小 LLMManager 替身：只需要主模型元信息供包装器展示。"""

    def __init__(self) -> None:
        self._primary = ("stub-provider", "stub-model")
        self.calls: list[object] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return "stub-response"

    def stream_chat(self, messages, **kwargs):
        return iter(())


class TestDefaultAgentLlmInjection:
    def test_agents_receive_fallback_capable_client(self) -> None:
        from app.llm.factory import FallbackChatClient
        from app.orchestration.runtime import RuntimeContainer

        container = RuntimeContainer()
        stub = _StubManager()
        container.llm_manager = stub

        for agent_type in ("executor", "validator", "reviewer"):
            agent = container.create_agent(agent_type)
            assert isinstance(agent.llm, FallbackChatClient), f"{agent_type} 未注入降级客户端"
            assert agent.llm.model == "stub-model"

        planner = container.create_planner()
        assert isinstance(planner.llm, FallbackChatClient)

    async def test_client_chat_routes_through_manager(self) -> None:
        """`agent.llm.chat()` 必须走 manager.chat()，这样才有 fallback_chain。"""
        from app.orchestration.runtime import RuntimeContainer

        container = RuntimeContainer()
        stub = _StubManager()
        container.llm_manager = stub
        client = container.create_agent("executor").llm

        result = await client.chat([{"role": "user", "content": "ping"}])

        assert result == "stub-response"
        assert stub.calls, "未经过 manager.chat（降级链不会生效）"

    def test_falls_back_to_degraded_agents_without_manager(self) -> None:
        from app.orchestration.runtime import RuntimeContainer

        container = RuntimeContainer()
        container.llm_manager = None
        # 阻止 _llm_client 去读全局单例
        container._injected_llm = None

        class _Broken:
            def __getattr__(self, name):
                raise RuntimeError("no llm configured")

        container.llm_manager = _Broken()
        agent = container.create_agent("executor")
        # 构造包装器本身不会失败；失败发生在真正调用 chat 时
        assert agent.llm is not None
