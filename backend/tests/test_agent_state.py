"""Agent 状态机单元测试：全路径覆盖。

覆盖所有合法转换与非法转换，确保状态流转可控。
"""

import pytest

from app.orchestration.state import (
    AgentStateMachine,
    AgentStatus,
    IllegalStateTransitionError,
)


class TestAgentStateMachine:
    """状态机全路径覆盖测试。"""

    def test_initial_state_is_idle(self) -> None:
        """默认初始状态为 IDLE。"""
        sm = AgentStateMachine()
        assert sm.status is AgentStatus.IDLE
        assert sm.is_idle()

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (AgentStatus.IDLE, AgentStatus.RUNNING),
            (AgentStatus.RUNNING, AgentStatus.WAITING),
            (AgentStatus.RUNNING, AgentStatus.COMPLETED),
            (AgentStatus.RUNNING, AgentStatus.ERROR),
            (AgentStatus.WAITING, AgentStatus.RUNNING),
            (AgentStatus.WAITING, AgentStatus.COMPLETED),
            (AgentStatus.WAITING, AgentStatus.ERROR),
            (AgentStatus.COMPLETED, AgentStatus.IDLE),
            (AgentStatus.ERROR, AgentStatus.IDLE),
        ],
    )
    def test_legal_transitions(self, from_status: AgentStatus, to_status: AgentStatus) -> None:
        """所有合法转换均应成功。"""
        sm = AgentStateMachine(initial=from_status)
        sm.transition(to_status)
        assert sm.status is to_status

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (AgentStatus.IDLE, AgentStatus.COMPLETED),  # 不能直接跳到完成
            (AgentStatus.IDLE, AgentStatus.WAITING),  # 不能直接跳到等待
            (AgentStatus.IDLE, AgentStatus.ERROR),  # 不能直接跳到错误
            (AgentStatus.COMPLETED, AgentStatus.RUNNING),  # 完成后不能直接运行
            (AgentStatus.ERROR, AgentStatus.RUNNING),  # 错误后不能直接运行
        ],
    )
    def test_illegal_transitions_raise(
        self, from_status: AgentStatus, to_status: AgentStatus
    ) -> None:
        """非法转换应抛异常，避免静默进入不一致状态。"""
        sm = AgentStateMachine(initial=from_status)
        with pytest.raises(IllegalStateTransitionError):
            sm.transition(to_status)

    def test_full_lifecycle(self) -> None:
        """完整生命周期：IDLE → RUNNING → WAITING → RUNNING → COMPLETED → IDLE。"""
        sm = AgentStateMachine()
        sm.transition(AgentStatus.RUNNING)
        assert sm.is_running()
        sm.transition(AgentStatus.WAITING)
        sm.transition(AgentStatus.RUNNING)
        sm.transition(AgentStatus.COMPLETED)
        sm.transition(AgentStatus.IDLE)
        assert sm.is_idle()

    def test_error_recovery(self) -> None:
        """错误恢复：RUNNING → ERROR → IDLE。"""
        sm = AgentStateMachine()
        sm.transition(AgentStatus.RUNNING)
        sm.transition(AgentStatus.ERROR)
        sm.transition(AgentStatus.IDLE)
        assert sm.is_idle()

    def test_can_transition_check(self) -> None:
        """can_transition 不实际变更状态，仅检查合法性。"""
        sm = AgentStateMachine()
        assert sm.can_transition(AgentStatus.RUNNING)
        assert not sm.can_transition(AgentStatus.COMPLETED)
        assert sm.status is AgentStatus.IDLE  # 状态未变
