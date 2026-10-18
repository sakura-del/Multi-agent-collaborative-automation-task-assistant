"""TaskExecution 深模块：任务生命周期的唯一编排入口（Plan 2 Task 4）。

对外六个接口：
    submit / approve_plan / control / get_snapshot / events_after / recover

设计要点：
- HTTP 层只做参数校验并调用本模块，不接触数据库与调度器；
- 后台流程（规划、执行）各自创建独立 session，不依赖请求作用域；
- 每次任务状态转换与子任务转换都会写入 EXECUTION_LOG（递增 event_seq），
  提交后通过 EventPublisher 发布；
- 执行期事实来源是 PostgreSQL：重启后 recover() 只重新调度未完成节点。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.log import ExecutionLog
from app.db.models.task import Subtask, Task
from app.db.repositories.event import EventRepository
from app.db.repositories.task import TaskRepository
from app.knowledge.services.citation_validator import CitationValidator
from app.knowledge.services.context_builder import LiteratureContextBuilder
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.contracts import (
    ExecutionResult,
    LiteratureReviewRequest,
    SubtaskSnapshot,
    TaskCommand,
    TaskControlRequest,
    TaskEvent,
    TaskPlan,
    TaskSnapshot,
    TaskStatus,
)
from app.orchestration.dag_engine import TaskDAG
from app.orchestration.recovery import RecoveryManager
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.scheduler import TaskScheduler
from app.orchestration.services.literature_source import resolve_literature_source
from app.orchestration.workflows.literature_review import (
    LiteratureNodeExecutor,
    LiteratureNodeValidator,
    LiteratureReviewWorkflow,
)

logger = get_logger(__name__)

# 可被 recover() 重新调度的执行中状态
_RECOVERABLE_STATUSES = frozenset({TaskStatus.RUNNING, TaskStatus.REVIEWING})
# 允许暂停的状态
_PAUSABLE_STATUSES = frozenset({TaskStatus.RUNNING, TaskStatus.REVIEWING, TaskStatus.WAITING_HUMAN})
# 允许取消的状态
_CANCELLABLE_STATUSES = frozenset(
    {
        TaskStatus.CREATED,
        TaskStatus.PLANNING,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.RUNNING,
        TaskStatus.REVIEWING,
        TaskStatus.PAUSED,
        TaskStatus.RETRYING,
        TaskStatus.WAITING_HUMAN,
    }
)
# 终态
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED})


class TaskExecution:
    """任务运行时深模块：规划、确认、执行、恢复与查询。"""

    def __init__(
        self,
        container: RuntimeContainer,
        *,
        session_factory: Any | None = None,
    ) -> None:
        self.container = container
        self._session_factory = session_factory or container.session_factory

    # ===== 对外接口 =====

    async def submit(self, user_id: UUID, request: LiteratureReviewRequest) -> UUID:
        """创建任务：持久化 CREATED → PLANNING，提交后启动后台规划。"""
        trace_id = uuid.uuid4().hex
        config = request.model_dump(mode="json")
        config["trace_id"] = trace_id
        description = self._describe(request)

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = Task(
                user_id=user_id,
                title=request.topic,
                description=description,
                task_type="literature_review",
                status=TaskStatus.CREATED.value,
                config=config,
            )
            session.add(task)
            await session.flush()
            await repo.transition(task, {TaskStatus.CREATED}, TaskStatus.PLANNING)
            event = await self._append_event(
                session,
                task.id,
                event_type="task_status",
                status=TaskStatus.PLANNING.value,
                payload={"trace_id": trace_id},
            )
            task_id = task.id
            await session.commit()

        await self._publish(event)
        self.container.track(task_id, self._plan(task_id))
        logger.info("task_submitted", task_id=str(task_id), trace_id=trace_id)
        return task_id

    async def approve_plan(
        self,
        user_id: UUID,
        task_id: UUID,
        revision: int,
    ) -> TaskSnapshot:
        """确认规划：版本匹配且处于 WAITING_APPROVAL 时进入 RUNNING 并开始执行。"""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await self._lock_owned(session, repo, user_id, task_id)

            if task.status != TaskStatus.WAITING_APPROVAL.value:
                raise ValidationException(
                    "任务当前不在等待确认状态",
                    details={"status": task.status},
                )
            if task.plan_version != revision:
                raise ValidationException(
                    "规划版本不匹配",
                    details={"expected": task.plan_version, "received": revision},
                )

            await repo.transition(
                task,
                {TaskStatus.WAITING_APPROVAL},
                TaskStatus.RUNNING,
            )
            event = await self._append_event(
                session,
                task_id,
                event_type="plan_approved",
                status=TaskStatus.RUNNING.value,
                payload={"revision": revision},
            )
            snapshot = await self._build_snapshot(session, task)
            await session.commit()

        await self._publish(event)
        self.container.track(task_id, self._execute(task_id))
        logger.info("task_plan_approved", task_id=str(task_id), revision=revision)
        return snapshot

    async def control(
        self,
        user_id: UUID,
        task_id: UUID,
        request: TaskControlRequest,
    ) -> TaskSnapshot:
        """人工控制：pause / resume / cancel / retry_node / supply_input。"""
        retrack = False
        cancel_after_commit = False
        events: list[TaskEvent] = []
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await self._lock_owned(session, repo, user_id, task_id)
            status = TaskStatus(task.status)
            command = request.command

            if command is TaskCommand.PAUSE:
                events.append(await self._control_pause(session, repo, task, status))
                cancel_after_commit = True
            elif command is TaskCommand.RESUME:
                events.append(await self._control_resume(session, repo, task, status))
                retrack = TaskStatus(task.status) in {
                    TaskStatus.RUNNING,
                    TaskStatus.REVIEWING,
                }
            elif command is TaskCommand.CANCEL:
                events.append(await self._control_cancel(session, repo, task, status))
                cancel_after_commit = True
            elif command is TaskCommand.RETRY_NODE:
                events.append(
                    await self._control_retry_node(session, task, status, request.node_id)
                )
                retrack = True
            elif command is TaskCommand.SUPPLY_INPUT:
                events.append(
                    await self._control_supply_input(session, repo, task, status, request.input)
                )
                retrack = TaskStatus(task.status) is TaskStatus.RUNNING
            else:  # pragma: no cover - 枚举已穷尽
                raise ValidationException(f"不支持的任务命令: {command}")

            snapshot = await self._build_snapshot(session, task)
            await session.commit()

        for event in events:
            await self._publish(event)
        if cancel_after_commit:
            await self.container.cancel(task_id)
        if retrack and not self.container.is_tracking(task_id):
            self.container.track(task_id, self._execute(task_id))
        logger.info("task_control", task_id=str(task_id), command=request.command.value)
        return snapshot

    async def get_snapshot(self, user_id: UUID, task_id: UUID) -> TaskSnapshot:
        """查询任务快照（owner-scoped；越权与不存在统一 404）。"""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            return await self._build_snapshot(session, task)

    async def list_tasks(
        self,
        user_id: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> list[TaskSnapshot]:
        """分页列出当前用户的任务快照（HTTP 列表接口使用）。"""
        async with self._session_factory() as session:
            tasks = await TaskRepository(session).list_for_user(user_id, skip=skip, limit=limit)
            return [await self._build_snapshot(session, task) for task in tasks]

    async def events_after(
        self,
        user_id: UUID,
        task_id: UUID,
        event_id: int,
    ) -> list[TaskEvent]:
        """回放 event_id 之后的任务事件（owner-scoped、递增序号）。"""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            logs = await EventRepository(session).events_after(user_id, task_id, event_id)
            return [EventRepository.to_event(log) for log in logs]

    async def recover(self) -> None:
        """重启恢复：只重新调度执行中任务，已完成/终态任务不动。"""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Task).where(
                    Task.status.in_([status.value for status in _RECOVERABLE_STATUSES])
                )
            )
            tasks = list(result.scalars().all())

        for task in tasks:
            if self.container.is_tracking(task.id):
                continue
            logger.info("task_recovered", task_id=str(task.id), status=task.status)
            self.container.track(task.id, self._execute(task.id))

    # ===== 后台流程：规划 =====

    async def _plan(self, task_id: UUID) -> None:
        """后台规划：生成 TaskPlan + DAG，失败时进行一次 schema 修复。"""
        try:
            async with self._session_factory() as session:
                task = await TaskRepository(session).get(task_id)
                if task is None:
                    return
                requirement = self._requirement(task)
                trace_id = self._trace_id(task)
                task_type = task.task_type

            if task_type == "literature_review":
                # 文献场景：由工作流承担规划 + 一次契约修复；失败抛 HumanActionRequiredError
                workflow = self._literature_workflow(task)
                plan = await workflow.plan(task)
                dag = PlannerAgent.build_dag(plan)
            else:
                planner = self.container.create_planner()
                plan, dag = await self._generate_plan(planner, requirement, trace_id)

            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                if task.status != TaskStatus.PLANNING.value:
                    return  # 已被取消/暂停
                await repo.replace_plan(task, plan, dag)
                await repo.transition(
                    task,
                    {TaskStatus.PLANNING},
                    TaskStatus.WAITING_APPROVAL,
                )
                event = await self._append_event(
                    session,
                    task_id,
                    event_type="plan_ready",
                    status=TaskStatus.WAITING_APPROVAL.value,
                    payload={"revision": plan.version, "topic": plan.topic},
                )
                await session.commit()

            await self._publish(event)
            logger.info("task_planned", task_id=str(task_id), revision=plan.version)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("task_planning_failed", task_id=str(task_id))
            await self._mark_waiting_human(task_id, f"规划失败: {exc}")

    async def _generate_plan(
        self,
        planner: Any,
        requirement: str,
        trace_id: str,
    ) -> tuple[TaskPlan, TaskDAG]:
        """调用 Planner；第一次失败后执行且仅执行一次 schema 修复调用。"""
        try:
            result = await planner.execute({"requirement": requirement, "trace_id": trace_id})
            return self._validate_plan_result(result)
        except Exception as first_error:
            logger.warning(
                "planner_retry_with_schema_repair",
                error=str(first_error),
            )
            repair = {
                "error": str(first_error),
                "previous_output": getattr(first_error, "raw_output", ""),
            }
            result = await planner.execute(
                {"requirement": requirement, "trace_id": trace_id, "repair": repair}
            )
            return self._validate_plan_result(result)

    @staticmethod
    def _validate_plan_result(result: Any) -> tuple[TaskPlan, TaskDAG]:
        """校验 Planner 输出：TaskPlan schema + TaskDAG 依赖合法。"""
        if not isinstance(result, dict):
            raise ValueError("规划输出不是字典")
        plan_payload = result.get("plan")
        if not isinstance(plan_payload, dict):
            raise ValueError("规划输出缺少 plan")
        plan = TaskPlan.model_validate(plan_payload)

        dag_payload = result.get("dag")
        if isinstance(dag_payload, dict):
            dag = TaskDAG.from_dict(dag_payload)
        else:
            from app.orchestration.agents.planner import PlannerAgent

            dag = PlannerAgent.build_dag(plan)
        return plan, dag

    # ===== 后台流程：执行 =====

    async def _execute(self, task_id: UUID) -> None:
        """后台执行：调度未完成节点 → 复盘 → 完成；支持重启后续跑。"""
        try:
            (
                dag,
                completed,
                subtask_ids,
                trace_id,
                block_reason,
                recovery_event,
                task,
            ) = await self._load_execution_state(task_id)
            if recovery_event is not None:
                await self._publish(recovery_event)
            if dag is None:
                if block_reason:
                    await self._mark_waiting_human(task_id, block_reason)
                return

            observer = self.container.create_observer()
            scheduler = self.container.create_scheduler(observer)
            workflow = self._literature_execution_workflow(task)
            if workflow is not None:
                scheduler = TaskScheduler(
                    agent_factory=self._literature_agent_factory(workflow, task),
                    observer=observer,
                )
            result = await scheduler.run(
                dag,
                task_id=str(task_id),
                pre_completed=completed,
                trace_id=trace_id,
                subtask_ids=subtask_ids,
            )

            if not result.success:
                await self._finish_waiting_human(task_id, result)
                return

            await self._start_reviewing(task_id, result)
            await self._finish_reviewing(task_id, result, dag.topological_order())
        except asyncio.CancelledError:
            logger.info("task_execution_cancelled", task_id=str(task_id))
            raise
        except Exception as exc:
            logger.exception("task_execution_failed", task_id=str(task_id))
            await self._mark_waiting_human(task_id, f"执行失败: {exc}")

    async def _load_execution_state(
        self,
        task_id: UUID,
    ) -> tuple[
        TaskDAG | None,
        set[str],
        dict[str, str],
        str,
        str | None,
        TaskEvent | None,
        Any | None,
    ]:
        """读取 DAG、已完成节点、子任务 ID 映射、trace ID、恢复事件与任务对象。

        返回 (dag, completed, subtask_ids, trace_id, block_reason, recovery_event, task)；
        dag 为 None 且 block_reason 非空时表示任务数据不完整，需要转入人工处理。
        已完成节点与遗留运行态节点的恢复统一委托 `RecoveryManager.load_state()`。
        """
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            try:
                task = await repo.lock_for_update(task_id)
            except LookupError:
                return None, set(), {}, "task", None, None, None
            if task.status not in {status.value for status in _RECOVERABLE_STATUSES}:
                return None, set(), {}, "task", None, None, None
            if not task.plan:
                return None, set(), {}, "task", "任务缺少规划数据", None, task

            state = await RecoveryManager.load_state(session, task_id)
            if state is None:
                return None, set(), {}, "task", "任务缺少 DAG 数据", None, task

            subtask_rows = (
                await session.execute(select(Subtask).where(Subtask.task_id == task_id))
            ).scalars()
            subtask_ids = {row.node_id: str(row.id) for row in subtask_rows}
            # 重置遗留运行态节点需要落库，恢复事件在提交后由调用方发布
            await session.commit()
            return (
                state.dag,
                state.pre_completed,
                subtask_ids,
                self._trace_id(task),
                None,
                state.event,
                task,
            )

    # ===== 文献场景接线 =====

    def _literature_workflow(self, task: Any) -> LiteratureReviewWorkflow:
        """构造规划用工作流（Task 3 只接规划；执行接线见 `_literature_execution_workflow`）。"""
        return LiteratureReviewWorkflow(planner=self.container.create_planner())

    def _literature_execution_workflow(self, task: Any | None) -> LiteratureReviewWorkflow | None:
        """文献任务且容器提供知识库时，返回证据约束执行工作流；否则 None（走通用 Agent）。"""
        if task is None or getattr(task, "task_type", "") != "literature_review":
            return None
        knowledge = self.container.knowledge_query
        if knowledge is None:
            return None
        try:
            plan = TaskPlan.model_validate(task.plan or {})
        except PydanticValidationError:
            logger.warning("literature_plan_invalid_for_execution", task_id=str(task.id))
            return None
        workflow = LiteratureReviewWorkflow(
            executor=self.container.create_agent("executor"),
            validator=ValidatorAgent(
                # 语义补充校验需要 LLM；确定性引用校验始终先跑（Plan 5 Task 1 实跑发现此前漏注入）
                llm=self.container.create_llm(),
                rules={"min_length": 1, "required_keywords": []},
                citation_validator=CitationValidator(),
            ),
            context_builder=LiteratureContextBuilder(knowledge),
            # 离线演示任务走冻结清单；其余用容器注入的源或 arXiv 适配器
            source=resolve_literature_source(task, fallback=self.container.literature_source),
        )
        return workflow.use_plan(plan)

    def _literature_agent_factory(
        self,
        workflow: LiteratureReviewWorkflow,
        task: Any,
    ) -> Any:
        """按类型返回文献场景 Agent（executor 走证据约束，validator 走工作流校验）。"""

        def factory(agent_type: str) -> Any:
            if agent_type == "executor":
                return LiteratureNodeExecutor(workflow, task)
            if agent_type == "validator":
                return LiteratureNodeValidator(workflow, task)
            return self.container.create_agent(agent_type)

        return factory

    async def _finish_waiting_human(self, task_id: UUID, result: Any) -> None:
        """调度未全部完成：任务转入 WAITING_HUMAN 并记录事件。"""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            if task.status != TaskStatus.RUNNING.value:
                return
            await repo.transition(task, {TaskStatus.RUNNING}, TaskStatus.WAITING_HUMAN)
            event = await self._append_event(
                session,
                task_id,
                event_type="task_waiting_human",
                status=TaskStatus.WAITING_HUMAN.value,
                payload={
                    "completed": result.completed,
                    "waiting_human": result.waiting_human,
                },
            )
            await session.commit()
        await self._publish(event)

    async def _start_reviewing(self, task_id: UUID, result: Any) -> None:
        """调度全部成功：RUNNING → REVIEWING 并发布事件。"""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            if task.status != TaskStatus.RUNNING.value:
                return
            await repo.transition(task, {TaskStatus.RUNNING}, TaskStatus.REVIEWING)
            event = await self._append_event(
                session,
                task_id,
                event_type="task_status",
                status=TaskStatus.REVIEWING.value,
                payload={"node_count": result.total_nodes},
            )
            await session.commit()
        await self._publish(event)

    async def _finish_reviewing(
        self,
        task_id: UUID,
        result: Any,
        ordered_nodes: list[str] | None = None,
    ) -> None:
        """复盘汇总 → 导出产物 → COMPLETED（同一事务写入 final_output/review_report/artifacts）。"""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            if task.status != TaskStatus.REVIEWING.value:
                return
            trace_id = self._trace_id(task)
            task_type = task.task_type
            user_id = task.user_id
            title = task.title
            event_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(ExecutionLog)
                        .where(ExecutionLog.task_id == task_id)
                    )
                ).scalar_one()
            )

        reviewer = self.container.create_agent("reviewer")
        if task_type == "literature_review":
            execution_results = self._ordered_execution_results(result, ordered_nodes or [])
            validations = self._ordered_validations(result, ordered_nodes or [])
            review_report = await reviewer.execute(
                {
                    "trace_id": trace_id,
                    "topic": title,
                    "results": [item.model_dump(mode="json") for item in execution_results],
                    "validations": validations,
                    "event_stats": {"event_count": event_count, "task_id": str(task_id)},
                }
            )
            artifacts = self._export_literature_artifacts(user_id, task_id, review_report)
        else:
            review_report = await reviewer.execute(
                {
                    "trace_id": trace_id,
                    "logs": [],
                    "results": {
                        node_id: {
                            "status": node_result.status.value,
                            "error": node_result.error,
                            "attempts": node_result.attempts,
                        }
                        for node_id, node_result in result.node_results.items()
                    },
                }
            )
            artifacts = [{"type": "review_summary", "summary": review_report.get("summary", "")}]

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            if task.status != TaskStatus.REVIEWING.value:
                return
            task.review_report = review_report
            task.final_output = str(
                review_report.get("final_markdown") or review_report.get("summary") or ""
            )
            task.artifacts = artifacts
            await repo.transition(task, {TaskStatus.REVIEWING}, TaskStatus.COMPLETED)
            event = await self._append_event(
                session,
                task_id,
                event_type="task_completed",
                status=TaskStatus.COMPLETED.value,
                payload={
                    "summary": str(review_report.get("summary", ""))[:500],
                    "artifacts": [
                        item.get("artifact_id")
                        for item in artifacts
                        if isinstance(item, dict) and item.get("artifact_id")
                    ],
                },
            )
            await session.commit()
        await self._publish(event)
        logger.info("task_completed", task_id=str(task_id), artifacts=len(artifacts))

    # ===== 复盘产物 =====

    @staticmethod
    def _ordered_execution_results(
        result: Any,
        ordered_nodes: list[str],
    ) -> list[ExecutionResult]:
        """按 DAG 拓扑序还原类型化执行结果；缺少类型化负载时退化为正文结果。"""
        items: list[ExecutionResult] = []
        for node_id in ordered_nodes:
            node_result = result.node_results.get(node_id)
            if node_result is None:
                continue
            output = node_result.output if isinstance(node_result.output, dict) else {}
            payload = output.get("execution_result")
            if isinstance(payload, dict):
                try:
                    items.append(ExecutionResult.model_validate(payload))
                    continue
                except PydanticValidationError:
                    pass
            content = str(output.get("result") or "").strip()
            if content:
                items.append(ExecutionResult(node_id=node_id, content=content, duration_ms=0))
        return items

    @staticmethod
    def _ordered_validations(result: Any, ordered_nodes: list[str]) -> list[dict[str, Any]]:
        """按 DAG 拓扑序收集已持久化的校验报告。"""
        validations: list[dict[str, Any]] = []
        for node_id in ordered_nodes:
            node_result = result.node_results.get(node_id)
            if node_result is None or not isinstance(node_result.validation, dict):
                continue
            validations.append(node_result.validation)
        return validations

    def _export_literature_artifacts(
        self,
        user_id: UUID,
        task_id: UUID,
        review_report: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """导出 Markdown/DOCX；复盘输出不符合 ReviewReport 时退化为摘要产物。"""
        try:
            return self._export_artifacts(user_id, task_id, review_report)
        except PydanticValidationError:
            logger.warning("literature_review_report_invalid", task_id=str(task_id))
            return [
                {
                    "type": "review_summary",
                    "summary": str(review_report.get("summary", "")),
                }
            ]

    def _export_artifacts(
        self,
        user_id: UUID,
        task_id: UUID,
        review_report: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """把 ReviewReport 导出为 Markdown/DOCX，返回可持久化的 artifact 元数据。"""
        from app.orchestration.contracts import ReviewReport
        from app.tools.literature.exporter import LiteratureExporter

        exporter = LiteratureExporter(get_settings().app.artifact_root)
        # 复盘报告可能带 summary 等附加键，导出前只取 ReviewReport 字段
        known_fields = set(ReviewReport.model_fields)
        report = ReviewReport.model_validate(
            {key: value for key, value in review_report.items() if key in known_fields}
        )
        return [
            exporter.export_markdown(user_id, task_id, report).model_dump(mode="json"),
            exporter.export_docx(user_id, task_id, report).model_dump(mode="json"),
        ]

    async def resolve_artifact(
        self,
        user_id: UUID,
        task_id: UUID,
        artifact_id: str,
    ) -> tuple[Path, str]:
        """owner-scoped 解析产物路径与固定下载名；不存在/越权一律 404。"""
        from app.tools.literature.exporter import LiteratureExporter

        async with self._session_factory() as session:
            task = await TaskRepository(session).get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            download_name = ""
            for item in task.artifacts or []:
                if isinstance(item, dict) and item.get("artifact_id") == artifact_id:
                    download_name = str(item.get("download_name") or "")
                    break

        exporter = LiteratureExporter(get_settings().app.artifact_root)
        path = exporter.resolve_for_user(user_id, task_id, artifact_id)
        return path, download_name or f"artifact-{artifact_id}{path.suffix}"

    # ===== 控制命令实现 =====

    async def _control_pause(
        self,
        session: Any,
        repo: TaskRepository,
        task: Task,
        status: TaskStatus,
    ) -> TaskEvent:
        if status not in _PAUSABLE_STATUSES:
            raise ValidationException("当前状态不允许暂停", details={"status": status.value})
        task.paused_from_status = status.value
        await repo.transition(task, {status}, TaskStatus.PAUSED)
        return await self._append_event(
            session,
            task.id,
            event_type="task_status",
            status=TaskStatus.PAUSED.value,
            payload={"from": status.value},
        )

    async def _control_resume(
        self,
        session: Any,
        repo: TaskRepository,
        task: Task,
        status: TaskStatus,
    ) -> TaskEvent:
        if status is not TaskStatus.PAUSED or not task.paused_from_status:
            raise ValidationException("任务当前不在暂停状态", details={"status": status.value})
        target = TaskStatus(task.paused_from_status)
        await repo.transition(task, {TaskStatus.PAUSED}, target)
        return await self._append_event(
            session,
            task.id,
            event_type="task_status",
            status=target.value,
            payload={"from": TaskStatus.PAUSED.value},
        )

    async def _control_cancel(
        self,
        session: Any,
        repo: TaskRepository,
        task: Task,
        status: TaskStatus,
    ) -> TaskEvent:
        if status in _TERMINAL_STATUSES or status not in _CANCELLABLE_STATUSES:
            raise ValidationException("当前状态不允许取消", details={"status": status.value})
        await repo.transition(task, {status}, TaskStatus.CANCELLED)
        return await self._append_event(
            session,
            task.id,
            event_type="task_status",
            status=TaskStatus.CANCELLED.value,
            payload={"from": status.value},
        )

    async def _control_retry_node(
        self,
        session: Any,
        task: Task,
        status: TaskStatus,
        node_id: str | None,
    ) -> TaskEvent:
        if status not in {TaskStatus.WAITING_HUMAN, TaskStatus.FAILED}:
            raise ValidationException(
                "仅等待人工/失败状态允许重跑节点",
                details={"status": status.value},
            )
        subtask = await session.scalar(
            select(Subtask).where(Subtask.task_id == task.id, Subtask.node_id == node_id)
        )
        if subtask is None:
            raise NotFoundException("子任务不存在")
        subtask.status = "ready"
        subtask.completed_at = None
        await TaskRepository(session).transition(
            task,
            {TaskStatus.WAITING_HUMAN, TaskStatus.FAILED},
            TaskStatus.RUNNING,
        )
        return await self._append_event(
            session,
            task.id,
            event_type="node_retry",
            status=TaskStatus.RUNNING.value,
            subtask_id=subtask.id,
            payload={"node_id": node_id},
        )

    async def _control_supply_input(
        self,
        session: Any,
        repo: TaskRepository,
        task: Task,
        status: TaskStatus,
        human_input: dict[str, Any] | None,
    ) -> TaskEvent:
        task.config = {**(task.config or {}), "human_input": human_input or {}}
        new_status = status
        if status is TaskStatus.WAITING_HUMAN:
            await repo.transition(task, {TaskStatus.WAITING_HUMAN}, TaskStatus.RUNNING)
            new_status = TaskStatus.RUNNING
        return await self._append_event(
            session,
            task.id,
            event_type="human_input",
            status=new_status.value,
            payload={"keys": sorted((human_input or {}).keys())},
        )

    # ===== 辅助 =====

    async def _lock_owned(
        self,
        session: Any,
        repo: TaskRepository,
        user_id: UUID,
        task_id: UUID,
    ) -> Task:
        """锁定任务行并校验归属；越权与不存在统一 404。"""
        try:
            task = await repo.lock_for_update(task_id)
        except LookupError as exc:
            raise NotFoundException("任务不存在") from exc
        if task.user_id != user_id:
            raise NotFoundException("任务不存在")
        return task

    async def _build_snapshot(self, session: Any, task: Task) -> TaskSnapshot:
        """由 ORM 任务与子任务构造 TaskSnapshot。"""
        rows = (
            (await session.execute(select(Subtask).where(Subtask.task_id == task.id)))
            .scalars()
            .all()
        )
        subtasks = [self._subtask_snapshot(row) for row in rows]
        # 时间戳单独查询：Core UPDATE 后 ORM 对象的 onupdate 列可能已过期
        timestamp_row = (
            await session.execute(
                select(Task.created_at, Task.updated_at).where(Task.id == task.id)
            )
        ).one_or_none()
        created_at, updated_at = timestamp_row if timestamp_row is not None else (None, None)
        return TaskSnapshot(
            task_id=task.id,
            status=task.status,
            title=task.title,
            plan_version=task.plan_version,
            revision=task.plan_version,
            plan=task.plan,
            subtasks=subtasks,
            artifacts=list(task.artifacts or []),
            review_report=task.review_report,
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _subtask_snapshot(row: Subtask) -> SubtaskSnapshot:
        data = row.output_data if isinstance(row.output_data, dict) else {}
        output = data.get("output") if isinstance(data.get("output"), dict) else data or None
        # 校验结果（passed/retryable/checks）必须随快照下发：
        # 界面节点详情与验收脚本都靠它判断「为什么停在 WAITING_HUMAN」（Plan 5 Task 2 发现此前被丢弃）
        validation = data.get("validation")
        if isinstance(validation, dict):
            output = {**(output or {}), "validation": validation}
        return SubtaskSnapshot(
            node_id=row.node_id,
            title=row.title,
            status=row.status,
            retry_count=row.retry_count,
            started_at=row.started_at,
            completed_at=row.completed_at,
            output=output,
            error=data.get("error") if isinstance(data, dict) else None,
        )

    async def _append_event(
        self,
        session: Any,
        task_id: UUID,
        *,
        event_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
        subtask_id: UUID | None = None,
    ) -> TaskEvent:
        """在当前事务内追加任务事件（调用方负责 commit）。"""
        return await EventRepository(session).append(
            task_id,
            event_type=event_type,
            status=status,
            subtask_id=subtask_id,
            payload=payload or {},
        )

    async def _publish(self, event: TaskEvent | None) -> None:
        """提交后发布事件；发布失败不影响已提交状态。"""
        if event is None:
            return
        try:
            await self.container.event_publisher.publish(event)
        except Exception:
            logger.warning(
                "task_event_publish_failed",
                event_id=event.event_id,
                event_type=event.event_type,
            )

    async def _mark_waiting_human(self, task_id: UUID, reason: str) -> None:
        """把执行中任务标记为 WAITING_HUMAN 并记录失败原因。"""
        try:
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                current = TaskStatus(task.status)
                if current in _TERMINAL_STATUSES or current is TaskStatus.WAITING_HUMAN:
                    return
                if current not in {
                    TaskStatus.PLANNING,
                    TaskStatus.RUNNING,
                    TaskStatus.REVIEWING,
                    TaskStatus.RETRYING,
                }:
                    return
                await repo.transition(task, {current}, TaskStatus.WAITING_HUMAN)
                event = await self._append_event(
                    session,
                    task_id,
                    event_type="task_waiting_human",
                    status=TaskStatus.WAITING_HUMAN.value,
                    payload={"reason": reason[:2000]},
                )
                await session.commit()
            await self._publish(event)
        except Exception:
            logger.exception("task_mark_waiting_human_failed", task_id=str(task_id))

    @staticmethod
    def _describe(request: LiteratureReviewRequest) -> str:
        """把结构化请求压成任务描述（供规划 Agent 作为需求输入）。"""
        parts = [f"研究主题：{request.topic}"]
        parts.append(f"关键词：{', '.join(request.keywords)}")
        if request.date_from or request.date_to:
            parts.append(f"时间范围：{request.date_from or '不限'} ~ {request.date_to or '不限'}")
        parts.append(f"文献数量：{request.max_documents}")
        parts.append(f"输出语言：{request.output_language}")
        parts.append(f"引用格式：{request.citation_style}")
        if request.document_ids:
            parts.append(f"指定文献数：{len(request.document_ids)}")
        if request.offline_dataset:
            parts.append("使用离线语料")
        return "；".join(parts)

    @staticmethod
    def _requirement(task: Task) -> str:
        """从任务记录重建规划需求文本。"""
        config = task.config or {}
        keywords = config.get("keywords") or []
        parts = [f"研究主题：{task.title}"]
        if keywords:
            parts.append(f"关键词：{', '.join(str(item) for item in keywords)}")
        if config.get("output_language"):
            parts.append(f"输出语言：{config['output_language']}")
        parts.append("输出带可回溯引用的文献综述")
        return "；".join(parts)

    @staticmethod
    def _trace_id(task: Task | None) -> str:
        if task is None:
            return "task"
        config = task.config or {}
        return str(config.get("trace_id") or f"task-{task.id}")
