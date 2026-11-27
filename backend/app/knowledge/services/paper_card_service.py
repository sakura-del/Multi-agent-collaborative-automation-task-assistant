"""Paper Card 服务（Issue #73 Phase A · Task 6）。

全文首次落库时用一次**轻量** LLM 调用抽取论文卡片（研究问题 / 方法 / 数据或样本 /
关键结果 / 局限），写入 `fulltext_cache.paper_card` 后按 DOI 缓存、供后续任务复用
（规范 §5.5）。卡片是增强信息，不是全文入库的前置条件：

- 失败 / 超时 / 非法 JSON 一律返回 ``None``：**不抛异常、不重试、不阻塞任务**；
- 已有卡片（``existing``）直接复用，一次 LLM 都不调用；
- 输入只取摘要 + method/result/limitation 三类章节前段，总量 ≤3000 字，
  ``max_tokens=450``，避免为一张卡片付出整篇全文的 token 成本。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.llm.base import BaseLLM
from app.llm.models import Message, MessageRole

logger = get_logger(__name__)

#: 生成 paper card 的系统提示词：严格 JSON、不要 Markdown 围栏、缺失字段留空。
PAPER_CARD_PROMPT = """你是论文信息抽取器。根据给定片段输出严格 JSON：
{"research_question": str, "method": str, "dataset_or_sample": str,
 "key_results": str, "limitations": str}
只输出 JSON，不要 Markdown 代码块；信息缺失的字段填空字符串。"""

#: 输出字段（即 `fulltext_cache.paper_card` 的键），顺序即对外契约。
PAPER_CARD_FIELDS: tuple[str, ...] = (
    "research_question",
    "method",
    "dataset_or_sample",
    "key_results",
    "limitations",
)

#: 送给模型的输入总字符预算。
MAX_INPUT_CHARS = 3000
#: 单个章节（method/result/limitation）前段字符预算。
MAX_SECTION_CHARS = 1200
#: 输出 token 上限：五个短字段足够，避免模型长篇大论。
MAX_OUTPUT_TOKENS = 450
#: 抽取任务用低温，减少字段漂移。
LLM_TEMPERATURE = 0.1
#: 参与抽取的章节角色；背景 / 相关工作对卡片没有信息量，不送模型。
_FOCUS_ROLES = frozenset({"method", "result", "limitation"})


class PaperCardService:
    """论文卡片生成器：一次轻量 LLM 调用，产出可跨任务复用的结构化摘要。

    ``llm`` 显式注入便于测试与复用；为 ``None`` 时回落到平台默认模型
    （延迟导入，导入本模块不产生副作用）。
    """

    def __init__(self, llm: BaseLLM | None = None) -> None:
        self._llm = llm

    async def get_or_create(
        self,
        *,
        abstract: str,
        sections: list[dict[str, Any]],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, str] | None:
        """返回论文卡片；``existing`` 有值则直接复用，不调用 LLM。

        任何失败（无可用输入 / 非 JSON / 调用异常 / 超时）都返回 ``None``，
        调用方把 ``fulltext_cache.paper_card`` 留空即可——卡片失败不影响全文入库。
        """
        if existing is not None:
            reused = _normalize_card(existing)
            return reused if any(reused.values()) else None

        user_content = _build_user_content(abstract, sections)
        if not user_content:
            logger.info("paper_card_skipped_empty_input")
            return None

        messages = [
            Message(role=MessageRole.SYSTEM, content=PAPER_CARD_PROMPT),
            Message(role=MessageRole.USER, content=user_content),
        ]
        try:
            response = await self._chat(messages)
        except Exception as exc:  # 卡片是增强信息：任何失败都只降级，不阻塞全文入库
            logger.warning("paper_card_generation_failed", error=str(exc))
            return None

        raw = str(getattr(response, "content", "") or "")
        card = _parse_card(raw)
        if card is None:
            logger.warning("paper_card_invalid_json", output_chars=len(raw))
            return None
        if not any(card.values()):
            # 空卡没有复用价值：写进缓存会让后续任务误以为「已生成」
            logger.warning("paper_card_all_fields_empty")
            return None
        return card

    async def _chat(self, messages: list[Message]) -> Any:
        """调用注入的 LLM；未注入时回落到平台默认模型（含降级链）。"""
        if self._llm is not None:
            return await self._llm.chat(
                messages,
                temperature=LLM_TEMPERATURE,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        # 延迟导入：只有生产路径（未注入 LLM）才初始化平台模型管理器
        from app.llm.factory import get_llm_manager

        return await get_llm_manager().chat(
            messages,
            temperature=LLM_TEMPERATURE,
            max_tokens=MAX_OUTPUT_TOKENS,
        )


def _build_user_content(abstract: str, sections: list[dict[str, Any]]) -> str:
    """拼装送模型的片段：摘要 + method/result/limitation 章节前段。"""
    parts: list[str] = []
    abstract_text = (abstract or "").strip()
    if abstract_text:
        parts.append(f"摘要：{abstract_text}")
    for section in sections:
        role = str(section.get("role") or "")
        if role not in _FOCUS_ROLES:
            continue
        text = str(section.get("text") or "").strip()
        if not text:
            continue
        title = str(section.get("title") or role).strip() or role
        parts.append(f"## {title}\n{text[:MAX_SECTION_CHARS]}")
    return "\n\n".join(parts)[:MAX_INPUT_CHARS]


def _parse_card(raw: str) -> dict[str, str] | None:
    """宽松解析模型输出：容忍 Markdown 围栏与前后废话，但顶层必须是 JSON 对象。"""
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return _normalize_card(parsed)


def _normalize_card(card: dict[str, Any]) -> dict[str, str]:
    """只保留五个契约字段并统一成字符串（缺失字段补空串）。"""
    return {field: _coerce_text(card.get(field)) for field in PAPER_CARD_FIELDS}


def _coerce_text(value: Any) -> str:
    """把模型返回值统一成字符串：``None`` → 空串，数字/布尔走 JSON 表示。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
