"""离线演示语料测试（Plan 3 Task 6）。

- 清单 schema / 唯一 ID / 路径约束 / 哈希校验使用临时文件（无需网络、无需真实语料）；
- 真实语料未准备时，整备校验以明确原因 SKIP（`python scripts/prepare_demo_corpus.py`）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.exceptions import ValidationException
from app.tools.literature.models import LiteratureSearch
from app.tools.literature.offline import (
    FILES_DIRNAME,
    MANIFEST_FILENAME,
    ManifestLiteratureSource,
    load_offline_corpus,
)

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo_data" / "literature_review"
QUERY_PATH = DEMO_DIR / "query.json"


def _record_payload(index: int) -> dict:
    return {
        "source_id": f"2401.1234{index}v1",
        "title": f"Paper {index}",
        "authors": ["Alice Zhang", "Bob Li"],
        "abstract": "abstract",
        "published_at": "2024-01-15T12:00:00Z",
        "source_url": f"https://arxiv.org/abs/2401.1234{index}v1",
        "pdf_url": f"https://arxiv.org/pdf/2401.1234{index}v1",
        "categories": ["cs.CL"],
    }


def _write_corpus(root: Path, *, count: int = 10, duplicate: bool = False) -> Path:
    files_dir = root / FILES_DIRNAME
    files_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for index in range(1, count + 1):
        payload = _record_payload(1 if duplicate else index)
        body = f"%PDF-1.7 paper {payload['source_id']}".encode()
        target = files_dir / f"{payload['source_id']}.pdf"
        target.write_bytes(body)
        payload["local_path"] = f"{FILES_DIRNAME}/{target.name}"
        payload["sha256"] = hashlib.sha256(body).hexdigest()
        payload["size"] = len(body)
        entries.append(payload)
    manifest = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "query": json.loads(QUERY_PATH.read_text(encoding="utf-8")),
        "record_count": len(entries),
        "records": entries,
    }
    path = root / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


class TestManifestSchema:
    def test_frozen_query_is_complete(self) -> None:
        query = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
        assert set(query) >= {
            "topic",
            "query",
            "date_from",
            "date_to",
            "max_results",
            "citation_style",
        }
        assert query["max_results"] == 10
        assert query["citation_style"] == "gbt7714"

    def test_loads_records_and_verifies_hashes(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path)
        corpus = load_offline_corpus(manifest)

        assert len(corpus.records) == 10
        assert len({record.source_id for record in corpus.records}) == 10
        corpus.verify_hashes()

    def test_rejects_duplicate_source_ids(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path, duplicate=True)
        with pytest.raises(ValidationException):
            load_offline_corpus(manifest)

    def test_detects_hash_mismatch(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path)
        target = tmp_path / FILES_DIRNAME / "2401.12341v1.pdf"
        target.write_bytes(b"%PDF-1.7 tampered")

        corpus = load_offline_corpus(manifest)
        with pytest.raises(ValidationException):
            corpus.verify_hashes()

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path)
        (tmp_path / FILES_DIRNAME / "2401.12341v1.pdf").unlink()
        corpus = load_offline_corpus(manifest)

        with pytest.raises(ValidationException):
            corpus.pdf_path("2401.12341v1")
        with pytest.raises(ValidationException):
            corpus.verify_hashes()

    def test_rejects_path_traversal_in_local_path(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["records"][0]["local_path"] = "../../etc/passwd"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

        corpus = load_offline_corpus(manifest)
        with pytest.raises(ValidationException):
            corpus.pdf_path("2401.12341v1")

    def test_rejects_malformed_manifest(self, tmp_path: Path) -> None:
        path = tmp_path / MANIFEST_FILENAME
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValidationException):
            load_offline_corpus(path)


class TestManifestSource:
    async def test_search_is_offline_and_returns_records(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path)
        source = ManifestLiteratureSource(manifest)

        records = await source.search(
            LiteratureSearch(query="retrieval augmented generation", max_results=5)
        )
        assert len(records) == 5
        assert records[0].source_id == "2401.12341v1"

    async def test_search_filters_dates(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path)
        source = ManifestLiteratureSource(manifest)

        records = await source.search(
            LiteratureSearch(
                query="retrieval augmented generation",
                max_results=10,
                date_from="2025-01-01",
            )
        )
        assert records == []

    async def test_pdf_path_resolves_inside_corpus(self, tmp_path: Path) -> None:
        manifest = _write_corpus(tmp_path)
        source = ManifestLiteratureSource(manifest)
        path = source.pdf_path("2401.12342v1")
        assert path.parent == (tmp_path / FILES_DIRNAME).resolve()
        assert path.read_bytes().startswith(b"%PDF-")


class TestPreparedCorpus:
    """真实语料整备校验：未准备时 SKIP 并给出明确命令。"""

    def test_prepared_corpus_has_ten_verified_records(self) -> None:
        manifest = DEMO_DIR / MANIFEST_FILENAME
        if not manifest.is_file():
            pytest.skip(
                "离线语料未准备：请先运行 `python scripts/prepare_demo_corpus.py`（唯一需要网络的步骤）"
            )
        corpus = load_offline_corpus(manifest)
        assert len(corpus.records) == 10
        corpus.verify_hashes()
