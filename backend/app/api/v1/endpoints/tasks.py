"""任务生命周期 HTTP adapter（Plan 2 Task 5）。

路由只做 HTTP 校验与响应映射，全部业务委托给 `TaskExecution` 深模块：

    POST   /api/v1/tasks                          创建文献综述任务
    GET    /api/v1/tasks                          当前用户任务列表
    GET    /api/v1/tasks/{task_id}                任务快照
    POST   /api/v1/tasks/{task_id}/plan/approve   确认规划
    POST   /api/v1/tasks/{task_id}/control        人工控制
    GET    /api/v1/tasks/{task_id}/events         事件回放

鉴权：全部端点要求 Bearer access token；跨用户访问统一 404。
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.api.deps import (
    get_current_active_user,
    get_current_user_or_api_key,
    require_scope,
)
from app.billing.services.plan_service import PlanService
from app.core.config import get_settings
from app.core.exceptions import ValidationException
from app.core.quota import QuotaService
from app.db.models.user import User
from app.db.session import get_db
from app.orchestration.contracts import TaskSnapshot, TaskStatus
from app.orchestration.services.task_execution import TaskExecution
from app.schemas.task import (
    PlanApproval,
    TaskControl,
    TaskCreate,
    TaskCreateOut,
    TaskEventPage,
    TaskSummaryOut,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_execution(request: Request) -> TaskExecution:
    """从应用状态取任务运行时（lifespan 中初始化）。"""
    execution = getattr(request.app.state, "runtime", None)
    if not isinstance(execution, TaskExecution):
        raise RuntimeError("任务运行时未初始化")
    return execution


async def get_visible_workspace_ids(
    # ⚠️ 必须用**双通道**依赖：若这里写 `get_current_active_user`（JWT only），
    # 用 API Key 调 `GET /tasks` 会在这一步 401 —— 而 `POST /tasks` 却是 201
    # （因为建任务端点没有这个依赖）。首版就是这样，实测「同一 Key 列任务 401、
    # 建任务 201」，排查了半天。
    current_user: Annotated[User, Depends(get_current_user_or_api_key)],
    db: Annotated[Any, Depends(get_db)],
) -> list[UUID]:
    """当前用户所属的工作区 id 列表（任务归属双轨用）。

    为什么单独抽成依赖：可见性口径要在**所有**任务读取端点一致生效
    （列表、详情、事件、产物下载）。散落在各端点里手写查询，
    漏一处就是一次越权。

    返回空列表时，`TaskExecution` 会退化成旧的 owner-only 行为 ——
    这也是未加入任何工作区的用户（绝大多数存量用户）走的最短路径。
    """
    from sqlalchemy import select

    from app.db.models.workspace import WorkspaceMember

    rows = await db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == current_user.id)
    )
    return [row[0] for row in rows.all()]


#: 单次文献综述任务的 token 预估（提交时先按此扣减，跑完再按实际用量校正）。
#:
#: 取值依据：一次综述约 5 类节点 × 若干子任务，历史实测中位数量级在 10 万 token 上下；
#: 这里取整为 100k 作为**保守预估**——宁可先扣多了再退还，也不要先扣少了超支。
ESTIMATED_TOKENS_PER_TASK = 100_000


def build_quota_service() -> QuotaService | None:
    """按配置构造配额服务；`quota.enabled=false` 时返回 None（调用方跳过检查）。

    独立成函数便于测试直接验证「关闭配额时不检查」，也避免在模块导入期建 Redis 连接。
    """
    cfg = get_settings().quota
    if not cfg.enabled:
        return None
    return QuotaService(
        redis_client=_quota_redis_client(),
        daily_limit=cfg.daily_token_limit,
        window_seconds=cfg.window_seconds,
        key_prefix=cfg.key_prefix,
    )


def _quota_redis_client() -> Any | None:
    """配额专用 Redis 客户端（短超时；不可用时返回 None → `QuotaService` fail-open 放行）。"""
    try:
        import redis.asyncio as aioredis

        return aioredis.from_url(
            get_settings().redis.url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
            decode_responses=False,
        )
    except Exception:  # pragma: no cover - 依赖缺失/构造失败时的兜底
        return None


def get_quota_service(request: Request) -> QuotaService | None:
    """从应用状态取配额服务（lifespan 中构造一次并复用）。

    复用同一实例有两个好处：不必每请求建 Redis 连接；且与 `TaskExecution`
    内的实例一致（提交时预扣、完成时退还作用于同一份配置）。
    """
    runtime = getattr(request.app.state, "runtime", None)
    quota = getattr(runtime, "_quota", None)
    if quota is not None:
        return quota  # type: ignore[no-any-return]
    # 未接线（如测试/独立构造）时按配置兜底构造；`enabled=false` 会返回 None
    return build_quota_service()


def get_plan_service(request: Request) -> PlanService:
    """构造套餐服务（Phase 2 计费）。

    ## 为什么用独立的 session factory 而不是请求级 session

    `PlanService` 内部会开**多个**短事务（查订阅、原子扣减、读用量），
    每个方法都 `commit`。若与端点的 `get_db` 共用同一个 session：

    - `commit` 会让端点那边已加载的 ORM 对象过期；
    - 随后访问属性触发懒加载 IO，在 async 上下文里可能撞 `MissingGreenlet`
      （实测就是这么炸的）。

    所以套餐服务走自己的 session（与 `runtime` 的 `session_factory` 同源），
    事务边界互不干扰。

    优先级：`app.state.plan_service`（测试注入）→ `runtime` 的 factory → 报错。
    """
    injected = getattr(request.app.state, "plan_service", None)
    # 鸭子类型而非 isinstance：测试常注入轻量 stub（只实现当前用例用到的方法），
    # 强制 isinstance 会逼测试去构造真实 service（连带 DB 依赖）。
    # 判定用 `require_feature`（**工作区端点也调它**，而 stub 未必实现扣篇数）。
    if injected is not None and hasattr(injected, "require_feature"):
        return injected  # type: ignore[no-any-return]

    runtime = getattr(request.app.state, "runtime", None)
    session_factory = getattr(runtime, "_session_factory", None)
    if session_factory is None:
        raise RuntimeError("套餐服务未初始化：runtime 缺少 session_factory")
    return PlanService(session_factory)


ExecutionDep = Annotated[TaskExecution, Depends(get_task_execution)]
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
#: 双通道鉴权（Phase 2 开放 API）：`X-API-Key` 优先，其次 Bearer。
#: **只用于开放端点**（任务读写与产物下载）；Key 管理端点仍强制 JWT only。
ApiUserDep = Annotated[User, Depends(get_current_user_or_api_key)]
QuotaDep = Annotated["QuotaService | None", Depends(get_quota_service)]
PlanServiceDep = Annotated[PlanService, Depends(get_plan_service)]
WorkspaceIdsDep = Annotated[list[UUID], Depends(get_visible_workspace_ids)]

#: 端点 scope 依赖（只对 Key 认证的请求生效；JWT 请求直接放行）。
TasksReadScope = Annotated[None, Depends(require_scope("tasks:read"))]
TasksWriteScope = Annotated[None, Depends(require_scope("tasks:write"))]
ArtifactsReadScope = Annotated[None, Depends(require_scope("artifacts:read"))]


@router.post(
    "",
    response_model=TaskCreateOut,
    status_code=201,
    summary="创建文献综述任务",
)
async def create_task(
    payload: TaskCreate,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    _scope: TasksWriteScope,
    plan_service: PlanServiceDep,
    quota: QuotaDep = None,
) -> TaskCreateOut:
    """创建任务：**先查篇数，再查 token，最后落库**。

    ## 检查顺序（不能颠倒）

    1. **篇数（月，套餐）** —— 用户可理解的限制，可通过升级/等下月解决；
    2. **token（日，成本护栏）** —— 内部保护，用户无法自行解决。

    反过来会让用户先看到「token 预算用尽」这种他无法理解的错误，
    而他真正撞的是篇数限制。

    ## 所有检查必须在 `execution.submit` 之前

    先建后拒会留下一个注定失败的僵尸任务，且用户已消耗一次建任务限额。

    ## 篇数扣减**不退还**

    任务失败也消耗了真实算力，所以篇数不退。这与 token 预扣不同
    （token 在任务终态按实际用量退还差额）。
    """
    # ① 篇数（数据库原子扣减；超限抛 PLAN_QUOTA_EXCEEDED）
    await plan_service.check_and_consume_review(current_user.id)

    # ② token 成本护栏（Redis；超限抛 TOKEN_BUDGET_EXCEEDED）
    if quota is not None:
        await quota.check_and_consume(current_user.id, tokens=ESTIMATED_TOKENS_PER_TASK)

    task_id = await execution.submit(current_user.id, payload.to_contract())
    return TaskCreateOut(task_id=task_id, status=TaskStatus.PLANNING.value)


@router.get("", response_model=list[TaskSummaryOut], summary="列出当前用户任务")
async def list_tasks(
    execution: ExecutionDep,
    current_user: ApiUserDep,
    workspace_ids: WorkspaceIdsDep,
    _scope: TasksReadScope,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[TaskSummaryOut]:
    """分页列出可见任务（个人任务 + 我所属工作区的任务，按创建时间倒序）。"""
    snapshots = await execution.list_tasks(
        current_user.id, skip=skip, limit=limit, workspace_ids=workspace_ids
    )
    return [
        TaskSummaryOut(
            task_id=snapshot.task_id,
            status=snapshot.status,
            title=snapshot.title,
            plan_version=snapshot.plan_version,
            revision=snapshot.revision,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
        )
        for snapshot in snapshots
    ]


@router.get("/{task_id}", response_model=TaskSnapshot, summary="查询任务快照")
async def get_task(
    task_id: UUID,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    workspace_ids: WorkspaceIdsDep,
    _scope: TasksReadScope,
) -> TaskSnapshot:
    """返回任务状态、规划、子任务与复盘报告（个人任务或我所属工作区的任务）。"""
    return await execution.get_snapshot(current_user.id, task_id, workspace_ids=workspace_ids)


@router.post(
    "/{task_id}/plan/approve",
    response_model=TaskSnapshot,
    summary="确认规划并开始执行",
)
async def approve_plan(
    task_id: UUID,
    payload: PlanApproval,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    _scope: TasksWriteScope,
) -> TaskSnapshot:
    """确认规划：revision 必须等于当前 plan_version。"""
    return await execution.approve_plan(current_user.id, task_id, payload.revision)


@router.post("/{task_id}/control", response_model=TaskSnapshot, summary="人工控制任务")
async def control_task(
    task_id: UUID,
    payload: TaskControl,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    _scope: TasksWriteScope,
) -> TaskSnapshot:
    """pause / resume / cancel / retry_node / supply_input。"""
    return await execution.control(current_user.id, task_id, payload.to_contract())


@router.get("/{task_id}/events", response_model=TaskEventPage, summary="回放任务事件")
async def list_events(
    task_id: UUID,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    _scope: TasksReadScope,
    after: Annotated[int, Query(ge=0)] = 0,
) -> TaskEventPage:
    """返回 after 之后的递增事件，用于断线重连补拉。"""
    events = await execution.events_after(current_user.id, task_id, after)
    next_event_id = events[-1].event_id if events else after
    return TaskEventPage(events=events, next_event_id=next_event_id)


@router.get(
    "/{task_id}/artifacts/{artifact_id}",
    summary="下载任务导出产物（Markdown/DOCX）",
    response_class=FileResponse,
)
async def download_artifact(
    task_id: UUID,
    artifact_id: str,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    _scope: ArtifactsReadScope,
) -> FileResponse:
    """owner-scoped 下载：路径由服务端 artifact_id 解析，下载名固定。"""
    path, download_name = await execution.resolve_artifact(current_user.id, task_id, artifact_id)
    media_type = (
        "text/markdown"
        if path.suffix == ".md"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path, media_type=media_type, filename=download_name)


@router.get(
    "/{task_id}/references.bib",
    summary="下载参考文献（BibTeX）",
    response_class=Response,
)
async def download_references_bibtex(
    task_id: UUID,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    _scope: ArtifactsReadScope,
) -> Response:
    """把已持久化的参考文献导出为 BibTeX（Issue #4 M2，owner-scoped）。"""
    try:
        body, filename = await execution.export_references(current_user.id, task_id, fmt="bibtex")
    except ValidationException as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _reference_response(body, filename, "application/x-bibtex; charset=utf-8")


@router.get(
    "/{task_id}/references.ris",
    summary="下载参考文献（RIS）",
    response_class=Response,
)
async def download_references_ris(
    task_id: UUID,
    execution: ExecutionDep,
    current_user: ApiUserDep,
    _scope: ArtifactsReadScope,
) -> Response:
    """把已持久化的参考文献导出为 RIS（Issue #4 M2，owner-scoped）。"""
    try:
        body, filename = await execution.export_references(current_user.id, task_id, fmt="ris")
    except ValidationException as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return _reference_response(body, filename, "application/x-research-info-systems; charset=utf-8")


def _reference_response(body: str, filename: str, media_type: str) -> Response:
    """构造带附件头的文本响应（UTF-8，中文原样输出）。"""
    # RFC 5987：非 ASCII 文件名走 filename*，同时保留 ASCII 兜底
    quoted = quote(filename, safe="")
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"references\"; filename*=UTF-8''{quoted}"
        },
    )
