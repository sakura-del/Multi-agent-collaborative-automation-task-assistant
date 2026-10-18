"""Agent 状态机。

定义 Agent 的生命周期状态与合法转换路径，确保状态流转可控、可追溯。
状态实时持久化至数据库 AGENT 表，支持断点恢复。

状态流转图：
    IDLE → RUNNING → WAITING → COMPLETED
                    ↘          ↗
                     ERROR ←─── (任何状态均可转入 ERROR)

设计要点：
- 合法转换用集合显式声明，非法转换抛异常避免静默错误
- 状态变更记录日志，便于复盘 Agent 追踪全链路
- terminal 状态集合标识不可逆状态，防止已完成的 Agent 被误唤醒
"""

from __future__ import annotations

from enum import StrEnum

from app.core.logging import get_logger

logger = get_logger(__name__)


class AgentStatus(StrEnum):
    """Agent 生命周期状态。"""

    IDLE = "idle"  # 空闲，等待任务分配
    RUNNING = "running"  # 执行中
    WAITING = "waiting"  # 等待外部输入（如用户确认、其他 Agent 响应）
    COMPLETED = "completed"  # 已完成当前任务
    ERROR = "error"  # 异常终止


# 合法状态转换路径：from_status → {允许的 to_status 集合}
# 显式声明而非隐式规则，便于审计与扩展
_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.IDLE: frozenset({AgentStatus.RUNNING}),
    AgentStatus.RUNNING: frozenset({AgentStatus.WAITING, AgentStatus.COMPLETED, AgentStatus.ERROR}),
    AgentStatus.WAITING: frozenset({AgentStatus.RUNNING, AgentStatus.COMPLETED, AgentStatus.ERROR}),
    AgentStatus.COMPLETED: frozenset({AgentStatus.IDLE}),  # 完成后回到空闲，等待下一任务
    AgentStatus.ERROR: frozenset({AgentStatus.IDLE}),  # 错误恢复后回到空闲
}

# 终态：这些状态不可直接跳出（需先经过 IDLE 重置）
# 当前 COMPLETED 与 ERROR 均可回到 IDLE，故无绝对终态，保留集合供未来扩展
_TERMINAL: frozenset[AgentStatus] = frozenset()


class IllegalStateTransitionError(Exception):
    """非法状态转换，说明 Agent 逻辑存在缺陷。"""


class AgentStateMachine:
    """Agent 状态机：管理单个 Agent 的状态流转。

    线程安全说明：单个 Agent 实例不跨线程共享，状态机无需加锁。
    若未来支持多线程共享，需在 transition 外加锁（并行优化注意事项）。
    """

    def __init__(self, initial: AgentStatus = AgentStatus.IDLE) -> None:
        self._status = initial

    def reset(self, to: AgentStatus = AgentStatus.IDLE) -> None:
        """强制把状态归位（不走转换表）。

        仅用于"上一次执行未正常收敛"的现场修复：例如同一 Agent 实例被并发复用，
        上一次仍停在 RUNNING。正常流程应立即使用 `transition`。
        """
        old = self._status
        self._status = to
        logger.warning("agent_state_reset", old=old.value, new=to.value)

    @property
    def status(self) -> AgentStatus:
        """当前状态。"""
        return self._status

    def can_transition(self, to: AgentStatus) -> bool:
        """检查从当前状态到目标状态是否合法。"""
        allowed = _TRANSITIONS.get(self._status, frozenset())
        return to in allowed

    def transition(self, to: AgentStatus) -> None:
        """执行状态转换。

        非法转换抛 IllegalStateTransitionError，避免静默进入不一致状态。
        """
        if not self.can_transition(to):
            raise IllegalStateTransitionError(
                f"非法状态转换: {self._status.value} → {to.value}，"
                f"当前状态仅允许 → {[s.value for s in _TRANSITIONS.get(self._status, frozenset())]}"
            )
        old = self._status
        self._status = to
        logger.info("agent_state_transition", old=old.value, new=to.value)

    def is_idle(self) -> bool:
        return self._status is AgentStatus.IDLE

    def is_running(self) -> bool:
        return self._status is AgentStatus.RUNNING

    def is_terminal(self) -> bool:
        """是否处于终态（不可继续推进）。"""
        return self._status in _TERMINAL
