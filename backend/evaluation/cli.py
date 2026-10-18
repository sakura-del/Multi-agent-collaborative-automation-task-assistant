"""实验 CLI（Plan 5 Task 6）。

    cd backend
    python -m evaluation.cli validate
    python -m evaluation.cli run --profiles all --repetitions 2
    python -m evaluation.cli run --adapter fixed --profiles full --topic ai-rag-eval --repetitions 1

`--adapter fixed` 使用确定性执行器（不调用真实模型，适合 CI/演练）；
`--adapter live` 走真实 TaskExecution（需要数据库 + 已入库语料 + 有效模型密钥）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from evaluation.models import (
    EvaluationTopic,
    ExperimentConfig,
    load_experiment_config,
    load_topics,
)
from evaluation.runner import ExperimentRunner, FixedAdapterExecutor, build_matrix

BACKEND_DIR = Path(__file__).resolve().parents[1]
EVALUATION_DIR = BACKEND_DIR / "evaluation"
CONFIG_DIR = EVALUATION_DIR / "configs"
TOPICS_PATH = EVALUATION_DIR / "datasets" / "topics.json"
DEFAULT_OUTPUT_ROOT = EVALUATION_DIR / "runs"

PROFILE_FILES = {
    "baseline_a": "baseline_a.yaml",
    "baseline_b": "baseline_b.yaml",
    "baseline_c": "baseline_c.yaml",
    "full": "full.yaml",
}


def _load_selected_profiles(selection: str) -> list[ExperimentConfig]:
    """`all` 或逗号分隔的画像键（baseline_a,baseline_c,full）。"""
    if selection == "all":
        keys = list(PROFILE_FILES)
    else:
        keys = [item.strip() for item in selection.split(",") if item.strip()]
    unknown = [key for key in keys if key not in PROFILE_FILES]
    if unknown:
        raise SystemExit(f"未知画像: {unknown}（可选: all 或 {sorted(PROFILE_FILES)}）")
    return [load_experiment_config(CONFIG_DIR / PROFILE_FILES[key]) for key in keys]


def _load_selected_topics(topic_id: str | None) -> list[EvaluationTopic]:
    topics = load_topics(TOPICS_PATH)
    if topic_id is None:
        return topics
    selected = [topic for topic in topics if topic.topic_id == topic_id]
    if not selected:
        raise SystemExit(f"未知课题: {topic_id}（可用: {[t.topic_id for t in topics][:3]} …）")
    return selected


def _build_executor(adapter: str, args: argparse.Namespace) -> Any:
    if adapter == "fixed":
        return FixedAdapterExecutor()
    if adapter != "live":
        raise SystemExit(f"未知适配器: {adapter}（fixed / live）")
    raise SystemExit(
        "live 适配器需要在实验环境中装配 TaskExecution（数据库 + 语料入库 + 模型密钥），"
        "请参考 evaluation/TaskExecutionExecutor 在本机脚本中注入。"
    )


async def _run(args: argparse.Namespace) -> int:
    profiles = _load_selected_profiles(args.profiles)
    topics = _load_selected_topics(args.topic)
    specs = build_matrix(profiles, topics, args.repetitions)
    executor = _build_executor(args.adapter, args)

    output_root = Path(args.output_root).expanduser().resolve()
    runner = ExperimentRunner(
        executor,
        output_root=output_root,
        run_set_id=args.run_set_id,
        logger=lambda message: print(f"  {message}", flush=True),
    )
    print(
        f"run-set {runner.run_set_id}: {len(specs)} runs "
        f"({len(profiles)} 画像 × {len(topics)} 课题 × {args.repetitions} 次)"
    )

    def _progress(index: int, total: int, result: str) -> None:
        print(f"[{index}/{total}] {result}", flush=True)

    report = await runner.run(specs, on_progress=_progress)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evaluation.cli", description="实验运行 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="校验实验输入（画像/课题/语料/盲评模板）")

    run = sub.add_parser("run", help="执行实验矩阵（resume-safe）")
    run.add_argument("--profiles", default="all", help="all 或 baseline_a,baseline_c,full")
    run.add_argument("--topic", default=None, help="只跑指定 topic_id")
    run.add_argument("--repetitions", type=int, default=2, help="每个 (画像,课题) 的重复次数")
    run.add_argument("--adapter", default="fixed", choices=["fixed", "live"], help="执行器")
    run.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="运行产物根目录")
    run.add_argument("--run-set-id", default=None, help="复用已有 run-set（断点续跑）")
    run.add_argument("--retry-failed", action="store_true", help="重跑并归档失败运行")

    report = sub.add_parser("report", help="从 run-set 生成论文报告（CSV + 图表 + markdown）")
    report.add_argument(
        "--run-set",
        required=True,
        help="run-set ID，或 latest / latest-fixed 指向最近一次运行",
    )
    report.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="运行产物根目录")
    report.add_argument("--out", default=None, help="报告输出目录（默认 <run-set>/report）")
    report.add_argument("--seed", type=int, default=20261017, help="bootstrap/检验固定种子")
    return parser


def _resolve_run_set(arg: str, output_root: Path) -> Path:
    """把 `latest` / `latest-fixed` / 具体 ID 解析成 run-set 目录。"""
    if arg not in {"latest", "latest-fixed"}:
        candidate = output_root / arg
        if not candidate.is_dir():
            raise SystemExit(f"run-set 不存在: {candidate}")
        return candidate
    candidates = sorted(
        (path for path in output_root.glob("rs-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"{output_root} 下没有 run-set 目录")
    return candidates[0]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        from evaluation.validate import main as validate_main

        return validate_main([])
    if args.command == "report":
        from evaluation.reporting import generate_report

        output_root = Path(args.output_root).expanduser().resolve()
        run_set_dir = _resolve_run_set(args.run_set, output_root)
        out_dir = Path(args.out) if args.out else run_set_dir / "report"
        artifacts = generate_report(run_set_dir, out_dir, seed=args.seed)
        print(f"报告已生成：{out_dir}（{len(artifacts)} 个产物）")
        for path in artifacts:
            print(f"  {path.relative_to(out_dir).as_posix()}")
        return 0
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
