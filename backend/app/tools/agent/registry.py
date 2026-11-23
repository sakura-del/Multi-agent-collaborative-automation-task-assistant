"""工具注册表：12 个工具的声明、校验与分发。

新增工具的步骤固定为三步：写参数模型 → 写 handler → 在 `_TOOL_LIST` 登记。
登记数由 `assert len(TOOL_SPECS) == 12` 守住——数量变化必须显式改断言，
防止「悄悄少一个工具」在联调时才被发现。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.exceptions import NotFoundException
from app.tools.agent.context import ToolContext
from app.tools.agent.handlers import (
    ApproveTaskArgs,
    CreateTaskArgs,
    GetQuotaArgs,
    GetTaskArgs,
    ImportReferencesArgs,
    ListDocumentsArgs,
    ListTasksArgs,
    RetryNodeArgs,
    SearchKnowledgeArgs,
    SearchReferencesArgs,
    SearchWebArgs,
    TaskIdArgs,
    approve_task_plan,
    cancel_task,
    create_task,
    get_quota,
    get_task,
    import_references,
    list_knowledge_documents,
    list_tasks,
    retry_task_node,
    search_knowledge,
    search_references,
    search_web,
)
from app.tools.agent.spec import (
    ConfirmationRequiredException,
    ToolSpec,
    ToolValidationException,
)

_TOOL_LIST: list[ToolSpec] = [
    # ===== 任务 6 =====
    ToolSpec(
        name="list_tasks",
        domain="tasks",
        summary="列出我的综述任务（可按状态分组与标题关键词过滤）",
        args_model=ListTasksArgs,
        handler=list_tasks,
    ),
    ToolSpec(
        name="get_task",
        domain="tasks",
        summary="查询单个任务的进度、失败节点与正文（可选）",
        args_model=GetTaskArgs,
        handler=get_task,
    ),
    ToolSpec(
        name="create_task",
        domain="tasks",
        summary="创建文献综述任务（平台模型；受套餐篇数限制）",
        args_model=CreateTaskArgs,
        handler=create_task,
        write=True,
    ),
    ToolSpec(
        name="approve_task_plan",
        domain="tasks",
        summary="确认规划并开始执行（写操作）",
        args_model=ApproveTaskArgs,
        handler=approve_task_plan,
        write=True,
    ),
    ToolSpec(
        name="cancel_task",
        domain="tasks",
        summary="取消任务（写操作，不可恢复）",
        args_model=TaskIdArgs,
        handler=cancel_task,
        write=True,
    ),
    ToolSpec(
        name="retry_task_node",
        domain="tasks",
        summary="重试失败的 DAG 节点（写操作）",
        args_model=RetryNodeArgs,
        handler=retry_task_node,
        write=True,
    ),
    # ===== 引用库 2 =====
    ToolSpec(
        name="search_references",
        domain="references",
        summary="在我的引用库里按标题/作者/DOI 检索",
        args_model=SearchReferencesArgs,
        handler=search_references,
    ),
    ToolSpec(
        name="import_references",
        domain="references",
        summary="导入 BibTeX / RIS 到引用库（写操作，支持部分成功）",
        args_model=ImportReferencesArgs,
        handler=import_references,
        write=True,
    ),
    # ===== 知识库 2 =====
    ToolSpec(
        name="search_knowledge",
        domain="knowledge",
        summary="在我的知识库里做混合检索（向量 + BM25）",
        args_model=SearchKnowledgeArgs,
        handler=search_knowledge,
    ),
    ToolSpec(
        name="list_knowledge_documents",
        domain="knowledge",
        summary="列出我的知识库文档（取 document_ids 建任务时用）",
        args_model=ListDocumentsArgs,
        handler=list_knowledge_documents,
    ),
    # ===== 配额 1 =====
    ToolSpec(
        name="get_quota",
        domain="quota",
        summary="查询当前套餐、本月已用与剩余篇数",
        args_model=GetQuotaArgs,
        handler=get_quota,
    ),
    # ===== 搜索 1 =====
    ToolSpec(
        name="search_web",
        domain="search",
        summary="联网搜索（Tavily，与 /api/v1/search/web 同一 provider）",
        args_model=SearchWebArgs,
        handler=search_web,
    ),
]

TOOL_SPECS: dict[str, ToolSpec] = {spec.name: spec for spec in _TOOL_LIST}
assert len(TOOL_SPECS) == 12, "工具数量变化需显式更新契约（前端/文档依赖 12 个）"


def list_tools(domain: str | None = None) -> list[dict[str, Any]]:
    """工具目录（供 UI/模型读取）；`domain=None` 返回全部。"""
    specs = _TOOL_LIST if domain is None else [s for s in _TOOL_LIST if s.domain == domain]
    return [spec.public() for spec in specs]


def get_tool(name: str) -> ToolSpec:
    spec = TOOL_SPECS.get(name)
    if spec is None:
        raise NotFoundException(f"未知工具：{name}")
    return spec


async def invoke_tool(
    ctx: ToolContext,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    """校验参数 → 确认策略 → 执行 → 返回带工具名的结果信封。"""
    spec = get_tool(name)
    raw = arguments or {}

    if spec.write and not confirmed:
        raise ConfirmationRequiredException(
            f"「{spec.summary}」会修改数据，需要确认后执行",
            details={
                "tool": spec.name,
                "summary": spec.summary,
                "arguments": raw,
                "confirm_field": "confirmed=true",
            },
        )

    try:
        args = spec.args_model.model_validate(raw)
    except ValidationError as exc:
        raise ToolValidationException(
            f"工具 {spec.name} 的参数不合法",
            details={"errors": exc.errors(include_url=False)},
        ) from exc

    result = await spec.handler(ctx, args)
    return {
        "tool": spec.name,
        "domain": spec.domain,
        "write": spec.write,
        "result": result,
    }
