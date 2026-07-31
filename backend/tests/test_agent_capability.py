"""Agent 能力画像系统单元测试。"""

from app.orchestration.capability import (
    AgentCapability,
    CapabilityRegistry,
    get_capability_registry,
)


class TestAgentCapability:
    """能力画像数据结构测试。"""

    def test_to_dict_aligns_db_schema(self) -> None:
        """to_dict 结构对齐数据库 JSON 列。"""
        cap = AgentCapability(
            agent_type="planner",
            name="规划 Agent",
            task_types=["planning"],
            max_concurrency=2,
            avg_duration_s=1.5,
        )
        data = cap.to_dict()
        assert "task_types" in data
        assert "max_concurrency" in data
        assert "avg_duration_s" in data
        assert "current_load" in data

    def test_update_duration_ema(self) -> None:
        """指数移动平均更新平均时长。"""
        cap = AgentCapability(agent_type="executor", name="执行", max_concurrency=3)
        # 首次更新直接赋值
        cap.update_duration(10.0)
        assert cap.avg_duration_s == 10.0
        # 第二次按 EMA：0.3 * 20 + 0.7 * 10 = 13
        cap.update_duration(20.0, alpha=0.3)
        assert abs(cap.avg_duration_s - 13.0) < 0.01


class TestCapabilityRegistry:
    """注册中心测试。"""

    def test_register_and_get(self) -> None:
        """注册后可查询。"""
        registry = CapabilityRegistry()
        cap = AgentCapability(
            agent_type="custom",
            name="自定义 Agent",
            task_types=["custom_task"],
            max_concurrency=1,
        )
        registry.register(cap)
        assert registry.get("custom") is cap
        assert registry.get("nonexistent") is None

    def test_find_by_task_type_filters_load(self) -> None:
        """按 task_type 查找时排除满载 Agent。"""
        registry = CapabilityRegistry()
        registry.register(
            AgentCapability(
                agent_type="a1",
                name="A1",
                task_types=["reasoning"],
                max_concurrency=2,
                current_load=2,  # 已满载
            )
        )
        registry.register(
            AgentCapability(
                agent_type="a2",
                name="A2",
                task_types=["reasoning"],
                max_concurrency=3,
                current_load=1,  # 可用
            )
        )
        available = registry.find_by_task_type("reasoning")
        assert len(available) == 1
        assert available[0].agent_type == "a2"

    def test_find_by_task_type_no_match(self) -> None:
        """无匹配 task_type 返回空列表。"""
        registry = CapabilityRegistry()
        registry.register(AgentCapability(agent_type="a", name="A", task_types=["planning"]))
        assert registry.find_by_task_type("nonexistent") == []

    def test_to_persist_dict(self) -> None:
        """批量持久化结构正确。"""
        registry = CapabilityRegistry()
        registry.register(AgentCapability(agent_type="a", name="A", task_types=["t1"]))
        data = registry.to_persist_dict()
        assert "a" in data
        assert "task_types" in data["a"]


class TestDefaultRegistry:
    """默认注册中心单例测试。"""

    def test_singleton_has_four_agents(self) -> None:
        """单例注册中心包含四大 Agent 默认画像。"""
        registry = get_capability_registry()
        all_caps = registry.all()
        agent_types = {cap.agent_type for cap in all_caps}
        assert agent_types == {"planner", "executor", "validator", "reviewer"}

    def test_executor_supports_tool_call(self) -> None:
        """执行 Agent 默认支持 tool_call 任务类型。"""
        registry = get_capability_registry()
        cap = registry.get("executor")
        assert cap is not None
        assert "tool_call" in cap.task_types
