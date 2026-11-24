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

from app.llm.models import Message, MessageRole
from app.orchestration.contracts import WorkflowPreset
from app.schemas.intent import IntentResponse

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
