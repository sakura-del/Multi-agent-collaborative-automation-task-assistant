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

import re
import time
from typing import Any

from app.core.exceptions import EvidenceCitationError
from app.core.logging import get_logger
from app.llm.models import Message, MessageRole, ToolSchema
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import Evidence, ExecutionResult
from app.orchestration.messages import MessageType

logger = get_logger(__name__)

# 证据引用标记：[E1]、[E12]
_CITATION_MARKER = re.compile(r"\[E(\d+)\]")

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

    async def execute_result(
        self,
        *,
        node_id: str,
        title: str,
        description: str,
        evidence: list[Evidence] | None = None,
        evidence_text: str = "",
        dependency_outputs: dict[str, Any] | None = None,
        validation_feedback: str = "",
        trace_id: str = "",
    ) -> ExecutionResult:
        """证据约束执行：返回类型化 `ExecutionResult`，引用越界直接拒绝。

        与 `_run` 的差异：本路径面向文献工作流，输出带 `[E{n}]` 标记的正文，
        并在持久化前校验标记是否落在传入证据范围内。
        """
        evidence_list = list(evidence or [])
        start = time.perf_counter()
        prompt_tokens = completion_tokens = 0

        if self.llm is None:
            content = f"[降级输出] {title}（无 LLM，未引用证据）"
        else:
            # 方法内导入：workflows 包 __init__ 反向依赖 agents，避免导入环
            from app.orchestration.workflows.prompts import render_executor_prompt

            prompt = render_executor_prompt(
                title=title,
                description=description,
                evidence_text=evidence_text,
                dependency_section=self._dependency_section(dependency_outputs or {}),
                validation_feedback_section=(
                    f"\n修订要求：{validation_feedback}" if validation_feedback else ""
                ),
            )
            response = await self.llm.chat(
                [
                    Message(
                        role=MessageRole.SYSTEM,
                        content="你是文献综述执行 Agent，只输出正文内容。",
                    ),
                    Message(role=MessageRole.USER, content=prompt),
                ],
                temperature=0.2,
            )
            content = response.content or ""
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens

        cited = self._resolve_citations(content, len(evidence_list))
        warnings: list[str] = []
        if not evidence_list and cited:
            # 无证据却出现引用标记：**不抛异常**（否则 no_rag 画像整批实验失败），
            # 但必须留痕 —— 这是模型幻觉的直接证据，实验指标要能看到。
            #
            # 为什么不删掉标记：那会篡改模型输出，实验数据不再可信。
            # 保留原文 + 记 warning 更诚实。
            warnings.append("citation_without_evidence")
        if evidence_list and not cited:
            warnings.append("no_evidence_marker")

        logger.info(
            "executor_literature_done",
            node_id=node_id,
            trace_id=trace_id,
            cited=sorted(cited),
            warnings=warnings,
        )
        return ExecutionResult(
            node_id=node_id,
            content=content,
            evidence=[item for index, item in enumerate(evidence_list, start=1) if index in cited],
            artifacts=[],
            warnings=warnings,
            duration_ms=int((time.perf_counter() - start) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    @staticmethod
    def _resolve_citations(content: str, evidence_count: int) -> set[int]:
        """解析引用标记；编号超出证据范围时抛 `EvidenceCitationError`。

        ## `evidence_count == 0` 时**不校验**（2026-10-22 实验矩阵修复）

        无 RAG 画像（`retrieval_mode=none`）没有证据，但模型仍可能按训练习惯
        输出 `[E1]`。若照旧抛异常 → 节点重试 3 次 → 转人工 →
        **no_rag 画像永远跑不出结果**（实测 pilot 的 2 个 run 全卡在这里）。

        正确语义：没有证据时引用标记本身没有「范围」可越，谈不上越界。
        这里只**收集**标记（用于 warning 留痕），不判失败。

        有证据时保持严格：越界意味着模型编造了不存在的文献，必须拒绝。
        """
        cited = {int(match) for match in _CITATION_MARKER.findall(content)}
        if evidence_count == 0:
            return cited
        invalid = sorted(index for index in cited if index < 1 or index > evidence_count)
        if invalid:
            raise EvidenceCitationError(
                f"引用标记超出可用证据范围: {invalid}",
                details={"invalid_markers": invalid, "evidence_count": evidence_count},
            )
        return cited

    @staticmethod
    def _dependency_section(dependency_outputs: dict[str, Any]) -> str:
        if not dependency_outputs:
            return ""
        lines = []
        for dep_id, output in dependency_outputs.items():
            text = output.get("result", "") if isinstance(output, dict) else str(output)
            lines.append(f"[{dep_id}] {text}")
        return "\n依赖节点输出：\n" + "\n".join(lines) + "\n"

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
