"""WebSocket 端点预留。

阶段1：仅建立 WebSocket 端点骨架，验证连接握手可用。
阶段7（实时交互增强）将在此填充：
- 任务进度实时推送
- Agent 消息流广播
- 用户与 Agent 的交互式对话

设计要点：
- 鉴权：通过 query 参数 access_token 传递（WebSocket 不便使用 Header）
- 链路追踪：连接建立时绑定 trace_id 到 contextvars
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.logging import bind_request_context, get_logger

router = APIRouter(tags=["websocket"])

logger = get_logger(__name__)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket 预留端点。

    阶段1：仅完成握手并回显消息，验证连接链路。
    后续阶段填充业务逻辑（任务进度推送 / Agent 消息流）。
    """
    # 为本次 WebSocket 连接生成 trace_id，便于链路追踪
    trace_id = uuid.uuid4().hex
    bind_request_context(trace_id=trace_id)

    await websocket.accept()
    logger.info("websocket_connected", trace_id=trace_id)

    try:
        while True:
            # 阶段1：简单回显，验证双向通信
            data = await websocket.receive_text()
            logger.info("websocket_message", trace_id=trace_id, data=data)
            await websocket.send_text(f'{{"type":"echo","data":"{data}"}}')
    except WebSocketDisconnect:
        logger.info("websocket_disconnected", trace_id=trace_id)
    except Exception:
        # 异常时确保连接正确关闭
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close()
        logger.warning("websocket_error", trace_id=trace_id)
