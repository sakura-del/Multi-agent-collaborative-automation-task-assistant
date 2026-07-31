"""执行 Agent：子任务接收 → LLM 推理执行 → 结果上报。

职责：
1. 接收规划 Agent 分配的子任务
2. 调用 LLM 完成推理执行
3. 上报执行结果至校验 Agent
4. 预留工具调用接口（阶段6填充）

设计要点：
- 工具调用通过 self._available_tools 扩展，阶段6注入真实工具（开闭原则）
- 执行失败时由 BaseAgent.execute() 统一兜底，此处不重复 try/except
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole, ToolSchema
from app.orchestration.base_agent import BaseAgent
from app.orchestration.messages import MessageType

logger = get_logger(__name__)

# 执行推理 Prompt：约束 LLM 按子任务描述产出结果
_EXECUTE_PROMPT = """请执行以下子任务并输出结果。

子任务标题：{title}
子任务描述：{description}
上下文信息：{context}

要求：
- 严格按描述执行，不偏离任务范围
- 输出清晰可读的结果内容
- 如需工具辅助，说明所需工具
"""


class ExecutorAgent(BaseAgent):
    """执行 Agent：调用 LLM 完成子任务的推理执行。

    通过 _available_tools 列表支持工具调用（阶段6扩展点）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 工具调用扩展点：阶段6注入真实 ToolSchema 列表
        self._available_tools: list[ToolSchema] = []

    @property
    def agent_type(self) -> str:
        return "executor"

    def register_tool(self, tool: ToolSchema) -> None:
        """注册可用工具（阶段6调用）。"""
        self._available_tools.append(tool)

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行子任务：LLM 推理 → 上报结果。

        input_data 需包含 title、description，可选 context。
        返回 {node_id, result, status, used_tools}。
        """
        node_id = input_data.get("node_id", "")
        title = input_data["title"]
        description = input_data["description"]
        context = input_data.get("context", "")
        trace_id = input_data.get("trace_id", "")

        # LLM 推理执行（有工具时传入 tools 触发 Function Calling）
        result_text, tool_calls = await self._infer(title, description, context)

        # 上报执行结果至校验 Agent
        await self._send_message(
            "validator",
            MessageType.RESULT,
            {
                "node_id": node_id,
                "title": title,
                "result": result_text,
                "tool_calls": tool_calls,
            },
            trace_id=trace_id,
        )

        logger.info(
            "executor_done",
            node_id=node_id,
            trace_id=trace_id,
            used_tools=len(tool_calls),
        )
        return {
            "node_id": node_id,
            "result": result_text,
            "status": "completed",
            "used_tools": [tc.get("name", "") for tc in tool_calls],
        }

    async def _infer(
        self, title: str, description: str, context: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """调用 LLM 推理，返回（结果文本，工具调用列表）。

        无 LLM 时降级为占位输出，便于测试与无 API 环境运行。
        """
        if self.llm is None:
            return f"[降级输出] 已接收子任务：{title}", []

        response = await self.llm.chat(
            [
                Message(
                    role=MessageRole.SYSTEM,
                    content="你是任务执行 Agent，按指令完成子任务。",
                ),
                Message(
                    role=MessageRole.USER,
                    content=_EXECUTE_PROMPT.format(
                        title=title, description=description, context=context
                    ),
                ),
            ],
            tools=self._available_tools or None,
            temperature=0.4,
        )

        # 提取工具调用记录（若有）
        tool_calls: list[dict[str, Any]] = []
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_calls.append({"name": tc.name, "arguments": tc.arguments})

        return response.content or "", tool_calls
