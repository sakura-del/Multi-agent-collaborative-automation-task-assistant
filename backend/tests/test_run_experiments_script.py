"""实验运行脚本的知识库初始化（2026-10-22 实验矩阵，实测缺陷回归）。

## 缺陷（pilot-8 首跑抓到）

`scripts/run_experiments.py` 装配了 `KnowledgeSearchProvider` 但**没有调用
`init_kb()`**。后果：

| 画像 | 是否检索 | 结果 |
| --- | --- | --- |
| `single_agent_no_rag` | ❌ | ✅ completed |
| `multi_agent_no_rag` | ❌ | ✅ completed |
| `multi_agent_vector_rag` | ✅ | ❌ waiting_human（重试耗尽） |
| `multi_agent_hybrid_rerank` | ✅ | ❌ waiting_human |

错误：`知识库未初始化：请先在应用生命周期中调用 init_kb()（测试可用 force_mock 参数）`

**这个缺陷特别危险**：恰好只有需要检索的两组失败，看起来像「画像差异」，
很容易被当成实验结论写进论文。实际上四组里两组根本没跑成。

## 为什么这个测试是静态检查

真实初始化需要 Chroma + 语料 + 模型。单测里只验证「脚本里确实调了
init_kb，且**在**构造 KnowledgeSearchProvider 之前」—— 这是那个缺陷的
充要条件（应用侧在 app/main.py:58 也做同样的事）。
"""

from __future__ import annotations

import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_experiments.py"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _call_line_numbers() -> list[int]:
    """返回 `init_kb()` 作为**独立语句**出现的行号（1-based）。

    ⚠️ 不能简单 `"init_kb()" in source`：注释与文档字符串里也有这个词，
    删掉真正的调用后测试**仍然绿** —— 首版就是这样写的假测试
    （回退验证时抓到）。必须逐行判断「去掉缩进后正好是 `init_kb()`」。
    """
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    return [index for index, line in enumerate(lines, start=1) if line.strip() == "init_kb()"]


class TestInitKbCalled:
    def test_init_kb_is_called(self) -> None:
        """脚本必须**实际调用** `init_kb()`（否则检索画像全废）。"""
        assert _call_line_numbers(), (
            "run_experiments.py 必须实际调用 init_kb()（独立语句），否则 "
            "KnowledgeSearchProvider 在检索时抛「知识库未初始化」，"
            "vector / hybrid_rerank 画像全部失败"
        )

    def test_init_kb_imported_from_factory(self) -> None:
        source = _source()

        assert "from app.knowledge.services.factory import init_kb" in source

    def test_init_kb_called_before_search_provider(self) -> None:
        """顺序也重要：初始化必须在构造检索提供者**之前**。

        provider 是惰性的（先构造后 init 也能工作），但把 init 放前面能保证
        「一旦 init 失败就立刻退出」，不会跑到第 40 个 run 才发现。
        """
        calls = _call_line_numbers()
        assert calls, "init_kb() 未被调用"
        source_lines = _source().splitlines()
        provider_line = next(
            index
            for index, line in enumerate(source_lines, start=1)
            if "KnowledgeSearchProvider()" in line and "knowledge_query=" in line
        )

        assert calls[0] < provider_line, (
            f"init_kb()（第 {calls[0]} 行）应在构造 KnowledgeSearchProvider()"
            f"（第 {provider_line} 行）之前调用"
        )

    def test_dry_run_does_not_init(self) -> None:
        """`--dry-run` 不该初始化知识库（否则会加载 28344 个分块，很慢）。"""
        calls = _call_line_numbers()
        assert calls, "init_kb() 未被调用"
        source_lines = _source().splitlines()
        dry_line = next(
            index for index, line in enumerate(source_lines, start=1) if "if args.dry_run:" in line
        )

        assert dry_line < calls[0], (
            f"dry-run 分支（第 {dry_line} 行）应在 init_kb()（第 {calls[0]} 行）之前"
            " —— 否则 dry-run 也会加载全部分块"
        )
