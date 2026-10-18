"""任务调度器：DAG 拓扑序调度 + 并行执行 + Agent 集成。

职责：
- 按拓扑序逐层调度：就绪节点 → 并行执行 → 收集结果 → 释放后继
- 无依赖任务并行执行（asyncio.gather），缩短总耗时
- 优先级排序：紧急度 × 依赖深度，关键路径优先
- 与四大 Agent 集成：执行 Agent 推理 → 校验 Agent 质检 → 失败重试
- 断点续传：从已完成集合继续执行剩余子图
- 状态转换观察者：每次子任务状态变更与最终结果都通知 ScheduleObserver

设计要点：
- 调度器不持有 Agent 实例，通过 agent_factory 按需创建（内存优化）
- 每个节点执行流程：PENDING → READY → RUNNING → VALIDATING → COMPLETED/FAILED
- 失败自动重试（RetryExecutor），耗尽触发 WAITING_HUMAN
- 状态变更通过 TaskStateMachine 管理，observer 负责外部持久化
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

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


class ScheduleObserver(Protocol):
    """调度观察者：接收每次状态转换与节点最终结果。

    生产实现是 DatabaseScheduleObserver（锁行 → 更新子任务 → 追加事件 → commit
    → 发布事件）；测试可注入内存实现。
    """

    async def on_transition(
        self,
        task_id: str,
        node_id: str,
        old: SubtaskStatus,
        new: SubtaskStatus,
    ) -> None: ...

    async def on_result(self, task_id: str, result: NodeResult) -> None: ...


class NullScheduleObserver:
    """默认空观察者：兼容旧的无持久化调用路径。"""

    async def on_transition(
        self,
        task_id: str,
        node_id: str,
        old: SubtaskStatus,
        new: SubtaskStatus,
    ) -> None:
        return None

    async def on_result(self, task_id: str, result: NodeResult) -> None:
        return None


class TaskScheduler:
    """任务调度器：驱动 DAG 执行的核心引擎。

    用法：
        scheduler = TaskScheduler(
            agent_factory=create_agent,
            retry_policy=...,
            observer=DatabaseScheduleObserver(session_factory),
        )
        result = await scheduler.run(dag, task_id=str(task.id))
    """

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        retry_policy: RetryPolicy | None = None,
        max_concurrency: int = 5,
        observer: ScheduleObserver | None = None,
        max_regenerations: int = 2,
    ) -> None:
        self.agent_factory = agent_factory
        self.retry_executor = RetryExecutor(retry_policy or RetryPolicy())
        self.max_concurrency = max_concurrency
        # 校验不通过时允许的定向重做次数（同一节点、同一证据、带 feedback）
        self.max_regenerations = max_regenerations
        self._observer: ScheduleObserver = observer or NullScheduleObserver()
        # 节点状态机注册表：node_id → TaskStateMachine
        self._state_machines: dict[str, TaskStateMachine] = {}
        # 已完成节点集合（断点续传时可预填充）
        self._completed: set[str] = set()
        # 已完成节点输出：供后继节点作为 dependency_outputs
        self._completed_outputs: dict[str, dict[str, Any]] = {}

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
        trace_id: str | None = None,
        subtask_ids: dict[str, str] | None = None,
    ) -> ScheduleResult:
        """执行整个 DAG：逐层并行调度。

        参数：
        - pre_completed：断点续传时已完成节点集合；
        - trace_id：任务持久化 trace ID（默认由 task_id 派生，不按节点生成）；
        - subtask_ids：node_id → 数据库 Subtask UUID 字符串，供节点上下文与 observer 使用。
        """
        self._completed = set(pre_completed or [])
        self._completed_outputs = {}
        resolved_trace_id = trace_id or (f"task-{task_id}" if task_id else "task")
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
                *(
                    self._execute_node(
                        dag,
                        nid,
                        task_id=task_id,
                        trace_id=resolved_trace_id,
                        subtask_ids=subtask_ids or {},
                    )
                    for nid in batch
                ),
                return_exceptions=True,
            )

            # 处理结果：无论成功失败都标记为已处理，避免无限循环
            for nid, nr in zip(batch, node_results, strict=False):
                processed.add(nid)
                if isinstance(nr, BaseException):
                    # gather 返回的异常（_execute_node 内部未捕获的意外错误）
                    unexpected = NodeResult(
                        node_id=nid,
                        status=SubtaskStatus.WAITING_HUMAN,
                        error=str(nr),
                    )
                    result.node_results[nid] = unexpected
                    result.waiting_human.append(nid)
                    await self._observer.on_result(task_id, unexpected)
                    continue
                # 此处 nr 已确定为 NodeResult（异常分支已 continue）
                if nr.status is SubtaskStatus.COMPLETED:
                    result.completed.append(nid)
                    self._completed.add(nid)
                    if nr.output is not None:
                        self._completed_outputs[nid] = nr.output
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

    async def _transition(
        self,
        task_id: str,
        node_id: str,
        state_machine: TaskStateMachine,
        target: SubtaskStatus,
    ) -> None:
        """调度器驱动的状态转换：先改状态，再等待 observer 持久化完成。"""
        old = state_machine.status
        state_machine.transition(target)
        await self._observer.on_transition(task_id, node_id, old, target)

    def _transition_notifier(self, task_id: str, node_id: str):
        """构造 RetryExecutor 使用的异步转换回调（保证观察者顺序一致）。"""

        async def notify(old: SubtaskStatus, new: SubtaskStatus) -> None:
            await self._observer.on_transition(task_id, node_id, old, new)

        return notify

    async def _execute_node(
        self,
        dag: TaskDAG,
        node_id: str,
        *,
        task_id: str,
        trace_id: str,
        subtask_ids: dict[str, str],
    ) -> NodeResult:
        """执行单个节点：ExecutorAgent 推理 → ValidatorAgent 校验 → 重试。

        完整闭环：PENDING → READY → RUNNING → 执行 → VALIDATING → 校验 → COMPLETED/FAILED。
        每个节点的状态转换与最终结果都通知 ScheduleObserver。
        """
        sm = self.get_state_machine(node_id)
        node_attrs = dag.get_node(node_id)
        title = node_attrs.get("title", node_id)
        description = node_attrs.get("description", "")

        # 节点执行上下文：任务身份、trace、依赖输出（供 Agent 与持久化使用）
        dependency_outputs = {
            dep: self._completed_outputs[dep]
            for dep in dag.get_predecessors(node_id)
            if dep in self._completed_outputs
        }
        input_data: dict[str, Any] = {
            "node_id": node_id,
            "title": title,
            "description": description,
            "task_id": task_id,
            "attempt": 0,
            "trace_id": trace_id,
            "dependency_outputs": dependency_outputs,
        }
        subtask_id = subtask_ids.get(node_id)
        if subtask_id is not None:
            input_data["subtask_id"] = subtask_id

        if sm.status is SubtaskStatus.PENDING:
            await self._transition(task_id, node_id, sm, SubtaskStatus.READY)
        if sm.status is SubtaskStatus.READY:
            await self._transition(task_id, node_id, sm, SubtaskStatus.RUNNING)

        # 执行 + 校验；校验可重试失败时只重做当前节点（最多 max_regenerations 次）
        executor_agent = self.agent_factory("executor")
        validator_agent = self.agent_factory("validator")

        regeneration = 0
        validation_feedback = ""
        while True:
            exec_input = dict(input_data)
            exec_input["attempt"] = regeneration
            if validation_feedback:
                exec_input["regeneration"] = regeneration
                exec_input["validation_feedback"] = validation_feedback

            retry_result: RetryResult = await self.retry_executor.execute(
                executor_agent.execute,
                kwargs={"input_data": exec_input},
                state_machine=sm,
                on_transition=self._transition_notifier(task_id, node_id),
            )

            if not retry_result.success:
                node_result = NodeResult(
                    node_id=node_id,
                    status=SubtaskStatus.WAITING_HUMAN,
                    error=retry_result.error,
                    attempts=retry_result.attempts + regeneration,
                )
                await self._observer.on_result(task_id, node_result)
                return node_result

            # 校验阶段
            exec_output = retry_result.result or {}
            await self._transition(task_id, node_id, sm, SubtaskStatus.VALIDATING)
            attempts = retry_result.attempts + regeneration

            validation_input: dict[str, Any] = {
                "node_id": node_id,
                "title": title,
                "result": exec_output.get("result", ""),
                "task_id": task_id,
                "trace_id": trace_id,
            }
            # 文献场景：把类型化执行结果与证据池一并交给校验器
            if isinstance(exec_output.get("execution_result"), dict):
                validation_input["execution_result"] = exec_output["execution_result"]
            if isinstance(exec_output.get("evidence_pool"), list):
                validation_input["evidence_pool"] = exec_output["evidence_pool"]

            try:
                validation = await validator_agent.execute(validation_input)
            except Exception as exc:
                logger.error("validation_failed", node_id=node_id, error=str(exc))
                await self._transition(task_id, node_id, sm, SubtaskStatus.FAILED)
                await self._transition(task_id, node_id, sm, SubtaskStatus.WAITING_HUMAN)
                node_result = NodeResult(
                    node_id=node_id,
                    status=SubtaskStatus.WAITING_HUMAN,
                    error=f"校验异常: {exc}",
                    output=exec_output,
                    attempts=attempts,
                )
                await self._observer.on_result(task_id, node_result)
                return node_result

            if validation.get("passed", False):
                await self._transition(task_id, node_id, sm, SubtaskStatus.COMPLETED)
                node_result = NodeResult(
                    node_id=node_id,
                    status=SubtaskStatus.COMPLETED,
                    output=exec_output,
                    validation=validation,
                    attempts=attempts,
                )
                await self._observer.on_result(task_id, node_result)
                return node_result

            retryable = bool(validation.get("retryable", False))
            if not retryable or regeneration >= self.max_regenerations:
                await self._transition(task_id, node_id, sm, SubtaskStatus.FAILED)
                await self._transition(task_id, node_id, sm, SubtaskStatus.WAITING_HUMAN)
                node_result = NodeResult(
                    node_id=node_id,
                    status=SubtaskStatus.WAITING_HUMAN,
                    output=exec_output,
                    validation=validation,
                    error="校验未通过",
                    attempts=attempts,
                )
                await self._observer.on_result(task_id, node_result)
                return node_result

            # 定向重做：同一节点、同一证据集、带 validation_feedback；不提高温度
            regeneration += 1
            validation_feedback = str(validation.get("feedback") or "校验未通过，请按问题修订")
            logger.info(
                "node_regeneration_scheduled",
                node_id=node_id,
                task_id=task_id,
                regeneration=regeneration,
            )
            await self._transition(task_id, node_id, sm, SubtaskStatus.FAILED)
            await self._transition(task_id, node_id, sm, SubtaskStatus.RETRYING)
            await self._transition(task_id, node_id, sm, SubtaskStatus.READY)

    def get_completed_nodes(self) -> set[str]:
        """返回已完成节点集合（供断点续传持久化）。"""
        return set(self._completed)

    def get_state_snapshot(self) -> dict[str, str]:
        """返回所有节点的状态快照（供持久化与恢复）。"""
        return {nid: sm.status.value for nid, sm in self._state_machines.items()}
