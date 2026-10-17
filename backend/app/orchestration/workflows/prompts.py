"""文献场景 Prompt 渲染（Plan 3 Task 3）。

统一经既有 `PromptManager` 加载/渲染 `app/llm/templates/*.j2`：
首次使用注册模板（幂等），之后直接渲染；StrictUndefined 保证变量不遗漏。
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import NotFoundException
from app.llm.prompts import TEMPLATE_DIR, get_prompt_manager

PLANNER_TEMPLATE_NAME = "literature.planner"
EXECUTOR_TEMPLATE_NAME = "literature.executor"


def _render(name: str, filename: str, variables: dict[str, Any]) -> str:
    manager = get_prompt_manager()
    try:
        manager.get_template(name)
    except NotFoundException:
        manager.load_from_file(
            name,
            TEMPLATE_DIR / filename,
            version="1.0",
            description=f"Plan 3 文献场景模板 {filename}",
        )
    return manager.render(name, variables)


def render_planner_prompt(requirement: str, *, repair_section: str = "") -> str:
    """文献规划 Prompt：限定五类节点、无环依赖、write 依赖 extract。"""
    return _render(
        PLANNER_TEMPLATE_NAME,
        "literature_planner_system.j2",
        {"requirement": requirement, "repair_section": repair_section},
    )


def render_executor_prompt(
    *,
    title: str,
    description: str,
    evidence_text: str,
    dependency_section: str = "",
    validation_feedback_section: str = "",
) -> str:
    """证据约束执行 Prompt：检索文本不可信、形式化结论需引用标记。"""
    return _render(
        EXECUTOR_TEMPLATE_NAME,
        "literature_executor_system.j2",
        {
            "title": title,
            "description": description,
            "evidence_text": evidence_text or "（无检索证据）",
            "dependency_section": dependency_section,
            "validation_feedback_section": validation_feedback_section,
        },
    )
