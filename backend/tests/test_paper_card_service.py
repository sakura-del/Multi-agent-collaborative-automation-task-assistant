"""Paper Card 服务测试（Issue #73 Phase A · Task 6）。

## 本文件锁定

1. 合法 JSON → 五个契约字段齐全，且能原样写入 `fulltext_cache.paper_card`；
2. 非 JSON / 调用异常 / 超时 → 返回 `None`、不抛异常、不覆盖已有卡片；
3. 已有卡片 → 直接复用，一次 LLM 都不调用；
4. 输入预算：单章节 ≤1200 字、总输入 ≤3000 字，且只有 method/result/limitation
   三类章节参与抽取；
5. 调用参数：`max_tokens=450`、`temperature=0.1`、system prompt 为
   `PAPER_CARD_PROMPT`。

## 为什么用 duck-typed fake 而不是 `tests/llm/mock_llm.py::MockLLM`

`MockLLM` 继承 `BaseLLM`，`chat()` 内置重试/退避：超时用例会被真实 `sleep`
拖慢，且重试会掩盖「服务层是否捕获异常」这一断言。这里用只实现 `chat()` 的
替身：**零网络、零等待**，并记录调用次数、消息与参数。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FulltextCache
from app.knowledge.services.paper_card_service import (
    PAPER_CARD_FIELDS,
    PAPER_CARD_PROMPT,
    PaperCardService,
)
from app.llm.models import Message

_CARD: dict[str, str] = {
    "research_question": "如何降低 RAG 检索延迟？",
    "method": "两阶段重排 + 结果缓存",
    "dataset_or_sample": "BEIR 的 5 个子集",
    "key_results": "P99 延迟下降 38%，nDCG 持平",
    "limitations": "仅在英文语料上验证",
}


class _FakeLLM:
    """duck-typed LLM 替身：记录每次调用，按预设返回内容或抛错（不触网）。"""

    def __init__(self, *, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.requests: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    async def chat(self, messages: list[Message], **kwargs: Any) -> Any:
        self.requests.append({"messages": list(messages), **kwargs})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=self.content)


def _card_json(card: dict[str, Any] | None = None) -> str:
    return json.dumps(_CARD if card is None else card, ensure_ascii=False)


def _system_prompt(request: dict[str, Any]) -> str:
    return str(request["messages"][0].content)


def _user_content(request: dict[str, Any]) -> str:
    return str(request["messages"][1].content)


async def _generate(
    fake: _FakeLLM,
    *,
    abstract: str = "本文研究检索增强生成的延迟问题。",
    sections: list[dict[str, Any]] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    return await PaperCardService(llm=fake).get_or_create(
        abstract=abstract,
        sections=sections or [],
        existing=existing,
    )


async def test_valid_json_returns_card_and_persists_to_fulltext_cache(
    db_session: AsyncSession,
) -> None:
    """合法 JSON → 五字段齐全；卡片能原样落进 `fulltext_cache.paper_card`。"""
    fake = _FakeLLM(content=_card_json())
    card = await _generate(
        fake,
        sections=[
            {"title": "3 Method", "role": "method", "text": "我们采用两阶段重排。"},
            {"title": "5 Limitation", "role": "limitation", "text": "仅英文语料。"},
        ],
    )

    assert card == _CARD
    assert fake.call_count == 1

    row = FulltextCache(
        doi="10.1234/abc",
        arxiv_id=None,
        pmid=None,
        sha256="a" * 64,
        provider="arxiv_pdf",
        source_url="https://arxiv.org/pdf/2401.00001",
        license="arxiv",
        content_format="pdf",
        page_count=12,
        parsed_sections=[],
        paper_card=card,
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.paper_card == _CARD


async def test_non_json_output_returns_none() -> None:
    """模型返回非 JSON 文本 → `None`，不抛异常。"""
    fake = _FakeLLM(content="抱歉，我无法从这些片段中抽取结构化信息。")

    assert await _generate(fake) is None
    assert fake.call_count == 1


async def test_llm_exception_returns_none() -> None:
    """LLM 抛异常 → `None`（失败只降级，不阻塞全文入库）。"""
    fake = _FakeLLM(error=RuntimeError("provider 500"))

    assert await _generate(fake) is None
    assert fake.call_count == 1


async def test_llm_timeout_returns_none() -> None:
    """LLM 超时 → `None`。"""
    fake = _FakeLLM(error=TimeoutError("upstream timed out"))

    assert await _generate(fake) is None
    assert fake.call_count == 1


async def test_existing_card_is_reused_without_llm_call() -> None:
    """已有卡片 → 原样复用；即使 LLM 会抛错也不调用（不覆盖已有值）。"""
    fake = _FakeLLM(error=RuntimeError("不应被调用"))

    card = await _generate(fake, existing=_CARD)

    assert card == _CARD
    assert fake.call_count == 0


async def test_section_text_is_truncated_to_1200_chars() -> None:
    """单章节只取前 1200 字，避免长章节吃满输入预算。"""
    fake = _FakeLLM(content=_card_json())

    await _generate(
        fake, abstract="", sections=[{"title": "3 Method", "role": "method", "text": "M" * 5000}]
    )

    assert _user_content(fake.requests[0]) == "## 3 Method\n" + "M" * 1200


async def test_total_input_is_capped_at_3000_chars() -> None:
    """摘要 + 章节合计不超过 3000 字。"""
    fake = _FakeLLM(content=_card_json())

    await _generate(fake, abstract="A" * 5000, sections=[])

    content = _user_content(fake.requests[0])
    assert len(content) == 3000
    assert content == "摘要：" + "A" * 2997


async def test_only_focus_roles_are_included() -> None:
    """只有 method / result / limitation 章节参与抽取，背景与相关工作不进输入。"""
    fake = _FakeLLM(content=_card_json())
    sections = [
        {"title": "1 Introduction", "role": "background", "text": "背景不应进入"},
        {"title": "2 Related Work", "role": "background", "text": "相关工作不应进入"},
        {"title": "3 Method", "role": "method", "text": "方法"},
        {"title": "4 Results", "role": "result", "text": "结果"},
        {"title": "5 Limitation", "role": "limitation", "text": "局限"},
    ]

    await _generate(fake, abstract="", sections=sections)

    content = _user_content(fake.requests[0])
    assert content == "## 3 Method\n方法\n\n## 4 Results\n结果\n\n## 5 Limitation\n局限"


async def test_empty_input_returns_none_without_llm_call() -> None:
    """没有可用输入 → 直接 `None`，不浪费一次 LLM 调用。"""
    fake = _FakeLLM(content=_card_json())
    sections = [{"title": "1 Introduction", "role": "background", "text": "只有背景"}]

    assert await _generate(fake, abstract="   ", sections=sections) is None
    assert fake.call_count == 0


async def test_markdown_fenced_json_is_tolerated() -> None:
    """模型偶尔包 ```json 围栏 → 仍能解析出卡片。"""
    fake = _FakeLLM(content="```json\n" + _card_json() + "\n```")

    assert await _generate(fake) == _CARD


async def test_unknown_keys_are_dropped() -> None:
    """模型多返回的键被丢弃，对外只暴露五个契约字段。"""
    fake = _FakeLLM(content=_card_json({**_CARD, "title": "应被丢弃", "year": 2026}))

    card = await _generate(fake)

    assert card is not None
    assert set(card) == set(PAPER_CARD_FIELDS)


async def test_missing_fields_become_empty_strings() -> None:
    """模型漏字段 → 缺失字段补空字符串，调用方无需做键存在性判断。"""
    fake = _FakeLLM(content=json.dumps({"method": "两阶段重排"}, ensure_ascii=False))

    card = await _generate(fake)

    assert card == {
        "research_question": "",
        "method": "两阶段重排",
        "dataset_or_sample": "",
        "key_results": "",
        "limitations": "",
    }


async def test_all_empty_fields_returns_none() -> None:
    """五字段全空 → `None`（空卡没有复用价值，不写入缓存）。"""
    fake = _FakeLLM(content=_card_json({field: "" for field in PAPER_CARD_FIELDS}))

    assert await _generate(fake) is None


async def test_llm_call_uses_low_temperature_and_450_tokens() -> None:
    """调用参数：低温 + max_tokens=450 + 计划规定的 system prompt。"""
    fake = _FakeLLM(content=_card_json())

    await _generate(fake)

    request = fake.requests[0]
    assert request["temperature"] == 0.1
    assert request["max_tokens"] == 450
    assert _system_prompt(request) == PAPER_CARD_PROMPT
    assert "本文研究检索增强生成的延迟问题。" in _user_content(request)
