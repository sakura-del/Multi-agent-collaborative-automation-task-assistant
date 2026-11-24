"""Routines 跨期增量记忆（Issue #42 轻量试点）。

原则：
- 全程确定性（0 额外 LLM 调用），不破坏前缀缓存，不产生黑盒漂移；
- 首次从 ReviewReport（含 #32 evidence_matrix）+ TaskPlan 初始化；
- 每次刷新后滚动记录近 5 期已报要点（防重复播报）与连续 3 期 0 命中的失效检索式。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_OPEN_GAPS = 6
MAX_RECENT_UPDATES = 5
MAX_PROMPT_CHARS = 800
STALE_QUERY_THRESHOLD = 3


class RoutineMemoryBrief(BaseModel):
    """TaskRoutine.memory_brief 的唯一结构定义。"""

    model_config = ConfigDict(extra="forbid")

    topic_summary: str = Field(default="", max_length=240)
    #: 来自 #32 evidence_matrix 的空白/弱证据子问题，或降级取 research_questions
    open_gaps: list[str] = Field(default_factory=list, max_length=MAX_OPEN_GAPS)
    #: 最近几期通过引用闸门的增量要点摘要（用于防重复播报）
    recent_updates: list[str] = Field(default_factory=list, max_length=MAX_RECENT_UPDATES)
    #: 每条检索式连续 0 新增的期数
    query_empty_streaks: dict[str, int] = Field(default_factory=dict)
    #: 连续 >= 3 期 0 新增的检索式（供前端温和提示用户优化）
    stale_queries: list[str] = Field(default_factory=list)
    updated_at: str | None = None


def parse_memory_brief(raw: Any) -> RoutineMemoryBrief | None:
    """容错解析数据库 JSON；格式损坏时返回 None 触发安全重建，不阻断刷新。"""
    if not isinstance(raw, dict):
        return None
    try:
        return RoutineMemoryBrief.model_validate(raw)
    except Exception:
        return None


def bootstrap_memory_brief(task: Any) -> RoutineMemoryBrief:
    """从已完成任务的 review_report / plan 确定性初始化记忆（0 LLM 调用）。"""
    title = str(getattr(task, "title", "") or "").strip()
    report = getattr(task, "review_report", None) or {}
    plan = getattr(task, "plan", None) or {}

    # 1. 优先从 #32 evidence_matrix 提取空白或弱证据子问题
    open_gaps: list[str] = []
    if isinstance(report, dict):
        matrix = report.get("evidence_matrix")
        if isinstance(matrix, dict):
            questions = matrix.get("questions") or []
            cells = matrix.get("cells") or []
            gap_indices: set[int] = set()
            for cell in cells:
                if isinstance(cell, dict) and cell.get("bucket") in {"gap", "weak_or_caveat"}:
                    idx = cell.get("question_index")
                    if (
                        isinstance(idx, int)
                        and not isinstance(idx, bool)
                        and 0 <= idx < len(questions)
                    ):
                        gap_indices.add(idx)
            for idx in sorted(gap_indices):
                q = str(questions[idx]).strip()
                if q and q not in open_gaps:
                    open_gaps.append(q[:120])

    # 降级：若无矩阵，则取规划研究问题
    if not open_gaps and isinstance(plan, dict):
        for q in plan.get("research_questions") or []:
            text = str(q).strip()
            if text and text not in open_gaps:
                open_gaps.append(text[:120])

    # 2. 提取原综述首段作为主题基线（去 Markdown 标题）
    summary = title
    md = ""
    if isinstance(report, dict):
        md = str(report.get("final_markdown") or "")
    if not md:
        md = str(getattr(task, "final_output", "") or "")
    for block in md.split("\n\n"):
        cleaned = block.strip()
        if cleaned and not cleaned.startswith("#"):
            summary = cleaned.replace("\n", " ")[:220]
            break

    return RoutineMemoryBrief(
        topic_summary=summary[:240],
        open_gaps=open_gaps[:MAX_OPEN_GAPS],
        recent_updates=[],
        query_empty_streaks={},
        stale_queries=[],
        updated_at=datetime.now(UTC).isoformat(),
    )


def format_memory_for_prompt(brief: RoutineMemoryBrief | None) -> str:
    """LOAD：将记忆压成紧凑文本块，注入 user 消息开头（严格 <= 800 字符）。"""
    if brief is None:
        return ""
    lines: list[str] = ["已有研究状态（仅供对照，不得在无新文献支撑时复述）："]
    if brief.topic_summary:
        lines.append(f"- 原综述基线：{brief.topic_summary}")
    if brief.open_gaps:
        lines.append("- 待补充的研究空白：" + "；".join(brief.open_gaps[:4]))
    if brief.recent_updates:
        lines.append("- 往期已汇报要点（请勿重复）：" + "；".join(brief.recent_updates[-3:]))
    text = "\n".join(lines)
    return text[:MAX_PROMPT_CHARS]


def update_memory_brief(
    current: RoutineMemoryBrief,
    *,
    bullets: str | None,
    citation_ok: bool,
    new_count_by_query: dict[str, int],
) -> RoutineMemoryBrief:
    """OFFLOAD：刷新完成后确定性更新记忆与失效检索式计数。"""
    updates = list(current.recent_updates)
    if bullets and citation_ok:
        first_line = next(
            (line.lstrip("-*• ").strip() for line in bullets.splitlines() if line.strip()),
            "",
        )
        if first_line:
            compact = first_line[:120]
            if compact not in updates:
                updates.append(compact)
            updates = updates[-MAX_RECENT_UPDATES:]

    streaks = dict(current.query_empty_streaks)
    for query, count in new_count_by_query.items():
        if count > 0:
            streaks[query] = 0
        else:
            streaks[query] = streaks.get(query, 0) + 1

    stale = [q for q, n in streaks.items() if n >= STALE_QUERY_THRESHOLD]
    return RoutineMemoryBrief(
        topic_summary=current.topic_summary,
        open_gaps=current.open_gaps,
        recent_updates=updates,
        query_empty_streaks=streaks,
        stale_queries=stale,
        updated_at=datetime.now(UTC).isoformat(),
    )
