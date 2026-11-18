"""复盘输入裁剪（Issue #22：review prompt 实测 47,954 tokens）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.orchestration.agents.reviewer import ReviewerAgent


def _item(node_id: str, content: str) -> Any:
    return SimpleNamespace(model_dump=lambda mode="json": {"node_id": node_id, "content": content})


class TestTrimReviewItems:
    def test_long_content_truncated_per_item(self) -> None:
        trimmed = ReviewerAgent._trim_review_items(
            [_item("n1", "字" * 5000)], per_item=1200, total=24000
        )
        assert len(trimmed) == 1
        assert len(trimmed[0]["content"]) < 1300
        assert trimmed[0]["content"].endswith("（已截断）")

    def test_short_content_untouched(self) -> None:
        trimmed = ReviewerAgent._trim_review_items(
            [_item("n1", "短内容")], per_item=1200, total=24000
        )
        assert trimmed[0]["content"] == "短内容"

    def test_total_budget_replaces_overflow_with_placeholder(self) -> None:
        items = [_item(f"n{i}", "字" * 1000) for i in range(10)]
        trimmed = ReviewerAgent._trim_review_items(items, per_item=1000, total=3000)
        placeholders = [t for t in trimmed if t.get("truncated") is True]
        assert placeholders, "超出总量预算的条目必须降级为占位"
        assert all("content" not in p for p in placeholders)
