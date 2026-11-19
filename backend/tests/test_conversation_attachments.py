"""会话附件回归（P2）。

⚠️ 铁律 #0：本机不跑 pytest；本文件按静态检查交付，实际在测试机执行。

覆盖：
- 附件进入 LLM 上下文时带标题与 document_id（模型才知道「这一篇」指哪篇）；
- 附件不注入正文（防 token 打穿）；
- 契约：最多 5 个、kind 只能是 document。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.db.models.conversation import Message
from app.orchestration.services.conversation_stream import build_context
from app.schemas.conversation import MessageAttachment, MessageCreateIn


class _SessionStub:
    """按 id 返回消息；只实现 build_context 用到的 get。"""

    def __init__(self, rows: dict[uuid.UUID, Message]) -> None:
        self._rows = rows

    async def get(self, model: Any, key: uuid.UUID) -> Message | None:
        del model
        return self._rows.get(key)


def test_attachment_is_injected_with_id_but_not_body() -> None:
    document_id = uuid.uuid4()
    user = Message(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        parent_id=None,
        role="user",
        content="用这篇建综述",
        status="completed",
        attachments=[
            {"kind": "document", "document_id": str(document_id), "title": "RAG 质量评估综述"}
        ],
    )
    assistant = Message(
        id=uuid.uuid4(),
        conversation_id=user.conversation_id,
        parent_id=user.id,
        role="assistant",
        content="好的",
        status="completed",
        attachments=[],
    )
    target = Message(
        id=uuid.uuid4(),
        conversation_id=user.conversation_id,
        parent_id=assistant.id,
        role="user",
        content="开始吧",
        status="completed",
        attachments=[],
    )
    session = _SessionStub({user.id: user, assistant.id: assistant})

    messages = asyncio.run(build_context(session, target))

    attachment_turn = next(item for item in messages if "用这篇建综述" in item.content)
    assert "[本条消息附带的知识库文档]" in attachment_turn.content
    assert "RAG 质量评估综述" in attachment_turn.content
    assert str(document_id) in attachment_turn.content
    # 正文没有随附件注入（只注入引用）
    assert "正文" not in attachment_turn.content


def test_max_five_attachments() -> None:
    attachment = {
        "kind": "document",
        "document_id": str(uuid.uuid4()),
        "title": "某文献",
    }
    with pytest.raises(ValidationError):
        MessageCreateIn(content="x", attachments=[attachment] * 6)


def test_attachment_kind_is_document_only() -> None:
    with pytest.raises(ValidationError):
        MessageAttachment.model_validate(
            {"kind": "image", "document_id": str(uuid.uuid4()), "title": "图"}
        )
