"""引用一致性校验（Issue #25 沉淀项 1，关联 #17）。

## 为什么要在落库前拦

#17 的现象是「正文 [E1]~[E16]，参考文献只有 8 条」——用户在结果页看到的是
**自相矛盾**的产出。事后修编号（#17 的四层修复）解决的是「算错」，但没有任何
机制阻止下一次再存入不一致的报告：只要 reviewer 的映射出一次岔子，脏数据就
进了库，且任务显示「已完成」。

本模块提供**落库前闸门**：正文引用集合与参考文献编号集合必须双向对齐；
不通过就由调用方把任务置为可重试的失败状态，不写 `review_report`/`artifacts`。

## 双向为什么都要查

- **缺条目**（正文有 [E15]，参考文献无 15）：用户会看到不存在的引用 → 必须拦；
- **孤儿条目**（参考文献有 18，正文从未引用）：说明映射多登记了一条，
  同样意味着编号体系不可信 → 一并拦下（当前实现里 bibliography 只从被引用
  证据登记，正常不会出现孤儿，出现即异常）。

## 什么算「合法空」

正文没有任何 `[E#]` 且参考文献为空 → 通过（例如纯说明性输出）。
只有一边为空 → 不通过。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: 正文全局引用编号（#17 之后所有节点编号已重写为全局 [E#]）
_CITATION_RE = re.compile(r"\[E(\d+)\]")


@dataclass(frozen=True)
class CitationAudit:
    """一次审计的结果（不可变，便于日志与事件负载直接序列化）。"""

    ok: bool
    cited: tuple[int, ...]
    indexed: tuple[int, ...]
    missing_entries: tuple[int, ...]
    orphan_entries: tuple[int, ...]
    reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """写入任务事件/日志的紧凑形态（编号列表截断到前 20 个）。"""
        return {
            "ok": self.ok,
            "cited": len(self.cited),
            "indexed": len(self.indexed),
            "missing_entries": list(self.missing_entries[:20]),
            "orphan_entries": list(self.orphan_entries[:20]),
        }


def _entry_index(entry: Any) -> int | None:
    """bibliography 条目的编号（`{"index": N, ...}`）；非法条目返回 None。"""
    if not isinstance(entry, dict):
        return None
    value = entry.get("index")
    return value if isinstance(value, int) and value > 0 else None


def audit_citation_alignment(
    markdown: str | None,
    bibliography: list[Any] | None,
) -> CitationAudit:
    """正文 `[E#]` 与参考文献 `index` 的双向一致性审计。"""
    cited = tuple(sorted({int(match) for match in _CITATION_RE.findall(markdown or "")}))
    entries = list(bibliography or [])
    malformed = [entry for entry in entries if _entry_index(entry) is None]
    indexed = tuple(sorted({idx for entry in entries if (idx := _entry_index(entry)) is not None}))

    cited_set = set(cited)
    indexed_set = set(indexed)
    missing = tuple(sorted(cited_set - indexed_set))
    orphan = tuple(sorted(indexed_set - cited_set))

    if malformed:
        # 条目缺 index 比编号不匹配更糟：导出时会整条消失，直接判不通过
        return CitationAudit(
            ok=False,
            cited=cited,
            indexed=indexed,
            missing_entries=missing,
            orphan_entries=orphan,
            reason=f"参考文献有 {len(malformed)} 条缺少 index 字段",
        )
    if not cited and not indexed:
        return CitationAudit(
            ok=True, cited=cited, indexed=indexed, missing_entries=(), orphan_entries=()
        )
    if cited and not indexed:
        return CitationAudit(
            ok=False,
            cited=cited,
            indexed=indexed,
            missing_entries=cited,
            orphan_entries=(),
            reason=f"正文引用 {len(cited)} 条，但参考文献为空",
        )
    if indexed and not cited:
        return CitationAudit(
            ok=False,
            cited=cited,
            indexed=indexed,
            missing_entries=(),
            orphan_entries=indexed,
            reason=f"参考文献有 {len(indexed)} 条，但正文没有任何 [E#] 引用",
        )

    problems: list[str] = []
    if missing:
        shown = "、".join(f"[E{idx}]" for idx in missing[:5])
        problems.append(f"正文引用的 {shown} 在参考文献中不存在")
    if orphan:
        shown = "、".join(f"[E{idx}]" for idx in orphan[:5])
        problems.append(f"参考文献中的 {shown} 未被正文引用")
    if problems:
        return CitationAudit(
            ok=False,
            cited=cited,
            indexed=indexed,
            missing_entries=missing,
            orphan_entries=orphan,
            reason="；".join(problems),
        )
    return CitationAudit(
        ok=True,
        cited=cited,
        indexed=indexed,
        missing_entries=(),
        orphan_entries=(),
    )
