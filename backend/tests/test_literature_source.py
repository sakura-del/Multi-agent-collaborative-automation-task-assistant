"""文献源解析测试（Plan 4 Task 8 实跑发现的装配缺口）。"""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestration.services.literature_source import (
    ArxivLiteratureSource,
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

    def test_defaults_to_arxiv_source_when_nothing_injected(self, tmp_path: Path) -> None:
        source = resolve_literature_source(
            _Task(offline_dataset=False), manifest_path=tmp_path / "missing.json"
        )

        assert isinstance(source, ArxivLiteratureSource)
