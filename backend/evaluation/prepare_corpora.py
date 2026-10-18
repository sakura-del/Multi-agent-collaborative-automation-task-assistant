"""准备各领域离线语料（Plan 5 Task 5 / roadmap §6.3）。

唯一需要网络的步骤：按领域检索 arXiv → 下载 **每个领域 10 篇** PDF → 写 sha256 清单。
清单进仓库（`evaluation/datasets/corpora/<domain>.json`），PDF 目录被 gitignore
（与 Plan 3 Task 6 的冻结语料同一策略）。

用法（在能访问 arXiv 的环境执行一次）：
    cd backend
    python -m evaluation.prepare_corpora            # 三个领域全准备
    python -m evaluation.prepare_corpora --domain ai
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

from app.knowledge.services.document_storage import DocumentStorage
from app.tools.literature.arxiv import ArxivClient
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

CORPORA_DIR = Path(__file__).resolve().parent / "datasets" / "corpora"
DOCS_PER_DOMAIN = 10
MAX_FILE_BYTES = 30 * 1024 * 1024

# 领域 → 冻结检索式（与 topics.json 的 query 语义一致，但更宽以凑够 10 篇）
DOMAIN_QUERIES: dict[str, str] = {
    "ai": "retrieval augmented generation multi agent evaluation",
    "software_engineering": "automated code review test generation defect prediction",
    "education_technology": "adaptive learning analytics automated feedback tutoring",
}
DOMAIN_DATE_FROM = "2023-01-01"
DOMAIN_DATE_TO = "2025-12-31"


async def search_domain(domain: str) -> list[LiteratureRecord]:
    """检索候选（取 3× 窗口，抵消本地日期过滤损耗）。"""
    client = ArxivClient()
    try:
        return await client.search(
            LiteratureSearch(
                query=DOMAIN_QUERIES[domain],
                max_results=DOCS_PER_DOMAIN * 3,
                date_from=_parse_date(DOMAIN_DATE_FROM),
                date_to=_parse_date(DOMAIN_DATE_TO),
            )
        )
    finally:
        await client.aclose()


async def download_domain(domain: str, staging: Path) -> list[dict[str, Any]]:
    """下载该领域前 N 篇 PDF 到 staging，返回清单条目。"""
    records = (await search_domain(domain))[:DOCS_PER_DOMAIN]
    if len(records) < DOCS_PER_DOMAIN:
        raise RuntimeError(f"{domain}: 候选不足 {DOCS_PER_DOMAIN} 篇（{len(records)}）")

    storage = DocumentStorage(staging, max_file_bytes=MAX_FILE_BYTES)
    client = ArxivClient()
    import uuid

    entries: list[dict[str, Any]] = []
    try:
        for record in records:
            stored = await client.download_pdf(record, storage, uuid.UUID(int=0))
            target = staging / f"{record.source_id}.pdf"
            stored.path.replace(target)
            payload = record.model_dump(mode="json")
            payload["local_path"] = target.name
            payload["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            payload["size"] = target.stat().st_size
            entries.append(payload)
    finally:
        await client.aclose()
    shutil.rmtree(staging / str(uuid.UUID(int=0)), ignore_errors=True)
    return entries


async def prepare(domains: list[str], *, out_dir: Path = CORPORA_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for domain in domains:
        staging = Path(tempfile.mkdtemp(prefix=f"{domain}-", dir=out_dir))
        try:
            entries = await download_domain(domain, staging)
            files_dir = out_dir / f"{domain}-files"
            if files_dir.exists():
                shutil.rmtree(files_dir)
            staging.replace(files_dir)
            manifest = {
                "version": 1,
                "domain": domain,
                "query": DOMAIN_QUERIES[domain],
                "date_from": DOMAIN_DATE_FROM,
                "date_to": DOMAIN_DATE_TO,
                "generated_at": datetime.now(UTC).isoformat(),
                "record_count": len(entries),
                "records": entries,
            }
            path = out_dir / f"{domain}.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(path)
            print(f"[{domain}] {len(entries)} 篇 → {path.name}")
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return written


def _parse_date(value: str):
    from datetime import date

    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="准备各领域离线实验语料（需网络）")
    parser.add_argument(
        "--domain",
        action="append",
        choices=sorted(DOMAIN_QUERIES),
        help="只准备指定领域（可重复）；默认三个领域",
    )
    args = parser.parse_args(argv)
    domains = args.domain or list(DOMAIN_QUERIES)
    try:
        asyncio.run(prepare(domains))
    except (RuntimeError, OSError) as exc:
        print(f"准备失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
