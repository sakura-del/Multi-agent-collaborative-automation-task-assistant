"""BaseAgent 抽象基类：四大 Agent 的统一骨架。

采用模板方法模式：
- BaseAgent.execute() 统管状态转换、日志、异常处理等通用逻辑
- 子类只需实现 _run() 完成具体业务推理

设计要点：
- 依赖注入：LLM 与消息总线通过构造函数注入，便于测试用 Mock（松耦合）
- 扩展性：_run() 可调用 self.llm 进行推理，后续阶段可注入工具/RAG（开闭原则）
- 异常隔离：_run() 抛异常时自动转入 ERROR 状态并广播错误消息，不崩溃
- 日志追踪：所有操作携带 agent_type + trace_id，便于全链路复盘
"""

from __future__ import annotations

import abc
import time
import uuid
from typing import Any

from app.core.logging import get_logger
from app.llm.base import BaseLLM
from app.orchestration.messages import AgentMessage, MessageType
from app.orchestration.state import AgentStateMachine, AgentStatus

logger = get_logger(__name__)


class BaseAgent(abc.ABC):
    """Agent 抽象基类。

    子类需实现：
        _run(input_data: dict) -> dict   核心业务逻辑
        agent_type -> str                Agent 类型标识（planner/executor/...）

    可选覆盖：
        _handle_message(msg)             处理收到的消息
        initialize()                     异步初始化
    """

    def __init__(
        self,
        *,
        llm: BaseLLM | None = None,
        message_bus: Any | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.llm = llm
        self.message_bus = message_bus
        self.agent_id = agent_id or str(uuid.uuid4())
        self._state = AgentStateMachine()
        # 执行统计：供复盘 Agent 采集
        self._exec_count = 0
        self._last_duration: float | None = None

    @property
    @abc.abstractmethod
    def agent_type(self) -> str:
        """Agent 类型标识，如 planner / executor / validator / reviewer。"""

    @property
    def status(self) -> AgentStatus:
        """当前 Agent 状态。"""
        return self._state.status

    def get_status(self) -> dict[str, Any]:
        """返回状态快照，供调度器与 API 查询。"""
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "status": self._state.status.value,
            "exec_count": self._exec_count,
            "last_duration_s": self._last_duration,
        }

    async def initialize(self) -> None:
        """异步初始化钩子，子类可覆盖以加载配置、注册能力。

        默认空实现。基类不做重逻辑，避免强制子类承担不必要开销。
        """
        logger.info("agent_initialized", agent_type=self.agent_type, agent_id=self.agent_id)

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行任务（模板方法）。

        统一处理：状态流转 → 调用子类 _run → 耗时统计 → 异常兜底。
        子类不应覆盖此方法，而应实现 _run。

        并发说明：调度器会并行执行 DAG 中的多个节点，而 Agent 实例被复用。
        因此进入 RUNNING 前先把上一次残留状态归位到 IDLE，避免并发/重入时
        抛 `非法状态转换: running → running`（Plan 5 Task 8 彩排实测）。
        """
        trace_id = input_data.get("trace_id", str(uuid.uuid4()))
        if self._state.status is not AgentStatus.IDLE:
            # 上一次执行尚未回到 IDLE（并发重入或异常遗留）→ 显式重置
            self._state.reset(AgentStatus.IDLE)
        self._state.transition(AgentStatus.RUNNING)
        start = time.perf_counter()
        try:
            result = await self._run(input_data)
            self._last_duration = time.perf_counter() - start
            self._exec_count += 1
            # 任务完成后回到空闲，等待下一任务
            self._settle(AgentStatus.COMPLETED)
            self._settle(AgentStatus.IDLE)
            logger.info(
                "agent_execute_ok",
                agent_type=self.agent_type,
                trace_id=trace_id,
                duration_s=round(self._last_duration, 3),
            )
            return result
        except Exception as exc:
            self._last_duration = time.perf_counter() - start
            self._settle(AgentStatus.ERROR)
            logger.error(
                "agent_execute_failed",
                agent_type=self.agent_type,
                trace_id=trace_id,
                error=str(exc),
                duration_s=round(self._last_duration, 3),
            )
            # 广播错误消息，供复盘 Agent 采集与其他 Agent 感知
            await self._broadcast_error(exc, trace_id)
            # 错误恢复到空闲，允许后续重试调度
            self._settle(AgentStatus.IDLE)
            raise

    def _settle(self, target: AgentStatus) -> None:
        """把状态推进到 target；并发场景下若已被其它协程改走则跳过。

        背景（Plan 5 Task 8 实跑）：调度器并行执行节点而复用同一 Agent 实例，
        两个协程共享状态机时，A 已把状态推到 COMPLETED/IDLE，B 再 transition(ERROR)
        就会抛 `非法状态转换: idle → error`，把正常结果变成"校验异常"转人工。
        收尾状态只用于可观测性，不应让并发竞争中断业务结果。
        """
        if self._state.can_transition(target):
            self._state.transition(target)
        else:
            self._state.reset(target)

    @abc.abstractmethod
    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """子类实现的核心业务逻辑。

        参数 input_data 携带任务输入与 trace_id。
        返回执行结果字典，结构由子类按业务定义。
        抛出的异常由 execute() 统一兜底。
        """

    async def handle_message(self, message: AgentMessage) -> dict[str, Any] | None:
        """处理收到的消息。

        默认按消息类型分发：ERROR 转日志，其余交由子类 _handle_message。
        """
        logger.info("agent_recv_message", **message.to_log_dict())
        if message.message_type is MessageType.ERROR:
            logger.warning("agent_recv_error", **message.to_log_dict())
        # 所有消息类型均交给子类处理（含 ERROR），复盘 Agent 需采集错误日志
        return await self._handle_message(message)

    async def _handle_message(self, message: AgentMessage) -> dict[str, Any] | None:
        """子类可覆盖的消息处理逻辑，默认空实现。"""
        return None

    async def _send_message(
        self,
        to_agent: str,
        message_type: MessageType,
        content: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> None:
        """通过消息总线发送消息。

        消息总线未注入时仅记日志，不阻断（便于无总线环境下的单元测试）。
        """
        msg = AgentMessage(
            from_agent=self.agent_type,
            to_agent=to_agent,
            message_type=message_type,
            content=content,
            metadata={"trace_id": trace_id or str(uuid.uuid4())},
        )
        if self.message_bus is not None:
            await self.message_bus.publish(msg)
        else:
            logger.debug("agent_send_no_bus", **msg.to_log_dict())

    async def _broadcast_error(self, exc: Exception, trace_id: str) -> None:
        """向 reviewer 广播错误，供复盘归因。"""
        await self._send_message(
            "reviewer",
            MessageType.ERROR,
            {
                "agent_type": self.agent_type,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            trace_id=trace_id,
        )
