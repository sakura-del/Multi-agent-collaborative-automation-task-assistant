"""文献源解析测试（Plan 4 Task 8 实跑发现的装配缺口 + Issue #3 R6 离线判定）。"""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestration.services.literature_source import (
    ArxivLiteratureSource,
    is_offline_literature_task,
    resolve_literature_source,
)


class _Task:
    def __init__(self, **config: object) -> None:
        self.config = config


def _write_manifest(root: Path) -> Path:
    files_dir = root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for index in range(1, 3):
        body = f"%PDF-1.7 doc {index}".encode()
        target = files_dir / f"2401.0000{index}v1.pdf"
        target.write_bytes(body)
        records.append(
            {
                "source_id": f"2401.0000{index}v1",
                "title": f"Paper {index}",
                "authors": ["A"],
                "abstract": "a",
                "published_at": "2024-01-15T12:00:00Z",
                "source_url": f"https://arxiv.org/abs/2401.0000{index}v1",
                "pdf_url": f"https://arxiv.org/pdf/2401.0000{index}v1",
                "categories": ["cs.CL"],
                "local_path": f"files/{target.name}",
            }
        )
    path = root / "manifest.json"
    path.write_text(json.dumps({"version": 1, "records": records}), encoding="utf-8")
    return path


class TestResolveLiteratureSource:
    def test_offline_task_uses_frozen_manifest(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path)
        from app.tools.literature.offline import ManifestLiteratureSource

        source = resolve_literature_source(_Task(offline_dataset=True), manifest_path=manifest)

        assert isinstance(source, ManifestLiteratureSource)
        assert len(source.records) == 2

    def test_offline_task_without_manifest_falls_back(self, tmp_path: Path) -> None:
        marker = object()

        source = resolve_literature_source(
            _Task(offline_dataset=True),
            fallback=marker,
            manifest_path=tmp_path / "missing.json",
        )

        assert source is marker

    def test_online_task_prefers_injected_source(self, tmp_path: Path) -> None:
        marker = object()

        source = resolve_literature_source(
            _Task(offline_dataset=False), fallback=marker, manifest_path=tmp_path
        )

        assert source is marker

    def test_defaults_to_multi_source_when_nothing_injected(self, tmp_path: Path) -> None:
        """Phase 1 P1-B：未注入源时默认走多源聚合（arXiv + OpenAlex），不再只有 arXiv。"""
        from app.tools.literature.source_registry import MultiSourceLiteratureSource

        source = resolve_literature_source(
            _Task(offline_dataset=False), manifest_path=tmp_path / "missing.json"
        )

        assert isinstance(source, MultiSourceLiteratureSource)

    def test_task_config_can_select_sources(self, tmp_path: Path) -> None:
        """任务 config 的 `literature_sources` 决定用哪些源。"""
        from app.tools.literature.source_registry import MultiSourceLiteratureSource

        task = _Task(offline_dataset=False, literature_sources=["openalex", "pubmed"])

        source = resolve_literature_source(task, manifest_path=tmp_path / "missing.json")

        assert isinstance(source, MultiSourceLiteratureSource)

    def test_unknown_source_name_falls_back_to_arxiv(self, tmp_path: Path) -> None:
        """未知源名不得让任务失败：退回单 arXiv 源（可跑优先）。"""
        task = _Task(offline_dataset=False, literature_sources=["not-a-source"])

        source = resolve_literature_source(task, manifest_path=tmp_path / "missing.json")

        assert isinstance(source, ArxivLiteratureSource)


class TestIsOfflineLiteratureTask:
    """R6：核验接线用同一条件判定「离线清单任务」（必须与源解析口径一致）。"""

    def test_offline_task_with_existing_manifest_is_true(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path)

        assert is_offline_literature_task(_Task(offline_dataset=True), manifest_path=manifest)

    def test_offline_task_without_manifest_is_false(self, tmp_path: Path) -> None:
        """清单缺失时实际会回退 arXiv 源 → 不得按离线处理（否则漏核验）。"""
        assert not is_offline_literature_task(
            _Task(offline_dataset=True), manifest_path=tmp_path / "missing.json"
        )

    def test_online_task_is_false(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path)

        assert not is_offline_literature_task(_Task(offline_dataset=False), manifest_path=manifest)

    def test_task_without_config_is_false(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path)

        class _NoConfig:
            config = None

        assert not is_offline_literature_task(_NoConfig(), manifest_path=manifest)

    def test_defaults_to_repository_manifest(self) -> None:
        """不传 manifest_path 时使用仓库内冻结清单；存在则为离线任务。"""
        from app.orchestration.services.literature_source import MANIFEST_PATH

        task = _Task(offline_dataset=True)

        assert is_offline_literature_task(task) is MANIFEST_PATH.is_file()
