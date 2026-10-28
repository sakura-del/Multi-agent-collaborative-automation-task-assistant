"""报告必须如实统计「非 completed」的运行。

## 缺陷（2026-10-27 实测）

跑完 60 run 后，报告头部写：

```
运行总数：60
完成：56　失败：0　不完整：0
```

**56 + 0 + 0 = 56 ≠ 60**。4 个 run 凭空消失了。

第 4 节还写「无失败或不完整运行」。

## 根因

`reporting.py` 只把 `status == "failed"` 算作失败：

```python
failed = [record for record in records if record["status"] == "failed"]
incomplete = [record for record in records if record["status"] == "incomplete"]
```

而这次 4 个 run 的 `metrics.json` 里 `status` 是 **`waiting_human`**（校验失败转人工）
—— 它既不是 `completed` 也不是 `failed`，于是**从三个计数里全部漏掉**。

## 为什么严重

`aggregate.csv` 只统计 `completed`（`reporting.py:207`），所以
`multi_agent_hybrid_rerank` 的 `success` 显示 **1.0**（n=11）——
把 4 个失败排除后，成功率看起来是 100%。

真实成功率是 11/15 = 73.3%。

**报告掩盖了「最强画像表现最差」这个核心发现**，直接误导论文结论。

## 修法

计数改成「完成 / 非完成」互补：凡是 `status != "completed"` 的，都必须
落在某个桶里（`failed` / `incomplete` / 其他状态如实列出）。
并断言 `completed + 非完成 == 总数`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.reporting import _build_report_markdown, collect_run_records


def _write_run(
    root: Path,
    *,
    profile: str,
    topic: str,
    repetition: int,
    status: str,
    metrics: dict | None = None,
) -> None:
    run_dir = root / profile / topic / f"rep-{repetition}"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({"name": profile}), encoding="utf-8")
    payload = {
        "status": status,
        "success": 1.0 if status == "completed" else 0.0,
        "duration_ms": 1000.0,
        "retries": 0,
        **(metrics or {}),
    }
    (run_dir / "metrics.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def run_set(tmp_path: Path) -> Path:
    root = tmp_path / "rs-test"
    # 2 个成功
    _write_run(
        root, profile="multi_agent_hybrid_rerank", topic="t1", repetition=1, status="completed"
    )
    _write_run(
        root, profile="multi_agent_hybrid_rerank", topic="t2", repetition=1, status="completed"
    )
    # 1 个转人工（既不是 completed 也不是 failed）
    _write_run(
        root,
        profile="multi_agent_hybrid_rerank",
        topic="t3",
        repetition=1,
        status="waiting_human",
    )
    # 1 个真 failed
    _write_run(root, profile="multi_agent_hybrid_rerank", topic="t4", repetition=1, status="failed")
    return root


class TestNonCompletedRunsAreCounted:
    def test_waiting_human_is_not_silently_dropped(self, run_set: Path) -> None:
        """核心断言：4 个 run 必须被完整分类，不能少。"""
        records = collect_run_records(run_set)
        assert len(records) == 4

        completed = [r for r in records if r["status"] == "completed"]
        others = [r for r in records if r["status"] != "completed"]

        assert len(completed) == 2
        assert len(others) == 2, "waiting_human 与 failed 都应算作非完成"
        assert len(completed) + len(others) == len(records), "总数必须互补，不能有 run 消失"

    def test_report_header_counts_add_up(self, run_set: Path) -> None:
        """报告头部所有分类计数之和必须等于运行总数。

        表头形如：
        ```
        - 运行总数：**4**
        - 完成：**2**　失败：**1**　不完整：**0**
        - 其他未完成状态：**1**（waiting_human 1）
        ```
        把所有「**数字**」的计数行加总，必须等于总数 ——
        只要有任何一类 run 没被归入某个桶，总和就会小于总数。
        """
        import re

        markdown = _build_report_markdown(
            run_set_id="rs-test",
            records=collect_run_records(run_set),
            aggregate_rows=[],
            significance_rows=[],
        )

        lines = markdown.splitlines()
        total_line = next(line for line in lines if line.startswith("- 运行总数："))
        total_match = re.search(r"\*\*(\d+)\*\*", total_line)
        assert total_match is not None, f"运行总数行格式异常: {total_line}"
        total = int(total_match.group(1))

        count_lines = [
            line for line in lines if line.startswith(("- 完成：", "- 其他未完成状态："))
        ]
        assert count_lines, "报告缺少计数行"

        counted = 0
        for line in count_lines:
            counted += sum(int(n) for n in re.findall(r"\*\*(\d+)\*\*", line))

        assert counted == total, (
            f"各分类计数之和 = {counted}，但运行总数 = {total} —— 有 run 从统计里漏掉了。\n"
            f"计数行：{count_lines}"
        )

    def test_waiting_human_is_named_in_report(self, run_set: Path) -> None:
        """报告必须点名 waiting_human，不能只说「无失败」。"""
        markdown = _build_report_markdown(
            run_set_id="rs-test",
            records=collect_run_records(run_set),
            aggregate_rows=[],
            significance_rows=[],
        )

        assert (
            "无失败或不完整运行" not in markdown
        ), "实际有 1 个 waiting_human + 1 个 failed，报告却说「无失败或不完整运行」"
        assert "waiting_human" in markdown, "转人工的运行必须在报告里被明确列出"
