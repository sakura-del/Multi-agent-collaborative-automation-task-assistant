"""实验输入契约测试（Plan 5 Task 5）。

这些测试**不需要网络**：语料清单在测试里用临时文件构造；
真实语料是否就绪由 `python -m evaluation.validate` 单独判断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.models import (
    FROZEN_MODEL,
    FROZEN_SEED,
    FROZEN_TEMPERATURE,
    EvaluationTopic,
    ExperimentConfig,
    HumanRating,
    load_experiment_config,
    load_human_ratings,
    load_topics,
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "evaluation" / "configs"
TOPICS_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "datasets" / "topics.json"
RATINGS_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "evaluation" / "datasets" / "ratings_template.csv"
)


def _profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "multi_agent_no_rag",
        "agent_mode": "multi",
        "retrieval_mode": "none",
        "validation_enabled": True,
        "review_enabled": True,
        "provider": "openai",
        "model": FROZEN_MODEL,
        "temperature": FROZEN_TEMPERATURE,
        "max_output_tokens": 2048,
        "prompt_version": "lit-v1",
        "corpus_version": "corpus-10-v1",
        "seed": FROZEN_SEED,
        "runs_per_topic": 2,
    }
    payload.update(overrides)
    return payload


class TestExperimentProfiles:
    def test_four_profiles_load_and_share_frozen_parameters(self) -> None:
        names = ["baseline_a.yaml", "baseline_b.yaml", "baseline_c.yaml", "full.yaml"]
        profiles = [load_experiment_config(CONFIG_DIR / name) for name in names]

        assert [profile.name for profile in profiles] == [
            "single_agent_no_rag",
            "multi_agent_no_rag",
            "multi_agent_vector_rag",
            "multi_agent_hybrid_rerank",
        ]
        # 四组除画像开关外必须完全一致（可复现性前提）
        for profile in profiles:
            assert profile.model == FROZEN_MODEL
            assert profile.temperature == FROZEN_TEMPERATURE
            assert profile.seed == FROZEN_SEED

    def test_rejects_wrong_temperature(self) -> None:
        with pytest.raises(ValueError, match="温度"):
            ExperimentConfig.model_validate(_profile_payload(temperature=0.7))

    def test_rejects_unknown_profile_name(self) -> None:
        with pytest.raises(ValueError, match="未知实验画像"):
            ExperimentConfig.model_validate(_profile_payload(name="custom_group"))

    def test_rejects_switch_inconsistent_with_name(self) -> None:
        """名为 baseline_b 却打开检索 → 必须失败。"""
        with pytest.raises(ValueError, match="retrieval_mode"):
            ExperimentConfig.model_validate(_profile_payload(retrieval_mode="vector"))

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValueError):
            ExperimentConfig.model_validate(_profile_payload(unknown_switch=True))

    def test_rejects_model_drift(self) -> None:
        with pytest.raises(ValueError, match="model"):
            ExperimentConfig.model_validate(_profile_payload(model="gpt-4o-mini"))

    def test_profiles_are_immutable(self) -> None:
        profile = load_experiment_config(CONFIG_DIR / "full.yaml")
        with pytest.raises(ValueError):
            profile.model = "other"  # type: ignore[misc]


class TestTopics:
    def test_fifteen_topics_across_three_domains(self) -> None:
        topics = load_topics(TOPICS_PATH)

        assert len(topics) == 15
        counts: dict[str, int] = {}
        for topic in topics:
            counts[topic.domain.value] = counts.get(topic.domain.value, 0) + 1
        assert counts == {"ai": 5, "software_engineering": 5, "education_technology": 5}
        assert len({topic.topic_id for topic in topics}) == 15
        for topic in topics:
            assert topic.query_en, "每个课题都需要英文检索式"
            assert topic.corpus_manifest.endswith(".json")

    def test_rejects_duplicate_topic_ids(self, tmp_path: Path) -> None:
        payload = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
        payload["topics"].append(dict(payload["topics"][0]))
        path = tmp_path / "topics.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        with pytest.raises(ValueError, match="重复 topic_id"):
            load_topics(path)

    def test_rejects_bad_topic_id_format(self) -> None:
        with pytest.raises(ValueError, match="topic_id"):
            EvaluationTopic.model_validate(
                {
                    "topic_id": "BAD ID",
                    "domain": "ai",
                    "topic_zh": "主题",
                    "query_en": "query",
                    "corpus_manifest": "x.json",
                }
            )

    def test_rejects_unknown_domain(self) -> None:
        with pytest.raises(ValueError):
            EvaluationTopic.model_validate(
                {
                    "topic_id": "ai-ok",
                    "domain": "chemistry",
                    "topic_zh": "主题",
                    "query_en": "query",
                    "corpus_manifest": "x.json",
                }
            )


class TestHumanRatings:
    def test_template_has_exact_header(self) -> None:
        header = RATINGS_TEMPLATE.read_text(encoding="utf-8").strip()
        assert header == (
            "anonymous_output_id,evaluator_id,accuracy,coherence,completeness,"
            "citation_quality,notes"
        )

    def test_parses_valid_ratings_and_computes_mean(self, tmp_path: Path) -> None:
        path = tmp_path / "ratings.csv"
        path.write_text(
            "anonymous_output_id,evaluator_id,accuracy,coherence,completeness,"
            "citation_quality,notes\n"
            "out-0001,e1,5,4,4,5,很好\n",
            encoding="utf-8",
        )
        ratings = load_human_ratings(path)

        assert len(ratings) == 1
        assert ratings[0].mean_score == 4.5

    def test_rejects_out_of_range_scores(self) -> None:
        with pytest.raises(ValueError):
            HumanRating.model_validate(
                {
                    "anonymous_output_id": "out-1",
                    "evaluator_id": "e1",
                    "accuracy": 6,
                    "coherence": 3,
                    "completeness": 3,
                    "citation_quality": 3,
                }
            )

    def test_rejects_group_leaking_output_id(self) -> None:
        """匿名 ID 泄漏分组即破坏盲评，必须失败。"""
        with pytest.raises(ValueError, match="泄漏分组"):
            HumanRating.model_validate(
                {
                    "anonymous_output_id": "baseline_a-run1",
                    "evaluator_id": "e1",
                    "accuracy": 4,
                    "coherence": 4,
                    "completeness": 4,
                    "citation_quality": 4,
                }
            )


class TestValidateCli:
    def test_validation_reports_blocking_problem_when_corpora_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """语料未准备时校验必须失败（不能静默通过）。"""
        from evaluation import validate as validate_module

        def _missing(_topics: object) -> dict[str, int]:
            raise ValueError("语料清单不存在")

        monkeypatch.setattr(validate_module, "validate_corpora", _missing)
        assert validate_module.main([]) == 1

    def test_validation_passes_with_synthetic_corpora(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """用临时语料清单替换真实语料，验证校验逻辑本身正确。"""
        from evaluation import validate as validate_module

        corpus_dir = tmp_path / "corpora"
        corpus_dir.mkdir()
        records = [
            {"source_id": f"2401.{i:05d}v1", "title": f"P{i}", "sha256": "0" * 64}
            for i in range(10)
        ]
        for domain in ("ai", "software_engineering", "education_technology"):
            (corpus_dir / f"{domain}.json").write_text(
                json.dumps({"version": 1, "records": records}), encoding="utf-8"
            )

        topics_payload = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
        for topic in topics_payload["topics"]:
            topic["corpus_manifest"] = str(corpus_dir / f"{topic['domain']}.json")
        topics_file = tmp_path / "topics.json"
        topics_file.write_text(json.dumps(topics_payload, ensure_ascii=False), encoding="utf-8")

        monkeypatch.setattr(validate_module, "TOPICS_PATH", topics_file)
        monkeypatch.setattr(validate_module, "BACKEND_DIR", Path("/"))

        report = validate_module.run_validation()

        assert report["topics"] == 15
        assert report["runs"] == 120  # 4 组 × 15 课题 × 2 次
        assert set(report["domains"].values()) == {5}
