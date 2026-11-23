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
REVIEWER_TEMPLATE_NAME = "literature.reviewer"


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


#: 模板中静态规则与动态内容的分隔标记（三份模板统一使用）
_PROMPT_SEPARATOR = "\n---\n"


def split_prompt(rendered: str) -> tuple[str, str]:
    """把渲染结果拆成（静态规则, 动态内容）。

    ## 为什么要拆（2026-11-18 生产实测，Issue #22）

    DeepSeek/OpenAI 的前缀缓存以 **system message 开头**为锚点：把静态规则
    全部放进 system，多个节点的 user 消息各不相同也能复用同一段缓存前缀。
    实测（任务 3ff878f5）：静态规则放在 user 开头时，7 个节点间 cache_hit=0；
    同一 prompt 重发（规划重试）才命中 128 tokens。

    拆分点与模板对齐：模板用 `---` 分隔，静态块在前、动态块在后。
    找不到分隔符时**整体作为 user**（system 留空），保证向后兼容旧模板。
    """
    text = rendered.replace("\r\n", "\n")
    if _PROMPT_SEPARATOR not in text:
        return "", rendered
    static, dynamic = text.split(_PROMPT_SEPARATOR, 1)
    return static.strip(), dynamic.strip()


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


def render_reviewer_prompt(
    *,
    topic: str,
    results_json: str,
    validations_json: str,
    event_stats_json: str,
) -> str:
    """复盘 Prompt：只允许基于已持久化结果生成摘要/局限性。"""
    return _render(
        REVIEWER_TEMPLATE_NAME,
        "literature_reviewer_system.j2",
        {
            "topic": topic,
            "results_json": results_json,
            "validations_json": validations_json,
            "event_stats_json": event_stats_json,
        },
    )
