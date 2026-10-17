"""离线文献源：冻结清单（manifest.json）读取与检索（Plan 3 Task 6）。

清单由 `scripts/prepare_demo_corpus.py` 生成，结构：

    {
      "version": 1,
      "generated_at": "...",
      "query": {"topic": ..., "query": ..., "max_results": 10, ...},
      "records": [{LiteratureRecord 字段..., "local_path": "files/<id>.pdf", "sha256": "..."}]
    }

`ManifestLiteratureSource` 让工作流的 search 节点在 `--offline` 下完全不走网络，
并可按 source_id 解析本地 PDF 路径（供知识库入库与引用回溯）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.core.exceptions import ValidationException
from app.core.logging import get_logger
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

logger = get_logger(__name__)

MANIFEST_FILENAME = "manifest.json"
FILES_DIRNAME = "files"
REQUIRED_CORPUS_SIZE = 10


@dataclass(frozen=True)
class OfflineCorpus:
    """已冻结的离线语料：清单路径 + 解析后的记录。"""

    manifest_path: Path
    records: list[LiteratureRecord]

    @property
    def root(self) -> Path:
        return self.manifest_path.parent

    def pdf_path(self, source_id: str) -> Path:
        """按 source_id 返回本地 PDF 路径（越界或缺失时抛 ValidationException）。"""
        payload = _raw_records(self.manifest_path)
        for item in payload:
            if item.get("source_id") != source_id:
                continue
            candidate = (self.root / str(item.get("local_path", ""))).resolve()
            if not candidate.is_file() or self.root.resolve() not in candidate.parents:
                raise ValidationException(f"离线语料缺少文件: {source_id}")
            return candidate
        raise ValidationException(f"离线语料不含 source_id: {source_id}")

    def verify_hashes(self) -> None:
        """校验每个记录的 sha256 与本地文件一致。"""
        for item in _raw_records(self.manifest_path):
            candidate = (self.root / str(item.get("local_path", ""))).resolve()
            if not candidate.is_file():
                raise ValidationException(f"离线语料缺少文件: {item.get('source_id')}")
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest != item.get("sha256"):
                raise ValidationException(f"离线语料哈希不一致: {item.get('source_id')}")


class ManifestLiteratureSource:
    """把冻结清单适配为工作流的文献检索源（search 不走网络）。"""

    def __init__(self, manifest_path: str | Path) -> None:
        self.corpus = load_offline_corpus(manifest_path)

    @property
    def records(self) -> list[LiteratureRecord]:
        return self.corpus.records

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        """按日期区间过滤清单记录；不做网络请求。"""
        filtered: list[LiteratureRecord] = []
        for record in self.corpus.records:
            published: date = record.published_at.date()
            if search.date_from is not None and published < search.date_from:
                continue
            if search.date_to is not None and published > search.date_to:
                continue
            filtered.append(record)
        return filtered[: search.max_results]

    def pdf_path(self, source_id: str) -> Path:
        return self.corpus.pdf_path(source_id)


def _raw_records(manifest_path: Path) -> list[dict[str, Any]]:
    path = Path(manifest_path)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationException("离线清单不可读或不是合法 JSON") from exc
    items = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise ValidationException("离线清单缺少 records 列表")
    return [item for item in items if isinstance(item, dict)]


def load_offline_corpus(manifest_path: str | Path) -> OfflineCorpus:
    """读取并校验清单结构（不做哈希校验，哈希由 `verify_hashes()` 负责）。"""
    path = Path(manifest_path)
    known_fields = set(LiteratureRecord.model_fields)
    records: list[LiteratureRecord] = []
    for item in _raw_records(path):
        subset = {key: value for key, value in item.items() if key in known_fields}
        records.append(LiteratureRecord.model_validate(subset))
    source_ids = [record.source_id for record in records]
    if len(set(source_ids)) != len(source_ids):
        raise ValidationException("离线清单存在重复 source_id")
    logger.info("offline_corpus_loaded", records=len(records), path=str(path))
    return OfflineCorpus(manifest_path=path, records=records)


def build_manifest(
    *,
    query: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造清单字典（records 必须已含 local_path 与 sha256）。"""
    return {
        "version": 1,
        "query": query,
        "record_count": len(entries),
        "records": entries,
    }
