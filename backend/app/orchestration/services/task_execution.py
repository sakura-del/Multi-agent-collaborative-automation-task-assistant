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
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.exceptions import HumanActionRequiredError, NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.log import ExecutionLog
from app.db.models.task import Subtask, Task
from app.db.repositories.event import EventRepository
from app.db.repositories.task import TaskRepository
from app.knowledge.services.citation_validator import CitationValidator, ReferenceCheckMode
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
    TaskListSummary,
    TaskPlan,
    TaskSnapshot,
    TaskStatus,
)
from app.orchestration.dag_engine import TaskDAG
from app.orchestration.recovery import RecoveryManager
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.scheduler import TaskScheduler
from app.orchestration.services.literature_source import (
    is_offline_literature_task,
    resolve_literature_source,
)
from app.orchestration.workflows.literature_review import (
    LiteratureNodeExecutor,
    LiteratureNodeValidator,
    LiteratureReviewWorkflow,
)

logger = get_logger(__name__)


def _safe_download_stem(title: str) -> str:
    """任务标题 → 安全的下载文件名主干（保留中文，去掉路径分隔符与控制字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or "literature")[:60]


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

#: 单次文献综述任务的 token 预估（提交时按此预扣配额，结束后按实际用量校正）。
#:
#: 取值依据：一次综述约 5 类节点 × 若干子任务，历史实测中位数量级在 10 万 token 上下；
#: 这里取整为 100k 作为**保守预估**——宁可先扣多了再退还，也不要先扣少了超支。
ESTIMATED_TOKENS_PER_TASK = 100_000


def _tokens_from_payload(payload: Mapping[str, Any]) -> int:
    """从一份 payload 读 token（`prompt_tokens + completion_tokens`，缺省按 0）。"""
    return int(payload.get("prompt_tokens") or 0) + int(payload.get("completion_tokens") or 0)


def sum_actual_tokens(node_results: Mapping[str, Any]) -> int:
    """从 DAG 各节点结果汇总**实际** token 用量。

    ## 两个位置都要看（2026-10-26 用量看板修）

    生产载荷（`LiteratureNodeExecutor._run`）把 token 放在
    `output["execution_result"]` **里面**：

    ```python
    {"node_id": ..., "result": ..., "status": "completed",
     "execution_result": {"prompt_tokens": N, "completion_tokens": M, ...}}
    ```

    而历史测试替身与老 Agent 把 token **平铺在 output 顶层**。旧实现只读顶层 →
    生产恒为 0 → `task.used_tokens` 永远是 0。后果有两层：

    1. **成本失控**：配额结算按 `100k − used_tokens` 退差额，用量记 0 就是每次
       任务白嫖满额算力（实测服务器 42 个任务共 169372 token 全被记成 0）；
    2. **看板说谎**：`/observability/overview` 的「累计 token」直接读 `used_tokens`，
       于是看板永远显示 0——用户看不出来，因为 0 看起来像「还没用过」。

    ## 嵌套优先、只算一次

    同一节点若两处都有值，**只取嵌套那一份**（生产权威来源）。两处相加会让用量
    翻倍，等于多扣用户额度——比少记更糟。
    """
    total = 0
    for node_result in node_results.values():
        output = getattr(node_result, "output", None)
        if not isinstance(output, Mapping):
            continue
        nested = output.get("execution_result")
        if isinstance(nested, Mapping):
            total += _tokens_from_payload(nested)
        else:
            total += _tokens_from_payload(output)
    return total


async def quota_refund_for_usage(quota: Any | None, user_id: UUID, *, actual_tokens: int) -> None:
    """按实际用量退还预扣的配额差额。

    语义：
    - `quota is None`（配额关闭）→ 空操作；
    - 实际 >= 预估 → **不退还也不补扣**（预估值是保守上限；补扣会让下一个任务莫名失败）；
    - **fail-open**：配额组件故障只记 warning，绝不能让已完成的任务因此失败。
    """
    if quota is None:
        return
    refund_tokens = ESTIMATED_TOKENS_PER_TASK - max(0, int(actual_tokens))
    if refund_tokens <= 0:
        return
    try:
        await quota.refund(user_id, tokens=refund_tokens)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        get_logger(__name__).warning(
            "quota_refund_failed",
            user_id=str(user_id),
            tokens=refund_tokens,
            error=str(exc),
        )


def _dag_node_types(dag: Any) -> dict[str, str]:
    """从 DAG 图里取 `{node_id: type}`（可观测性用）。

    `TaskDAG` 把节点属性存在 networkx 的 `graph.nodes[nid]` 上，键名是 `type`
    （见 `PlannerAgent` 的 `dag.add_node(..., type=node.type)`）。
    **不要**从节点执行结果里猜类型：不同 agent 返回结构不同，会大量退化成 unknown。
    """
    try:
        graph = dag.graph
        return {
            str(node_id): str(attrs.get("type") or "") for node_id, attrs in graph.nodes.items()
        }
    except Exception:  # pragma: no cover - 防御性兜底
        return {}


class TaskExecution:
    """任务运行时深模块：规划、确认、执行、恢复与查询。"""

    def __init__(
        self,
        container: RuntimeContainer,
        *,
        session_factory: Any | None = None,
        quota_service: Any | None = None,
    ) -> None:
        self.container = container
        self._session_factory = session_factory or container.session_factory
        # 配额服务（P1-D）：提交时预扣、完成时按实际用量退还差额。
        # None 表示配额关闭（`quota.enabled=false`）或未接线 → 全部跳过。
        self._quota = quota_service

    # ===== 配额结算（P1-D 遗留修复）=====

    async def add_used_tokens(self, task_id: UUID, tokens: int) -> None:
        """累加任务实际消耗的 token（**增量**，不覆盖）。

        为什么增量而不是赋值：任务可能分多段跑（重启续跑、重试节点），
        每段的 `node_results` 只含本段节点；累加才能得到全过程用量。

        失败只记 warning：用量记账失败不该让正在执行的任务崩掉
        （最坏情况是少退额度——宁可少退也不多退）。
        """
        amount = int(tokens)
        if amount <= 0:
            return
        try:
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                task.used_tokens = int(task.used_tokens or 0) + amount
                await session.commit()
        except LookupError:
            return
        except Exception as exc:  # pragma: no cover - 防御性兜底
            logger.warning(
                "task_used_tokens_add_failed",
                task_id=str(task_id),
                tokens=amount,
                error=str(exc),
            )

    async def settle_quota(self, task_id: UUID) -> None:
        """按实际用量结算配额（**幂等**：每个任务只结算一次）。

        退款额 = 预扣 − `used_tokens`；实际超出预扣则不补扣（与既有语义一致）。

        **顺序很重要**：先在事务里「判定 + 置标记 + commit」，**再**调
        `quota.refund()`。原因：退款写的是 Redis，事务回滚不了外部副作用；
        若先退后标记，一旦标记失败下次还会再退（额度越退越多）。
        宁可少退（Redis 故障时标记已落库，不再重试）也不可多退。
        """
        refund_tokens: int | None = None
        user_id: UUID | None = None
        try:
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                if task.quota_settled:
                    return  # 已结算：绝不重复退
                task.quota_settled = True
                used = int(task.used_tokens or 0)
                refund_tokens = ESTIMATED_TOKENS_PER_TASK - used
                user_id = task.user_id
                await session.commit()
        except LookupError:
            # 任务不存在（已被清理）：无事可做，不当作错误
            return
        except Exception as exc:  # pragma: no cover - 防御性兜底
            logger.warning("quota_settlement_failed", task_id=str(task_id), error=str(exc))
            return

        if user_id is None or refund_tokens is None or refund_tokens <= 0:
            return
        if self._quota is None:
            return
        try:
            await self._quota.refund(user_id, tokens=refund_tokens)
        except Exception as exc:  # pragma: no cover - fail-open
            logger.warning(
                "quota_refund_failed",
                task_id=str(task_id),
                user_id=str(user_id),
                tokens=refund_tokens,
                error=str(exc),
            )
            return
        logger.info(
            "quota_settled",
            task_id=str(task_id),
            user_id=str(user_id),
            refunded=refund_tokens,
        )

    async def _prereserve_quota(self, user_id: UUID, task_id: UUID) -> None:
        """从「已结算」状态**继续执行**前，重新预扣剩余额度（`ESTIMATED − used`）。

        为什么必须重新扣：`WAITING_APPROVAL` / `WAITING_HUMAN` → 继续执行时，
        预扣已在转态时退还；不重新扣就等于「先结算、再免费跑完」。

        额度不足时抛 `QuotaExceededError`（HTTP 429），**拒绝继续**
        —— 明确告诉用户额度不够，而不是跑到一半失败留下半成品。
        """
        if self._quota is None:
            return
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            used = int(task.used_tokens or 0)
            already_settled = bool(task.quota_settled)
            if not already_settled:
                # 尚未结算（例如任务从未转态过）：预扣仍在，无需重复扣
                return
            remaining = ESTIMATED_TOKENS_PER_TASK - used
            if remaining <= 0:
                return  # 额度已用完（甚至超出）：不再扣，执行阶段自行按 0 额度继续
            # 先扣再解除标记：扣失败（额度不足）时保持「已结算」状态，
            # 用户重新充值后再确认仍然成立，不会出现「标记解了但没扣到钱」。
            await self._quota.check_and_consume(user_id, tokens=remaining)
            task.quota_settled = False
            await session.commit()
        logger.info("quota_prereserved", task_id=str(task_id), tokens=remaining)

    async def _undo_prereserve(self, user_id: UUID, task_id: UUID) -> None:
        """撤销一次预扣（并发冲突时回滚已扣的额度）。

        为什么需要：`approve_plan` 分两步（校验 → 预扣 → 再校验 → 转态），
        预扣与转态之间状态可能被并发改变。若不撤销，用户会为一次**未发生**的
        执行白付额度。
        """
        if self._quota is None:
            return
        try:
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                used = int(task.used_tokens or 0)
                if not task.quota_settled:
                    return  # 预扣并未生效（未结算态），无需撤销
                remaining = ESTIMATED_TOKENS_PER_TASK - used
                task.quota_settled = True
                await session.commit()
            if remaining > 0:
                await self._quota.refund(user_id, tokens=remaining)
        except Exception as exc:  # pragma: no cover - fail-open
            logger.warning(
                "quota_prereserve_undo_failed",
                task_id=str(task_id),
                error=str(exc),
            )

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
        """确认规划：版本匹配且处于 WAITING_APPROVAL 时进入 RUNNING 并开始执行。

        `WAITING_APPROVAL` 期间预扣是**已退还**的（见 `_plan` 的结算），所以确认时
        要按 `ESTIMATED − used` 重新预扣；额度不足则拒绝确认（429），
        而不是跑到一半因额度问题失败。
        """
        # 先校验状态与版本，**再**预扣：否则状态不对的请求也会白扣额度
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
        await session.commit()

        # 重新预扣：额度不足时抛 429，任务保持 WAITING_APPROVAL 不动
        await self._prereserve_quota(user_id, task_id)

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await self._lock_owned(session, repo, user_id, task_id)
            if task.status != TaskStatus.WAITING_APPROVAL.value:
                # 预扣期间状态被并发改变（另一处确认/取消）→ 撤销预扣并报冲突
                await self._undo_prereserve(user_id, task_id)
                raise ValidationException(
                    "任务当前不在等待确认状态",
                    details={"status": task.status},
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
        #: 取消是终态 → 需要结算配额
        settle_after_commit = False
        #: 从「已结算」状态继续执行（转人工后重试/补输入）→ 需要重新预扣
        prereserve_after_commit = False
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
                settle_after_commit = True
            elif command is TaskCommand.RETRY_NODE:
                events.append(
                    await self._control_retry_node(session, task, status, request.node_id)
                )
                retrack = True
                prereserve_after_commit = True
            elif command is TaskCommand.SUPPLY_INPUT:
                events.append(
                    await self._control_supply_input(session, repo, task, status, request.input)
                )
                retrack = TaskStatus(task.status) is TaskStatus.RUNNING
                prereserve_after_commit = retrack
            else:  # pragma: no cover - 枚举已穷尽
                raise ValidationException(f"不支持的任务命令: {command}")

            snapshot = await self._build_snapshot(session, task)
            await session.commit()

        for event in events:
            await self._publish(event)
        if cancel_after_commit:
            await self.container.cancel(task_id)
        if settle_after_commit:
            # 取消是终态：按已用额度结算预扣（否则预扣永久占用）
            await self.settle_quota(task_id)
            # 可观测性：在 commit 之后打点，避免「事务回滚但指标已 +1」
            await self._record_task_terminal(task_id, TaskStatus.CANCELLED.value)
        if prereserve_after_commit:
            # 从「已结算」状态继续跑：必须重新预扣剩余额度
            await self._prereserve_quota(user_id, task_id)
        if retrack and not self.container.is_tracking(task_id):
            self.container.track(task_id, self._execute(task_id))
        logger.info("task_control", task_id=str(task_id), command=request.command.value)
        return snapshot

    async def get_snapshot(
        self,
        user_id: UUID,
        task_id: UUID,
        *,
        workspace_ids: Sequence[UUID] = (),
    ) -> TaskSnapshot:
        """查询任务快照（**归属双轨**；越权与不存在统一 404）。

        `workspace_ids` 是调用者所属的工作区 id（Phase 2 团队协作）。
        **默认空序列 = 旧行为**（只认 owner），这样存量调用点（后台流程、
        内部诊断脚本）不受影响。
        """
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = (
                await repo.get_visible(user_id, workspace_ids, task_id)
                if workspace_ids
                else await repo.get_for_user(user_id, task_id)
            )
            if task is None:
                raise NotFoundException("任务不存在")
            return await self._build_snapshot(session, task)

    async def list_tasks(
        self,
        user_id: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
        workspace_ids: Sequence[UUID] = (),
    ) -> list[TaskListSummary]:
        """分页列出任务摘要（**单条 SQL**，HTTP 列表接口使用）。

        `workspace_ids` 非空时按双轨可见性（个人 + 工作区）；为空时仅 owner。

        ## 为什么不再构造 TaskSnapshot（2026-11-04）

        旧实现对每个任务跑 2 条额外 SQL（subtasks + 时间戳回查），
        20 个任务 ≈ 90 条；迁移到 Supabase 后每条 SQL 有 ~380ms 往返，
        一次列表实测 34s（前端 15s 超时）。列表页只要 6 个字段，
        一条汇总查询足够；**详情接口 `get_snapshot` 保持原样**。
        """
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            rows = await repo.list_summaries(user_id, workspace_ids, skip=skip, limit=limit)
            return [
                TaskListSummary(
                    task_id=row.id,
                    status=row.status,
                    title=row.title,
                    plan_version=row.plan_version or 0,
                    revision=row.plan_version or 0,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

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
        """后台规划：生成 TaskPlan + DAG，失败时进行一次 schema 修复。

        上游模型端点可能瞬时 5xx/超时（Plan 5 Task 8 实跑遇到 503），
        因此规划调用带**有限次退避重试**；配置类错误（401/400）不重试，
        直接转人工，避免无意义等待。
        """
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
                plan = await self._retry_transient(
                    lambda: workflow.plan(task), task_id=task_id, stage="planning"
                )
                dag = PlannerAgent.build_dag(plan)
            else:
                planner = self.container.create_planner()
                plan, dag = await self._retry_transient(
                    lambda: self._generate_plan(planner, requirement, trace_id),
                    task_id=task_id,
                    stage="planning",
                )

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
            # 进入 WAITING_APPROVAL 即先结算：用户可能永远不确认执行，
            # 而预扣若一直挂着就会白占额度（2026-10-21 实测：6 个任务占满日配额）。
            # 确认执行时会重新预扣（`approve_plan` → `_prereserve_quota`）。
            await self.settle_quota(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("task_planning_failed", task_id=str(task_id))
            await self._mark_waiting_human(task_id, f"规划失败: {exc}")

    async def _retry_transient(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        task_id: UUID,
        stage: str,
        attempts: int = 4,
        base_delay: float = 1.0,
    ) -> Any:
        """对**瞬态**上游故障做有限次退避重试。

        哪些算瞬态：HTTP 5xx（含 503 Service temporarily unavailable）、
        连接错误、读超时、429。哪些不算：401/403（密钥问题）、400/422（请求非法）——
        这些重试也不会成功，直接抛出交给上层转人工。

        注意：`HumanActionRequiredError`（规划契约修复后仍非法）同样不重试。
        """
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except HumanActionRequiredError:
                raise
            except Exception as exc:
                if not self._is_transient_error(exc) or attempt >= attempts:
                    raise
                last_error = exc
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "transient_upstream_retry",
                    task_id=str(task_id),
                    stage=stage,
                    attempt=attempt,
                    max_attempts=attempts,
                    delay_s=delay,
                    error=str(exc)[:200],
                )
                await asyncio.sleep(delay)
        raise last_error if last_error else RuntimeError("重试未执行")

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        """判定是否为值得重试的瞬态上游错误。"""
        name = type(exc).__name__
        # openai SDK 的 5xx / 429 / 连接类异常
        if name in {
            "InternalServerError",
            "ServiceUnavailableError",
            "APIStatusError",
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "ConnectError",
            "ReadTimeout",
            "ConnectTimeout",
            "TimeoutException",
        }:
            return True
        # 其他库包装过的同类错误：看状态码与消息
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and (status >= 500 or status == 429):
            return True
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "service temporarily unavailable",
                "temporarily unavailable",
                "connection error",
                "timed out",
                "read timeout",
                "connection reset",
                "503",
                "502",
                "504",
                "429",
            )
        )

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
            # 引用库文献源需要 DB 会话（2026-10-26 引用库计划 §3.3）：
            # 任务执行期间保持一个会话打开，供 `LibraryLiteratureSource` 查询用户引用库。
            # 不用 `async with` 包整个执行流程：执行可能持续数分钟，
            # 长事务会占住连接；这里只把它传下去，读取时由 source 自己发查询。
            async with self._session_factory() as source_session:
                workflow = self._literature_execution_workflow(task, source_session=source_session)
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

            # 先记账再判断成败：转人工 / 失败路径同样要按已用额度结算，
            # 若放在成功分支里就会漏记（「跑了 30k 然后转人工」被当成 0 用量全额退款）。
            node_results = getattr(result, "node_results", {}) or {}
            await self.add_used_tokens(task_id, sum_actual_tokens(node_results))
            # 可观测性：节点维度能回答「卡在哪个环节」，成功率只说成/败
            self._record_node_metrics(node_results, _dag_node_types(dag))

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

    def _literature_execution_workflow(
        self, task: Any | None, *, source_session: Any | None = None
    ) -> LiteratureReviewWorkflow | None:
        """文献任务且容器提供知识库时，返回证据约束执行工作流；否则 None（走通用 Agent）。

        `source_session` 用于引用库文献源（`reference_library=true` 时需要 DB 访问）。
        不传时引用库任务会抛 `ReferenceLibraryUnavailableError` —— 这是有意的：
        **绝不静默回退到外部检索**（否则用户以为在用自己导入的文献）。
        """
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
                citation_validator=CitationValidator(reference_mode=self._reference_mode()),
            ),
            context_builder=LiteratureContextBuilder(knowledge),
            # 离线演示任务走冻结清单；引用库任务走用户引用库；其余用容器注入的源或 arXiv 适配器
            source=resolve_literature_source(
                task,
                fallback=self.container.literature_source,
                session=source_session,
            ),
            # 引用真实性核验（Issue #3）：核验器未启用或离线任务（R6）时为 None
            reference_verifier=self._reference_verifier_for_task(task),
        )
        return workflow.use_plan(plan)

    @staticmethod
    def _reference_mode() -> ReferenceCheckMode:
        """核验模式：未启用或配置为 off 时返回 off（保持既有报告形状不变）。"""
        config = get_settings().reference_verification
        if not config.enabled:
            return "off"
        return config.mode

    def _reference_verifier_for_task(self, task: Any) -> Any | None:
        """校验节点是否注入核验器：按任务级开关收敛，缺省沿用 R6（离线零外呼）。"""
        if not self._should_verify_references(task):
            return None
        return self.container.reference_verifier

    @staticmethod
    def _verify_references_switch(task: Any) -> bool | None:
        """读取任务级核验开关（Issue #3）：`None` 表示沿用全局配置。"""
        config = getattr(task, "config", None)
        if not isinstance(config, dict):
            return None
        value = config.get("verify_references")
        return value if isinstance(value, bool) else None

    @classmethod
    def _should_verify_references(cls, task: Any) -> bool:
        """任务是否做外部引用核验（Issue #3 开关 + R6 默认）。

        优先级：显式 `verify_references` > 全局 `verify_offline_tasks` > 离线零外呼。
        """
        switch = cls._verify_references_switch(task)
        if switch is not None:
            return switch
        return not cls._offline_without_verification(task)

    @staticmethod
    def _offline_without_verification(task: Any) -> bool:
        """离线冻结清单任务且未显式打开 `verify_offline_tasks` → 不做外部核验。"""
        if get_settings().reference_verification.verify_offline_tasks:
            return False
        return is_offline_literature_task(task)

    def _apply_reference_verification(self, reviewer: Any, task: Any) -> None:
        """复盘 Agent 的核验器按任务类型收敛（R6 的复盘路径）。

        `RuntimeContainer._build_default_agent("reviewer")` 会注入真实核验器，而离线 e2e 的
        bibliography 有约 10 条，逐条外呼才是超时主因；离线演示必须零外呼、可复现，因此这里
        把核验器摘掉（注入式测试替身没有该属性时跳过）。

        Issue #3 起尊重任务级开关：显式 `verify_references=True` 时**保留**核验器。
        """
        if self._should_verify_references(task):
            return
        # 注入式测试替身 / 老 Agent 没有该属性（或本来就没有核验器）时直接跳过
        if getattr(reviewer, "reference_verifier", None) is None:
            return
        reviewer.reference_verifier = None

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
        """调度未全部完成：任务转入 WAITING_HUMAN 并记录事件。

        转人工意味着**本段执行结束**（用户可能随时放弃），因此按已用额度结算预扣；
        若之后 `retry_node` / `supply_input` 继续执行，会重新预扣（见 `_prereserve_quota`）。
        """
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
        await self.settle_quota(task_id)
        await self._record_task_terminal(task_id, TaskStatus.WAITING_HUMAN.value)

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
            # Issue #4：引用样式在**渲染期**生效（此前只拼进提示词）；缺省 None 表示沿用既有渲染
            task_config = task.config if isinstance(task.config, dict) else {}
            citation_style = str(task_config.get("citation_style") or "").strip() or None
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
        # R6 复盘路径：离线清单任务摘掉核验器（否则最终参考文献逐条外呼，离线演示不再可复现）
        self._apply_reference_verification(reviewer, task)
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
            artifacts = self._export_literature_artifacts(
                user_id, task_id, review_report, citation_style=citation_style
            )
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

        # P1-D：按**实际用量**结算提交时的预扣（差额退还）。
        # 用量已在 `_execute` 调度结束后记账（`add_used_tokens`），这里只做结算，
        # 否则同一批 `node_results` 会被累加两次。
        await self.settle_quota(task_id)
        logger.info(
            "quota_corrected",
            task_id=str(task_id),
            estimated=ESTIMATED_TOKENS_PER_TASK,
        )
        # 可观测性：完成是终态之一，记状态 + token 成本 + 耗时
        await self._record_task_terminal(task_id, TaskStatus.COMPLETED.value)

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
        *,
        citation_style: str | None = None,
    ) -> list[dict[str, Any]]:
        """导出 Markdown/DOCX；复盘输出不符合 ReviewReport 时退化为摘要产物。"""
        try:
            return self._export_artifacts(
                user_id, task_id, review_report, citation_style=citation_style
            )
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
        *,
        citation_style: str | None = None,
    ) -> list[dict[str, Any]]:
        """把 ReviewReport 导出为 Markdown/DOCX，返回可持久化的 artifact 元数据。"""
        from app.orchestration.contracts import ReviewReport
        from app.tools.literature.exporter import LiteratureExporter

        exporter = LiteratureExporter(
            get_settings().app.artifact_root,
            style_resolver=getattr(self.container, "style_registry", None),
        )
        # 复盘报告可能带 summary 等附加键，导出前只取 ReviewReport 字段
        known_fields = set(ReviewReport.model_fields)
        report = ReviewReport.model_validate(
            {key: value for key, value in review_report.items() if key in known_fields}
        )
        return [
            exporter.export_markdown(
                user_id, task_id, report, citation_style=citation_style
            ).model_dump(mode="json"),
            exporter.export_docx(
                user_id, task_id, report, citation_style=citation_style
            ).model_dump(mode="json"),
        ]

    async def export_references(
        self,
        user_id: UUID,
        task_id: UUID,
        *,
        fmt: Literal["bibtex", "ris"],
    ) -> tuple[str, str]:
        """把任务已持久化的参考文献导出为 BibTeX / RIS（Issue #4 M2）。

        返回 `(正文, 下载文件名)`；**不重新跑检索**，只消费 `review_report.bibliography`。
        owner-scoped：跨用户/不存在一律 404；无参考文献时 400（不返回空文件）。
        """
        from app.core.exceptions import ValidationException
        from app.tools.csl.exporters import to_bibtex, to_ris
        from app.tools.csl.models import csl_item_from_bibliography_entry

        async with self._session_factory() as session:
            task = await TaskRepository(session).get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            report = task.review_report if isinstance(task.review_report, dict) else {}
            bibliography = report.get("bibliography")
            title = task.title

        entries = bibliography if isinstance(bibliography, list) else []
        items = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            item = csl_item_from_bibliography_entry(entry)
            if item is not None:
                items.append(item)
        if not items:
            raise ValidationException("该任务暂无参考文献，无法导出")

        body = to_bibtex(items) if fmt == "bibtex" else to_ris(items)
        stem = _safe_download_stem(title)
        suffix = "bib" if fmt == "bibtex" else "ris"
        return body, f"{stem}-references.{suffix}"

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
            # 正文必须随快照下发：MCP/开放 API 的 get_task_output 读的就是它。
            # 曾经漏掉 → MCP 永远返回 markdown: null（tests 用 dict 替身掩盖了）。
            final_output=task.final_output,
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
            return
        # 转人工 = 本段执行结束：按已用额度结算预扣（规划失败时用量为 0 → 全额退还）
        await self.settle_quota(task_id)
        await self._record_task_terminal(task_id, TaskStatus.WAITING_HUMAN.value)

    # ===== 可观测性（Phase 2）=====

    async def _record_task_terminal(self, task_id: UUID, status: str) -> None:
        """记录任务终态指标（状态计数 / token 成本 / 耗时）。

        在**状态转换提交之后**调用，因此并发/重试下不会重复计数
        （转换本身有 `lock_for_update` + 状态断言兜底）。

        fail-open：指标故障绝不影响任务（`core.metrics` 内部已吞异常，
        这里再包一层，避免读库失败把终态处理搞崩）。
        """
        try:
            async with self._session_factory() as session:
                task = await TaskRepository(session).get(task_id)
                if task is None:
                    return
                used = int(task.used_tokens or 0)
                created = task.created_at
                completed = task.completed_at or task.updated_at
            from app.core import metrics as metrics_module

            metrics_module.inc_counter("maa_tasks", status=status)
            if used > 0:
                metrics_module.inc_counter("maa_task_tokens", amount=used)
            if created is not None and completed is not None:
                elapsed = (completed - created).total_seconds()
                if elapsed >= 0:
                    metrics_module.observe_histogram(
                        "maa_task_duration_seconds", elapsed, status=status
                    )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            logger.debug("task_metrics_record_failed", task_id=str(task_id), error=str(exc))

    @staticmethod
    def _record_node_metrics(
        node_results: Mapping[str, Any], node_types: Mapping[str, str] | None = None
    ) -> None:
        """记录节点执行结果（按 node_type + status）。

        为什么需要：任务成功率只说"成功/失败"，**卡在哪个环节**要靠节点维度。
        `node_type` 是低基数枚举（search/extract/write/validate/review），
        完全可以做标签。

        `node_type` 的取法：优先用 DAG 里的 `PlanNode.type`（权威），
        其次 output 里的 `execution_result.node_type`。**不要**只靠 output——
        不同 agent 的返回结构不同（测试替身只回 `{"result": ...}`），
        只靠 output 会大量退化成 `unknown`，那时指标看起来"有数据"但毫无用处。
        """
        try:
            from app.core import metrics as metrics_module

            types = node_types or {}
            for node_id, node_result in node_results.items():
                node_type = types.get(node_id)
                if not node_type:
                    output = getattr(node_result, "output", None)
                    if isinstance(output, Mapping):
                        payload = output.get("execution_result")
                        if isinstance(payload, Mapping):
                            node_type = payload.get("node_type")
                        else:
                            node_type = output.get("node_type")
                status = getattr(getattr(node_result, "status", None), "value", None)
                metrics_module.inc_counter(
                    "maa_task_nodes",
                    node_type=str(node_type or "unknown"),
                    status=str(status or "unknown"),
                )
        except Exception as exc:  # pragma: no cover - 防御性兜底
            logger.debug("node_metrics_record_failed", error=str(exc))

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
