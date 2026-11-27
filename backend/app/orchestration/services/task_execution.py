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
import inspect
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.exceptions import (
    HumanActionRequiredError,
    LLMException,
    NotFoundException,
    ValidationException,
)
from app.core.logging import get_logger
from app.db.models.log import ExecutionLog
from app.db.models.task import Subtask, Task
from app.db.repositories.event import EventRepository
from app.db.repositories.task import TaskRepository
from app.knowledge.services.citation_validator import CitationValidator, ReferenceCheckMode
from app.knowledge.services.context_builder import LiteratureContextBuilder
from app.knowledge.services.screening_service import ScreeningService, source_of
from app.llm.usage_ledger import UsageLedger, use_usage_ledger
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.contracts import (
    ExecutionResult,
    LiteratureReviewRequest,
    ModelSelectionSnapshot,
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
from app.orchestration.validation import audit_citation_alignment
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


def _set_task_reason(
    task: Task,
    *,
    waiting_reason: str | None = None,
    error: str | None = None,
) -> None:
    """在 task.config 中写入当前等待/失败原因并清除另一方（触发 SQLAlchemy dirty 检测）。"""
    cfg = dict(task.config) if isinstance(task.config, dict) else {}
    if waiting_reason is not None:
        cfg["_waiting_reason"] = waiting_reason[:2000]
        cfg.pop("_error", None)
    elif error is not None:
        cfg["_error"] = error[:2000]
        cfg.pop("_waiting_reason", None)
    task.config = cfg


def _clear_task_reasons(task: Task) -> None:
    """清理 task.config 中残留的等待/失败原因。"""
    if not isinstance(task.config, dict):
        return
    if "_waiting_reason" not in task.config and "_error" not in task.config:
        return
    cfg = dict(task.config)
    cfg.pop("_waiting_reason", None)
    cfg.pop("_error", None)
    task.config = cfg


def _summarize_waiting_reason(result: Any) -> str:
    """从调度结果中汇总需要人工介入的可读原因。"""
    waiting_ids = [str(nid) for nid in (getattr(result, "waiting_human", None) or [])]
    node_results = getattr(result, "node_results", None) or {}
    parts: list[str] = []
    for nid in waiting_ids:
        nr = node_results.get(nid)
        err = getattr(nr, "error", None) if nr is not None else None
        if isinstance(err, str) and err.strip():
            parts.append(f"{nid}: {err.strip()}")
    if not parts and isinstance(node_results, dict):
        for nid, nr in node_results.items():
            err = getattr(nr, "error", None)
            if isinstance(err, str) and err.strip():
                parts.append(f"{nid}: {err.strip()}")
    if parts:
        return "；".join(parts)[:2000]
    if waiting_ids:
        return f"节点 {', '.join(waiting_ids)} 执行需要人工介入"[:2000]
    return "任务执行需要人工介入"


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


@dataclass(frozen=True)
class TokenUsage:
    """一次执行的真实 token 拆分（用于配额与成本估算）。"""

    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt=self.prompt + other.prompt,
            completion=self.completion + other.completion,
        )


def _tokens_from_payload(payload: Mapping[str, Any]) -> TokenUsage:
    """从一份 payload 读输入/输出 token（缺省按 0）。"""
    return TokenUsage(
        prompt=int(payload.get("prompt_tokens") or 0),
        completion=int(payload.get("completion_tokens") or 0),
    )


def sum_actual_usage(node_results: Mapping[str, Any]) -> TokenUsage:
    """从 DAG 各节点结果汇总**实际** token 用量（含输入/输出拆分）。

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
    total = TokenUsage()
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


def sum_actual_tokens(node_results: Mapping[str, Any]) -> int:
    """只要总量时的便捷入口（兼容旧调用与测试）。"""
    return sum_actual_usage(node_results).total


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

    async def add_used_tokens(
        self,
        task_id: UUID,
        tokens: int,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_hit_tokens: int = 0,
    ) -> None:
        """累加任务实际消耗的 token（**增量**，不覆盖）。

        为什么增量而不是赋值：任务可能分多段跑（重启续跑、重试节点），
        每段的 `node_results` 只含本段节点；累加才能得到全过程用量。

        输入/输出拆分同时累加（Issue #15 P2）：成本估算要按不同单价计价。
        老调用只传总量时拆分保持 0，导出会标记为「按占比估算」。

        缓存命中 token 同样增量累加（Issue #22）：命中价通常只有未命中的
        1/10~1/25，落库后成本报表才能算出「优化省了多少钱」。

        失败只记 warning：用量记账失败不该让正在执行的任务崩掉
        （最坏情况是少退额度——宁可少退也不多退）。
        """
        amount = int(tokens)
        if amount <= 0:
            return
        prompt_amount = max(0, int(prompt_tokens))
        completion_amount = max(0, int(completion_tokens))
        cache_amount = max(0, int(cache_hit_tokens))
        try:
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                task.used_tokens = int(task.used_tokens or 0) + amount
                task.prompt_tokens = int(task.prompt_tokens or 0) + prompt_amount
                task.completion_tokens = int(task.completion_tokens or 0) + completion_amount
                task.cache_hit_tokens = int(task.cache_hit_tokens or 0) + cache_amount
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
        max_lock_retries = 3
        for attempt in range(max_lock_retries + 1):
            try:
                async with self._session_factory() as session:
                    repo = TaskRepository(session)
                    task = await repo.lock_for_update(task_id)
                    if task.quota_settled:
                        return  # 已结算：绝不重复退
                    fallback_base = task.fallback_token_base
                    if self._skips_platform_quota(task) and fallback_base is None:
                        # BYOK 或首次示例免扣任务从未预扣平台 token：只置标记，绝不退款
                        task.quota_settled = True
                        await session.commit()
                        logger.info("quota_settlement_skipped_byok", task_id=str(task_id))
                        return
                    task.quota_settled = True
                    used = int(task.used_tokens or 0)
                    if fallback_base is not None:
                        # 回退过平台（Issue #28）：预扣在回退时才发生，退款只退
                        # **平台实际消耗**（总用量 − 回退前基线）。BYOK 阶段花的是
                        # 用户自己的钱，算进退款等于凭空发额度。
                        platform_used = max(0, used - int(fallback_base))
                        refund_tokens = ESTIMATED_TOKENS_PER_TASK - platform_used
                    else:
                        refund_tokens = ESTIMATED_TOKENS_PER_TASK - used
                    user_id = task.user_id
                    await session.commit()
                break
            except LookupError:
                # 任务不存在（已被清理）：无事可做，不当作错误
                return
            except OperationalError as exc:
                msg = str(exc).lower()
                is_transient_lock = any(k in msg for k in ("locked", "busy", "deadlock"))
                if is_transient_lock and attempt < max_lock_retries:
                    await asyncio.sleep(0.05 * (attempt + 1))
                    continue
                logger.warning("quota_settlement_failed", task_id=str(task_id), error=str(exc))
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
            if self._skips_platform_quota(task):
                # BYOK 或首次示例免扣任务不占平台 token：继续执行时也不该重新预扣
                return
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
                if self._skips_platform_quota(task):
                    # 未预扣过，撤销就是凭空加额度
                    return
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

    async def submit(
        self,
        user_id: UUID,
        request: LiteratureReviewRequest,
        *,
        llm_credential_id: UUID | None = None,
        model_snapshot: dict[str, Any] | None = None,
    ) -> UUID:
        """创建任务：持久化 CREATED → PLANNING，提交后启动后台规划。

        `llm_credential_id` 只影响「用哪把 key」：运行时由 `_bind_task_llm`
        解析并在当前协程内绑定。**不写进 `config`**（那是编排层合约）。
        """
        trace_id = uuid.uuid4().hex
        config = request.model_dump(mode="json")
        config["trace_id"] = trace_id

        async with self._session_factory() as session:
            reference_library_count: int | None = None
            repo = TaskRepository(session)
            inherited_context: dict[str, Any] | None = None
            if request.source_task_id is not None:
                # Phase 2：开题报告可继承已完成的文献综述（研究空白 + 基线摘要）。
                # 归属/状态校验失败统一给可读原因，绝不静默忽略 source_task_id。
                source = await repo.get_for_user(user_id, request.source_task_id)
                if source is None:
                    raise NotFoundException("来源综述任务不存在")
                if source.status != TaskStatus.COMPLETED.value:
                    raise ValidationException(
                        "只有已完成的文献综述任务才能作为开题报告的衍生来源",
                        details={"status": source.status},
                    )
                inherited_context = self._inherited_context(source)
                config["inherited_context"] = inherited_context
            if request.reference_library:
                from app.knowledge.services.reference_library_service import (
                    ReferenceLibraryService,
                )

                reference_library_count = await ReferenceLibraryService(session).count(user_id)
                if reference_library_count <= 0:
                    raise ValidationException("引用库为空，请先导入文献后再启用「使用我的引用库」")
                config["reference_library_count"] = reference_library_count
                config["max_documents"] = min(request.max_documents, reference_library_count)

            description = self._describe(
                request,
                reference_library_count=reference_library_count,
                inherited_context=inherited_context,
            )
            task = Task(
                user_id=user_id,
                title=request.topic,
                description=description,
                task_type="literature_review",
                status=TaskStatus.CREATED.value,
                config=config,
                llm_credential_id=llm_credential_id,
                model_snapshot=model_snapshot,
                # 分账口径在创建时写死：凭据之后被删也不改变历史任务归属
                is_byok=llm_credential_id is not None,
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
        if request.workflow_preset == "verify_citations":
            self.container.track(task_id, self._run_verify_citations(task_id))
        else:
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
                # waiting_human 有两种来源，直接说清该怎么办（Issue #23 现场排查耗时）
                hint = (
                    "规划已确认过，请到执行页重试失败节点；若规划未生成，请重新规划"
                    if task.status == TaskStatus.WAITING_HUMAN.value
                    else "只有等待确认状态才能确认规划"
                )
                raise ValidationException(
                    f"任务当前不在等待确认状态：{hint}",
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
        """人工控制：pause / resume / cancel / retry_node / supply_input / replan。"""
        retrack = False
        cancel_after_commit = False
        #: 取消是终态 → 需要结算配额
        settle_after_commit = False
        #: 从「已结算」状态继续执行（转人工后重试/补输入）→ 需要重新预扣
        prereserve_after_commit = False
        #: 重新规划：提交后要启动 `_plan`（不是 `_execute`），版本号从旧值往上走
        replan_after_commit = False
        replan_base_version: int | None = None
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
            elif command is TaskCommand.REPLAN:
                replan_base_version = task.plan_version or 0
                events.append(await self._control_replan(session, repo, task, status))
                replan_after_commit = True
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
        if replan_after_commit:
            # 上一轮规划的协程可能还在收尾（写 waiting_human 后要结算配额），
            # 用 after_current 排在它后面，否则 track 会因「已在跟踪」被静默丢弃，
            # 任务就永远停在 PLANNING。
            self.container.track(
                task_id,
                self._plan(task_id, base_version=replan_base_version),
                after_current=True,
            )
        if retrack and not self.container.is_tracking(task_id):
            self.container.track(task_id, self._execute(task_id))
        logger.info("task_control", task_id=str(task_id), command=request.command.value)
        return snapshot

    # ===== 增量刷新与定时重跑（Issue #27）=====

    async def start_refresh(
        self,
        user_id: UUID,
        task_id: UUID,
        *,
        trigger: Literal["manual", "schedule"] = "manual",
    ) -> Any:
        """启动一次增量刷新，返回运行记录（`TaskRefresh`）。

        ## 为什么只允许已完成任务

        刷新是「给已经成稿的综述补新证据」。任务还在跑时刷新，会和执行线程争
        同一批 screening 记录与 reviewer 产出，两条链路互相看不到对方写的东西。
        等它跑完（哪怕 failed）再说 —— 失败任务本就该先 replan/retry。

        ## 为什么手动刷新要冷却

        检索 + 摘要都是真金白银（平台模型时花平台的钱）。没有冷却的话，
        用户连点五下就是五次检索 + 五次模型调用，还都是同一批文献。
        定时刷新不走冷却（周期由排期决定）。
        """
        from datetime import UTC, datetime, timedelta

        from app.db.models.routine import TaskRefresh
        from app.orchestration.services.task_refresh import as_utc

        cfg = get_settings().routine
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            if TaskStatus(task.status) is not TaskStatus.COMPLETED:
                raise ValidationException(
                    "只有已完成的任务可以检索新文献", details={"status": task.status}
                )

            running = (
                await session.execute(
                    select(TaskRefresh)
                    .where(
                        TaskRefresh.task_id == task_id,
                        TaskRefresh.status == "running",
                    )
                    .order_by(TaskRefresh.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if running is not None:
                # 进程重启会让在跑的刷新永远停在 running（协程没了），
                # 之后所有刷新都会被这条挡住。超过 15 分钟仍未结束的按「中断」处理。
                started = as_utc(running.started_at)
                stale = started is None or (now - started) > timedelta(minutes=15)
                if not stale:
                    raise ValidationException(
                        "上一次检索还在进行中，请稍候", details={"refresh_id": str(running.id)}
                    )
                running.status = "failed"
                running.error = "进程重启导致本次刷新中断"
                running.finished_at = now
                await session.flush()

            last = (
                await session.execute(
                    select(TaskRefresh)
                    .where(TaskRefresh.task_id == task_id, TaskRefresh.status == "completed")
                    .order_by(TaskRefresh.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if trigger == "manual" and cfg.manual_cooldown_seconds > 0 and last is not None:
                anchor = as_utc(last.started_at)
                if anchor is not None:
                    earliest = anchor + timedelta(seconds=cfg.manual_cooldown_seconds)
                    if now < earliest:
                        raise ValidationException(
                            "刚检索过，请稍后再试",
                            details={
                                "retry_at": earliest.isoformat(),
                                "cooldown_seconds": cfg.manual_cooldown_seconds,
                            },
                        )

            # 窗口左端：上次刷新（没有则任务完成时刻）− 索引延迟的回退天数
            base = as_utc(last.started_at) if last is not None else as_utc(task.completed_at)
            window_from = (base or now) - timedelta(days=cfg.overlap_days)
            refresh = TaskRefresh(
                task_id=task_id,
                user_id=user_id,
                trigger=trigger,
                status="running",
                window_from=window_from,
                window_to=now,
                queries=[],
                started_at=now,
            )
            session.add(refresh)
            await session.commit()

        self.container.track(task_id, self._refresh(refresh.id))
        logger.info(
            "task_refresh_started",
            task_id=str(task_id),
            refresh_id=str(refresh.id),
            trigger=trigger,
        )
        return refresh

    async def fill_matrix_gap(
        self,
        user_id: UUID,
        task_id: UUID,
        *,
        question_index: int,
        query: str | None = None,
    ) -> dict[str, Any]:
        """研究空白矩阵空格子一键定向补检索（Issue #32 Phase 3）。

        复用文献源解析、三键去重、增量要点生成与引用一致性闸门，
        将新增文献合并回 `review_report` 并重算该子问题行矩阵格子。
        """
        from app.orchestration.contracts import EvidenceMatrix
        from app.orchestration.services.evidence_matrix import (
            build_suggested_query,
            merge_gap_records_into_matrix,
        )
        from app.orchestration.services.task_refresh import (
            SessionBoundSource,
            run_gap_fill_search,
        )

        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            if TaskStatus(task.status) is not TaskStatus.COMPLETED:
                raise ValidationException(
                    "只有已完成的任务支持研究空白补检索", details={"status": task.status}
                )
            report = dict(task.review_report or {}) if isinstance(task.review_report, dict) else {}
            raw_matrix = report.get("evidence_matrix")
            if not isinstance(raw_matrix, dict):
                raise ValidationException("该任务报告尚未生成证据矩阵")
            matrix = EvidenceMatrix.model_validate(raw_matrix)
            if not (0 <= question_index < len(matrix.questions)):
                raise ValidationException(
                    "question_index 越界",
                    details={"question_index": question_index, "total": len(matrix.questions)},
                )
            question = matrix.questions[question_index]
            suggested = next(
                (
                    cell.suggested_query
                    for cell in matrix.cells
                    if cell.question_index == question_index and cell.suggested_query
                ),
                None,
            )
            effective_query = (
                (query or "").strip()
                or suggested
                or build_suggested_query(question, topic=task.title)
            )
            existing_bib = list(report.get("bibliography") or [])

        await self._bind_task_llm(task)
        source_session = self._session_factory()
        try:
            # Issue #67 BYOK：补检索同样用用户自己配置的万方 Key
            wfdata_app_key = await self._load_wfdata_app_key(source_session, task)
            source = resolve_literature_source(
                task, session=source_session, wfdata_app_key=wfdata_app_key
            )
            if source is None:
                raise ValidationException("任务没有可用的文献源，无法执行空白补检索")
            bound_source = SessionBoundSource(source, source_session)
            try:
                reviewer = self.container.create_agent("reviewer")
                llm = getattr(reviewer, "llm", None)
                gap_result = await run_gap_fill_search(
                    source=bound_source,
                    query=effective_query,
                    question=question,
                    existing_bibliography=existing_bib,
                    llm=llm,
                    max_results=8,
                )
            finally:
                await bound_source.aclose()
        except Exception:
            await source_session.close()
            raise

        merged_bib = [*existing_bib, *gap_result.new_bibliography_entries]
        base_md = str(report.get("final_markdown") or "")
        updated_md = (
            f"{base_md}\n\n{gap_result.section_markdown}".strip()
            if gap_result.new_bibliography_entries
            else base_md
        )
        if gap_result.new_bibliography_entries:
            # 合并后的正文与参考文献表必须继续通过引用一致性闸门
            audit = audit_citation_alignment(updated_md, merged_bib)
            if not audit.ok:
                raise ValidationException(f"补检索结果未通过引用一致性校验：{audit.reason}")

        updated_matrix = merge_gap_records_into_matrix(
            matrix,
            question_index=question_index,
            added_ref_indices=[int(e["index"]) for e in gap_result.new_bibliography_entries],
            query=effective_query,
        )

        if gap_result.new_bibliography_entries:
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                locked = await repo.lock_for_update(task_id)
                latest_report = (
                    dict(locked.review_report or {})
                    if isinstance(locked.review_report, dict)
                    else {}
                )
                latest_report["bibliography"] = merged_bib
                latest_report["final_markdown"] = updated_md
                latest_report["evidence_matrix"] = updated_matrix.model_dump()
                locked.review_report = latest_report
                locked.final_output = updated_md
                await session.commit()

        if gap_result.tokens_used > 0:
            await self.add_used_tokens(task_id, gap_result.tokens_used)

        return {
            "task_id": task_id,
            "question_index": question_index,
            "query": effective_query,
            "added_count": len(gap_result.new_bibliography_entries),
            "duplicate_count": gap_result.duplicate_count,
            "summary_markdown": gap_result.section_markdown,
            "evidence_matrix": updated_matrix.model_dump(),
            "bibliography": merged_bib,
        }

    async def _refresh(self, refresh_id: UUID) -> None:
        """后台刷新协程：绑模型 → 跑（检索/摘要/落库）→ 汇报。"""
        from app.db.models.routine import TaskRefresh
        from app.orchestration.services.task_refresh import RefreshRunner, SessionBoundSource

        trigger = "manual"
        try:
            async with self._session_factory() as session:
                refresh = await session.get(TaskRefresh, refresh_id)
                if refresh is None:
                    return
                task = await session.get(Task, refresh.task_id)
                trigger = refresh.trigger
            if task is None:
                return

            # 与执行链路一致：刷新用任务指定的凭据（BYOK 花用户自己的钱）
            await self._bind_task_llm(task)

            async def source_factory(_context: Any) -> Any:
                source_session = self._session_factory()
                try:
                    # Issue #67 BYOK：刷新检索同样用用户自己配置的万方 Key
                    wfdata_app_key = await self._load_wfdata_app_key(source_session, task)
                    source = resolve_literature_source(
                        task, session=source_session, wfdata_app_key=wfdata_app_key
                    )
                except Exception:
                    await source_session.close()
                    raise
                if source is None:
                    await source_session.close()
                    raise ValidationException("任务没有可用的文献源，无法检索新文献")
                return SessionBoundSource(source, source_session)

            runner = RefreshRunner(
                session_factory=self._session_factory,
                source_factory=source_factory,
            )
            outcome = await runner.run(refresh_id)
            await self._notify_refresh(outcome, trigger=trigger)
        except Exception as exc:  # 后台协程不能把异常抛给事件循环
            logger.exception("task_refresh_failed", refresh_id=str(refresh_id), error=str(exc))
            await self._fail_refresh(refresh_id, str(exc))

    async def _fail_refresh(self, refresh_id: UUID, reason: str) -> None:
        """把刷新记录标失败（协程异常兜底）。"""
        from datetime import UTC, datetime

        from app.db.models.routine import TaskRefresh

        try:
            async with self._session_factory() as session:
                refresh = await session.get(TaskRefresh, refresh_id)
                if refresh is None or refresh.status != "running":
                    return
                refresh.status = "failed"
                refresh.error = reason[:2000]
                refresh.finished_at = datetime.now(UTC)
                await session.commit()
        except Exception:
            logger.warning("task_refresh_mark_failed_skipped", refresh_id=str(refresh_id))

    async def _notify_refresh(self, outcome: Any, *, trigger: str) -> None:
        """定时刷新有新证据时发一封汇报邮件（手动刷新由页面自己展示）。"""
        from app.db.models.routine import TaskRefresh, TaskRoutine

        if trigger != "schedule" or outcome.status != "completed" or outcome.new_count == 0:
            return
        try:
            async with self._session_factory() as session:
                routine = (
                    await session.execute(
                        select(TaskRoutine).where(TaskRoutine.task_id == outcome.task_id)
                    )
                ).scalar_one_or_none()
                if routine is None or routine.notify != "email":
                    return
                refresh = await session.get(TaskRefresh, outcome.refresh_id)
                if refresh is None or refresh.notified:
                    return
                from app.db.models.user import User

                user = await session.get(User, outcome.user_id)
                email = getattr(user, "email", None)
                if not email:
                    return
                refresh.notified = True
                await session.commit()

            from app.services.email_sender import get_cached_email_sender

            settings = get_settings()
            link = f"{settings.email.frontend_base_url.rstrip('/')}/tasks/{outcome.task_id}"
            titles = "\n".join(f"- {title}" for title in outcome.new_titles[:5])
            body = (
                f"《{outcome.task_title}》本期新增 {outcome.new_count} 篇文献。\n\n"
                f"{titles}\n\n"
                f"完整摘要与新增文献清单：{link}\n\n"
                "（这是定时重跑的汇报邮件；可在任务详情页关闭排期或改为不发邮件。）"
            )
            await get_cached_email_sender().send(
                to=email,
                subject=f"【VeriPaper】《{outcome.task_title}》新增 {outcome.new_count} 篇证据",
                body=body,
            )
        except Exception as exc:
            # 邮件失败不影响刷新结果本身（与账号邮件同一原则）
            logger.warning(
                "refresh_notify_failed",
                task_id=str(outcome.task_id),
                refresh_id=str(outcome.refresh_id),
                error=str(exc),
            )

    async def start_scheduled_refresh(self, task_id: UUID, user_id: UUID) -> None:
        """调度器回调：按排期启动刷新。

        任务可能已经被删/被重规划成非 completed，这类情况只记日志不抛错——
        调度器不该因为一条过期排期而中断整轮扫描。
        """
        from app.db.models.routine import TaskRoutine

        try:
            await self.start_refresh(user_id, task_id, trigger="schedule")
        except (ValidationException, NotFoundException) as exc:
            reason = getattr(exc, "message", str(exc))
            logger.info("routine_refresh_skipped", task_id=str(task_id), reason=reason)
            # 任务已不是「已完成」（被 replan/取消/删除）：排期不可能再成功，
            # 留着会每个周期空跑一次。直接停用，让用户在页面上看到状态变化。
            try:
                async with self._session_factory() as session:
                    routine = (
                        await session.execute(
                            select(TaskRoutine).where(TaskRoutine.task_id == task_id)
                        )
                    ).scalar_one_or_none()
                    if routine is not None and routine.enabled:
                        routine.enabled = False
                        await session.commit()
                        logger.info("routine_auto_disabled", task_id=str(task_id))
            except Exception:
                logger.warning("routine_auto_disable_failed", task_id=str(task_id))

    async def list_refreshes(self, user_id: UUID, task_id: UUID, *, limit: int = 20) -> list[Any]:
        """列出刷新历史（最新在前）。owner-scoped。"""
        from app.db.models.routine import TaskRefresh

        async with self._session_factory() as session:
            task = await TaskRepository(session).get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            result = await session.execute(
                select(TaskRefresh)
                .where(TaskRefresh.task_id == task_id)
                .order_by(TaskRefresh.created_at.desc())
                .limit(max(1, min(limit, 50)))
            )
            return list(result.scalars())

    async def get_routine(self, user_id: UUID, task_id: UUID) -> Any | None:
        """读取任务的排期（没有则 None）。owner-scoped。"""
        from app.db.models.routine import TaskRoutine

        async with self._session_factory() as session:
            task = await TaskRepository(session).get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            return (
                await session.execute(select(TaskRoutine).where(TaskRoutine.task_id == task_id))
            ).scalar_one_or_none()

    async def save_routine(
        self,
        user_id: UUID,
        task_id: UUID,
        *,
        enabled: bool,
        cadence: str,
        notify: str,
    ) -> Any:
        """新建/更新排期。

        `next_run_at` 每次都从**当前时刻**往后推：用户改了设置就是要按新设置
        重新计时；沿用旧的下次运行时刻会出现「关了又开，立刻就跑一次」。
        """
        from datetime import UTC, datetime

        from app.db.models.routine import TaskRoutine
        from app.orchestration.services.routine_scheduler import advance

        if cadence not in {"weekly", "monthly"}:
            raise ValidationException("排期频率只支持 weekly / monthly")
        if notify not in {"none", "email"}:
            raise ValidationException("汇报方式只支持 none / email")

        async with self._session_factory() as session:
            task = await TaskRepository(session).get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            if TaskStatus(task.status) is not TaskStatus.COMPLETED:
                raise ValidationException(
                    "只有已完成的任务可以设置定时重跑", details={"status": task.status}
                )
            routine = (
                await session.execute(select(TaskRoutine).where(TaskRoutine.task_id == task_id))
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if routine is None:
                routine = TaskRoutine(
                    task_id=task_id,
                    user_id=user_id,
                    enabled=enabled,
                    cadence=cadence,
                    notify=notify,
                    next_run_at=advance(cadence, now),
                )
                session.add(routine)
            else:
                routine.enabled = enabled
                routine.cadence = cadence
                routine.notify = notify
                routine.next_run_at = advance(cadence, now)
            await session.commit()
            logger.info(
                "task_routine_saved",
                task_id=str(task_id),
                cadence=cadence,
                enabled=enabled,
                notify=notify,
            )
            return routine

    async def delete_routine(self, user_id: UUID, task_id: UUID) -> None:
        """删除排期（任务本身不受影响）。"""
        from app.db.models.routine import TaskRoutine

        async with self._session_factory() as session:
            task = await TaskRepository(session).get_for_user(user_id, task_id)
            if task is None:
                raise NotFoundException("任务不存在")
            routine = (
                await session.execute(select(TaskRoutine).where(TaskRoutine.task_id == task_id))
            ).scalar_one_or_none()
            if routine is None:
                return
            await session.delete(routine)
            await session.commit()

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

    async def count_tasks(
        self,
        user_id: UUID,
        *,
        workspace_ids: Sequence[UUID] = (),
        statuses: Sequence[str] | None = None,
        query: str | None = None,
    ) -> int:
        """可见任务总数（HTTP 列表接口用于算总页数）。

        口径必须与 `list_tasks` 完全一致：这是分页正确性的前提，
        两边任何一处改可见性/过滤条件都会让页数与实际对不上。
        """
        async with self._session_factory() as session:
            return await TaskRepository(session).count_summaries(
                user_id, workspace_ids, statuses=statuses, query=query
            )

    async def task_status_counts(
        self,
        user_id: UUID,
        *,
        workspace_ids: Sequence[UUID] = (),
        query: str | None = None,
    ) -> dict[str, int]:
        """按状态分组的计数（列表页顶部数字用）。"""
        async with self._session_factory() as session:
            return await TaskRepository(session).counts_by_status(
                user_id, workspace_ids, query=query
            )

    async def list_tasks(
        self,
        user_id: UUID,
        *,
        skip: int = 0,
        limit: int = 20,
        workspace_ids: Sequence[UUID] = (),
        statuses: Sequence[str] | None = None,
        query: str | None = None,
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
            rows = await repo.list_summaries(
                user_id,
                workspace_ids,
                skip=skip,
                limit=limit,
                statuses=statuses,
                query=query,
            )
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
        """重启恢复：重新调度规划中/执行中任务，已完成/终态任务不动。

        PLANNING 也要恢复：规划是一次几十秒的 LLM 调用，正好撞上重启的概率不低，
        而「重新规划」也会把任务放回 PLANNING。此前这类任务重启后永远停在
        规划中，既没有协程在跑，前端也没有可点的动作。
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(Task).where(
                    Task.status.in_(
                        [status.value for status in (*_RECOVERABLE_STATUSES, TaskStatus.PLANNING)]
                    )
                )
            )
            tasks = list(result.scalars().all())

        for task in tasks:
            if self.container.is_tracking(task.id):
                continue
            logger.info("task_recovered", task_id=str(task.id), status=task.status)
            if task.status == TaskStatus.PLANNING.value:
                self.container.track(
                    task.id,
                    self._plan(task.id, base_version=task.plan_version or 0),
                )
            else:
                self.container.track(task.id, self._execute(task.id))

    # ===== 后台流程：规划 =====

    @staticmethod
    def _is_byok_task(task: Any) -> bool:
        """任务是否使用用户自带模型（Issue #15）。

        两个判据缺一不可：
        - `is_byok`：创建时写死的分账标记（P2 起）；
        - `llm_credential_id` / `model_snapshot.is_byok`：兼容 P2 之前的历史任务
          （那时还没有 `is_byok` 列），凭据被删后快照仍在 ——
          此时若不认 BYOK，会去退一笔**从未预扣过**的 token，凭空多给额度。
        """
        if getattr(task, "is_byok", None) is True:
            return True
        if getattr(task, "llm_credential_id", None) is not None:
            return True
        snapshot = getattr(task, "model_snapshot", None)
        return isinstance(snapshot, dict) and bool(snapshot.get("is_byok"))

    @staticmethod
    def _skips_platform_quota(task: Any) -> bool:
        """是否跳过平台 token 预扣/结算（BYOK 任务或首次示例免扣任务）。"""
        if TaskExecution._is_byok_task(task):
            return True
        snapshot = getattr(task, "model_snapshot", None)
        return isinstance(snapshot, dict) and bool(snapshot.get("demo_exempt"))

    async def _load_user_routes(self, session: Any, user_id: UUID) -> list[dict[str, Any]]:
        """读用户在「我的模型」页配置的节点路由（Issue #22 延伸）。"""
        from sqlalchemy import select

        from app.db.models.llm_credential import LLMNodeRouting

        result = await session.execute(
            select(LLMNodeRouting.node_type, LLMNodeRouting.credential_id).where(
                LLMNodeRouting.user_id == user_id
            )
        )
        return [{"node_type": row.node_type, "credential_id": row.credential_id} for row in result]

    def _platform_llm(self) -> Any | None:
        """平台主模型客户端（回退目标）；未配置时返回 None。"""
        try:
            from app.llm.factory import get_llm_manager

            return get_llm_manager().get_primary()
        except Exception as exc:
            logger.warning("platform_llm_unavailable_for_fallback", error=str(exc))
            return None

    async def _apply_platform_fallback(self, *, task_id: UUID, reason: str) -> bool:
        """把任务标注为「已回退平台」并完成平台侧计费；只生效一次。

        - 标注 `actual_provider` / `actual_model`（界面据此提示「本次已消耗平台额度」）；
        - 记 `fallback_token_base`（回退时刻的已用量），结算只退平台实际消耗；
        - 篇数：BYOK 默认免篇数，回退平台后「照扣」——但提交时若本就按平台扣过
          （`BILLING__BYOK_CONSUMES_REVIEW_QUOTA=true`），不重复扣；
        - token：按整任务预估预扣，结算按基线差值退。

        篇数扣减失败**不阻断回退**（call 已经发生，任务不能因为记账失败而崩）；
        记 warning 留痕，运营可以从 `actual_provider` 反查补账。
        """
        from datetime import UTC, datetime

        from app.billing.services.plan_service import PlanService

        marked = False
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            if task is None or task.actual_provider is not None:
                return False  # 已回退过（并发/重复触发）：不重复计费

            platform = self._platform_llm()
            task.actual_provider = getattr(platform, "provider", None) or "platform"
            task.actual_model = getattr(platform, "model", None) or "platform"
            task.fallback_token_base = int(task.used_tokens or 0)
            if task.is_byok and not get_settings().billing.byok_consumes_review_quota:
                try:
                    await PlanService(self._session_factory).check_and_consume_review(task.user_id)
                except Exception as exc:
                    logger.warning(
                        "fallback_review_quota_consume_failed",
                        task_id=str(task_id),
                        error=str(exc),
                    )
            if self._quota is not None:
                try:
                    await self._quota.check_and_consume(
                        task.user_id, tokens=ESTIMATED_TOKENS_PER_TASK
                    )
                except Exception as exc:
                    # 平台额度不足：不再阻断（调用已经发生），结算时按实际用量校正
                    logger.warning(
                        "fallback_token_reserve_failed", task_id=str(task_id), error=str(exc)
                    )
            task.updated_at = datetime.now(UTC)
            await session.commit()
            marked = True
        logger.info(
            "task_platform_fallback_applied",
            task_id=str(task_id),
            actual_model=task.actual_model,
            reason=reason[:200],
        )
        return marked

    async def _bind_task_llm(self, task: Any | None) -> None:
        """把当前后台协程的 LLM 固定到任务指定的凭据（Issue #15 BYOK）。

        必须在 `asyncio.create_task` **之前**执行：子任务会复制当前 context，
        晚于创建就复制不到，会静默回落到平台模型。

        默认**解析失败不回退平台模型**：那会在用户不知情时花平台的钱，
        抛 `LLMException` 由调用方把任务转人工。用户在凭据上显式开启
        `allow_platform_fallback`（Issue #28）后才有两条回退路径：

        1. **绑定期失败**（凭据被禁用/停用）：直接改绑平台模型；
        2. **调用期 401/402/403**：由 `PlatformFallbackClient` 包装，首次失败即
           粘住平台。429 限流与超时永远不回退。
        """
        if task is None:
            return
        credential_id = getattr(task, "llm_credential_id", None)
        # 平台任务（无凭据）也允许有用户路由（Issue #22 延伸）：
        # 主模型走平台默认，但 extract 等节点可路由到用户的本地/免费凭据。
        client: Any | None = None
        user_routes: dict[str, Any] = {}
        allow_fallback = False
        if credential_id is None:
            # 凭据被删除时外键 SET NULL，但快照还在。**不能**因此静默回落到平台模型：
            # 用户以为在用自己的 key，实际花的是平台的钱。直接失败并说明原因。
            snapshot = getattr(task, "model_snapshot", None)
            if isinstance(snapshot, dict) and snapshot.get("is_byok"):
                raise LLMException("任务指定的模型凭据已被删除，无法继续；请新建任务或改用平台模型")

        from app.core.exceptions import AppException
        from app.llm.byok import get_credential_resolver
        from app.llm.scope import bind_llm_client, bind_user_routes

        try:
            async with self._session_factory() as session:
                if credential_id is not None:
                    from app.db.models.llm_credential import LLMCredential

                    record = (
                        await session.execute(
                            select(LLMCredential).where(
                                LLMCredential.id == credential_id,
                                LLMCredential.user_id == task.user_id,
                            )
                        )
                    ).scalar_one_or_none()
                    allow_fallback = bool(record is not None and record.allow_platform_fallback)
                    try:
                        client = await get_credential_resolver().resolve(
                            session, task.user_id, credential_id
                        )
                    except AppException as resolve_exc:
                        if not allow_fallback:
                            raise
                        # 绑定期就失败（凭据被停用/状态非 active）：直接回平台，
                        # 不让任务转人工——这正是用户开这个开关要买的行为。
                        await self._apply_platform_fallback(
                            task_id=task.id,
                            reason=getattr(resolve_exc, "message", str(resolve_exc)),
                        )
                        logger.warning(
                            "task_llm_fallback_at_bind",
                            task_id=str(task.id),
                            reason=getattr(resolve_exc, "message", str(resolve_exc)),
                        )
                        client = None
                        allow_fallback = False
                # 用户级节点路由（Issue #22 延伸）：「我的模型」页配置的
                # (node_type → 凭据) 逐条解析为客户端；解析失败的路由**跳过并
                # 留痕**而不是整体失败——路由是省钱优化，不该卡死任务主链路。
                for route in await self._load_user_routes(session, task.user_id):
                    try:
                        user_routes[route["node_type"]] = await get_credential_resolver().resolve(
                            session, task.user_id, route["credential_id"]
                        )
                    except AppException as route_exc:
                        logger.warning(
                            "user_route_resolve_failed",
                            task_id=str(task.id),
                            node_type=route["node_type"],
                            error=str(route_exc.message),
                        )
        except AppException as exc:
            raise LLMException(f"任务指定的模型凭据不可用：{exc.message}") from exc

        if client is not None and allow_fallback:
            from app.llm.fallback import PlatformFallbackClient

            async def _on_fallback(reason: str) -> None:
                # 回调契约只要求通知，计费结果由 `_apply_platform_fallback` 自己留痕
                await self._apply_platform_fallback(task_id=task.id, reason=reason)

            client = PlatformFallbackClient(
                client,
                platform=self._platform_llm,
                on_fallback=_on_fallback,
            )
        if client is not None:
            bind_llm_client(client)
        bind_user_routes(user_routes)
        if user_routes:
            logger.info(
                "user_routes_bound",
                task_id=str(task.id),
                routes={k: v.model for k, v in user_routes.items()},
            )
        logger.info(
            "task_llm_bound",
            task_id=str(task.id),
            credential_id=str(credential_id),
            provider=client.provider if client else "platform",
            model=client.model if client else "(platform default)",
        )

    async def _run_verify_citations(self, task_id: UUID) -> None:
        """执行 `verify_citations` 独立短链路（Issue #32 Phase 2A）。

        跳过多节点规划确认门禁：自动写入单步验证计划，直接复用 ReferenceVerifier 与
        ClaimVerifier 产出标准 ReviewReport，落库前走引用一致性闸门并结算实际 token。
        """
        from app.db.models.knowledge import DocChunk, KnowledgeDoc
        from app.orchestration.contracts import PlanNode, TaskPlan
        from app.orchestration.workflows.verify_citations import (
            DocumentChunkInput,
            VerifyCitationsRunner,
        )

        used_tokens = 0
        try:
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                if task.status != TaskStatus.PLANNING.value:
                    return
                config = dict(task.config or {})
                user_id = task.user_id
                title = task.title
                draft_text = config.get("draft_text")
                raw_doc_ids = config.get("document_ids") or []
                citation_style = str(config.get("citation_style") or "").strip() or None

                single_plan = TaskPlan(
                    version=1,
                    topic=title[:200] if len(title) >= 5 else f"{title}引用核验"[:200],
                    research_questions=["核验草稿引用的题录真实性、来源质量与论断支撑度"],
                    search_queries=["citation verification"],
                    acceptance_criteria=["输出四态题录核验、来源质量标记与论断核验报告"],
                    nodes=[
                        PlanNode(
                            id="n1",
                            type="validate",
                            title="核验草稿引用与论断",
                            dependencies=[],
                            input={"workflow_preset": "verify_citations"},
                            required_evidence=0,
                        )
                    ],
                )
                dag = PlannerAgent.build_dag(single_plan)
                await repo.replace_plan(task, single_plan, dag)
                await repo.transition(task, {TaskStatus.PLANNING}, TaskStatus.RUNNING)
                start_event = await self._append_event(
                    session,
                    task_id,
                    event_type="task_status",
                    status=TaskStatus.RUNNING.value,
                    payload={"workflow_preset": "verify_citations"},
                )

                # 读取用户勾选的知识库原文分块（owner-scoped）作为核验依据
                doc_chunks: list[DocumentChunkInput] = []
                parsed_doc_ids: list[UUID] = []
                for item in raw_doc_ids:
                    try:
                        parsed_doc_ids.append(UUID(str(item)))
                    except (TypeError, ValueError):
                        continue
                if parsed_doc_ids:
                    rows = (
                        await session.execute(
                            select(DocChunk, KnowledgeDoc.title)
                            .join(KnowledgeDoc, DocChunk.doc_id == KnowledgeDoc.id)
                            .where(
                                KnowledgeDoc.user_id == user_id,
                                DocChunk.doc_id.in_(parsed_doc_ids),
                            )
                            .order_by(DocChunk.doc_id, DocChunk.chunk_index)
                            .limit(60)
                        )
                    ).all()
                    for chunk, doc_title in rows:
                        doc_chunks.append(
                            DocumentChunkInput(
                                doc_id=chunk.doc_id,
                                chunk_id=chunk.id,
                                title=str(doc_title or ""),
                                content=str(chunk.content or ""),
                            )
                        )
                await session.commit()

            await self._publish(start_event)
            await self._bind_task_llm(task)
            await self._emit_stage_progress(
                task_id,
                status=TaskStatus.RUNNING.value,
                stage="verifying_references",
                message="正在核验引用文献与论断支撑...",
                extra={"workflow_preset": "verify_citations"},
            )

            reviewer = self.container.create_agent("reviewer")
            # verify_citations 的核心目的即引用核验；仅离线模式且未显式开启时跳过外呼
            if config.get("offline_dataset") and config.get("verify_references") is not True:
                reference_verifier = None
            else:
                reference_verifier = getattr(reviewer, "reference_verifier", None)
            llm = getattr(reviewer, "llm", None)

            with use_usage_ledger() as ledger:
                runner = VerifyCitationsRunner(
                    reference_verifier=reference_verifier,
                    llm=llm,
                )
                report_model = await runner.run(
                    topic=title,
                    draft_text=str(draft_text) if isinstance(draft_text, str) else None,
                    document_chunks=doc_chunks,
                )
            used_tokens = ledger.total_tokens

            review_report = report_model.model_dump(mode="json")

            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                if task.status != TaskStatus.RUNNING.value:
                    return
                await repo.transition(task, {TaskStatus.RUNNING}, TaskStatus.REVIEWING)
                await session.commit()

            if used_tokens > 0:
                await self.add_used_tokens(task_id, used_tokens)

            if not await self._guard_citation_consistency(task_id, review_report):
                return

            await self._emit_stage_progress(
                task_id,
                status=TaskStatus.REVIEWING.value,
                stage="exporting_artifacts",
                message="引用一致性校验通过，正在导出 Markdown 与 DOCX 产物...",
                extra={"workflow_preset": "verify_citations"},
            )

            artifacts = self._export_literature_artifacts(
                user_id, task_id, review_report, citation_style=citation_style
            )

            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                if task.status != TaskStatus.REVIEWING.value:
                    return
                task.review_report = review_report
                task.final_output = str(review_report.get("final_markdown") or "")
                task.artifacts = artifacts
                _clear_task_reasons(task)
                await repo.transition(task, {TaskStatus.REVIEWING}, TaskStatus.COMPLETED)
                done_event = await self._append_event(
                    session,
                    task_id,
                    event_type="task_completed",
                    status=TaskStatus.COMPLETED.value,
                    payload={"workflow_preset": "verify_citations"},
                )
                await session.commit()
            await self._publish(done_event)
            await self.settle_quota(task_id)
            await self._record_task_terminal(task_id, TaskStatus.COMPLETED.value)
        except Exception as exc:
            logger.warning("verify_citations_failed", task_id=str(task_id), error=str(exc))
            if used_tokens > 0:
                await self.add_used_tokens(task_id, used_tokens)
            await self._mark_waiting_human(task_id, f"引用核验执行失败：{exc}")

    async def _plan(self, task_id: UUID, *, base_version: int | None = None) -> None:
        """后台规划：生成 TaskPlan + DAG，失败时进行一次 schema 修复。

        上游模型端点可能瞬时 5xx/超时（Plan 5 Task 8 实跑遇到 503），
        因此规划调用带**有限次退避重试**；配置类错误（401/400）不重试，
        直接转人工，避免无意义等待。

        `base_version` 非空表示这是「重新规划」（`replan`）：规划器永远输出
        version=1，直接落库会让新规划沿用旧版本号，前端拿着过期的 revision
        也能确认它。这里把它抬到旧版本 +1，版本号才继续单调。
        """
        try:
            async with self._session_factory() as session:
                task = await TaskRepository(session).get(task_id)
                if task is None:
                    return
                if isinstance(task.config, dict) and task.config.get("reference_library"):
                    from app.knowledge.services.reference_library_service import (
                        ReferenceLibraryService,
                    )

                    lib_count = await ReferenceLibraryService(session).count(task.user_id)
                    if lib_count > 0:
                        updated_config = dict(task.config)
                        updated_config["reference_library_count"] = lib_count
                        if isinstance(updated_config.get("max_documents"), int):
                            updated_config["max_documents"] = min(
                                updated_config["max_documents"], lib_count
                            )
                        task.config = updated_config
                requirement = self._requirement(task)
                trace_id = self._trace_id(task)
                task_type = task.task_type

            # 规划阶段就要用对模型：用户选了 BYOK，规划也必须走他的 key
            await self._emit_plan_progress(task_id, "binding_model", "正在加载模型与任务配置…")
            await self._bind_task_llm(task)
            await self._emit_stage_progress(
                task_id,
                status=TaskStatus.PLANNING.value,
                stage="planning_llm",
                message="正在生成研究规划与任务 DAG...",
            )
            await self._emit_plan_progress(
                task_id, "generating_plan", "正在拆解研究问题、检索式与任务流程…"
            )

            async def _on_plan_progress(stage_name: str, msg: str) -> None:
                if stage_name in {"repairing_plan", "planning_repair"}:
                    await self._emit_stage_progress(
                        task_id,
                        status=TaskStatus.PLANNING.value,
                        stage="planning_repair",
                        message=msg,
                        level="warning",
                    )
                    await self._emit_plan_progress(task_id, "repairing_plan", msg)
                    return
                await self._emit_plan_progress(task_id, stage_name, msg)

            if task_type == "literature_review":
                # 文献场景：由工作流承担规划 + 一次契约修复；失败抛 HumanActionRequiredError
                workflow = self._literature_workflow(task)
                plan_params: Mapping[str, Any] = {}
                try:
                    plan_params = inspect.signature(workflow.plan).parameters
                except (ValueError, TypeError):
                    plan_params = {}
                if "on_progress" in plan_params:
                    plan = await self._retry_transient(
                        lambda: workflow.plan(task, on_progress=_on_plan_progress),
                        task_id=task_id,
                        stage="planning",
                    )
                else:
                    plan = await self._retry_transient(
                        lambda: workflow.plan(task), task_id=task_id, stage="planning"
                    )
                dag = PlannerAgent.build_dag(plan)
            else:
                planner = self.container.create_planner()
                plan, dag = await self._retry_transient(
                    lambda: self._generate_plan(
                        planner, requirement, trace_id, on_progress=_on_plan_progress
                    ),
                    task_id=task_id,
                    stage="planning",
                )

            if base_version is not None and plan.version <= base_version:
                plan = plan.model_copy(update={"version": base_version + 1})

            await self._emit_plan_progress(
                task_id, "persisting_plan", "规划校验通过，正在保存任务图…"
            )
            async with self._session_factory() as session:
                repo = TaskRepository(session)
                task = await repo.lock_for_update(task_id)
                if task.status != TaskStatus.PLANNING.value:
                    return  # 已被取消/暂停
                await repo.replace_plan(task, plan, dag)
                _clear_task_reasons(task)
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
                stage_status = (
                    TaskStatus.PLANNING.value
                    if stage == "planning"
                    else (
                        TaskStatus.REVIEWING.value
                        if stage == "reviewing"
                        else TaskStatus.RUNNING.value
                    )
                )
                await self._emit_stage_progress(
                    task_id,
                    status=stage_status,
                    stage=f"{stage}_retry",
                    level="warning",
                    message=f"上游服务暂时不可用，{delay:.0f}s 后进行第 {attempt + 1}/{attempts} 次重试",
                    extra={
                        "retry_delay_s": delay,
                        "attempt": attempt,
                        "max_attempts": attempts,
                    },
                )
                if stage == "planning":
                    await self._emit_plan_progress(
                        task_id,
                        "retrying_upstream",
                        f"上游模型响应较慢或临时拥堵，正在退避重试（第 {attempt}/{attempts - 1} 次）…",
                        attempt=attempt,
                        max_attempts=attempts - 1,
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
        *,
        on_progress: Callable[[str, str], Awaitable[None]] | None = None,
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
            if on_progress is not None:
                try:
                    await on_progress("planning_repair", "规划格式校验未通过，正在自动修复...")
                except Exception:
                    pass
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
        """后台执行入口：绑定任务级用量账本（Issue #16）。"""
        with use_usage_ledger() as ledger:
            await self._execute_with_ledger(task_id, ledger)

    async def _execute_with_ledger(self, task_id: UUID, ledger: UsageLedger) -> None:
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

            # 执行阶段的 Agent/工作流都从容器取 LLM，必须在建它们之前绑定
            await self._bind_task_llm(task)

            observer = self.container.create_observer()
            scheduler = self.container.create_scheduler(observer)
            # 引用库文献源需要 DB 会话（2026-10-26 引用库计划 §3.3）：
            # 任务执行期间保持一个会话打开，供 `LibraryLiteratureSource` 查询用户引用库。
            # 不用 `async with` 包整个执行流程：执行可能持续数分钟，
            # 长事务会占住连接；这里只把它传下去，读取时由 source 自己发查询。
            async with self._session_factory() as source_session:
                # Issue #67 BYOK：万方 Key 属于当前用户，解密后只在内存里传递
                wfdata_app_key = await self._load_wfdata_app_key(source_session, task)
                # 全文获取 Phase A · Task 7：装配 OA 全文抓取。离线 / 引用库 / 显式关闭
                # 时返回 (None, None)，检索节点保持零外呼。
                fulltext_service, fulltext_resolver = self._create_fulltext_service(
                    task, source_session
                )
                workflow = self._literature_execution_workflow(
                    task,
                    source_session=source_session,
                    wfdata_app_key=wfdata_app_key,
                    fulltext_service=fulltext_service,
                )
                if workflow is not None:
                    scheduler = TaskScheduler(
                        agent_factory=self._literature_agent_factory(workflow, task),
                        observer=observer,
                    )
                try:
                    result = await scheduler.run(
                        dag,
                        task_id=str(task_id),
                        pre_completed=completed,
                        trace_id=trace_id,
                        subtask_ids=subtask_ids,
                    )
                finally:
                    # resolver 可能自带 httpx.AsyncClient：执行结束（含异常/取消）必须关闭
                    if fulltext_resolver is not None:
                        await fulltext_resolver.aclose()

            # 先记账再判断成败：转人工 / 失败路径同样要按已用额度结算，
            # 若放在成功分支里就会漏记（「跑了 30k 然后转人工」被当成 0 用量全额退款）。
            node_results = getattr(result, "node_results", {}) or {}
            # 用量取自任务账本（Issue #16）：覆盖规划/校验/重试/路由调用，
            # 不再依赖节点结果里的 usage（旧口径实测漏计 60%+）。
            usage = ledger.take()
            await self.add_used_tokens(
                task_id,
                usage.total_tokens,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cache_hit_tokens=usage.cache_hit_tokens,
            )
            # 可观测性：节点维度能回答「卡在哪个环节」，成功率只说成/败
            self._record_node_metrics(node_results, _dag_node_types(dag))

            if not result.success:
                await self._finish_waiting_human(task_id, result)
                return

            await self._start_reviewing(task_id, result)
            await self._finish_reviewing(task_id, result, dag.topological_order())
            # 复盘段结算（Issue #16）：review 调用发生在上面的记账之后，
            # 必须再取一次增量补记，否则复盘用量永久丢失。
            review_usage = ledger.take()
            if review_usage.total_tokens > 0:
                await self.add_used_tokens(
                    task_id,
                    review_usage.total_tokens,
                    prompt_tokens=review_usage.prompt_tokens,
                    completion_tokens=review_usage.completion_tokens,
                    cache_hit_tokens=review_usage.cache_hit_tokens,
                )
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

    async def _load_wfdata_app_key(self, session: Any, task: Any) -> str:
        """读取并解密当前任务用户的万方 App Key（Issue #67 BYOK）。

        - 未配置 / 已停用 → `""`（中文任务回落免费源，不报错）；
        - 解密失败（主密钥轮换、密文损坏）→ 记 warning 后 `""`：
          **数据源凭据坏了不该让整个综述任务失败**，跳过万方即可；
        - 返回的明文只在单次执行的内存里存在，不写日志、不进 task.config。
        """
        user_id = getattr(task, "user_id", None)
        if user_id is None:
            return ""
        try:
            from app.security.data_source_credential_service import (
                DataSourceCredentialService,
            )

            key = await DataSourceCredentialService().get_decrypted_key(session, user_id, "wanfang")
        except Exception as exc:  # 解密失败 / 主密钥缺失 / 表未迁移
            logger.warning(
                "wanfang_credential_unavailable",
                task_id=str(getattr(task, "id", "")),
                error=str(exc),
            )
            return ""
        return (key or "").strip()

    def _create_fulltext_service(
        self,
        task: Any | None,
        session: Any,
    ) -> tuple[Any | None, Any | None]:
        """按任务配置装配 OA 全文抓取（全文获取 Phase A · Task 7）。

        返回 ``(FulltextService | None, FullTextResolver | None)``；``(None, None)``
        表示不抓取，检索节点保持零外呼。不装配的三种情况：

        - 非文献任务 / 容器没有知识库（走通用 Agent 路径）；
        - 任务显式 ``full_text=false``、离线数据集（``offline_dataset``）或
          引用库模式（``reference_library``）—— 这些场景的证据必须来自本地；
        - 全局 ``fulltext.enabled=false``，或知识库组件不可用（直接 DB 调用场景）。

        本方法是全文抓取的**唯一**开关点：``FulltextService`` 在 enabled=false 时
        只会把记录记为 fallback（不访问网络，但统计无意义），零外呼必须在这里决掉。
        """
        if task is None or getattr(task, "task_type", "") != "literature_review":
            return None, None
        if self.container.knowledge_query is None:
            return None, None
        config = getattr(task, "config", None) or {}
        if not self._config_flag(config, "full_text", default=True):
            return None, None
        if self._config_flag(config, "offline_dataset", default=False):
            return None, None
        if self._config_flag(config, "reference_library", default=False):
            return None, None
        if not bool(getattr(get_settings().fulltext, "enabled", True)):
            return None, None
        return self._build_fulltext_service(session)

    @staticmethod
    def _config_flag(config: Any, name: str, *, default: bool) -> bool:
        """读任务 config 里的布尔开关（兼容 dict 与 Pydantic 对象）。"""
        if isinstance(config, dict):
            value = config.get(name)
        else:
            value = getattr(config, name, None)
        return default if value is None else bool(value)

    @staticmethod
    def _build_fulltext_service(session: Any) -> tuple[Any | None, Any | None]:
        """构造 FulltextService + resolver；知识库未初始化等异常时降级为不抓取。

        延迟导入：知识库服务只在文献执行路径用到，顶部导入会把 knowledge 包
        拖进通用 Agent 路径（且可能触发循环导入）。
        """
        try:
            from app.knowledge.services.document_service import KnowledgeDocumentService
            from app.knowledge.services.factory import (
                get_index_manager,
                get_kb,
                get_storage,
            )
            from app.knowledge.services.fulltext_parser import CleanFullTextParser
            from app.knowledge.services.fulltext_service import FulltextService
            from app.knowledge.services.paper_card_service import PaperCardService
            from app.tools.literature.fulltext_resolver import FullTextResolver

            storage = get_storage()
            resolver = FullTextResolver()
            documents = KnowledgeDocumentService(
                session,
                storage=storage,
                index_manager=get_index_manager(),
                kb=get_kb(),
            )
            service = FulltextService(
                session,
                resolver=resolver,
                parser=CleanFullTextParser(),
                documents=documents,
                storage=storage,
                paper_cards=PaperCardService(),
            )
        except Exception as exc:  # 知识库未初始化 / 依赖缺失：降级为不抓取（零外呼）
            logger.warning("fulltext_service_unavailable", error=str(exc))
            return None, None
        return service, resolver

    def _literature_execution_workflow(
        self,
        task: Any | None,
        *,
        source_session: Any | None = None,
        wfdata_app_key: str = "",
        fulltext_service: Any | None = None,
    ) -> LiteratureReviewWorkflow | None:
        """文献任务且容器提供知识库时，返回证据约束执行工作流；否则 None（走通用 Agent）。

        `source_session` 用于引用库文献源（`reference_library=true` 时需要 DB 访问）。
        不传时引用库任务会抛 `ReferenceLibraryUnavailableError` —— 这是有意的：
        **绝不静默回退到外部检索**（否则用户以为在用自己导入的文献）。

        `wfdata_app_key` 是当前用户自带的万方 Key（已解密，Issue #67 BYOK）。
        只放在内存里传参，**不写进 task.config**：config 会随任务落库并被前端快照读取。

        `fulltext_service` 是检索节点的 OA 全文抓取服务（全文获取 Phase A · Task 7）；
        None 表示不抓取（离线数据集 / 引用库 / 显式关闭 / 全局关闭）。
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
                wfdata_app_key=wfdata_app_key,
            ),
            # 引用真实性核验（Issue #3）：核验器未启用或离线任务（R6）时为 None
            reference_verifier=self._reference_verifier_for_task(task),
            # 检索结果落筛选表：不接这条线，筛选页永远是空的（PRISMA 全 0）
            screening_recorder=self._record_screening,
            # OA 全文抓取（全文获取 Phase A · Task 7）：None 表示零外呼（离线/引用库/关闭）
            fulltext_service=fulltext_service,
        )
        return workflow.use_plan(plan)

    async def _record_screening(
        self,
        task_id: UUID,
        query: str,
        records: list[Any],
    ) -> None:
        """检索结果 → 筛选记录（PRISMA「识别」阶段）。

        ## 幂等

        检索节点可能因重试、重生成而重复执行。同一任务 + 同一检索式只写一次，
        否则 PRISMA「识别」会翻倍，筛选队列里也会出现重复文献。

        ## 为什么不在这里 try/except

        调用方（工作流）已经兜住异常并且只记 warning。这里让异常照常抛出，
        是为了在测试里能直接断言失败原因，而不是被静默吞掉。
        """
        # 按 task_id 串行化筛选落库：并发检索节点（如 n1/n2）同时落库时避免跨节点去重竞态（Issue #59）
        locks: dict[UUID, asyncio.Lock] = getattr(self, "_screening_locks", {})
        if not hasattr(self, "_screening_locks"):
            self._screening_locks = locks
        lock = locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            async with self._session_factory() as session:
                service = ScreeningService(session)
                if await service.has_search_log(task_id, query):
                    return
                await service.record_search(
                    task_id,
                    query=query,
                    sources=sorted({source_of(record) for record in records}),
                    records=records,
                )

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
        waiting_reason = _summarize_waiting_reason(result)
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            if task.status != TaskStatus.RUNNING.value:
                return
            _set_task_reason(task, waiting_reason=waiting_reason)
            await repo.transition(task, {TaskStatus.RUNNING}, TaskStatus.WAITING_HUMAN)
            event = await self._append_event(
                session,
                task_id,
                event_type="task_waiting_human",
                status=TaskStatus.WAITING_HUMAN.value,
                payload={
                    "completed": result.completed,
                    "waiting_human": result.waiting_human,
                    "reason": waiting_reason,
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

    async def _guard_citation_consistency(
        self, task_id: UUID, review_report: dict[str, Any]
    ) -> bool:
        """落库前引用一致性闸门：不通过则任务转 FAILED，返回 False。

        独立成方法是为了可测：直接对「报告 + 任务状态」断言，不用跑完整个
        调度/复盘流程（那需要 reviewer、容器、证据链的全部替身）。
        """
        audit = audit_citation_alignment(
            str(review_report.get("final_markdown") or ""),
            list(review_report.get("bibliography") or []),
        )
        # to_payload() 已含 ok；再显式传会 TypeError（实测：整条任务失败）
        logger.info("citation_consistency_audit", task_id=str(task_id), **audit.to_payload())
        if audit.ok:
            return True
        # 不落库不一致产出：review_report/artifacts 都不写，任务可 replan 重试
        await self._fail_review(task_id, f"引用一致性校验未通过：{audit.reason}")
        return False

    async def _fail_review(self, task_id: UUID, reason: str) -> None:
        """复盘阶段校验失败：REVIEWING → FAILED（不落库不一致产出）。

        与 `_mark_waiting_human` 的区别：
        - 这里的失败**不是缺数据/缺输入**，而是产出不满足硬性一致性要求；
        - 用 FAILED 而不是 WAITING_HUMAN，是因为用户唯一有效的动作是
          「换模型或重试」——`replan` 允许 FAILED 状态，且失败率看板应统计到它。
        """
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            task = await repo.lock_for_update(task_id)
            if task.status != TaskStatus.REVIEWING.value:
                return
            _set_task_reason(task, error=reason)
            await repo.transition(task, {TaskStatus.REVIEWING}, TaskStatus.FAILED)
            event = await self._append_event(
                session,
                task_id,
                event_type="task_failed",
                status=TaskStatus.FAILED.value,
                payload={"reason": reason[:2000], "stage": "review_validation"},
            )
            await session.commit()
        await self._publish(event)
        logger.warning("task_review_validation_failed", task_id=str(task_id), reason=reason[:300])
        await self.settle_quota(task_id)
        await self._record_task_terminal(task_id, TaskStatus.FAILED.value)

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
            task_plan = task.plan if isinstance(task.plan, dict) else {}
            research_questions = [
                str(q) for q in (task_plan.get("research_questions") or []) if str(q).strip()
            ]
            node_titles = {
                str(node.get("id")): str(node.get("title") or "")
                for node in (task_plan.get("nodes") or [])
                if isinstance(node, dict) and node.get("id")
            }
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
        await self._emit_stage_progress(
            task_id,
            status=TaskStatus.REVIEWING.value,
            stage="review_aggregating",
            message="正在汇总各节点成果并核验引用...",
        )
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
                    "research_questions": research_questions,
                    "node_titles": node_titles,
                }
            )
            # Issue #25 沉淀项 1：Agent 产出**落库前**强制校验引用一致性。
            if not await self._guard_citation_consistency(task_id, review_report):
                return
            await self._emit_stage_progress(
                task_id,
                status=TaskStatus.REVIEWING.value,
                stage="exporting_artifacts",
                message="引用一致性校验通过，正在导出 Markdown 与 DOCX 产物...",
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
            _clear_task_reasons(task)
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
        _clear_task_reasons(task)
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
        _clear_task_reasons(task)
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

    async def _control_replan(
        self,
        session: Any,
        repo: TaskRepository,
        task: Task,
        status: TaskStatus,
    ) -> TaskEvent:
        """退回规划阶段并丢弃旧规划/DAG（`replan` 命令）。

        ## 为什么必须单独一条命令（2026-11-17 生产实测）

        规划失败时任务还没有 DAG，也没有任何子任务；前端「重新生成」发的是
        `retry_node n1`，后端 `_control_retry_node` 查不到 subtask 直接 404 ——
        用户唯一能做的动作是坏的，任务永远卡在 waiting_human。

        `replan` 不依赖任何节点：清空（可能残缺的）旧规划与子任务，转回
        PLANNING，由后台重新走一遍规划。凭据在这里**重新解析**，所以用户
        在「我的模型」里换掉超时的模型后，点一下就能真正重试。
        """
        if status not in {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_HUMAN,
            TaskStatus.FAILED,
        }:
            raise ValidationException(
                "仅等待确认/等待人工/失败状态允许重新规划",
                details={"status": status.value},
            )
        previous_version = task.plan_version or 0
        # 旧规划与旧子任务必须一起清：只清 plan 会留下上一版 DAG 的节点，
        # 新规划成功前用户点开执行页看到的是过期状态。
        task.plan = None
        _clear_task_reasons(task)
        await session.execute(delete(Subtask).where(Subtask.task_id == task.id))
        if status is TaskStatus.FAILED:
            task.completed_at = None
        await repo.transition(task, {status}, TaskStatus.PLANNING)
        return await self._append_event(
            session,
            task.id,
            event_type="plan_regenerate",
            status=TaskStatus.PLANNING.value,
            payload={"from": status.value, "previous_version": previous_version},
        )

    async def _control_supply_input(
        self,
        session: Any,
        repo: TaskRepository,
        task: Task,
        status: TaskStatus,
        human_input: dict[str, Any] | None,
    ) -> TaskEvent:
        updated_cfg = {**(task.config or {}), "human_input": human_input or {}}
        updated_cfg.pop("_waiting_reason", None)
        updated_cfg.pop("_error", None)
        task.config = updated_cfg
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
        model = None
        snapshot = getattr(task, "model_snapshot", None)
        if isinstance(snapshot, dict):
            try:
                model = ModelSelectionSnapshot(**snapshot)
            except Exception:  # 历史脏数据不该让整个详情接口 500
                logger.warning("task_model_snapshot_invalid", task_id=str(task.id))

        raw_config = getattr(task, "config", None)
        config = raw_config if isinstance(raw_config, dict) else {}
        raw_preset = config.get("workflow_preset")
        workflow_preset = (
            raw_preset.strip() if isinstance(raw_preset, str) and raw_preset.strip() else None
        )

        # 按当前状态门控错误/等待原因：仅对应状态透出，并兼容历史库缺 config 键回溯
        waiting_reason: str | None = None
        error: str | None = None
        if task.status == TaskStatus.WAITING_HUMAN.value:
            raw_reason = config.get("_waiting_reason")
            if isinstance(raw_reason, str) and raw_reason.strip():
                waiting_reason = raw_reason.strip()
            else:
                for sub in subtasks:
                    if sub.status in {"waiting_human", "failed"} and isinstance(sub.error, str):
                        if sub.error.strip():
                            waiting_reason = f"{sub.node_id}: {sub.error.strip()}"
                            break
                if waiting_reason is None:
                    log_rows = (
                        (
                            await session.execute(
                                select(ExecutionLog)
                                .where(
                                    ExecutionLog.task_id == task.id,
                                    ExecutionLog.event_type == "task_waiting_human",
                                )
                                .order_by(ExecutionLog.event_seq.desc())
                                .limit(1)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if log_rows:
                        ctx = getattr(log_rows[0], "context", None)
                        if isinstance(ctx, dict):
                            log_reason = ctx.get("reason")
                            if isinstance(log_reason, str) and log_reason.strip():
                                waiting_reason = log_reason.strip()
        elif task.status == TaskStatus.FAILED.value:
            raw_error = config.get("_error")
            if isinstance(raw_error, str) and raw_error.strip():
                error = raw_error.strip()
            else:
                log_rows = (
                    (
                        await session.execute(
                            select(ExecutionLog)
                            .where(
                                ExecutionLog.task_id == task.id,
                                ExecutionLog.event_type == "task_failed",
                            )
                            .order_by(ExecutionLog.event_seq.desc())
                            .limit(1)
                        )
                    )
                    .scalars()
                    .all()
                )
                if log_rows:
                    ctx = getattr(log_rows[0], "context", None)
                    if isinstance(ctx, dict):
                        log_err = ctx.get("reason")
                        if isinstance(log_err, str) and log_err.strip():
                            error = log_err.strip()

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
            model=model,
            # 用 getattr：历史任务对象/测试替身可能没有这两列（Issue #28 新增）
            actual_provider=getattr(task, "actual_provider", None),
            actual_model=getattr(task, "actual_model", None),
            # 正文必须随快照下发：MCP/开放 API 的 get_task_output 读的就是它。
            # 曾经漏掉 → MCP 永远返回 markdown: null（tests 用 dict 替身掩盖了）。
            final_output=task.final_output,
            error=error,
            waiting_reason=waiting_reason,
            workflow_preset=workflow_preset,
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

    async def _emit_stage_progress(
        self,
        task_id: UUID,
        *,
        status: str,
        stage: str,
        message: str,
        level: str = "info",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """发出任务级长耗时阶段进度事件（fail-open，兼容 __new__ 单测替身）。"""
        session_factory = getattr(self, "_session_factory", None)
        container = getattr(self, "container", None)
        if session_factory is None or container is None:
            return
        payload: dict[str, Any] = {
            "stage": stage,
            "message": message,
            "level": level,
        }
        if extra:
            payload.update(extra)

        async def _persist() -> TaskEvent | None:
            async with session_factory() as session:
                ev = await self._append_event(
                    session,
                    task_id,
                    event_type="stage_progress",
                    status=status,
                    subtask_id=None,
                    payload=payload,
                )
                await session.commit()
                return ev

        event: TaskEvent | None = None
        persist_task = asyncio.create_task(_persist())
        try:
            event = await asyncio.shield(persist_task)
        except asyncio.CancelledError:
            # 取消若恰好落在短事务提交中途，必须等底层连接完成提交并释放锁后再上抛，
            # 否则 SQLite/连接池会遗留未结束的写事务导致后续 settle_quota 报 database is locked。
            try:
                await persist_task
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.debug(
                "stage_progress_persist_skipped",
                task_id=str(task_id),
                stage=stage,
                error=str(exc),
            )
            return
        await self._publish(event)

    async def _emit_plan_progress(
        self,
        task_id: UUID,
        stage: str,
        message: str,
        *,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        """向事件流推送规划阶段细粒度进度（fail-open，失败只记 warning，绝不阻断规划）。"""
        session_factory = getattr(self, "_session_factory", None)
        container = getattr(self, "container", None)
        if session_factory is None or container is None:
            return
        payload: dict[str, Any] = {"stage": stage, "message": message}
        if attempt is not None:
            payload["attempt"] = attempt
        if max_attempts is not None:
            payload["max_attempts"] = max_attempts

        async def _persist() -> TaskEvent | None:
            async with session_factory() as session:
                ev = await self._append_event(
                    session,
                    task_id,
                    event_type="plan_progress",
                    status=TaskStatus.PLANNING.value,
                    payload=payload,
                )
                await session.commit()
                return ev

        event: TaskEvent | None = None
        persist_task = asyncio.create_task(_persist())
        try:
            event = await asyncio.shield(persist_task)
        except asyncio.CancelledError:
            try:
                await persist_task
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.warning(
                "plan_progress_emit_failed",
                task_id=str(task_id),
                stage=stage,
                error=str(exc),
            )
            return
        if event is not None:
            await self._publish(event)

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
                _set_task_reason(task, waiting_reason=reason)
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

    #: 工作流预设目标指令（Issue #32）：注入动态需求区，不改动静态模板头部以保护前缀缓存
    _PRESET_DIRECTIVES: ClassVar[dict[str, str]] = {
        "literature_review": "工作流目标：输出结构清晰、带可回溯引用的文献综述",
        "research_gaps": (
            "工作流目标：聚焦研究空白分析，系统识别尚未充分研究的问题、证据薄弱点、"
            "结论冲突与未来研究方向；所有空白判断必须有检索记录与可回溯证据支撑"
        ),
        "verify_citations": "工作流目标：核验草稿已有引用的题录真实性、来源质量与论断支撑度",
        "citation_snowballing": (
            "工作流目标：围绕种子论文执行引文滚雪球（向后参考文献 + 向前施引文献），"
            "梳理研究脉络、方法演进与后续跟进工作"
        ),
        "thesis_proposal": (
            "工作流目标：按高校开题报告规范输出「问题提出背景 / 论文主要内容与技术路线 / "
            "研究计划进度安排及预期目标」三段式开题报告，技术路线 4–6 个阶段并用 "
            "![本文研究技术路线图](fig://tech_roadmap) 标记技术路线图位置，进度安排用四列"
            " Markdown 三线表（起止时间 / 研究阶段 / 主要工作内容 / 阶段性成果）"
        ),
    }

    @classmethod
    def _preset_directive(cls, preset: Any) -> str:
        """按预设名取目标指令；历史任务缺失或未知取值时回落 literature_review。"""
        key = str(preset or "literature_review")
        return cls._PRESET_DIRECTIVES.get(key, cls._PRESET_DIRECTIVES["literature_review"])

    #: 开题报告（Phase 2）从源综述继承的上限：研究空白条数与基线摘要字符数
    _INHERITED_GAP_LIMIT: ClassVar[int] = 4
    _INHERITED_BASELINE_CHARS: ClassVar[int] = 600

    @classmethod
    def _inherited_context(cls, source: Task) -> dict[str, Any]:
        """从已完成的文献综述抽取开题报告可继承的研究空白与基线摘要（0 LLM 调用）。"""
        report = getattr(source, "review_report", None) or {}
        gaps: list[str] = []
        if isinstance(report, Mapping):
            matrix = report.get("evidence_matrix")
            if isinstance(matrix, Mapping):
                questions = matrix.get("questions") or []
                cells = matrix.get("cells") or []
                gap_indices: set[int] = set()
                for cell in cells:
                    if isinstance(cell, Mapping) and cell.get("bucket") in {
                        "gap",
                        "weak_or_caveat",
                    }:
                        idx = cell.get("question_index")
                        if (
                            isinstance(idx, int)
                            and not isinstance(idx, bool)
                            and 0 <= idx < len(questions)
                        ):
                            gap_indices.add(idx)
                for idx in sorted(gap_indices):
                    text = str(questions[idx]).strip()
                    if text and text not in gaps:
                        gaps.append(text[:160])

        baseline = ""
        markdown = ""
        if isinstance(report, Mapping):
            markdown = str(report.get("final_markdown") or "")
        if not markdown:
            markdown = str(getattr(source, "final_output", "") or "")
        for block in markdown.split("\n\n"):
            cleaned = block.strip()
            if cleaned and not cleaned.startswith("#"):
                baseline = re.sub(r"\s+", " ", cleaned)[: cls._INHERITED_BASELINE_CHARS]
                break

        return {
            "source_task_id": str(source.id),
            "source_title": str(getattr(source, "title", "") or ""),
            "open_gaps": gaps[: cls._INHERITED_GAP_LIMIT],
            "baseline": baseline,
        }

    @staticmethod
    def _format_inherited_context(context: Mapping[str, Any] | None) -> str:
        """把继承上下文压成可注入规划需求的一段文本；无内容时返回空串。"""
        if not context:
            return ""
        parts = ["【继承自前置文献综述的研究空白与基线】"]
        gaps = [str(item).strip() for item in (context.get("open_gaps") or []) if str(item).strip()]
        if gaps:
            parts.append("待进一步研究的问题：" + "；".join(gaps))
        baseline = str(context.get("baseline") or "").strip()
        if baseline:
            parts.append(f"原综述基线：{baseline}")
        return "；".join(parts)

    @classmethod
    def _describe(
        cls,
        request: LiteratureReviewRequest,
        *,
        reference_library_count: int | None = None,
        inherited_context: Mapping[str, Any] | None = None,
    ) -> str:
        """把结构化请求压成任务描述（供规划 Agent 作为需求输入）。"""
        parts = [f"研究主题：{request.topic}"]
        parts.append(f"关键词：{', '.join(request.keywords)}")
        parts.append(cls._preset_directive(request.workflow_preset))
        inherited_text = cls._format_inherited_context(inherited_context)
        if inherited_text:
            parts.append(inherited_text)
        if request.date_from or request.date_to:
            parts.append(f"时间范围：{request.date_from or '不限'} ~ {request.date_to or '不限'}")
        effective_max_docs = (
            min(request.max_documents, reference_library_count)
            if request.reference_library
            and reference_library_count is not None
            and reference_library_count > 0
            else request.max_documents
        )
        parts.append(f"文献数量：{effective_max_docs}")
        parts.append(f"输出语言：{request.output_language}")
        parts.append(f"引用格式：{request.citation_style}")
        if request.document_ids:
            parts.append(f"指定文献数：{len(request.document_ids)}")
        if request.reference_library:
            if reference_library_count is not None and reference_library_count > 0:
                parts.append(
                    f"仅使用用户引用库（共 {reference_library_count} 篇，不访问外部检索源），"
                    f"所有节点目标文献数与 required_evidence 不得超过 {reference_library_count}"
                )
            else:
                parts.append("仅使用用户引用库（不访问外部检索源）")
        if request.offline_dataset:
            parts.append("使用离线语料")
        return "；".join(parts)

    @classmethod
    def _requirement(cls, task: Task) -> str:
        """从任务记录重建规划需求文本（旧任务缺失 workflow_preset 时回落默认）。"""
        config = task.config or {}
        keywords = config.get("keywords") or []
        parts = [f"研究主题：{task.title}"]
        if keywords:
            parts.append(f"关键词：{', '.join(str(item) for item in keywords)}")
        if config.get("output_language"):
            parts.append(f"输出语言：{config['output_language']}")
        parts.append(cls._preset_directive(config.get("workflow_preset")))
        inherited = config.get("inherited_context")
        if isinstance(inherited, Mapping):
            inherited_text = cls._format_inherited_context(inherited)
            if inherited_text:
                parts.append(inherited_text)
        return "；".join(parts)

    @staticmethod
    def _trace_id(task: Task | None) -> str:
        if task is None:
            return "task"
        config = task.config or {}
        return str(config.get("trace_id") or f"task-{task.id}")
