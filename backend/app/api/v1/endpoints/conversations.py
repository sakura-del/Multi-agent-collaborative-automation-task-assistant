"""会话内核端点（Issue #8 P0）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/conversations` | 新建会话 |
| `GET` | `/conversations` | 我的会话列表（默认不含归档） |
| `GET` | `/conversations/{id}` | 详情：会话 + 全部消息（扁平，前端建树） |
| `PATCH` | `/conversations/{id}` | 改名 / 归档 / 切换分支末端 |
| `DELETE` | `/conversations/{id}` | 删除（消息级联） |
| `POST` | `/conversations/{id}/messages` | 发送用户消息 → 生成 assistant 占位 |
| `GET` | `/conversations/{id}/messages/{mid}/stream` | **SSE** 流式生成（delta/done/error） |

## 停止生成的约定

**没有** stop 端点：前端 abort 掉 SSE 连接即为停止——服务端
`StreamingResponse` 的生成器会被 cancel，`CancelledError` 分支把已生成内容
落库为 `interrupted`。加一个需要轮询协同的 stop 端点只会引入「停了但还在写」
的竞态，不如让「连接即生成」这一个真相说了算。

## 分支与树

`POST /messages` 不传 `parent_id` 时接在当前分支末端（`conversation.leaf_id`，
缺省取最新叶子）；传 `parent_id` = 从任意节点继续（编辑重发/分支）。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.api.v1.endpoints.tasks import (
    ExecutionDep,
    PlanServiceDep,
    QuotaDep,
    WorkspaceIdsDep,
)
from app.core.config import get_settings
from app.core.exceptions import AppException, ValidationException
from app.db.models.conversation import Conversation, Message
from app.db.models.knowledge import KnowledgeDoc
from app.db.models.user import User
from app.db.session import get_db
from app.orchestration.services.conversation_stream import (
    ConversationStreamer,
    latest_leaf,
    request_stop,
)
from app.schemas.conversation import (
    ConversationCapabilitiesOut,
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationUpdate,
    MessageAttachment,
    MessageCreateIn,
    MessageOut,
    MessagePairOut,
    MessagePendingTool,
    MessageToolCall,
    ToolConfirmIn,
)
from app.tools.agent import ToolContext, invoke_tool

router = APIRouter(prefix="/conversations", tags=["conversations"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _tool_result_summary(name: str, result: dict[str, Any], ok: bool) -> str:
    """把工具结果写成人能读的一句话（不把整段 JSON 倒进聊天）。"""
    if not ok:
        return f"执行失败：{result.get('error', '未知错误')}"
    if name == "create_task" and result.get("task_id"):
        return (
            f"已创建综述任务 `{result['task_id']}`。规划生成后请在任务页确认，"
            "确认后才会开始检索与写作。"
        )
    if name == "cancel_task" and result.get("task_id"):
        return f"已取消任务 `{result['task_id']}`。"
    if name == "import_references":
        return (
            f"已导入文献：新增 {result.get('created', 0)} 条、"
            f"合并 {result.get('merged', 0)} 条、跳过 {result.get('skipped', 0)} 条。"
        )
    return "已执行该操作。"


def _to_message_out(row: Message) -> MessageOut:
    return MessageOut(
        id=row.id,
        conversation_id=row.conversation_id,
        parent_id=row.parent_id,
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        status=row.status,  # type: ignore[arg-type]
        model=row.model,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        error=row.error,
        created_at=row.created_at,
        attachments=[MessageAttachment.model_validate(item) for item in (row.attachments or [])],
        tool_calls=[MessageToolCall.model_validate(item) for item in (row.tool_calls or [])],
        pending_tool=(
            MessagePendingTool.model_validate(row.pending_tool) if row.pending_tool else None
        ),
    )


def _to_conversation_out(row: Conversation) -> ConversationOut:
    return ConversationOut(
        id=row.id,
        title=row.title,
        archived=row.archived,
        message_count=row.message_count,
        leaf_id=row.leaf_id,
        last_message_at=row.last_message_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _owned(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    """owner-scoped 取会话；不存在/越权统一 404（不泄露存在性）。"""
    row = await session.get(Conversation, conversation_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return row


@router.post("", response_model=ConversationOut, status_code=201, summary="新建会话")
async def create_conversation(
    payload: ConversationCreate, current_user: CurrentUserDep, db: DbDep
) -> ConversationOut:
    row = Conversation(
        user_id=current_user.id,
        title=(payload.title or "新会话").strip() or "新会话",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_conversation_out(row)


@router.get("", response_model=list[ConversationOut], summary="我的会话列表")
async def list_conversations(
    current_user: CurrentUserDep,
    db: DbDep,
    include_archived: bool = False,
    limit: int = 50,
) -> list[ConversationOut]:
    query = (
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.updated_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if not include_archived:
        query = query.where(Conversation.archived.is_(False))
    rows = (await db.execute(query)).scalars().all()
    return [_to_conversation_out(row) for row in rows]


@router.get(
    "/capabilities",
    response_model=ConversationCapabilitiesOut,
    summary="会话附件/语音能力（前端据此启用按钮）",
)
async def get_capabilities(current_user: CurrentUserDep) -> ConversationCapabilitiesOut:
    """能力来自**平台模型配置**，不由前端猜。

    图片：平台模型若为纯文本（当前 Qwen 就是），前端禁用图片按钮并说明原因；
    换成视觉模型时只需 `LLM__SUPPORTS_IMAGE=true`，无需改前端。

    文档：走知识库上传管线（pdf/docx/md/txt，≤30MB），始终可用。
    """
    del current_user  # 仅要求登录；能力与用户无关
    llm = get_settings().llm
    return ConversationCapabilitiesOut(
        documents=True,
        images=llm.supports_image,
        image_reason=(
            None
            if llm.supports_image
            else "当前平台模型不支持图片输入；切换视觉模型并设置 LLM__SUPPORTS_IMAGE=true 后开启"
        ),
        max_attachments=5,
        max_file_mb=30,
        voice="browser",
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetailOut,
    summary="会话详情（含消息树）",
)
async def get_conversation(
    conversation_id: uuid.UUID, current_user: CurrentUserDep, db: DbDep
) -> ConversationDetailOut:
    row = await _owned(db, current_user.id, conversation_id)
    messages = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == row.id)
                .order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    # 僵尸收敛：进程重启/异常退出会留下永远 streaming 的消息——
    # 读取时把超时的改成 interrupted（惰性修复，不做后台任务）
    stale_before = datetime.now(UTC) - timedelta(minutes=5)
    changed = False
    for message in messages:
        if message.status in ("pending", "streaming") and message.updated_at < stale_before:
            message.status = "interrupted"
            message.error = message.error or "生成中断（服务重启或连接丢失）"
            changed = True
    if changed:
        await db.commit()

    base = _to_conversation_out(row)
    return ConversationDetailOut(
        **base.model_dump(), messages=[_to_message_out(m) for m in messages]
    )


@router.patch("/{conversation_id}", response_model=ConversationOut, summary="改名/归档/切换分支")
async def update_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    current_user: CurrentUserDep,
    db: DbDep,
) -> ConversationOut:
    row = await _owned(db, current_user.id, conversation_id)
    if payload.title is not None:
        row.title = payload.title.strip()
    if payload.archived is not None:
        row.archived = payload.archived
    if payload.leaf_id is not None:
        leaf = await db.get(Message, payload.leaf_id)
        if leaf is None or leaf.conversation_id != row.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="分支末端不属于该会话"
            )
        row.leaf_id = leaf.id
    await db.commit()
    await db.refresh(row)
    return _to_conversation_out(row)


@router.delete("/{conversation_id}", status_code=204, summary="删除会话")
async def delete_conversation(
    conversation_id: uuid.UUID, current_user: CurrentUserDep, db: DbDep
) -> None:
    row = await _owned(db, current_user.id, conversation_id)
    await db.delete(row)
    await db.commit()


@router.post(
    "/{conversation_id}/messages",
    response_model=MessagePairOut,
    status_code=201,
    summary="发送消息（生成 assistant 占位，随后走 SSE）",
)
async def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreateIn,
    current_user: CurrentUserDep,
    db: DbDep,
) -> MessagePairOut:
    """写入用户消息 + assistant 占位；**不在这里调模型**（生成走 SSE 端点）。"""
    conversation = await _owned(db, current_user.id, conversation_id)

    # ⚠️ 必须区分「没传 parent_id」与「显式传 null」：
    # 编辑**首条**消息要挂回根（parent_id=null），若把 null 当成缺省，
    # 新分支会接到当前叶节点上，两条分支被串成一条链（E2E 实测 2026-11-19）。
    if "parent_id" in payload.model_fields_set:
        parent_id = payload.parent_id
        if parent_id is not None:
            parent = await db.get(Message, parent_id)
            if parent is None or parent.conversation_id != conversation.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="父消息不属于该会话"
                )
    else:
        parent_id = conversation.leaf_id or await latest_leaf(db, conversation.id)

    # 附件：只收自己名下的知识库文档；不复制文件（引用 + 标题快照）
    attachment_rows: list[dict[str, Any]] = []
    if payload.attachments:
        document_ids = [item.document_id for item in payload.attachments]
        owned = set(
            (
                await db.execute(
                    select(KnowledgeDoc.id).where(
                        KnowledgeDoc.user_id == current_user.id,
                        KnowledgeDoc.id.in_(document_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = [str(item) for item in document_ids if item not in owned]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"附件不存在或不属于你：{', '.join(missing)}",
            )
        attachment_rows = [item.model_dump(mode="json") for item in payload.attachments]

    user_message = Message(
        conversation_id=conversation.id,
        parent_id=parent_id,
        role="user",
        content=payload.content.strip(),
        status="completed",
        attachments=attachment_rows,
    )
    db.add(user_message)
    await db.flush()

    assistant_message = Message(
        conversation_id=conversation.id,
        parent_id=user_message.id,
        role="assistant",
        content="",
        status="pending",
    )
    db.add(assistant_message)
    await db.flush()

    conversation.message_count = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation.id)
            )
        )
        or 0
    )
    conversation.last_message_at = datetime.now(UTC)
    # 首条用户消息做标题（截断 40 字，去换行）
    if conversation.message_count <= 2:
        conversation.title = payload.content.strip().replace("\n", " ")[:40] or "新会话"
    # 新消息成为当前分支末端
    conversation.leaf_id = assistant_message.id
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(assistant_message)

    return MessagePairOut(
        user_message=_to_message_out(user_message),
        assistant_message=_to_message_out(assistant_message),
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/tool-confirm",
    response_model=MessageOut,
    summary="确认/取消待执行的写工具（P3）",
)
async def confirm_tool(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: ToolConfirmIn,
    current_user: CurrentUserDep,
    db: DbDep,
    execution: ExecutionDep,
    plan_service: PlanServiceDep,
    quota: QuotaDep,
    workspace_ids: WorkspaceIdsDep,
) -> MessageOut:
    """写工具的唯一执行入口：模型只能「提议」，用户点了确认才真的执行。

    拒绝也要落库（`rejected`），否则刷新后卡片会再次出现。
    """
    await _owned(db, current_user.id, conversation_id)
    row = await db.get(Message, message_id)
    if row is None or row.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
    if row.status != "waiting_approval" or not row.pending_tool:
        raise ValidationException("该消息没有待确认的工具操作")

    pending = dict(row.pending_tool)
    records = [dict(item) for item in (row.tool_calls or [])]
    index = next(
        (i for i, item in enumerate(records) if item.get("id") == pending.get("id")),
        -1,
    )

    if not payload.approve:
        if index >= 0:
            records[index]["status"] = "rejected"
            records[index]["result_preview"] = "用户取消"
        row.tool_calls = records
        row.pending_tool = None
        row.status = "completed"
        row.content = "已取消该操作。"
        await db.commit()
        await db.refresh(row)
        return _to_message_out(row)

    context = ToolContext(
        db=db,
        user_id=current_user.id,
        execution=execution,
        plan_service=plan_service,
        quota=quota,
        workspace_ids=workspace_ids,
    )
    try:
        outcome = await invoke_tool(
            context,
            str(pending.get("name", "")),
            dict(pending.get("arguments") or {}),
            confirmed=True,
        )
        result: Any = outcome.get("result", {})
        ok = True
    except AppException as exc:
        result = {"error": exc.message}
        ok = False

    preview = json.dumps(result, ensure_ascii=False, default=str)[:2000]
    if index >= 0:
        records[index]["status"] = "ok" if ok else "error"
        records[index]["result_preview"] = preview
    row.tool_calls = records
    row.pending_tool = None
    row.status = "completed" if ok else "failed"
    row.error = None if ok else str(result.get("error"))[:500]
    row.content = _tool_result_summary(str(pending.get("name", "")), result, ok)
    await db.commit()
    await db.refresh(row)
    return _to_message_out(row)


@router.post(
    "/{conversation_id}/messages/{message_id}/stop",
    summary="停止生成（协同取消；生成循环下一个 delta 内退出并落库 interrupted）",
)
async def stop_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbDep,
) -> dict[str, str]:
    """为什么需要它：客户端 abort 连接时，生成器挂在 LLM await 上感知不到断开
    （生产实测：abort 后消息长时间停在 streaming）。停止请求写进程内标志，
    循环每迭代检查一次；同时把 status 直接改 interrupted 兜底（循环已死的情形）。
    """
    await _owned(db, current_user.id, conversation_id)
    row = await db.get(Message, message_id)
    if row is None or row.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
    request_stop(message_id)
    # 兜底：循环已经不在跑（进程重启等），直接收敛状态
    if row.status in ("pending", "streaming"):
        row.status = "interrupted"
        row.error = "用户停止生成"
        await db.commit()
    return {"status": "stopping"}


@router.get(
    "/{conversation_id}/messages/{message_id}/stream",
    summary="SSE 流式生成 assistant 消息",
)
async def stream_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    execution: ExecutionDep,
    plan_service: PlanServiceDep,
    quota: QuotaDep,
    workspace_ids: WorkspaceIdsDep,
    tools: bool = False,
) -> StreamingResponse:
    """事件：`start` → (`delta` | `tool_call` | `tool_result`)* → `done` / `tool_approval`。

    `tools=false`（默认）保持 P0 纯聊天；前端开启「工具」时传 `?tools=1`，
    只读工具自动执行，写工具暂停为 `waiting_approval` 等确认。

    客户端 abort 即中断（落库 interrupted）。
    """
    await _owned(db, current_user.id, conversation_id)

    from app.db.session import AsyncSessionLocal
    from app.llm.factory import get_llm_manager

    llm = get_llm_manager().get_primary()
    tool_context = (
        ToolContext(
            db=db,
            user_id=current_user.id,
            execution=execution,
            plan_service=plan_service,
            quota=quota,
            workspace_ids=workspace_ids,
        )
        if tools
        else None
    )
    streamer = ConversationStreamer(
        AsyncSessionLocal,
        llm,
        tool_context=tool_context,
        tools_enabled=tools,
    )
    return StreamingResponse(
        streamer.stream(conversation_id, message_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 反代必须关缓冲，否则 delta 会攒成一坨（生产踩坑高发点）
            "X-Accel-Buffering": "no",
        },
    )
