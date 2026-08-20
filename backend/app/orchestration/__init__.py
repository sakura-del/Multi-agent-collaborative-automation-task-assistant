"""多 Agent 调度层：Agent 协作、DAG 任务编排、调度器。

阶段三交付：四大 Agent 核心骨架（规划/执行/校验/复盘）。
阶段四交付：DAG 引擎、任务状态机、调度器、自动重试、断点续传。

公共接口：
- BaseAgent：Agent 抽象基类
- 四大 Agent 实现：PlannerAgent / ExecutorAgent / ValidatorAgent / ReviewerAgent
- AgentMessage / MessageType：消息协议
- AgentStatus / AgentStateMachine：Agent 状态机
- MessageBus / InMemoryMessageBus / RedisMessageBus：通信层
- AgentCapability / CapabilityRegistry：能力画像
- TaskDAG：DAG 图结构（循环检测/拓扑排序/关键路径）
- SubtaskStatus / TaskStateMachine：子任务状态机
- RetryPolicy / RetryExecutor：自动重试（指数退避）
- TaskScheduler：任务调度器（拓扑序+并行+优先级）
- ScheduleSnapshot / RecoveryManager：断点续传
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
from app.orchestration.dag_engine import DAGCycleError, NodeNotFoundError, TaskDAG
from app.orchestration.messages import AgentMessage, MessageType
from app.orchestration.recovery import RecoveryManager, ScheduleSnapshot
from app.orchestration.retry import RetryExecutor, RetryPolicy, RetryResult
from app.orchestration.scheduler import NodeResult, ScheduleResult, TaskScheduler
from app.orchestration.state import (
    AgentStateMachine,
    AgentStatus,
    IllegalStateTransitionError,
)
from app.orchestration.task_state import (
    IllegalTaskStateError,
    SubtaskStatus,
    TaskStateMachine,
)

__all__ = [
    "AgentCapability",
    "AgentMessage",
    "AgentStateMachine",
    "AgentStatus",
    "BaseAgent",
    "CapabilityRegistry",
    "DAGCycleError",
    "ExecutorAgent",
    "IllegalStateTransitionError",
    "IllegalTaskStateError",
    "InMemoryMessageBus",
    "MessageBus",
    "MessageType",
    "NodeNotFoundError",
    "NodeResult",
    "PlannerAgent",
    "RecoveryManager",
    "RedisMessageBus",
    "RetryExecutor",
    "RetryPolicy",
    "RetryResult",
    "ReviewerAgent",
    "ScheduleResult",
    "ScheduleSnapshot",
    "SubtaskStatus",
    "TaskDAG",
    "TaskScheduler",
    "TaskStateMachine",
    "ValidatorAgent",
    "get_capability_registry",
]
