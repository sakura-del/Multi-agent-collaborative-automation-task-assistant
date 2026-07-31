"""AgentMessage 消息协议单元测试。"""

from datetime import datetime

from app.orchestration.messages import AgentMessage, MessageType


class TestAgentMessage:
    """消息协议核心功能测试。"""

    def test_create_message_with_defaults(self) -> None:
        """创建消息时自动生成 id 与 timestamp。"""
        msg = AgentMessage(
            from_agent="planner",
            to_agent="executor",
            message_type=MessageType.TASK_ASSIGN,
            content={"task": "test"},
        )
        assert msg.id  # UUID 非空
        assert isinstance(msg.timestamp, datetime)
        assert msg.content == {"task": "test"}
        assert msg.metadata == {}

    def test_message_type_enum_values(self) -> None:
        """消息类型枚举值与数据库定义一致。"""
        assert MessageType.TASK_ASSIGN == "task_assign"
        assert MessageType.RESULT == "result"
        assert MessageType.QUERY == "query"
        assert MessageType.STATUS_SYNC == "status_sync"
        assert MessageType.ERROR == "error"

    def test_to_channel(self) -> None:
        """channel 名按 to_agent 定向生成。"""
        msg = AgentMessage(
            from_agent="executor",
            to_agent="validator",
            message_type=MessageType.RESULT,
            content={},
        )
        assert msg.to_channel() == "agent:messages:validator"

    def test_to_log_dict_extracts_trace_id(self) -> None:
        """日志字典提取 trace_id 便于追踪。"""
        msg = AgentMessage(
            from_agent="planner",
            to_agent="executor",
            message_type=MessageType.TASK_ASSIGN,
            content={},
            metadata={"trace_id": "abc-123"},
        )
        log_dict = msg.to_log_dict()
        assert log_dict["trace_id"] == "abc-123"
        assert log_dict["from"] == "planner"
        assert log_dict["to"] == "executor"
        assert log_dict["type"] == "task_assign"

    def test_message_serialization(self) -> None:
        """消息可序列化为 JSON 并还原（Redis 传输依赖）。"""
        msg = AgentMessage(
            from_agent="planner",
            to_agent="executor",
            message_type=MessageType.TASK_ASSIGN,
            content={"node_id": "n1"},
            metadata={"trace_id": "t1"},
        )
        payload = msg.model_dump_json()
        restored = AgentMessage.model_validate_json(payload)
        assert restored.from_agent == "planner"
        assert restored.content == {"node_id": "n1"}
        assert restored.metadata["trace_id"] == "t1"
