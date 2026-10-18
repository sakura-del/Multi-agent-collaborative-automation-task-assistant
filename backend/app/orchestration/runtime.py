"""运行时依赖容器（Plan 2 Task 4）。

`RuntimeContainer` 统一持有任务执行所需的依赖，避免 TaskExecution 直接构造：
- LLMManager（启动时初始化，供后续文献 Agent 使用）
- Agent 工厂（executor / validator / reviewer）
- Planner 工厂
- Knowledge 查询接口（Plan 3 文献工作流接入）
- EventPublisher（默认空实现，Plan 2 Task 6 换成 Redis 实现）
- 后台任务注册表（track / cancel / close 只管理自己启动的协程）
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.orchestration.agents import ExecutorAgent, PlannerAgent, ReviewerAgent, ValidatorAgent
from app.orchestration.events import (
    EventPublisher,
    InMemoryEventPublisher,
    InMemoryTicketService,
    RedisEventPublisher,
    RedisTicketService,
)
from app.orchestration.persistence import DatabaseScheduleObserver
from app.orchestration.scheduler import ScheduleObserver, TaskScheduler

logger = get_logger(__name__)

# 工厂类型：返回可执行 Agent 实例
PlannerFactory = Callable[[], Any]
AgentFactory = Callable[[str], Any]


class RuntimeContainer:
    """任务运行时依赖容器与后台任务注册表。"""

    def __init__(
        self,
        *,
        session_factory: Any = AsyncSessionLocal,
        event_publisher: EventPublisher | None = None,
        ticket_service: Any | None = None,
        planner_factory: PlannerFactory | None = None,
        agent_factory: AgentFactory | None = None,
        knowledge_query: Any | None = None,
        literature_source: Any | None = None,
        llm: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.event_publisher: EventPublisher = event_publisher or InMemoryEventPublisher()
        self.ticket_service: Any = ticket_service or InMemoryTicketService()
        self.knowledge_query = knowledge_query
        self.literature_source = literature_source
        self.llm_manager: Any | None = None
        # 显式注入的 LLM 客户端：测试/验收用，优先于全局 LLMManager，避免改动模块单例
        self._injected_llm = llm
        self._planner_factory: PlannerFactory = planner_factory or PlannerAgent
        self._agent_factory: AgentFactory = agent_factory or self._build_default_agent
        self._llm_missing_warned = False
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._started = False
        self._explicit_publisher = event_publisher is not None
        self._explicit_ticket_service = ticket_service is not None

    async def start(self) -> None:
        """初始化运行时依赖；Redis 可用时把事件/票据升级为 Redis 实现。"""
        if self._started:
            return
        # 已显式注入 LLM（测试/验收）时不再读全局单例，避免污染进程状态
        if self._injected_llm is None:
            from app.llm.factory import get_llm_manager

            self.llm_manager = get_llm_manager()

        if not (self._explicit_publisher and self._explicit_ticket_service):
            try:
                from app.core.redis import RedisManager

                # 短超时探测：Redis 不可用时立即回退内存实现，不拖慢启动
                if await asyncio.wait_for(RedisManager.ping(), timeout=0.5):
                    client = RedisManager.get_client()
                    if not self._explicit_publisher:
                        self.event_publisher = RedisEventPublisher(client)
                    if not self._explicit_ticket_service:
                        self.ticket_service = RedisTicketService(client)
                    logger.info("runtime_realtime_backend", backend="redis")
                else:
                    logger.info("runtime_realtime_backend", backend="in_memory")
            except Exception:  # Redis 不可用时保持内存实现，不阻塞启动
                logger.warning("runtime_realtime_backend_fallback", backend="in_memory")

        self._started = True
        logger.info("runtime_container_started")

    async def close(self) -> None:
        """取消并等待本容器启动的后台任务，不触碰其他协程。"""
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._started = False
        logger.info("runtime_container_closed", cancelled=len(pending))

    def create_scheduler(self, observer: ScheduleObserver) -> TaskScheduler:
        """创建带观察者的调度器。"""
        return TaskScheduler(agent_factory=self._agent_factory, observer=observer)

    def create_observer(self) -> DatabaseScheduleObserver:
        """创建数据库调度观察者（每次执行一组独立 session）。"""
        return DatabaseScheduleObserver(
            session_factory=self.session_factory,
            publisher=self.event_publisher,
        )

    def create_planner(self) -> Any:
        """创建规划 Agent 实例（注入容器 LLM，缺失时保留降级路径）。"""
        llm = self._llm_client()
        try:
            return self._planner_factory(llm=llm)  # type: ignore[call-arg]
        except TypeError:
            # 注入式工厂（退出验收/测试）不接受 llm 参数
            return self._planner_factory()

    def create_agent(self, agent_type: str) -> Any:
        """按类型创建 Agent 实例（reviewer 等由 TaskExecution 使用）。"""
        return self._agent_factory(agent_type)

    def create_llm(self) -> Any | None:
        """取容器 LLM 客户端（供工作流内部自建 Agent 时注入，缺失时返回 None）。"""
        return self._llm_client()

    def use_planner(self, factory: PlannerFactory) -> None:
        """替换规划器工厂（退出验收/实验注入确定性规划器）。"""
        self._planner_factory = factory

    def use_agent_factory(self, factory: AgentFactory) -> None:
        """替换 Agent 工厂（退出验收/实验注入确定性执行与校验器）。"""
        self._agent_factory = factory

    def track(self, task_id: UUID, coroutine: Coroutine[Any, Any, None]) -> None:
        """登记并启动后台协程；同一任务已有运行中的协程时不重复启动。"""
        existing = self._tasks.get(task_id)
        if existing is not None and not existing.done():
            logger.warning("runtime_task_already_tracked", task_id=str(task_id))
            return

        task: asyncio.Task[None] = asyncio.create_task(coroutine, name=f"task-{task_id}")
        self._tasks[task_id] = task
        task.add_done_callback(lambda _finished: self._tasks.pop(task_id, None))

    async def cancel(self, task_id: UUID) -> None:
        """取消指定任务的后台协程（暂停/取消任务时使用）。"""
        task = self._tasks.pop(task_id, None)
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def is_tracking(self, task_id: UUID) -> bool:
        """判断容器是否仍跟踪该任务的后台协程。"""
        return task_id in self._tasks

    def _llm_client(self) -> Any | None:
        """取容器 LLM 客户端；显式注入优先，未配置时返回 None（Agent 走降级路径）。

        文献场景要求真实模型执行，因此首次缺失时打印一次告警，避免"静默降级"。
        """
        if self._injected_llm is not None:
            return self._injected_llm
        if self.llm_manager is None:
            try:
                from app.llm.factory import get_llm_manager

                self.llm_manager = get_llm_manager()
            except Exception:
                self.llm_manager = None
        if self.llm_manager is None:
            return None
        try:
            return self.llm_manager.get_primary()
        except Exception as exc:
            if not self._llm_missing_warned:
                logger.warning("runtime_llm_unavailable", error=str(exc))
                self._llm_missing_warned = True
            return None

    def _build_default_agent(self, agent_type: str) -> Any:
        """默认 Agent 工厂：有 LLM 时注入真实模型，缺失时保留降级路径。"""
        llm = self._llm_client()
        if agent_type == "executor":
            return ExecutorAgent(llm=llm)
        if agent_type == "validator":
            # 与降级执行输出兼容的宽松规则；文献场景由工作流注入确定性引用校验
            return ValidatorAgent(llm=llm, rules={"min_length": 1, "required_keywords": []})
        if agent_type == "reviewer":
            return ReviewerAgent(llm=llm)
        if agent_type == "planner":
            return self.create_planner()
        raise ValueError(f"未知 agent_type: {agent_type}")
