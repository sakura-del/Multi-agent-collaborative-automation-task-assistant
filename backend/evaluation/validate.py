"""实验输入校验 CLI（Plan 5 Task 5）。

一次性校验全部输入，任何不一致都以非零码退出：
    cd backend
    python -m evaluation.validate

校验项：
- 四组画像都能解析，且固定参数（模型/温度/语料与 Prompt 版本/种子）完全一致；
- 15 个课题、3 个领域各 5 个、无重复 ID；
- 每个课题的 corpus_manifest 存在，且清单内记录数与 `corpus_version` 声明一致；
- 盲评模板表头正确。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from evaluation.models import (
    FROZEN_CORPUS_VERSION,
    ExperimentConfig,
    load_experiment_config,
    load_topics,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
EVALUATION_DIR = BACKEND_DIR / "evaluation"
CONFIG_DIR = EVALUATION_DIR / "configs"
TOPICS_PATH = EVALUATION_DIR / "datasets" / "topics.json"
RATINGS_TEMPLATE = EVALUATION_DIR / "datasets" / "ratings_template.csv"

PROFILE_FILES = ["baseline_a.yaml", "baseline_b.yaml", "baseline_c.yaml", "full.yaml"]
EXPECTED_DOMAINS = {"ai": 5, "software_engineering": 5, "education_technology": 5}
EXPECTED_TOPICS = 15
EXPECTED_RECORDS = 10


def load_profiles() -> list[ExperimentConfig]:
    """加载四组画像，并断言固定参数严格一致。"""
    profiles = [load_experiment_config(CONFIG_DIR / name) for name in PROFILE_FILES]
    reference = profiles[0]
    frozen_fields = [
        "provider",
        "model",
        "temperature",
        "max_output_tokens",
        "prompt_version",
        "corpus_version",
        "seed",
        "runs_per_topic",
    ]
    for profile in profiles[1:]:
        for field in frozen_fields:
            if getattr(profile, field) != getattr(reference, field):
                raise ValueError(
                    f"画像 {profile.name} 的固定参数 {field} 与 {reference.name} 不一致："
                    f"{getattr(profile, field)!r} vs {getattr(reference, field)!r}"
                )
    return profiles


def validate_corpora(topics: list[Any]) -> dict[str, int]:
    """每个课题的语料清单必须存在且记录数符合 corpus_version 声明。"""
    checked: dict[str, int] = {}
    for topic in topics:
        path = BACKEND_DIR / topic.corpus_manifest
        if not path.is_file():
            raise ValueError(
                f"课题 {topic.topic_id} 的语料清单不存在: {topic.corpus_manifest}"
                "（先运行 python -m evaluation.prepare_corpora）"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") or []
        if len(records) != EXPECTED_RECORDS:
            raise ValueError(
                f"{topic.corpus_manifest} 记录数为 {len(records)}，"
                f"与 corpus_version={FROZEN_CORPUS_VERSION} 要求的 {EXPECTED_RECORDS} 不符"
            )
        checked[topic.corpus_manifest] = len(records)
    return checked


def run_validation() -> dict[str, Any]:
    profiles = load_profiles()
    topics = load_topics(TOPICS_PATH)

    if len(topics) != EXPECTED_TOPICS:
        raise ValueError(f"课题数应为 {EXPECTED_TOPICS}，实际 {len(topics)}")

    domain_counts: dict[str, int] = {}
    for topic in topics:
        domain_counts[topic.domain.value] = domain_counts.get(topic.domain.value, 0) + 1
    if domain_counts != EXPECTED_DOMAINS:
        raise ValueError(f"领域分布应为 {EXPECTED_DOMAINS}，实际 {domain_counts}")

    corpora = validate_corpora(topics)

    header = RATINGS_TEMPLATE.read_text(encoding="utf-8").strip()
    expected_header = (
        "anonymous_output_id,evaluator_id,accuracy,coherence,completeness," "citation_quality,notes"
    )
    if header != expected_header:
        raise ValueError(f"盲评模板表头不符: {header!r}")

    return {
        "profiles": [profile.name for profile in profiles],
        "topics": len(topics),
        "domains": domain_counts,
        "corpora": corpora,
        "runs": len(profiles) * len(topics) * profiles[0].runs_per_topic,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验实验输入（画像/课题/语料/盲评模板）")
    parser.parse_args(argv)
    try:
        report = run_validation()
    except (ValueError, FileNotFoundError) as exc:
        print(f"实验输入校验失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
