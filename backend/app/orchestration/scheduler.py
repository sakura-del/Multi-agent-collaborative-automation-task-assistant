"""任务调度器：DAG 拓扑序调度 + 并行执行 + Agent 集成。

职责：
- 按拓扑序逐层调度：就绪节点 → 并行执行 → 收集结果 → 释放后继
- 无依赖任务并行执行（asyncio.gather），缩短总耗时
- 优先级排序：紧急度 × 依赖深度，关键路径优先
- 与四大 Agent 集成：执行 Agent 推理 → 校验 Agent 质检 → 失败重试
- 断点续传：从已完成集合继续执行剩余子图

设计要点：
- 调度器不持有 Agent 实例，通过 agent_factory 按需创建（内存优化）
- 每个节点执行流程：RUNNING → ExecutorAgent → VALIDATING → ValidatorAgent → COMPLETED/FAILED
- 失败自动重试（RetryExecutor），耗尽触发 WAITING_HUMAN
- 状态变更通过 TaskStateMachine 管理，可持久化供断点续传
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.orchestration.dag_engine import TaskDAG
from app.orchestration.retry import RetryExecutor, RetryPolicy, RetryResult
from app.orchestration.task_state import SubtaskStatus, TaskStateMachine

logger = get_logger(__name__)

# Agent 工厂类型：按 agent_type 创建 Agent 实例
AgentFactory = Callable[[str], Any]


@dataclass
class NodeResult:
    """单个节点执行结果。"""

    node_id: str
    status: SubtaskStatus
    output: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 1


@dataclass
class ScheduleResult:
    """整个 DAG 调度结果。"""

    task_id: str
    total_nodes: int = 0
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    waiting_human: list[str] = field(default_factory=list)
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    success: bool = False

    @property
    def completion_rate(self) -> float:
        """完成率。"""
        if self.total_nodes == 0:
            return 0.0
        return len(self.completed) / self.total_nodes


class TaskScheduler:
    """任务调度器：驱动 DAG 执行的核心引擎。

    用法：
        scheduler = TaskScheduler(agent_factory=create_agent, retry_policy=...)
        result = await scheduler.run(dag, task_id="t1")
    """

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        retry_policy: RetryPolicy | None = None,
        max_concurrency: int = 5,
    ) -> None:
        self.agent_factory = agent_factory
        self.retry_executor = RetryExecutor(retry_policy or RetryPolicy())
        self.max_concurrency = max_concurrency
        # 节点状态机注册表：node_id → TaskStateMachine
        self._state_machines: dict[str, TaskStateMachine] = {}
        # 已完成节点集合（断点续传时可预填充）
        self._completed: set[str] = set()

    def get_state_machine(self, node_id: str) -> TaskStateMachine:
        """获取或创建节点的状态机。"""
        if node_id not in self._state_machines:
            sm = TaskStateMachine(node_id=node_id)
            self._state_machines[node_id] = sm
        return self._state_machines[node_id]

    async def run(
        self,
        dag: TaskDAG,
        *,
        task_id: str = "",
        pre_completed: set[str] | None = None,
    ) -> ScheduleResult:
        """执行整个 DAG：逐层并行调度。

        参数 pre_completed 用于断点续传：跳过已完成节点，从断点继续。
        """
        self._completed = set(pre_completed or [])
        result = ScheduleResult(task_id=task_id, total_nodes=dag.node_count)
        # 已处理节点集合：含成功与失败，避免失败节点被无限重调度
        processed: set[str] = set(self._completed)

        logger.info(
            "scheduler_start",
            task_id=task_id,
            total_nodes=dag.node_count,
            pre_completed=len(self._completed),
        )

        # 逐层调度：每轮获取就绪节点并并行执行
        while True:
            # 就绪节点 = 前置全部完成 且 未被处理过
            ready = [
                n for n in dag.get_ready_nodes_with_priority(self._completed) if n not in processed
            ]
            if not ready:
                break  # 无就绪节点，调度结束

            # 限制并发数，避免资源耗尽
            batch = ready[: self.max_concurrency]
            node_results = await asyncio.gather(
                *(self._execute_node(dag, nid) for nid in batch),
                return_exceptions=True,
            )

            # 处理结果：无论成功失败都标记为已处理，避免无限循环
            for nid, nr in zip(batch, node_results, strict=False):
                processed.add(nid)
                if isinstance(nr, Exception):
                    # gather 返回的异常（_execute_node 内部未捕获的意外错误）
                    result.node_results[nid] = NodeResult(
                        node_id=nid,
                        status=SubtaskStatus.WAITING_HUMAN,
                        error=str(nr),
                    )
                    result.waiting_human.append(nid)
                elif nr.status is SubtaskStatus.COMPLETED:
                    result.completed.append(nid)
                    self._completed.add(nid)
                    result.node_results[nid] = nr
                else:
                    result.waiting_human.append(nid)
                    result.node_results[nid] = nr

        # success 判定：已完成集合（含预完成）覆盖全部节点
        result.success = len(self._completed) == result.total_nodes
        logger.info(
            "scheduler_done",
            task_id=task_id,
            completed=len(result.completed),
            waiting=len(result.waiting_human),
            success=result.success,
        )
        return result

    async def _execute_node(self, dag: TaskDAG, node_id: str) -> NodeResult:
        """执行单个节点：ExecutorAgent 推理 → ValidatorAgent 校验 → 重试。

        完整闭环：RUNNING → 执行 → VALIDATING → 校验 → COMPLETED/FAILED。
        """
        sm = self.get_state_machine(node_id)
        node_attrs = dag.get_node(node_id)
        title = node_attrs.get("title", node_id)
        description = node_attrs.get("description", "")
        trace_id = f"task-{node_id}"

        # 执行阶段（含重试）
        executor_agent = self.agent_factory("executor")
        retry_result: RetryResult = await self.retry_executor.execute(
            executor_agent.execute,
            kwargs={
                "input_data": {
                    "node_id": node_id,
                    "title": title,
                    "description": description,
                    "trace_id": trace_id,
                }
            },
            state_machine=sm,
        )

        if not retry_result.success:
            return NodeResult(
                node_id=node_id,
                status=SubtaskStatus.WAITING_HUMAN,
                error=retry_result.error,
                attempts=retry_result.attempts,
            )

        # 校验阶段
        exec_output = retry_result.result or {}
        sm.transition(SubtaskStatus.VALIDATING)

        validator_agent = self.agent_factory("validator")
        try:
            validation = await validator_agent.execute(
                {
                    "node_id": node_id,
                    "title": title,
                    "result": exec_output.get("result", ""),
                    "trace_id": trace_id,
                }
            )
        except Exception as exc:
            logger.error("validation_failed", node_id=node_id, error=str(exc))
            sm.transition(SubtaskStatus.FAILED)
            sm.transition(SubtaskStatus.WAITING_HUMAN)
            return NodeResult(
                node_id=node_id,
                status=SubtaskStatus.WAITING_HUMAN,
                error=f"校验异常: {exc}",
                output=exec_output,
                attempts=retry_result.attempts,
            )

        # 根据校验结果决定最终状态
        passed = validation.get("passed", False)
        if passed:
            sm.transition(SubtaskStatus.COMPLETED)
            return NodeResult(
                node_id=node_id,
                status=SubtaskStatus.COMPLETED,
                output=exec_output,
                validation=validation,
                attempts=retry_result.attempts,
            )
        # 校验不通过 → 重试耗尽 → 人工干预
        sm.transition(SubtaskStatus.FAILED)
        sm.transition(SubtaskStatus.WAITING_HUMAN)
        return NodeResult(
            node_id=node_id,
            status=SubtaskStatus.WAITING_HUMAN,
            output=exec_output,
            validation=validation,
            error="校验未通过",
            attempts=retry_result.attempts,
        )

    def get_completed_nodes(self) -> set[str]:
        """返回已完成节点集合（供断点续传持久化）。"""
        return set(self._completed)

    def get_state_snapshot(self) -> dict[str, str]:
        """返回所有节点的状态快照（供持久化与恢复）。"""
        return {nid: sm.status.value for nid, sm in self._state_machines.items()}
