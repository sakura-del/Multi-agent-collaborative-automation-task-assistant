"""论文报告生成（Plan 5 Task 7）。

输入：一个 run-set 目录（`evaluation/runs/<run-set-id>/`）
输出（默认写到 `<run-set>/report/`）：

    summary.csv        每次运行一行（含失败/不完整，状态列标明）
    aggregate.csv      每组每指标：n / mean / median / 95% bootstrap CI
    significance.csv   各组 vs baseline A 的配对 Wilcoxon 检验
    figures/*.png      检索 / 内容质量 / 引用 / 延迟与 token / 恢复 五张图
    report.md          把每张表每张图挂到 run-set ID 上，并列出缺失运行

原则：
- **确定性指标与人工指标优先**；LLM Judge 只作补充，单独成节，不并入主质量分；
- bootstrap 与检验都带固定 seed，同输入两次生成结果完全一致；
- 失败/缺失运行**记录在案而非丢弃**（报告里单独列出）。
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from statistics import mean, median
from typing import Any

# 画像固定顺序（报告与图表都按此顺序，保证跨 run-set 可对比）
PROFILE_ORDER = [
    "single_agent_no_rag",
    "multi_agent_no_rag",
    "multi_agent_vector_rag",
    "multi_agent_hybrid_rerank",
]
PROFILE_LABELS = {
    "single_agent_no_rag": "A: 单 Agent 无 RAG",
    "multi_agent_no_rag": "B: 多 Agent 无 RAG",
    "multi_agent_vector_rag": "C: 多 Agent 向量 RAG",
    "multi_agent_hybrid_rerank": "FULL: 多 Agent 混合检索+重排",
}
# 图表用短英文标签（容器/CI 常缺 CJK 字体，中文会渲染成方框）
PROFILE_SHORT = {
    "single_agent_no_rag": "A: single, no RAG",
    "multi_agent_no_rag": "B: multi, no RAG",
    "multi_agent_vector_rag": "C: multi + vector RAG",
    "multi_agent_hybrid_rerank": "FULL: multi + hybrid",
}
BASELINE_PROFILE = "single_agent_no_rag"

# 主指标（确定性/人工）。LLM Judge 不在此列。
PRIMARY_METRICS = [
    "citation_resolvability",
    "evidence_coverage",
    "error_citation_rate",
    "duplicate_paragraph_ratio",
    "recall_at_5",
    "ndcg_at_5",
    "success",
]
EXECUTION_METRICS = ["duration_ms", "total_tokens", "retries", "recovery_success"]
ALL_METRICS = PRIMARY_METRICS + EXECUTION_METRICS

# 报告中"越高越好"的方向（用于文字结论；不参与计算）
HIGHER_IS_BETTER = {
    "citation_resolvability": True,
    "evidence_coverage": True,
    "error_citation_rate": False,
    "duplicate_paragraph_ratio": False,
    "recall_at_5": True,
    "ndcg_at_5": True,
    "success": True,
    "duration_ms": False,
    "total_tokens": False,
    "retries": False,
    "recovery_success": True,
}

DEFAULT_SEED = 20261017
BOOTSTRAP_ITERATIONS = 2000


# ===== 收集 =====


def collect_run_records(run_set_dir: Path) -> list[dict[str, Any]]:
    """扫描 run-set 目录，每次运行一条记录（含 failed / incomplete）。"""
    root = Path(run_set_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"run-set 目录不存在: {root}")

    records: list[dict[str, Any]] = []
    for metrics_path in sorted(root.glob("*/*/rep-*/metrics.json")):
        run_dir = metrics_path.parent
        profile = run_dir.parent.parent.name
        topic_id = run_dir.parent.name
        repetition = _parse_repetition(run_dir.name)
        payload = _read_json(metrics_path)
        # config.json 里的画像名更权威（目录名只作回退）
        config = _read_json(run_dir / "config.json")
        records.append(
            {
                "run_id": f"{profile}:{topic_id}:{repetition}",
                "profile": str(config.get("name") or profile),
                "topic_id": topic_id,
                "repetition": repetition,
                "status": str(payload.get("status") or "unknown"),
                "metrics": payload,
            }
        )

    # 目录存在但缺 metrics.json/为空 → 记为 incomplete
    for run_dir in sorted(root.glob("*/*/rep-*")):
        if not run_dir.is_dir() or (run_dir / "metrics.json").is_file():
            continue
        profile = run_dir.parent.parent.name
        records.append(
            {
                "run_id": f"{profile}:{run_dir.parent.name}:{_parse_repetition(run_dir.name)}",
                "profile": profile,
                "topic_id": run_dir.parent.name,
                "repetition": _parse_repetition(run_dir.name),
                "status": "incomplete",
                "metrics": {},
            }
        )
    return records


def _parse_repetition(dir_name: str) -> int:
    try:
        return int(dir_name.split("-")[-1])
    except ValueError:
        return 0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_summary_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每次运行一行；指标展平为列。"""
    rows: list[dict[str, Any]] = []
    for record in records:
        metrics = record.get("metrics") or {}
        row: dict[str, Any] = {
            "run_id": record["run_id"],
            "profile": record["profile"],
            "topic_id": record["topic_id"],
            "repetition": record["repetition"],
            "status": record["status"],
        }
        for metric in ALL_METRICS:
            row[metric] = metrics.get(metric)
        rows.append(row)
    return rows


# ===== 统计 =====


def bootstrap_ci(
    values: list[float],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_SEED,
    confidence: float = 0.95,
) -> tuple[float | None, float | None]:
    """均值的 percentile bootstrap 置信区间；样本 <2 时返回 (None, None)。"""
    clean = [float(value) for value in values if value is not None]
    if len(clean) < 2:
        return None, None
    generator = random.Random(seed)
    n = len(clean)
    means: list[float] = []
    for _ in range(iterations):
        sample = [clean[generator.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    low_index = max(0, int(alpha * iterations) - 1)
    high_index = min(iterations - 1, int((1.0 - alpha) * iterations))
    return means[low_index], means[high_index]


def _completed(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record["status"] == "completed"]


def _values_for(records: list[dict[str, Any]], metric: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = (record.get("metrics") or {}).get(metric)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def build_aggregate_table(
    records: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    """每组每指标一行：n / mean / median / 95% CI（只用 completed 运行）。"""
    completed = _completed(records)
    rows: list[dict[str, Any]] = []
    profiles = [name for name in PROFILE_ORDER if any(r["profile"] == name for r in completed)]
    profiles += sorted({r["profile"] for r in completed} - set(PROFILE_ORDER))
    for profile in profiles:
        subset = [record for record in completed if record["profile"] == profile]
        for metric in ALL_METRICS:
            values = _values_for(subset, metric)
            low, high = bootstrap_ci(values, seed=seed)
            rows.append(
                {
                    "profile": profile,
                    "metric": metric,
                    "n": len(values),
                    "mean": mean(values) if values else None,
                    "median": median(values) if values else None,
                    "ci_low": low,
                    "ci_high": high,
                }
            )
    return rows


def build_significance_table(
    records: list[dict[str, Any]],
    *,
    baseline: str = BASELINE_PROFILE,
) -> list[dict[str, Any]]:
    """各非基线组 vs baseline 的配对 Wilcoxon 秩和检验（配对键 = (topic, repetition)）。"""
    completed = _completed(records)
    by_profile: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for record in completed:
        key = (record["topic_id"], record["repetition"])
        by_profile.setdefault(record["profile"], {})[key] = record

    baseline_map = by_profile.get(baseline, {})
    rows: list[dict[str, Any]] = []
    for profile in [name for name in PROFILE_ORDER if name in by_profile and name != baseline]:
        for metric in ALL_METRICS:
            pairs: list[tuple[float, float]] = []
            for key, record in by_profile[profile].items():
                other = baseline_map.get(key)
                if other is None:
                    continue
                left = _metric_value(record, metric)
                right = _metric_value(other, metric)
                if left is None or right is None:
                    continue
                pairs.append((left, right))
            statistic, p_value = _wilcoxon(pairs)
            rows.append(
                {
                    "profile": profile,
                    "baseline": baseline,
                    "metric": metric,
                    "n_pairs": len(pairs),
                    "statistic": statistic,
                    "p_value": p_value,
                }
            )
    return rows


def _metric_value(record: dict[str, Any], metric: str) -> float | None:
    value = (record.get("metrics") or {}).get(metric)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _wilcoxon(pairs: list[tuple[float, float]]) -> tuple[float | None, float | None]:
    """配对 Wilcoxon 检验；样本不足（<2 对或全零差）时返回 (None, None)。"""
    if len(pairs) < 2:
        return None, None
    differences = [left - right for left, right in pairs]
    if all(abs(diff) < 1e-12 for diff in differences):
        return None, None  # 完全无差异 → 不编造 p 值
    try:
        from scipy.stats import wilcoxon
    except ImportError:  # 未安装 evaluation extras
        return None, None
    result = wilcoxon([left for left, _ in pairs], [right for _, right in pairs])
    return float(result.statistic), float(result.pvalue)


# ===== 图表 =====

FIGURE_SPECS: list[tuple[str, str, str, list[str]]] = [
    # (文件名, 图中标题（英文，避免 CJK 字体缺失）, y 轴标签, 指标列表)
    (
        "retrieval.png",
        "Retrieval quality (Recall@5 / nDCG@5)",
        "score (0-1)",
        ["recall_at_5", "ndcg_at_5"],
    ),
    (
        "content_quality.png",
        "Content quality (evidence coverage / duplicate ratio)",
        "score (0-1)",
        ["evidence_coverage", "duplicate_paragraph_ratio"],
    ),
    (
        "citations.png",
        "Citation reliability (resolvability / error rate)",
        "ratio (0-1)",
        ["citation_resolvability", "error_citation_rate"],
    ),
    (
        "latency_tokens.png",
        "Cost (mean duration ms / total tokens)",
        "value",
        ["duration_ms", "total_tokens"],
    ),
    (
        "recovery.png",
        "Robustness (success / recovery / retries)",
        "value",
        ["success", "recovery_success", "retries"],
    ),
]
# report.md 里用的中文图表标题
FIGURE_TITLES_ZH = {
    "retrieval.png": "检索质量（Recall@5 / nDCG@5）",
    "content_quality.png": "内容质量（证据覆盖 / 重复段落比）",
    "citations.png": "引用可靠性（可解析率 / 错误引用率）",
    "latency_tokens.png": "执行成本（平均耗时 / 总 token）",
    "recovery.png": "鲁棒性（成功率 / 恢复成功率 / 重试次数）",
}


def render_figures(
    aggregate_rows: list[dict[str, Any]],
    figures_dir: Path,
    *,
    run_set_id: str,
) -> list[Path]:
    """按固定规格生成 5 张 PNG；缺 matplotlib 时生成占位说明文件而不静默跳过。"""
    figures_dir.mkdir(parents=True, exist_ok=True)
    produced: list[Path] = []
    for filename, title, ylabel, metrics in FIGURE_SPECS:
        target = figures_dir / filename
        try:
            _plot(target, aggregate_rows, title, ylabel, metrics, run_set_id)
        except ImportError:
            target.with_suffix(".md").write_text(
                f"# {title}\n\n未安装 matplotlib（请 `pip install -e .[evaluation]`）。\n",
                encoding="utf-8",
            )
            target = target.with_suffix(".md")
        produced.append(target)
    return produced


def _plot(
    target: Path,
    aggregate_rows: list[dict[str, Any]],
    title: str,
    ylabel: str,
    metrics: list[str],
    run_set_id: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")  # 无显示环境（CI/服务器）
    # 图中文字用英文标签：容器/CI 常缺 CJK 字体，中文会变方框（标题与 y 轴仍标注清楚）
    import matplotlib.pyplot as plt

    profiles = [name for name in PROFILE_ORDER if any(r["profile"] == name for r in aggregate_rows)]
    lookup = {(row["profile"], row["metric"]): row for row in aggregate_rows}

    fig, ax = plt.subplots(figsize=(9, 4.5))
    width = 0.8 / max(len(metrics), 1)
    for offset, metric in enumerate(metrics):
        positions = []
        values = []
        errors_low = []
        errors_high = []
        for index, profile in enumerate(profiles):
            row = lookup.get((profile, metric))
            if row is None or row["mean"] is None:
                continue
            positions.append(index + offset * width)
            values.append(row["mean"])
            low = row["ci_low"] if row["ci_low"] is not None else row["mean"]
            high = row["ci_high"] if row["ci_high"] is not None else row["mean"]
            errors_low.append(max(0.0, row["mean"] - low))
            errors_high.append(max(0.0, high - row["mean"]))
        if not values:
            continue
        ax.bar(
            positions,
            values,
            width=width,
            label=metric,
            yerr=[errors_low, errors_high],
            capsize=3,
        )

    # 样本量取"该指标的实际配对样本数"，不能用其它指标的 n（否则会误显示 n=0）
    samples: dict[str, int] = {}
    for metric in metrics:
        for row in aggregate_rows:
            if row["metric"] == metric and row["profile"] not in samples:
                samples[row["profile"]] = row["n"]
    tick_labels = [
        f"{PROFILE_SHORT.get(profile, profile)}\n(n={samples.get(profile, 0)})"
        for profile in profiles
    ]
    ax.set_xticks(range(len(profiles)))
    ax.set_xticklabels(tick_labels, fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(f"{title} | run-set {run_set_id}", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(target, dpi=130)
    plt.close(fig)


# ===== CSV 与报告 =====


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _format_metric(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _build_report_markdown(
    *,
    run_set_id: str,
    records: list[dict[str, Any]],
    aggregate_rows: list[dict[str, Any]],
    significance_rows: list[dict[str, Any]],
) -> str:
    completed = _completed(records)
    failed = [record for record in records if record["status"] == "failed"]
    incomplete = [record for record in records if record["status"] == "incomplete"]

    lines: list[str] = [
        f"# 实验结果报告（run-set `{run_set_id}`）",
        "",
        f"- 运行总数：**{len(records)}**",
        f"- 完成：**{len(completed)}**　失败：**{len(failed)}**　不完整：**{len(incomplete)}**",
        "- 指标口径：确定性指标与人工指标为主；LLM Judge 仅作补充（见文末单独章节）。",
        "",
        "## 1. 汇总表",
        "",
        "逐次运行明细：[`summary.csv`](summary.csv)；分组聚合（含 95% bootstrap 置信区间）："
        "[`aggregate.csv`](aggregate.csv)；显著性检验（vs baseline A）："
        "[`significance.csv`](significance.csv)。",
        "",
        "| 画像 | 指标 | n | 均值 | 中位数 | 95% CI |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in aggregate_rows:
        if row["mean"] is None:
            continue
        ci = (
            f"[{_format_metric(row['ci_low'])}, {_format_metric(row['ci_high'])}]"
            if row["ci_low"] is not None
            else "—"
        )
        lines.append(
            f"| {PROFILE_LABELS.get(row['profile'], row['profile'])} | {row['metric']} | "
            f"{row['n']} | {_format_metric(row['mean'])} | "
            f"{_format_metric(row['median'])} | {ci} |"
        )

    lines += [
        "",
        "## 2. 图表",
        "",
        "每张图都带标题、样本量与置信区间误差线。",
        "",
    ]
    for filename, _title_en, _ylabel, metrics in FIGURE_SPECS:
        relative = f"figures/{filename}"
        title_zh = FIGURE_TITLES_ZH.get(filename, filename)
        lines.append(f"### {title_zh}")
        lines.append("")
        lines.append(f"![{title_zh}]({relative})")
        lines.append("")
        lines.append(
            f"指标：{', '.join(metrics)}。数据来源：`aggregate.csv`（run-set `{run_set_id}`）。"
        )
        lines.append("")

    lines += [
        "## 3. 显著性检验",
        "",
        "配对 Wilcoxon 秩和检验，配对键为 (课题, 重复次数)；样本不足或差值全零时记为 —，"
        "不编造 p 值。",
        "",
        "| 画像 | 指标 | 配对数 | 统计量 | p 值 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in significance_rows:
        if row["p_value"] is None:
            continue
        lines.append(
            f"| {PROFILE_LABELS.get(row['profile'], row['profile'])} | {row['metric']} | "
            f"{row['n_pairs']} | {_format_metric(row['statistic'])} | "
            f"{row['p_value']:.4f} |"
        )

    lines += [
        "",
        "## 4. 缺失与失败运行",
        "",
    ]
    if not failed and not incomplete:
        lines.append("无失败或不完整运行。")
    else:
        lines.append("以下运行**未计入聚合**，但保留在案以便复核：")
        lines.append("")
        for record in failed:
            lines.append(f"- `{record['run_id']}` — 失败")
        for record in incomplete:
            lines.append(f"- `{record['run_id']}` — 产物不完整")

    lines += [
        "",
        "## 5. LLM Judge（补充指标，不并入主质量分）",
        "",
        "LLM Judge 得分仅用于**补充**观察，需在论文中标注所用模型、版本与 Prompt 版本，"
        "且不得与确定性/人工指标平均成单一主质量分。本节数据需单独运行 Judge 脚本后填入。",
        "",
        "| 项目 | 值 |",
        "| --- | --- |",
        "| 模型 / 版本 | 待填写 |",
        "| Prompt 版本 | 待填写 |",
        "| 说明 | 仅作补充，不参与主指标计算 |",
        "",
        "## 6. 复现方式",
        "",
        "```bash",
        "cd backend",
        "python -m evaluation.cli validate",
        "python -m evaluation.cli run --profiles all --repetitions 2",
        "python -m evaluation.cli report --run-set <run-set-id>",
        "```",
        "",
        f"本报告由 `evaluation/reporting.py` 从 run-set `{run_set_id}` 自动生成，"
        "bootstrap 与检验均使用固定 seed，同输入两次生成结果一致。",
        "",
    ]
    return "\n".join(lines)


def generate_report(
    run_set_dir: Path,
    output_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
) -> list[Path]:
    """生成全部报告产物，返回文件路径列表。"""
    root = Path(run_set_dir)
    output = Path(output_dir)
    records = collect_run_records(root)
    run_set_id = _read_json(root / "run_set.json").get("run_set_id") or root.name

    summary_rows = build_summary_rows(records)
    aggregate_rows = build_aggregate_table(records, seed=seed)
    significance_rows = build_significance_table(records)

    output.mkdir(parents=True, exist_ok=True)
    produced: list[Path] = []

    summary_path = output / "summary.csv"
    _write_csv(
        summary_path,
        summary_rows,
        ["run_id", "profile", "topic_id", "repetition", "status", *ALL_METRICS],
    )
    produced.append(summary_path)

    aggregate_path = output / "aggregate.csv"
    _write_csv(
        aggregate_path,
        aggregate_rows,
        ["profile", "metric", "n", "mean", "median", "ci_low", "ci_high"],
    )
    produced.append(aggregate_path)

    significance_path = output / "significance.csv"
    _write_csv(
        significance_path,
        significance_rows,
        ["profile", "baseline", "metric", "n_pairs", "statistic", "p_value"],
    )
    produced.append(significance_path)

    produced.extend(render_figures(aggregate_rows, output / "figures", run_set_id=str(run_set_id)))

    report_path = output / "report.md"
    report_path.write_text(
        _build_report_markdown(
            run_set_id=str(run_set_id),
            records=records,
            aggregate_rows=aggregate_rows,
            significance_rows=significance_rows,
        ),
        encoding="utf-8",
    )
    produced.append(report_path)
    return produced
