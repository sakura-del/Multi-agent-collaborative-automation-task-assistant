"""产出落库前的结构化校验（Issue #25 沉淀项 1）。

原则：**Agent 产出不直接落库**——先过校验，校验通过才持久化。
第一个场景是文献综述的引用一致性（正文 [E#] ↔ 参考文献编号），
后续大纲/证据表等结构化产出复用同一模式：校验函数返回可读问题列表，
调用方决定「不落库 + 任务置为可重试失败」。
"""

from app.orchestration.validation.citation_consistency import (
    CitationAudit,
    audit_citation_alignment,
)

__all__ = ["CitationAudit", "audit_citation_alignment"]
