"""会话（conversation）域 schema：创建/更新/输出 + 消息树。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MessageRoleLiteral = Literal["user", "assistant", "system"]
MessageStatusLiteral = Literal["pending", "streaming", "completed", "interrupted", "failed"]


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


class MessagePairOut(BaseModel):
    """发送消息的响应：user 消息已落库，assistant 占位消息待流式生成。"""

    user_message: MessageOut
    assistant_message: MessageOut
