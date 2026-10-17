"""owner-scoped 证据上下文构建（Plan 3 Task 2）。

把 `KnowledgeDocumentService.search_for_context()` 的检索结果映射为
`EvidenceContext`：每条证据带稳定的 `[E{n}]` 标记、doc_id/chunk_id 与原文片段，
供 Executor/Validator 在引用校验时回溯（Task 3/4 消费）。

约定：
- owner 过滤由 `search_for_context()` 强制（`where={"user_id": ...}` + 独立 namespace）；
- excerpt 截断到 2000 字符（与 `Evidence` 契约上限一致）；
- 空白片段跳过；非法 UUID / 非法 URL 抛 `ValidationException`，不静默丢弃；
- 证据少于 2 条时 `insufficient=True`，由上层决定是否转人工。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationException
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.orchestration.contracts import Evidence, EvidenceContext

# 与 Evidence.excerpt 的 max_length 保持一致
MAX_EXCERPT_CHARS = 2000


class LiteratureContextBuilder:
    """把 owner-scoped 检索结果组装为可引用的证据上下文。"""

    def __init__(self, knowledge: KnowledgeDocumentService) -> None:
        self.knowledge = knowledge

    async def build(
        self,
        user_id: UUID,
        query: str,
        *,
        top_k: int = 8,
    ) -> EvidenceContext:
        """检索当前用户的知识库并构建证据上下文。"""
        results = await self.knowledge.search_for_context(user_id, query, top_k=top_k)

        evidence: list[Evidence] = []
        for item in results:
            excerpt = (item.text or "").strip()
            if not excerpt:
                continue
            try:
                doc_id = UUID(str(item.doc_id))
                chunk_id = UUID(str(item.chunk_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValidationException("检索结果缺少合法的文档/分块标识") from exc
            source_url = (item.metadata or {}).get("source_url")
            try:
                evidence.append(
                    Evidence(
                        doc_id=doc_id,
                        chunk_id=chunk_id,
                        source_url=source_url,
                        excerpt=excerpt[:MAX_EXCERPT_CHARS],
                    )
                )
            except PydanticValidationError as exc:
                raise ValidationException("检索结果证据字段不符合 schema") from exc

        text = "\n\n".join(
            f"[E{index}] doc={item.doc_id} chunk={item.chunk_id}\n{item.excerpt}"
            for index, item in enumerate(evidence, start=1)
        )
        return EvidenceContext(
            text=text,
            evidence=evidence,
            insufficient=len(evidence) < 2,
            query=query,
        )
