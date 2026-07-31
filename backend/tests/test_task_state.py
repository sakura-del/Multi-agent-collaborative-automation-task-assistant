"""子任务状态机单元测试：全路径覆盖。"""

import pytest

from app.orchestration.task_state import (
    IllegalTaskStateError,
    SubtaskStatus,
    TaskStateMachine,
)


class TestSubtaskStatusMachine:
    """子任务状态机全路径覆盖测试。"""

    def test_initial_state_is_pending(self) -> None:
        sm = TaskStateMachine(node_id="n1")
        assert sm.status is SubtaskStatus.PENDING

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (SubtaskStatus.PENDING, SubtaskStatus.RUNNING),
            (SubtaskStatus.PENDING, SubtaskStatus.TERMINATED),
            (SubtaskStatus.PENDING, SubtaskStatus.PAUSED),
            (SubtaskStatus.RUNNING, SubtaskStatus.VALIDATING),
            (SubtaskStatus.RUNNING, SubtaskStatus.FAILED),
            (SubtaskStatus.RUNNING, SubtaskStatus.TERMINATED),
            (SubtaskStatus.RUNNING, SubtaskStatus.PAUSED),
            (SubtaskStatus.VALIDATING, SubtaskStatus.COMPLETED),
            (SubtaskStatus.VALIDATING, SubtaskStatus.FAILED),
            (SubtaskStatus.VALIDATING, SubtaskStatus.TERMINATED),
            (SubtaskStatus.FAILED, SubtaskStatus.RETRYING),
            (SubtaskStatus.FAILED, SubtaskStatus.WAITING_HUMAN),
            (SubtaskStatus.FAILED, SubtaskStatus.TERMINATED),
            (SubtaskStatus.RETRYING, SubtaskStatus.RUNNING),
            (SubtaskStatus.WAITING_HUMAN, SubtaskStatus.RUNNING),
            (SubtaskStatus.PAUSED, SubtaskStatus.RUNNING),
        ],
    )
    def test_legal_transitions(self, from_status: SubtaskStatus, to_status: SubtaskStatus) -> None:
        """所有合法转换均成功。"""
        sm = TaskStateMachine(node_id="n1", initial=from_status)
        sm.transition(to_status)
        assert sm.status is to_status

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (SubtaskStatus.PENDING, SubtaskStatus.COMPLETED),
            (SubtaskStatus.PENDING, SubtaskStatus.VALIDATING),
            (SubtaskStatus.COMPLETED, SubtaskStatus.RUNNING),
            (SubtaskStatus.TERMINATED, SubtaskStatus.RUNNING),
            (SubtaskStatus.RUNNING, SubtaskStatus.COMPLETED),
            (SubtaskStatus.VALIDATING, SubtaskStatus.RUNNING),
        ],
    )
    def test_illegal_transitions_raise(
        self, from_status: SubtaskStatus, to_status: SubtaskStatus
    ) -> None:
        """非法转换抛异常。"""
        sm = TaskStateMachine(node_id="n1", initial=from_status)
        with pytest.raises(IllegalTaskStateError):
            sm.transition(to_status)

    def test_full_execute_lifecycle(self) -> None:
        """完整执行生命周期：PENDING→RUNNING→VALIDATING→COMPLETED。"""
        sm = TaskStateMachine(node_id="n1")
        sm.transition(SubtaskStatus.RUNNING)
        sm.transition(SubtaskStatus.VALIDATING)
        sm.transition(SubtaskStatus.COMPLETED)
        assert sm.is_completed
        assert sm.is_terminal

    def test_retry_lifecycle(self) -> None:
        """重试生命周期：RUNNING→FAILED→RETRYING→RUNNING→VALIDATING→COMPLETED。"""
        sm = TaskStateMachine(node_id="n1")
        sm.transition(SubtaskStatus.RUNNING)
        sm.transition(SubtaskStatus.FAILED)
        sm.transition(SubtaskStatus.RETRYING)
        sm.transition(SubtaskStatus.RUNNING)
        sm.transition(SubtaskStatus.VALIDATING)
        sm.transition(SubtaskStatus.COMPLETED)
        assert sm.is_completed

    def test_retry_exhausted_to_waiting_human(self) -> None:
        """重试耗尽转为人工干预。"""
        sm = TaskStateMachine(node_id="n1")
        sm.transition(SubtaskStatus.RUNNING)
        sm.transition(SubtaskStatus.FAILED)
        sm.transition(SubtaskStatus.WAITING_HUMAN)
        assert sm.status is SubtaskStatus.WAITING_HUMAN
        assert not sm.is_terminal  # 人工干预后可恢复

    def test_human_intervention_recovery(self) -> None:
        """人工干预后恢复执行。"""
        sm = TaskStateMachine(node_id="n1")
        sm.transition(SubtaskStatus.RUNNING)
        sm.transition(SubtaskStatus.FAILED)
        sm.transition(SubtaskStatus.WAITING_HUMAN)
        sm.transition(SubtaskStatus.RUNNING)  # 人工恢复
        assert sm.status is SubtaskStatus.RUNNING

    def test_pause_and_resume(self) -> None:
        """暂停与恢复。"""
        sm = TaskStateMachine(node_id="n1")
        sm.transition(SubtaskStatus.RUNNING)
        sm.transition(SubtaskStatus.PAUSED)
        sm.transition(SubtaskStatus.RUNNING)
        assert sm.status is SubtaskStatus.RUNNING

    def test_terminate_is_terminal(self) -> None:
        """终止为终态，不可再转换。"""
        sm = TaskStateMachine(node_id="n1")
        sm.transition(SubtaskStatus.RUNNING)
        sm.transition(SubtaskStatus.TERMINATED)
        assert sm.is_terminal
        with pytest.raises(IllegalTaskStateError):
            sm.transition(SubtaskStatus.RUNNING)

    def test_transition_hook_called(self) -> None:
        """状态转换钩子被调用。"""
        calls: list[tuple[str, str, str]] = []
        sm = TaskStateMachine(node_id="n1")
        sm.on_transition(lambda old, new, nid: calls.append((old.value, new.value, nid)))
        sm.transition(SubtaskStatus.RUNNING)
        assert len(calls) == 1
        assert calls[0] == ("pending", "running", "n1")

    def test_hook_error_does_not_block_transition(self) -> None:
        """钩子异常不阻断状态转换。"""

        def bad_hook(old: SubtaskStatus, new: SubtaskStatus, nid: str) -> None:
            raise RuntimeError("钩子故障")

        sm = TaskStateMachine(node_id="n1")
        sm.on_transition(bad_hook)
        sm.transition(SubtaskStatus.RUNNING)  # 不应抛异常
        assert sm.status is SubtaskStatus.RUNNING
