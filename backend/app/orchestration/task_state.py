"""子任务状态机：管理 Subtask 生命周期状态流转。

状态流转图（对齐数据库 SUBTASK.status）：
    pending → running → validating → completed
                   ↘          ↘
                    failed ←───── (校验不通过)
                      ↓
                  retrying → running (重试)
                      ↓ (重试耗尽)
                waiting_human → running (人工恢复)
    任意状态 → terminated / paused
    paused → running

设计要点：
- 合法转换显式声明，非法抛异常避免静默错误
- 事件钩子：状态转换时触发回调，供调度器/日志/持久化使用
- terminal 状态标识不可逆终止
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class SubtaskStatus(StrEnum):
    """子任务生命周期状态，对齐 SUBTASK 表。"""

    PENDING = "pending"  # 待执行
    RUNNING = "running"  # 执行中
    VALIDATING = "validating"  # 校验中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    RETRYING = "retrying"  # 重试中
    PAUSED = "paused"  # 暂停
    TERMINATED = "terminated"  # 终止
    WAITING_HUMAN = "waiting_human"  # 等待人工干预


# 合法状态转换路径
_TRANSITIONS: dict[SubtaskStatus, frozenset[SubtaskStatus]] = {
    SubtaskStatus.PENDING: frozenset(
        {SubtaskStatus.RUNNING, SubtaskStatus.TERMINATED, SubtaskStatus.PAUSED}
    ),
    SubtaskStatus.RUNNING: frozenset(
        {
            SubtaskStatus.VALIDATING,
            SubtaskStatus.FAILED,
            SubtaskStatus.TERMINATED,
            SubtaskStatus.PAUSED,
        }
    ),
    SubtaskStatus.VALIDATING: frozenset(
        {
            SubtaskStatus.COMPLETED,
            SubtaskStatus.FAILED,
            SubtaskStatus.TERMINATED,
            SubtaskStatus.PAUSED,
        }
    ),
    SubtaskStatus.COMPLETED: frozenset(),  # 终态
    SubtaskStatus.FAILED: frozenset(
        {SubtaskStatus.RETRYING, SubtaskStatus.WAITING_HUMAN, SubtaskStatus.TERMINATED}
    ),
    SubtaskStatus.RETRYING: frozenset({SubtaskStatus.RUNNING, SubtaskStatus.TERMINATED}),
    SubtaskStatus.WAITING_HUMAN: frozenset({SubtaskStatus.RUNNING, SubtaskStatus.TERMINATED}),
    SubtaskStatus.PAUSED: frozenset({SubtaskStatus.RUNNING, SubtaskStatus.TERMINATED}),
    SubtaskStatus.TERMINATED: frozenset(),  # 终态
}

# 绝对终态：不可再转换
_TERMINAL: frozenset[SubtaskStatus] = frozenset({SubtaskStatus.COMPLETED, SubtaskStatus.TERMINATED})


class IllegalTaskStateError(Exception):
    """非法子任务状态转换。"""


# 事件钩子类型：转换回调 (from_status, to_status, node_id) -> None
TransitionCallback = Callable[[SubtaskStatus, SubtaskStatus, str], Any]


class TaskStateMachine:
    """子任务状态机：管理单个子任务的状态流转。

    每个子任务节点对应一个状态机实例。
    状态变更触发已注册的钩子，供调度器感知状态变化。
    """

    def __init__(
        self,
        node_id: str,
        initial: SubtaskStatus = SubtaskStatus.PENDING,
    ) -> None:
        self.node_id = node_id
        self._status = initial
        self._hooks: list[TransitionCallback] = []

    @property
    def status(self) -> SubtaskStatus:
        return self._status

    @property
    def is_terminal(self) -> bool:
        """是否处于终态（不可继续推进）。"""
        return self._status in _TERMINAL

    @property
    def is_completed(self) -> bool:
        return self._status is SubtaskStatus.COMPLETED

    def can_transition(self, to: SubtaskStatus) -> bool:
        allowed = _TRANSITIONS.get(self._status, frozenset())
        return to in allowed

    def transition(self, to: SubtaskStatus) -> None:
        """执行状态转换，触发已注册钩子。

        钩子异常不阻断转换（状态变更已生效），仅记录日志。
        """
        if not self.can_transition(to):
            raise IllegalTaskStateError(
                f"子任务 {self.node_id} 非法状态转换: " f"{self._status.value} → {to.value}"
            )
        old = self._status
        self._status = to
        logger.info(
            "subtask_state_transition",
            node_id=self.node_id,
            old=old.value,
            new=to.value,
        )
        # 触发钩子（异常隔离，不阻断状态变更）
        for hook in self._hooks:
            try:
                hook(old, to, self.node_id)
            except Exception:
                logger.error("subtask_hook_error", node_id=self.node_id, hook=hook.__name__)

    def on_transition(self, callback: TransitionCallback) -> None:
        """注册状态转换钩子。

        调度器可注册钩子感知子任务完成，触发后继节点调度。
        """
        self._hooks.append(callback)
