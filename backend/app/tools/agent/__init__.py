"""Agent 工具网关：工具声明 + 参数契约 + 确认策略。

## 定位

会话内核（Issue #8 P0）只负责「说一句话」，真正代劳需要一组**受控工具**。
本包把「模型能做什么」收敛成 12 个有明确 JSON Schema 的工具，复用既有
service 层（任务执行 / 引用库 / 知识库 / 套餐 / 搜索），**不产生第二套业务逻辑**。

## 安全边界（写操作必须先确认）

- `write=False`：只读，可直接执行；
- `write=True`：创建任务、确认规划、取消、重试节点、导入文献——
  必须由调用方传 `confirmed=true`（前端应先出确认卡）。
  模型幻觉触发一次「取消任务」的代价不可接受。
"""

from app.tools.agent.context import ToolContext
from app.tools.agent.registry import TOOL_SPECS, get_tool, invoke_tool, list_tools
from app.tools.agent.spec import ConfirmationRequiredException, ToolSpec, ToolValidationException

__all__ = [
    "TOOL_SPECS",
    "ConfirmationRequiredException",
    "ToolContext",
    "ToolSpec",
    "ToolValidationException",
    "get_tool",
    "invoke_tool",
    "list_tools",
]
