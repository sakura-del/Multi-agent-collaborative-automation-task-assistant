"""Agent 能力画像系统：定义、注册与查询 Agent 能力标签。

职责：
- 为每个 Agent 定义能力标签（task_type / max_concurrency / avg_duration）
- 存入数据库 AGENT 表供调度器参考（负载均衡、任务路由）
- 运行时动态更新实际性能指标

设计要点：
- CapabilityRegistry 单例：全应用共享，避免重复注册（内存优化）
- 能力画像与 Agent 类解耦：Agent 类专注逻辑，画像供调度器消费（单一职责）
- avg_duration 随执行动态更新：指数移动平均，平滑异常值
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AgentCapability:
    """单个 Agent 的能力画像。

    字段对齐数据库 AGENT.capabilities JSON 列，便于持久化。
    """

    agent_type: str  # planner / executor / validator / reviewer
    name: str  # 显示名
    description: str = ""
    # 支持的任务类型列表
    task_types: list[str] = field(default_factory=list)
    # 最大并发数（调度器负载均衡参考）
    max_concurrency: int = 1
    # 平均处理时长（秒），随执行动态更新
    avg_duration_s: float = 0.0
    # 当前并发负载
    current_load: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典，对齐数据库 JSON 列结构。"""
        return {
            "task_types": self.task_types,
            "max_concurrency": self.max_concurrency,
            "avg_duration_s": round(self.avg_duration_s, 3),
            "current_load": self.current_load,
        }

    def update_duration(self, duration_s: float, alpha: float = 0.3) -> None:
        """用指数移动平均更新平均时长。

        alpha 越大越侧重近期数据，0.3 为经验值，平滑短期波动。
        """
        if self.avg_duration_s == 0.0:
            self.avg_duration_s = duration_s
        else:
            self.avg_duration_s = alpha * duration_s + (1 - alpha) * self.avg_duration_s


class CapabilityRegistry:
    """能力画像注册中心：管理所有 Agent 的能力标签。

    单例模式，全应用共享。调度器通过此注册表查询 Agent 能力做路由决策。
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, AgentCapability] = {}

    def register(self, capability: AgentCapability) -> None:
        """注册或更新 Agent 能力画像。"""
        self._capabilities[capability.agent_type] = capability
        logger.info(
            "capability_registered",
            agent_type=capability.agent_type,
            task_types=capability.task_types,
        )

    def get(self, agent_type: str) -> AgentCapability | None:
        """查询指定 Agent 的能力画像。"""
        return self._capabilities.get(agent_type)

    def all(self) -> list[AgentCapability]:
        """返回所有已注册的能力画像。"""
        return list(self._capabilities.values())

    def find_by_task_type(self, task_type: str) -> list[AgentCapability]:
        """按任务类型查找可用 Agent（供调度器路由）。

        仅返回支持该 task_type 且未满载的 Agent。
        """
        return [
            cap
            for cap in self._capabilities.values()
            if task_type in cap.task_types and cap.current_load < cap.max_concurrency
        ]

    def to_persist_dict(self) -> dict[str, dict[str, Any]]:
        """转换为可持久化结构（agent_type → capability dict），供批量入库。"""
        return {at: cap.to_dict() for at, cap in self._capabilities.items()}


# ===== 默认能力画像：四大 Agent 的开箱即用配置 =====

_DEFAULT_CAPABILITIES: list[AgentCapability] = [
    AgentCapability(
        agent_type="planner",
        name="规划 Agent",
        description="需求解析、任务拆解、DAG 生成",
        task_types=["planning", "decomposition"],
        max_concurrency=2,
    ),
    AgentCapability(
        agent_type="executor",
        name="执行 Agent",
        description="子任务执行、LLM 推理、工具调用",
        task_types=["execution", "reasoning", "tool_call"],
        max_concurrency=5,
    ),
    AgentCapability(
        agent_type="validator",
        name="校验 Agent",
        description="规则校验、语义自检、质量评分",
        task_types=["validation", "quality_check"],
        max_concurrency=3,
    ),
    AgentCapability(
        agent_type="reviewer",
        name="复盘 Agent",
        description="日志收集、错误归因、性能统计",
        task_types=["review", "attribution", "statistics"],
        max_concurrency=2,
    ),
]


# 模块级单例：全应用共享一个注册中心
_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    """获取能力画像注册中心单例。

    首次调用时初始化并注册四大 Agent 的默认画像。
    """
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        for cap in _DEFAULT_CAPABILITIES:
            _registry.register(cap)
        logger.info("capability_registry_initialized")
    return _registry
