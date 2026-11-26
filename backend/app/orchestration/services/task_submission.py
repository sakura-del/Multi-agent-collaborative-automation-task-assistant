"""任务提交的共享路径（HTTP 端点与 Agent 工具网关共用）。

## 为什么必须抽出来

创建任务前的检查顺序是**产品语义**，不是可选的实现细节：

1. **凭据**（BYOK 是否存在/启用）—— 用户立刻能修；
2. **篇数**（月配额）—— 用户能通过升级/等下月解决；
3. **token**（日成本护栏）—— 内部保护，用户无法自行解决。

顺序颠倒会让用户先撞到「token 预算用尽」这种他无法理解的错误。
Agent 工具如果各写一遍，漏掉任何一项都会造成「白扣篇数」或
「平台被刷爆」——所以只允许一条实现，两处调用。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.services.plan_service import PlanService
from app.core.config import settings
from app.core.exceptions import ForbiddenException
from app.core.quota import QuotaService
from app.db.models.task import Task
from app.orchestration.services.task_execution import TaskExecution
from app.schemas.task import TaskCreate
from app.security.llm_credential_service import LLMCredentialService

#: 单任务 token 预估值（与 `TaskExecution` 的预扣/退还口径同源）。
#: 取整 100k：宁可先扣多再退，也不要先扣少导致超支。
ESTIMATED_TOKENS_PER_TASK = 100_000


async def submit_review_task(
    *,
    db: AsyncSession,
    user_id: UUID,
    payload: TaskCreate,
    execution: TaskExecution,
    plan_service: PlanService,
    quota: QuotaService | None,
) -> UUID:
    """按固定门禁顺序创建综述任务，返回 task_id。

    BYOK 计费口径（Issue #15）：
    - token **始终不预扣**（算力用户已自费）；
    - 篇数默认免，`billing.byok_consumes_review_quota=true` 时与平台模型一致。
    """
    credential = None
    model_snapshot: dict[str, Any] | None = None
    if payload.llm_credential_id is not None:
        # 越权/不存在统一 404（服务层保证），停用单独 403
        credential = await LLMCredentialService().get(db, user_id, payload.llm_credential_id)
        if credential.status != "active":
            raise ForbiddenException("该模型凭据已停用，请先启用或更换")
        model_snapshot = {
            "credential_id": str(credential.id),
            "name": credential.name,
            "provider": credential.provider,
            "model": credential.model,
            "base_url": credential.base_url,
            "context_window": credential.context_window,
            "max_output_tokens": credential.max_output_tokens,
            "is_byok": True,
        }

    contract = payload.to_contract()
    byok = payload.llm_credential_id is not None

    # 仅「首次任务 + 离线示例」免扣篇数与日 token 配额（Issue #61 P1-6）；
    # 已有过任意任务的用户再点示例按常规任务扣减，防止被滥用刷免费算力。
    demo_exempt = False
    if payload.is_demo and payload.offline_dataset and not byok:
        existing_count = (
            await db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user_id))
            or 0
        )
        if existing_count == 0:
            demo_exempt = True
            model_snapshot = {"demo_exempt": True, "is_byok": False}

    # 〇 套餐单任务文献篇数上限（Issue #61 P0-3 控成本，仅限平台模型；BYOK 用户自费放行）
    if not byok and not demo_exempt:
        check_doc_limit = getattr(plan_service, "check_document_limit", None)
        if callable(check_doc_limit):
            await check_doc_limit(user_id, payload.max_documents)

    # ① 篇数（数据库原子扣减；超限抛 PLAN_QUOTA_EXCEEDED）
    if not demo_exempt and (not byok or settings.billing.byok_consumes_review_quota):
        await plan_service.check_and_consume_review(user_id)

    # ② token 成本护栏（Redis；超限抛 TOKEN_BUDGET_EXCEEDED）
    if quota is not None and not byok and not demo_exempt:
        await quota.check_and_consume(user_id, tokens=ESTIMATED_TOKENS_PER_TASK)

    # 不带凭据且非示例免扣时保持原调用形状：编排层替身（测试/验收）无需感知额外参数
    if payload.llm_credential_id is None and not demo_exempt:
        return await execution.submit(user_id, contract)
    return await execution.submit(
        user_id,
        contract,
        llm_credential_id=payload.llm_credential_id,
        model_snapshot=model_snapshot,
    )
