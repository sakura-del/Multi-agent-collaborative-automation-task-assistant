"""多 Agent 调度层：Agent 协作、DAG 任务编排、调度器。

阶段三交付：四大 Agent 核心骨架（规划/执行/校验/复盘）。

公共接口：
- BaseAgent：Agent 抽象基类
- 四大 Agent 实现：PlannerAgent / ExecutorAgent / ValidatorAgent / ReviewerAgent
- AgentMessage / MessageType：消息协议
- AgentStatus / AgentStateMachine：状态机
- MessageBus / InMemoryMessageBus / RedisMessageBus：通信层
- AgentCapability / CapabilityRegistry：能力画像
"""

from app.orchestration.agents import (
    ExecutorAgent,
    PlannerAgent,
    ReviewerAgent,
    ValidatorAgent,
)
from app.orchestration.base_agent import BaseAgent
from app.orchestration.capability import (
    AgentCapability,
    CapabilityRegistry,
    get_capability_registry,
)
from app.orchestration.communication import (
    InMemoryMessageBus,
    MessageBus,
    RedisMessageBus,
)
from app.orchestration.messages import AgentMessage, MessageType
from app.orchestration.state import (
    AgentStateMachine,
    AgentStatus,
    IllegalStateTransitionError,
)

__all__ = [
    "AgentCapability",
    "AgentMessage",
    "AgentStatus",
    "AgentStateMachine",
    "BaseAgent",
    "CapabilityRegistry",
    "ExecutorAgent",
    "IllegalStateTransitionError",
    "InMemoryMessageBus",
    "MessageBus",
    "MessageType",
    "PlannerAgent",
    "RedisMessageBus",
    "ReviewerAgent",
    "ValidatorAgent",
    "get_capability_registry",
]
