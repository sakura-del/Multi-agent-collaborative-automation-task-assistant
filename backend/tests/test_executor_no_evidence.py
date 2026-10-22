"""无证据时的引用约束（2026-10-22 实验矩阵，实测缺陷回归）。

## 缺陷（服务器实测抓到）

`retrieval_mode=none`（`no_rag` 画像）时 evidence 为空，但 LLM 仍输出
`[E1]` 引用标记 → `_resolve_citations` 抛 `EvidenceCitationError`：

```
引用标记超出可用证据范围: [1, 10]
```

后果：节点重试 3 次后转 `waiting_human`，**no_rag 画像永远跑不出结果**。
实测 pilot-8 的 2 个 `single_agent_no_rag` run 全部卡在这里。

## 为什么这是设计缺陷而不是模型不听话

提示词里写着「必须用 [E{n}] 标注引用」—— 在没有证据时这条要求**自相矛盾**。
无 RAG 基线的正确语义是：**没有证据就不引用**。

## 修法（两层）

1. **提示词**：无证据时明确要求「不引用、不标 [E{n}]」；
2. **校验**：`evidence_count == 0` 时**放行**引用标记（内容里若有 `[E1]`
   是模型的幻觉标记，不能因此让整批实验失败），但记 warning。

为什么不干脆删掉标记：那会篡改模型输出，实验数据不再可信。
保留原文 + 记 warning 更诚实。
"""

from __future__ import annotations

import pytest

from app.orchestration.agents.executor import ExecutorAgent
from app.orchestration.contracts import ExecutionResult


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage = type("Usage", (), {"prompt_tokens": 10, "completion_tokens": 20})()


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.prompts.append(messages[-1].content)
        return _FakeResponse(self._content)


class TestNoEvidenceAllowsCitations:
    @pytest.mark.asyncio
    async def test_no_evidence_does_not_raise_on_citation_marker(self) -> None:
        """**核心回归**：无证据 + 模型仍标 [E1] → 不能抛异常（否则整批实验失败）。"""
        llm = _FakeLLM("正文内容 [E1]，另见 [E2]。")
        agent = ExecutorAgent(llm=llm)

        result = await agent.execute_result(
            node_id="n2",
            title="提取",
            description="提取要点",
            evidence=[],
            evidence_text="",
        )

        assert isinstance(result, ExecutionResult)
        assert result.content  # 保留模型原文，不篡改

    @pytest.mark.asyncio
    async def test_no_evidence_records_warning(self) -> None:
        """无证据却出现引用标记时记 warning（实验数据要能看到模型幻觉）。"""
        llm = _FakeLLM("正文 [E1]。")
        agent = ExecutorAgent(llm=llm)

        result = await agent.execute_result(
            node_id="n2", title="提取", description="提取要点", evidence=[], evidence_text=""
        )

        assert any(
            "citation" in item.lower() or "引用" in item for item in result.warnings
        ), f"应记录引用相关 warning，实际 {result.warnings}"

    @pytest.mark.asyncio
    async def test_prompt_says_no_citation_when_no_evidence(self) -> None:
        """提示词必须明确「无证据不引用」，否则模型必然瞎标。"""
        llm = _FakeLLM("正文。")
        agent = ExecutorAgent(llm=llm)

        await agent.execute_result(
            node_id="n2", title="提取", description="提取要点", evidence=[], evidence_text=""
        )

        prompt = llm.prompts[0]
        assert "[E" in prompt, "提示词应提到引用标记格式（说明不要用）"
        assert (
            "不要" in prompt or "禁止" in prompt or "无需" in prompt
        ), f"提示词应明确要求不引用，实际片段：{prompt[:300]}"


class TestEvidenceStillStrict:
    """有证据时**必须**保持严格（不能因为放宽 no_rag 就把校验废掉）。"""

    @pytest.mark.asyncio
    async def test_out_of_range_still_raises(self) -> None:
        from app.core.exceptions import EvidenceCitationError
        from app.orchestration.contracts import Evidence

        llm = _FakeLLM("正文 [E5]。")
        agent = ExecutorAgent(llm=llm)

        with pytest.raises(EvidenceCitationError):
            await agent.execute_result(
                node_id="n2",
                title="提取",
                description="提取要点",
                evidence=[Evidence(excerpt="证据一")],
                evidence_text="[E1] 证据一",
            )

    @pytest.mark.asyncio
    async def test_valid_marker_accepted(self) -> None:
        from app.orchestration.contracts import Evidence

        llm = _FakeLLM("正文 [E1]。")
        agent = ExecutorAgent(llm=llm)

        result = await agent.execute_result(
            node_id="n2",
            title="提取",
            description="提取要点",
            evidence=[Evidence(excerpt="证据一")],
            evidence_text="[E1] 证据一",
        )

        assert len(result.evidence) == 1
