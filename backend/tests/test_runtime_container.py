"""RuntimeContainer 依赖注入测试。

回归背景（Plan 3 Exit Gate 实跑发现）：容器虽然在 start() 中初始化了 LLMManager，
但默认 Agent 工厂一直是 `ExecutorAgent()` 这类无 LLM 实例，导致真实任务**静默降级**
（0 次 LLM 调用、输出「[降级输出] …」）。这里锁定：容器创建的四类 Agent 必须拿到 LLM。
"""

from __future__ import annotations


class _StubManager:
    """最小 LLMManager 替身：只实现 get_primary。"""

    def __init__(self) -> None:
        self.client = object()

    def get_primary(self):
        return self.client


class TestDefaultAgentLlmInjection:
    def test_agents_receive_container_llm(self) -> None:
        from app.orchestration.runtime import RuntimeContainer

        container = RuntimeContainer()
        stub = _StubManager()
        container.llm_manager = stub

        for agent_type in ("executor", "validator", "reviewer"):
            agent = container.create_agent(agent_type)
            assert agent.llm is stub.client, f"{agent_type} 未注入 LLM"

        planner = container.create_planner()
        assert planner.llm is stub.client

    def test_falls_back_to_degraded_agents_without_llm(self) -> None:
        from app.orchestration.runtime import RuntimeContainer

        class _FailingManager:
            def get_primary(self):
                raise RuntimeError("no llm configured")

        container = RuntimeContainer()
        container.llm_manager = _FailingManager()

        # 缺 LLM 时仍可构造（降级路径），但会记录一次告警
        assert container.create_agent("executor").llm is None
