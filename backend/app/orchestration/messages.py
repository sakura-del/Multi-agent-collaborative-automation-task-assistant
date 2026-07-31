"""Agent 间消息协议。

定义统一的 AgentMessage 数据结构，所有 Agent 间通信均通过此协议，
实现松耦合：发送方无需知道接收方的具体实现，仅依赖消息类型与目标。

设计要点：
- Pydantic BaseModel，便于校验与 JSON 序列化（Redis 传输）
- 消息类型枚举覆盖全部协作场景：任务分配/结果上报/查询/状态同步/错误
- metadata 携带 trace_id 实现全链路追踪，优先级支持调度排序
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(StrEnum):
    """Agent 间消息类型。

    覆盖四大 Agent 协作的全部场景：
    - TASK_ASSIGN: 规划→执行，分配子任务
    - RESULT: 执行→校验/复盘，上报执行结果
    - QUERY: Agent 间信息查询（如执行向规划确认需求细节）
    - STATUS_SYNC: 状态广播，供调度器感知全局进度
    - ERROR: 异常上报，触发降级或人工干预
    """

    TASK_ASSIGN = "task_assign"
    RESULT = "result"
    QUERY = "query"
    STATUS_SYNC = "status_sync"
    ERROR = "error"


class AgentMessage(BaseModel):
    """Agent 间传递的统一消息结构。

    用法：
        msg = AgentMessage(
            from_agent="planner",
            to_agent="executor",
            message_type=MessageType.TASK_ASSIGN,
            content={"subtask_id": "...", "description": "..."},
        )
        await bus.publish(msg)

    字段对齐数据库 AgentInteraction 表，便于持久化。
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="消息唯一标识")
    from_agent: str = Field(..., description="发送方 Agent 类型（planner/executor/...）")
    to_agent: str = Field(..., description="接收方 Agent 类型")
    message_type: MessageType = Field(..., description="消息类型")
    content: dict[str, Any] = Field(default_factory=dict, description="消息内容（灵活 JSON 结构）")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="元数据：trace_id / 优先级 / 重试标记等",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="消息创建时间（UTC）",
    )

    def to_channel(self) -> str:
        """生成 Redis Pub/Sub channel 名。

        每个 Agent 监听自己的 channel：agent:messages:{agent_type}，
        发送方按 to_agent 字段定向投递。
        """
        return f"agent:messages:{self.to_agent}"

    def to_log_dict(self) -> dict[str, Any]:
        """转换为日志友好的字典（避免 datetime 序列化问题）。"""
        return {
            "msg_id": self.id,
            "from": self.from_agent,
            "to": self.to_agent,
            "type": self.message_type.value,
            "trace_id": self.metadata.get("trace_id"),
        }
