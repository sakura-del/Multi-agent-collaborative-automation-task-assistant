"""引用编号全局化回归（Issue #17）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。

验收：结果页与 md/docx 的参考文献与正文 [E#] 一一对应、编号连续。
"""

from __future__ import annotations

import re

from app.orchestration.agents.reviewer import _renumber_citations
from app.orchestration.contracts import ExecutionResult


class TestRenumberCitations:
    def test_local_numbers_rewritten_to_global(self) -> None:
        """节点 A/B 的局部 [E1][E2] 分别指向不同文献 → 正文重写为全局编号。"""
        # 节点 A：evidence n1→全局1，n2→全局2；节点 B：evidence n1→全局3
        local_to_global = {("n1", 1): 1, ("n1", 2): 2, ("n2", 1): 3}
        a = _renumber_citations("第一段 [E1] 与 [E2]。", "n1", local_to_global)
        b = _renumber_citations("第二段 [E1]。", "n2", local_to_global)
        assert a == "第一段 [E1] 与 [E2]。"  # A 恰好就是全局序
        assert b == "第二段 [E3]。"  # B 的局部 [E1] 指向全局第 3 条

    def test_overlapping_local_numbers_disambiguated(self) -> None:
        """两个节点的 [E1] 指向不同文献：同号不同义，必须各自映射。"""
        local_to_global = {("n1", 1): 1, ("n2", 1): 2}
        assert _renumber_citations("甲 [E1]", "n1", local_to_global) == "甲 [E1]"
        assert _renumber_citations("乙 [E1]", "n2", local_to_global) == "乙 [E2]"

    def test_unknown_number_preserved(self) -> None:
        """映射缺号（跨节点幻觉）保留原编号——诚实优于静默篡改。"""
        local_to_global = {("n1", 1): 1}
        out = _renumber_citations("[E1] 与 [E9]。", "n1", local_to_global)
        assert out == "[E1] 与 [E9]。"

    def test_no_mapping_returns_content(self) -> None:
        assert _renumber_citations("[E1] 保持原样", "nx", {}) == "[E1] 保持原样"


class TestBackwardCompat:
    def test_execution_result_without_cited_indices(self) -> None:
        """历史负载没有 cited_indices：默认空列表，复盘退化按列表顺序编号。"""
        r = ExecutionResult.model_validate({"node_id": "n1", "content": "x", "duration_ms": 0})
        assert r.cited_indices == []


class TestRenumberRegex:
    def test_only_citation_markers_replaced(self) -> None:
        """其他方括号内容（wikilink、markdown 链接文本）不受影响。"""
        local_to_global = {("n1", 1): 5, ("n1", 2): 6}
        content = "看 [E1] 和 [E2](不是引用) 与 [[E1]]。"
        out = _renumber_citations(content, "n1", local_to_global)
        assert "[E5]" in out
        # 链接文本 [E2](…) 与 wikilink [[E1]] 不是引用语义：保持原样。
        assert "[E2](不是引用)" in out, "markdown 链接文本不应被重写"
        assert re.search(r"\[E1\]\]", out), "嵌套标记只替换最内层一次出现"
