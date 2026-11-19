"""会话（conversation）域 schema：创建/更新/输出 + 消息树。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MessageRoleLiteral = Literal["user", "assistant", "system"]
MessageStatusLiteral = Literal[
    "pending", "streaming", "completed", "interrupted", "failed", "waiting_approval"
]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=120, description="缺省「新会话」")


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    leaf_id: uuid.UUID | None = Field(
        default=None, description="当前查看的分支末端；传 null 清除（回落到最新叶子）"
    )


class MessageAttachment(BaseModel):
    """消息附件：知识库文档引用（P2）。

    只存引用与标题快照：文件真源在 `knowledge_doc`，删除文档不应让历史消息
    变成「幽灵附件」——标题保留可读，`document_id` 查不到时由上层提示重新上传。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document"] = "document"
    document_id: uuid.UUID
    title: str = Field(min_length=1, max_length=256)


class MessageToolCall(BaseModel):
    """一条工具调用轨迹（P3）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    summary: str = ""
    status: Literal["pending_approval", "ok", "error", "rejected"] = "ok"
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_preview: str | None = None


class MessagePendingTool(BaseModel):
    """等待确认的写操作。`arguments` 供确认卡展示「将要做什么」。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    summary: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    conversation_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    role: MessageRoleLiteral
    content: str
    status: MessageStatusLiteral
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None
    created_at: datetime
    attachments: list[MessageAttachment] = Field(default_factory=list)
    tool_calls: list[MessageToolCall] = Field(default_factory=list)
    pending_tool: MessagePendingTool | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    title: str
    archived: bool
    message_count: int
    leaf_id: uuid.UUID | None = None
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(ConversationOut):
    """会话详情：消息**扁平返回**，前端按 parent_id 建树（含所有分支）。"""

    messages: list[MessageOut] = Field(default_factory=list)


class MessageCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000, description="用户消息（Markdown）")
    parent_id: uuid.UUID | None = Field(
        default=None, description="父消息；缺省 = 当前分支末端（leaf）"
    )
    attachments: list[MessageAttachment] = Field(
        default_factory=list, max_length=5, description="知识库文档附件（最多 5 个）"
    )


class MessagePairOut(BaseModel):
    """发送消息的响应：user 消息已落库，assistant 占位消息待流式生成。"""

    user_message: MessageOut
    assistant_message: MessageOut


class ConversationCapabilitiesOut(BaseModel):
    """会话附件/语音能力（前端据此启用按钮，不靠猜）。"""

    model_config = ConfigDict(extra="forbid")

    documents: bool = True
    images: bool = False
    image_reason: str | None = None
    max_attachments: int = 5
    max_file_mb: int = 30
    voice: str = "browser"


class ToolConfirmIn(BaseModel):
    """确认/取消待执行的写工具。"""

    model_config = ConfigDict(extra="forbid")

    approve: bool
