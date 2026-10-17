"""准备冻结离线演示语料（Plan 3 Task 6，唯一需要网络访问的步骤）。

用法：
    cd backend
    python scripts/prepare_demo_corpus.py            # 使用 demo_data/literature_review/query.json
    python scripts/prepare_demo_corpus.py --dry-run  # 只检索并打印候选，不下载

行为：
- 按冻结 query.json 检索 arXiv，要求至少 10 篇候选，否则直接失败；
- 先把 PDF 下载到临时目录 `files.tmp-<说明>`，全部校验通过后才原子替换 `files/`；
- 写入 `manifest.json`（含 sha256、local_path、标题/作者/来源 URL 等元数据）；
- 任一环节失败都不改动既有 `files/` 与 `manifest.json`（原子性）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.exceptions import AppException
from app.knowledge.services.document_storage import DocumentStorage
from app.tools.literature.arxiv import ArxivClient
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.offline import (
    FILES_DIRNAME,
    MANIFEST_FILENAME,
    REQUIRED_CORPUS_SIZE,
    build_manifest,
)

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo_data" / "literature_review"
DEFAULT_QUERY_PATH = DEMO_DIR / "query.json"
MAX_FILE_BYTES = 30 * 1024 * 1024


def load_query(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload.get("query"):
        raise ValueError(f"冻结查询缺少 query 字段: {path}")
    return payload


async def search_records(query: dict[str, Any]) -> list[LiteratureRecord]:
    """检索候选：请求量大于目标篇数，抵消本地日期过滤造成的损耗。"""
    requested = int(query.get("max_results", REQUIRED_CORPUS_SIZE))
    search = LiteratureSearch(
        query=str(query["query"]),
        max_results=max(requested * 3, 30),
        date_from=_parse_date(query.get("date_from")),
        date_to=_parse_date(query.get("date_to")),
    )
    client = ArxivClient()
    try:
        return await client.search(search)
    finally:
        await client.aclose()


async def download_all(
    records: list[LiteratureRecord],
    files_dir: Path,
) -> list[dict[str, Any]]:
    """把记录逐篇下载到 staging 目录，返回清单条目。"""
    storage = DocumentStorage(files_dir, max_file_bytes=MAX_FILE_BYTES)
    client = ArxivClient()
    entries: list[dict[str, Any]] = []
    try:
        for index, record in enumerate(records, start=1):
            stored = await client.download_pdf(record, storage, _storage_user_id())
            target = files_dir / f"{record.source_id}.pdf"
            stored.path.replace(target)
            payload = record.model_dump(mode="json")
            payload["local_path"] = f"{FILES_DIRNAME}/{target.name}"
            payload["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            payload["size"] = target.stat().st_size
            entries.append(payload)
            print(f"[{index}/{len(records)}] {record.source_id} -> {target.name}")
    finally:
        await client.aclose()
    return entries


async def prepare(
    *,
    query_path: Path = DEFAULT_QUERY_PATH,
    out_dir: Path = DEMO_DIR,
    dry_run: bool = False,
) -> Path | None:
    query = load_query(query_path)
    print(f"冻结查询: {query['topic']} / {query['query']}")
    records = await search_records(query)
    print(f"arXiv 命中 {len(records)} 篇（要求 ≥ {REQUIRED_CORPUS_SIZE}）")

    if dry_run:
        for record in records:
            print(f"- {record.source_id} {record.title}")
        return None

    if len(records) < REQUIRED_CORPUS_SIZE:
        raise RuntimeError(
            f"候选不足 {REQUIRED_CORPUS_SIZE} 篇（当前 {len(records)}），未做任何写入"
        )
    selected = records[:REQUIRED_CORPUS_SIZE]

    out_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="files.tmp-", dir=out_dir))
    try:
        entries = await download_all(selected, staging)
        if len(entries) < REQUIRED_CORPUS_SIZE:
            raise RuntimeError(f"有效 PDF 不足 {REQUIRED_CORPUS_SIZE} 篇，未做任何写入")

        files_dir = out_dir / FILES_DIRNAME
        if files_dir.exists():
            shutil.rmtree(files_dir)
        staging.replace(files_dir)
        # 清掉 DocumentStorage 的伪用户子目录，files/ 只保留 <source_id>.pdf
        shutil.rmtree(files_dir / str(_storage_user_id()), ignore_errors=True)

        manifest = build_manifest(query=query, entries=entries)
        manifest["generated_at"] = datetime.now(UTC).isoformat()
        manifest_path = out_dir / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"清单已写入 {manifest_path}（{len(entries)} 篇）")
        return manifest_path
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parse_date(value: Any):
    if not value:
        return None
    from datetime import date

    return date.fromisoformat(str(value))


def _storage_user_id():
    import uuid

    # 语料下载使用固定伪用户目录，最终按 source_id 重命名到 files/
    return uuid.UUID(int=0)


def main() -> int:
    parser = argparse.ArgumentParser(description="准备冻结离线文献语料")
    parser.add_argument("--query", type=Path, default=DEFAULT_QUERY_PATH)
    parser.add_argument("--out", type=Path, default=DEMO_DIR)
    parser.add_argument("--dry-run", action="store_true", help="只检索不下载")
    args = parser.parse_args()

    try:
        asyncio.run(prepare(query_path=args.query, out_dir=args.out, dry_run=args.dry_run))
    except (AppException, RuntimeError, OSError) as exc:
        print(f"准备失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
