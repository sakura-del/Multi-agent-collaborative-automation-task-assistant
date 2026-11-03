"""把 Chroma 里的存量向量原样搬到 pgvector（Supabase）。

```bash
# 1) 先看规模，不写库
python -m scripts.migrate_vectors_to_pgvector --dry-run

# 2) 全量搬运（幂等，可重复跑；中断后加 --start-offset 续跑）
python -m scripts.migrate_vectors_to_pgvector

# 3) 只校验（搬迁完成后单独复核）
python -m scripts.migrate_vectors_to_pgvector --verify-only
```

## 为什么原样搬而不是重新嵌入

- **零模型成本**：28344 条重嵌要跑几十分钟并消耗额度；
- **id 不变**：`vector_index.chroma_id` 映射继续有效，引用回溯不断；
- **可核对**：向量逐条相同，检索结果应当逐条相同——能比对，
  而重嵌后「顺序变了」无法区分是「迁移问题」还是「嵌入模型差异」。

## 校验三重

1. 行数（Chroma count vs pgvector count）；
2. id 集合（缺失 / 孤儿，各取前 20 条样本）；
3. 样例检索重叠率（取若干条真实向量当查询，比对两边 top-k 的 id 交集）。

第 3 项是唯一能抓住「向量搬错列/维度错位」的检查——数量与 id 全对、
但向量内容串了，前两项都发现不了。

## 幂等与续跑

写入是 `ON CONFLICT (collection, id) DO UPDATE`，重复跑只会覆盖同 id；
`--start-offset` 允许从 Chroma 的某个 offset 继续（跳过已导出的部分）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_ID_SAMPLE_LIMIT = 20
_DEFAULT_BATCH_SIZE = 500
_DEFAULT_DIMENSION = 1024


@dataclass
class Batch:
    """一批待写入的向量（已剔除缺 embedding 的脏行）。"""

    ids: list[str] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    metadatas: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0

    def __len__(self) -> int:
        return len(self.ids)


@dataclass
class VerifyReport:
    """迁移校验结论。"""

    chroma_count: int
    pgvector_count: int
    missing_ids: list[str] = field(default_factory=list)
    orphan_ids: list[str] = field(default_factory=list)
    overlap: float | None = None

    @property
    def counts_equal(self) -> bool:
        return self.chroma_count == self.pgvector_count

    @property
    def ids_equal(self) -> bool:
        return not self.missing_ids and not self.orphan_ids

    @property
    def ok(self) -> bool:
        # 重叠率阈值 0.8：HNSW 与 Chroma 的近似搜索实现不同，
        # 少量名次差异正常；低于 0.8 说明向量或 id 映射有问题。
        overlap_ok = self.overlap is None or self.overlap >= 0.8
        return self.counts_equal and self.ids_equal and overlap_ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "chroma_count": self.chroma_count,
            "pgvector_count": self.pgvector_count,
            "counts_equal": self.counts_equal,
            "ids_equal": self.ids_equal,
            "missing_ids": self.missing_ids,
            "orphan_ids": self.orphan_ids,
            "overlap": self.overlap,
            "ok": self.ok,
        }


def _to_float_list(vector: Any) -> list[float]:
    """Chroma 可能返回 numpy 数组；统一转成 Python float 列表。"""
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(x) for x in vector]


def build_batch(payload: dict[str, Any]) -> Batch:
    """把 Chroma `collection.get()` 的载荷转成 Batch。

    缺失 embedding 的行会被跳过并计数——**不能静默丢**：
    少一条就是一条证据永久搜不到，必须让运维看到 `skipped`。
    """
    ids = [str(i) for i in (payload.get("ids") or [])]
    embeddings = payload.get("embeddings")
    documents = payload.get("documents") or []
    metadatas = payload.get("metadatas") or []

    batch = Batch()
    if embeddings is None:
        batch.skipped = len(ids)
        return batch

    for index, chunk_id in enumerate(ids):
        if index >= len(embeddings) or embeddings[index] is None:
            batch.skipped += 1
            continue
        batch.ids.append(chunk_id)
        batch.vectors.append(_to_float_list(embeddings[index]))
        batch.documents.append(str(documents[index]) if index < len(documents) else "")
        meta = metadatas[index] if index < len(metadatas) else {}
        batch.metadatas.append(dict(meta or {}))
    return batch


def iter_batches(
    collection: Any,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    start_offset: int = 0,
) -> Iterator[Batch]:
    """按 offset 分页读取 Chroma，直到取空。

    用 `limit/offset` 而不是一次全量：28344 条一次拉回会把
    内存与 HTTP 响应体都撑大（迁移前 reindex 就是因为这个改成 500/批）。
    """
    offset = max(0, start_offset)
    while True:
        payload = collection.get(
            limit=batch_size,
            offset=offset,
            include=["embeddings", "documents", "metadatas"],
        )
        ids = list(payload.get("ids") or [])
        if not ids:
            return
        yield build_batch(payload)
        offset += len(ids)


def compare_ids(expected: Sequence[str], actual: Sequence[str]) -> tuple[list[str], list[str]]:
    """返回 (缺失, 孤儿)，各截断到 20 条样本。"""
    expected_set = {str(i) for i in expected}
    actual_set = {str(i) for i in actual}
    missing = sorted(expected_set - actual_set)[:_ID_SAMPLE_LIMIT]
    orphan = sorted(actual_set - expected_set)[:_ID_SAMPLE_LIMIT]
    return missing, orphan


def overlap_ratio(expected: Sequence[str], actual: Sequence[str]) -> float:
    """top-k 重叠率：两边都没有结果时按 1.0 处理（无法证伪就不判失败）。"""
    expected_set = {str(i) for i in expected}
    actual_set = {str(i) for i in actual}
    if not expected_set and not actual_set:
        return 1.0
    if not expected_set or not actual_set:
        return 0.0
    return len(expected_set & actual_set) / len(expected_set)


# ===== CLI =====


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_vectors_to_pgvector",
        description="Chroma → pgvector 存量向量迁移（保 id、不重算嵌入）",
    )
    parser.add_argument("--collection", default="knowledge_base", help="collection 名")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH_SIZE)
    parser.add_argument("--start-offset", type=int, default=0, help="从 Chroma 的该 offset 续跑")
    parser.add_argument("--limit", type=int, default=0, help="只搬前 N 条（0=全部，演练用）")
    parser.add_argument("--dimension", type=int, default=0, help="向量维度（0=从数据推断）")
    parser.add_argument("--dsn", default="", help="pgvector DSN（默认取配置）")
    parser.add_argument("--table", default="", help="目标表名（默认取配置）")
    parser.add_argument("--chroma-host", default="", help="Chroma 服务地址（默认取配置）")
    parser.add_argument("--chroma-port", type=int, default=0, help="Chroma 端口（默认取配置）")
    parser.add_argument("--chroma-path", default="", help="Chroma 本地持久化目录（不用 HTTP 时）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    parser.add_argument("--verify-only", action="store_true", help="只校验，不写库")
    parser.add_argument("--skip-verify", action="store_true", help="写完不校验（不推荐）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    return parser.parse_args(argv)


def _build_chroma(args: argparse.Namespace) -> Any:
    import chromadb

    if args.chroma_host:
        return chromadb.HttpClient(host=args.chroma_host, port=args.chroma_port or 8000)
    if args.chroma_path:
        return chromadb.PersistentClient(path=args.chroma_path)

    from app.core.config import get_settings

    rag = get_settings().rag
    host = (getattr(rag, "chroma_host", "") or "").strip()
    if host:
        return chromadb.HttpClient(host=host, port=int(getattr(rag, "chroma_port", 8000)))
    raise SystemExit(
        "未指定 Chroma 位置：请传 --chroma-host/--chroma-port 或 --chroma-path，"
        "或在配置里设置 RAG__CHROMA_HOST"
    )


def _build_backend(args: argparse.Namespace, dimension: int) -> Any:
    from app.knowledge.services.vector_backends.pgvector_backend import PgVectorBackend

    return PgVectorBackend(
        embedding_dimension=dimension or _DEFAULT_DIMENSION,
        collection_name=args.collection,
        dsn=args.dsn or None,
        table=args.table or None,
    )


def _verify(
    collection: Any,
    backend: Any,
    *,
    chroma_ids: list[str],
    sample_size: int = 5,
    top_k: int = 10,
) -> VerifyReport:
    """数量 + id 集合 + 样例 top-k 重叠率三重校验。"""
    report = VerifyReport(
        chroma_count=len(chroma_ids),
        pgvector_count=int(backend.count()),
    )
    report.missing_ids, report.orphan_ids = compare_ids(chroma_ids, backend.list_ids())

    if chroma_ids:
        sample = chroma_ids[:sample_size]
        payload = collection.get(ids=sample, include=["embeddings"])
        ratios: list[float] = []
        # ⚠️ chromadb 返回的是 **numpy 数组**：`x or []` 会触发
        # `truth value of an array ... is ambiguous`（2026-11-03 真机踩到）。
        # 没拿到就整体跳过，不做布尔转换。
        embeddings = payload.get("embeddings")
        if embeddings is None:
            return report
        for index, _chunk_id in enumerate(payload.get("ids") or []):
            if index >= len(embeddings) or embeddings[index] is None:
                continue
            vector = _to_float_list(embeddings[index])
            expected = collection.query(query_embeddings=[vector], n_results=top_k)
            expected_ids = (expected.get("ids") or [[]])[0]
            actual_ids = [hit.chunk_id for hit in backend.query(vector, top_k=top_k)]
            ratios.append(overlap_ratio(expected_ids, actual_ids))
        report.overlap = round(sum(ratios) / len(ratios), 4) if ratios else None
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    collection = _build_chroma(args).get_or_create_collection(
        name=args.collection, metadata={"hnsw:space": "cosine"}
    )
    chroma_ids = sorted(str(i) for i in (collection.get(include=[]).get("ids") or []))

    if args.dry_run:
        payload = {
            "collection": args.collection,
            "chroma_count": len(chroma_ids),
            "sample_ids": chroma_ids[:5],
            "batch_size": args.batch_size,
            "dry_run": True,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else payload)
        return 0

    # 维度：优先参数，其次从首条向量推断（避免表维度与嵌入维度不一致）
    dimension = args.dimension
    if not dimension and chroma_ids:
        probe = collection.get(ids=[chroma_ids[0]], include=["embeddings"])
        embeddings = probe.get("embeddings")
        if embeddings is not None and len(embeddings):
            dimension = len(_to_float_list(embeddings[0]))
    backend = _build_backend(args, dimension)

    if not args.verify_only:
        imported = 0
        skipped = 0
        for batch in iter_batches(
            collection, batch_size=args.batch_size, start_offset=args.start_offset
        ):
            if args.limit and imported >= args.limit:
                break
            if args.limit:
                remaining = args.limit - imported
                batch = Batch(
                    ids=batch.ids[:remaining],
                    vectors=batch.vectors[:remaining],
                    documents=batch.documents[:remaining],
                    metadatas=batch.metadatas[:remaining],
                    skipped=batch.skipped,
                )
            backend.upsert(
                ids=batch.ids,
                vectors=batch.vectors,
                documents=batch.documents,
                metadatas=batch.metadatas,
            )
            imported += len(batch)
            skipped += batch.skipped
            print(f"imported={imported} skipped={skipped}", file=sys.stderr)
        print(f"完成：写入 {imported} 条，跳过 {skipped} 条", file=sys.stderr)

    if args.skip_verify:
        return 0

    report = _verify(collection, backend, chroma_ids=chroma_ids)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) if args.json else report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
