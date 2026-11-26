"""意图分类的共享实现（HTTP 端点与 Agent 工具网关共用）。

之前分类 prompt 与容错解析写在 `api/v1/endpoints/intent.py` 里，
工具网关需要同一能力时只能复制——复制就会出现两套 prompt、两种
fallback 语义。这里收敛成唯一实现：

- `classify_text()`：一次平台模型轻量调用；
- **fail-open**：模型不可用/超时/JSON 解析失败 → 回落综述主流程，
  入口的本职是创建综述任务，分类器挂了不能挡住主链路。
"""

from __future__ import annotations

import json
import re
from typing import Literal

from app.llm.models import Message, MessageRole
from app.orchestration.contracts import WorkflowPreset
from app.schemas.intent import IntentResponse, KeywordSuggestion, KeywordSuggestResponse

#: 意图分类可返回的预设白名单（Issue #32 Phase 1）
_ALLOWED_INTENT_PRESETS: frozenset[str] = frozenset({"literature_review", "research_gaps"})

_CLASSIFY_SYSTEM = (
    "你是学术写作平台的意图路由器。用户会输入一句话，"
    "判断它是否是要生成文献综述或研究空白分析（intent 均为 literature_review）任务，"
    "并识别预设工作流 workflow_preset："
    "若用户聚焦寻找研究空白、证据缺口、尚未解决的问题或未来方向，输出 research_gaps；"
    "其余常规综述请求输出 literature_review。"
    "同时提取规范的研究主题（topic，去口语化，≤60 字）与检索关键词（keywords，2~6 个，"
    "中英文均可，单个 ≤20 字）。只输出 JSON："
    '{"intent":"literature_review","workflow_preset":"literature_review","topic":"...","keywords":["..."]}，'
    '非综述请求输出 {"intent":"other"}。不要输出任何其他文字。'
)


def _normalize_preset(raw: object) -> WorkflowPreset:
    """白名单校验 workflow_preset，缺失或非法值一律 fail-open 回落 literature_review。"""
    if isinstance(raw, str) and raw in _ALLOWED_INTENT_PRESETS:
        return raw  # type: ignore[return-value]
    return "literature_review"


def fallback(text: str) -> IntentResponse:
    """分类不可用时的安全回落：按综述任务处理（原文即主题）。"""
    return IntentResponse(
        intent="literature_review",
        topic=text[:60],
        keywords=[text[:30]],
        source="fallback",
        workflow_preset="literature_review",
    )


def parse_llm_json(content: str, text: str) -> IntentResponse | None:
    """容错解析：剥 markdown 代码栏 + 提取第一个 JSON 对象。"""
    cleaned = re.sub(r"```(?:json)?|```", "", content).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    intent = data.get("intent")
    if intent == "other":
        return IntentResponse(
            intent="other",
            topic=text[:60],
            keywords=[],
            source="llm",
            workflow_preset="literature_review",
        )
    if intent != "literature_review":
        return None
    topic = str(data.get("topic") or text[:60]).strip()
    raw_keywords = data.get("keywords") or []
    keywords = [
        str(item).strip() for item in raw_keywords if isinstance(item, str) and item.strip()
    ][:6]
    return IntentResponse(
        intent="literature_review",
        topic=topic[:60] or text[:60],
        keywords=keywords or [text[:30]],
        source="llm",
        workflow_preset=_normalize_preset(data.get("workflow_preset")),
    )


async def classify_text(text: str) -> IntentResponse:
    """平台模型分类；**任何异常都 fail-open**，不向调用方抛错。"""
    from app.llm.factory import get_llm_manager

    normalized = text.strip()
    try:
        response = await get_llm_manager().chat(
            [
                Message(role=MessageRole.SYSTEM, content=_CLASSIFY_SYSTEM),
                Message(role=MessageRole.USER, content=normalized),
            ],
            temperature=0.1,
            max_tokens=200,
        )
    except Exception:
        return fallback(normalized)

    parsed = parse_llm_json(str(response.content or ""), normalized)
    return parsed if parsed is not None else fallback(normalized)


_SUGGEST_KEYWORDS_SYSTEM = (
    "你是学术文献检索词拆分助手。根据用户提供的研究主题，拆分出适合学术数据库"
    "（万方、OpenAlex、arXiv、PubMed）的高命中率检索关键词。\n"
    "要求：\n"
    "1. 输出 6~8 个短语级检索词（不要整句），每个 ≤30 字；\n"
    "2. 若主题含中文，同时给出核心中文术语（3~4 个）与对应英文学术检索词（3~4 个），"
    "方便跨中英文数据库召回；若为纯英文主题则全输出英文；\n"
    "3. 避开用户已有的关键词列表；\n"
    '4. 只输出严格 JSON：{"suggestions":[{"term":"...","lang":"zh|en"}]}，不要输出其他文字。'
)

_FALLBACK_SPLIT_RE = re.compile(r"[，,、；;：:\s/（）()【】\[\]的与及和在中基于研究设计实现]+")


def _detect_term_lang(term: str) -> Literal["zh", "en"]:
    return "zh" if re.search(r"[\u4e00-\u9fff]", term) else "en"


def _fallback_suggest_keywords(
    topic: str,
    existing: set[str],
    max_keywords: int,
) -> KeywordSuggestResponse:
    """模型不可用时的确定性切词回落。"""
    parts = [p.strip() for p in _FALLBACK_SPLIT_RE.split(topic) if len(p.strip()) >= 2]
    suggestions: list[KeywordSuggestion] = []
    seen = set(existing)
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(KeywordSuggestion(term=part[:40], lang=_detect_term_lang(part)))
        if len(suggestions) >= max_keywords:
            break
    if not suggestions and topic.strip():
        clean = topic.strip()[:40]
        suggestions.append(KeywordSuggestion(term=clean, lang=_detect_term_lang(clean)))
    return KeywordSuggestResponse(suggestions=suggestions, source="fallback")


def _parse_keyword_suggestions(
    content: str,
    existing: set[str],
    max_keywords: int,
) -> KeywordSuggestResponse | None:
    """容错解析 LLM 拆词 JSON，过滤重复与已有词并截断至上限。"""
    cleaned = re.sub(r"```(?:json)?|```", "", content).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    raw_list = data.get("suggestions")
    if not isinstance(raw_list, list):
        return None

    suggestions: list[KeywordSuggestion] = []
    seen = set(existing)
    for item in raw_list:
        term = ""
        lang: Literal["zh", "en"] = "zh"
        if isinstance(item, str):
            term = item.strip()[:40]
            lang = _detect_term_lang(term)
        elif isinstance(item, dict):
            term = str(item.get("term") or "").strip()[:40]
            raw_lang = item.get("lang")
            if raw_lang == "zh":
                lang = "zh"
            elif raw_lang == "en":
                lang = "en"
            else:
                lang = _detect_term_lang(term)
        else:
            continue
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        suggestions.append(KeywordSuggestion(term=term, lang=lang))
        if len(suggestions) >= max_keywords:
            break

    return KeywordSuggestResponse(suggestions=suggestions, source="llm") if suggestions else None


async def suggest_keywords(
    topic: str,
    *,
    output_language: Literal["zh", "en"] = "zh",
    existing_keywords: list[str] | None = None,
    max_keywords: int = 8,
) -> KeywordSuggestResponse:
    """基于主题智能拆分学术检索关键词；异常 fail-open 回落本地切词。"""
    from app.llm.factory import get_llm_manager

    normalized = topic.strip()
    existing = {kw.strip().casefold() for kw in (existing_keywords or []) if kw.strip()}
    user_prompt = (
        f"研究主题：{normalized}\n"
        f"目标输出语言：{output_language}\n"
        f"已有关键词（请避开重复）：{', '.join(existing_keywords or []) or '无'}\n"
        f"请输出不超过 {max_keywords} 个候选检索词。"
    )
    try:
        response = await get_llm_manager().chat(
            [
                Message(role=MessageRole.SYSTEM, content=_SUGGEST_KEYWORDS_SYSTEM),
                Message(role=MessageRole.USER, content=user_prompt),
            ],
            temperature=0.2,
            max_tokens=240,
        )
    except Exception:
        return _fallback_suggest_keywords(normalized, existing, max_keywords)

    parsed = _parse_keyword_suggestions(str(response.content or ""), existing, max_keywords)
    return (
        parsed
        if parsed is not None
        else _fallback_suggest_keywords(normalized, existing, max_keywords)
    )
