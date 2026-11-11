"""按 DocChunk 重建向量索引（2026-10-22 向量一致性 Task 3）。

```bash
python -m scripts.reindex_vectors --dry-run          # 先看规模
python -m scripts.reindex_vectors                    # 全量重建
python -m scripts.reindex_vectors --user-id <uuid>   # 只重建某用户
python -m scripts.reindex_vectors --reconcile        # 只补缺项并删除孤儿
```

## 为什么不重新解析文件

`DocChunk.content` 已经在库里，直接向量化即可。重新解析 PDF 既慢
（28344 条要几分钟）又可能因文件被删而失败。

## 幂等性

同 `chunk_id` 重复写入是 **upsert**（覆盖同 id），所以可以安全重跑。
中断后再跑一次即可补齐。

## 耗时预期

28344 条 / 500 条每批 ≈ 57 批。**每批只有 1 次 upsert + 1 次批量 UPDATE**
（2026-11-04 之前是每 chunk 一条 UPDATE，在远程库上要 3.8 小时）。
Mock 在 ECS 同机库实测 86 秒；真实 BGE 在 2C 单线程样本 32 条为 54.7 秒
（含加载），全量可能需数小时。生产切换用影子表后台重建，切换前用
`--reconcile` 只补新增并删除孤儿，不再全量重算。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.reindex_vectors",
        description="按 DocChunk 重建向量索引（保持 chunk_id 不变）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="只对账缺项与孤儿，不重算已同步向量",
    )
    parser.add_argument("--batch-size", type=int, default=500, help="每批条数（默认 500）")
    parser.add_argument("--user-id", default=None, help="只重建指定用户（UUID）")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只重建前 N 条（0=全部）；远程库上先用小样本演练链路",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)
    if args.reconcile and args.limit:
        parser.error("--reconcile 不能与 --limit 同时使用")
    if args.reconcile and args.user_id:
        parser.error("--reconcile 不能与 --user-id 同时使用")
    return args


async def _main() -> int:
    args = _parse_args()

    from app.db.session import AsyncSessionLocal
    from app.knowledge.services.factory import get_index_manager, init_kb
    from app.knowledge.services.reindex import reindex_from_chunks

    user_id = None
    if args.user_id:
        import uuid

        try:
            user_id = uuid.UUID(args.user_id)
        except ValueError as exc:
            raise SystemExit(f"--user-id 不是合法 UUID：{args.user_id}") from exc

    init_kb()
    # 向量库本体在 IndexManager 里（`get_kb()` 是门面，没有写入接口）
    vector_store = get_index_manager()._vector_store
    async with AsyncSessionLocal() as session:
        result = await reindex_from_chunks(
            session=session,
            vector_store=vector_store,
            batch_size=args.batch_size,
            user_id=user_id,
            dry_run=args.dry_run,
            limit=args.limit,
            reconcile=args.reconcile,
        )

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"计划重建：{result.planned} 条")
        if args.dry_run:
            print("--dry-run：未写入任何向量。")
        else:
            print(f"实际写入：{result.indexed} 条（耗时 {result.elapsed_ms} ms）")
            if result.failed_batches:
                print(f"失败批次：{result.failed_batches}")
                for message in result.errors:
                    print(f"  - {message}")
            else:
                print("全部成功。")
        print()
        print("提示：重建后请运行 python -m scripts.check_kb_consistency 验证。")

    return 0 if result.failed_batches == 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
