"""实验脚本必须重建 BM25 索引（2026-10-22 实验矩阵，第二轮实测缺陷）。

## 缺陷

`init_kb()` 只建**向量库客户端**，不灌数据。BM25 索引是**进程内存**结构，
必须显式 `rebuild_bm25()` 才能从持久化分块恢复。

应用侧 lifespan 会做（`app/main.py:67`），但 `scripts/run_experiments.py`
首版只调了 `init_kb()` → 检索时 `bm25_hits=0`，混合检索退化成单路向量检索。

## 实测证据

pilot 日志：`hybrid_search_done bm25_hits=0 fused=0 vector_hits=0`

而库里 demo 用户有 2835 个分块、2835 条 vector_index —— 数据在，索引没灌。

## 为什么这个缺陷隐蔽

不报错。检索返回空列表，工作流把「没有证据」当成正常情况继续跑，
最终产出的正文里写着「本次子任务未获得任何检索证据」——
**看起来像模型能力问题，实际是索引没建**。

若不做这轮实测，四组画像的「检索差异」全是假的。
"""

from __future__ import annotations

import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_experiments.py"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _call_lines(keyword: str) -> list[int]:
    """返回包含 keyword 的**非注释**行号（1-based）。"""
    lines = _source().splitlines()
    return [
        index
        for index, line in enumerate(lines, start=1)
        if keyword in line and not line.strip().startswith("#")
    ]


class TestBm25Rebuilt:
    def test_rebuild_bm25_is_called(self) -> None:
        """脚本必须调用 `rebuild_bm25()`，否则 BM25 索引为空、检索退化。"""
        assert _call_lines("rebuild_bm25()"), (
            "run_experiments.py 必须调用 rebuild_bm25()，否则 BM25 内存索引为空，"
            "混合检索退化为单路向量检索（实测 bm25_hits=0）"
        )

    def test_rebuild_after_init_kb(self) -> None:
        """顺序：必须先 `init_kb()`（建索引管理器），再 `rebuild_bm25()`。"""
        init_lines = _call_lines("init_kb()")
        rebuild_lines = _call_lines("rebuild_bm25()")
        assert init_lines and rebuild_lines

        assert init_lines[0] < rebuild_lines[0], (
            f"init_kb()（第 {init_lines[0]} 行）应在 rebuild_bm25()"
            f"（第 {rebuild_lines[0]} 行）之前 —— 后者依赖前者建好的索引管理器"
        )

    def test_rebuild_failure_does_not_block(self) -> None:
        """重建失败不该让整个实验退出（空库场景应继续，与 lifespan 同策略）。"""
        source = _source()
        rebuild_pos = source.index("rebuild_bm25()")
        tail = source[rebuild_pos : rebuild_pos + 800]

        assert "except Exception" in tail, "rebuild_bm25() 应被 try/except 包裹"
