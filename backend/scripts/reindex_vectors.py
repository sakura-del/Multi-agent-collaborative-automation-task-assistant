"""按 DocChunk 重建向量索引（2026-10-22 向量一致性 Task 3）。

```bash
python -m scripts.reindex_vectors --dry-run          # 先看规模
python -m scripts.reindex_vectors                    # 全量重建
python -m scripts.reindex_vectors --user-id <uuid>   # 只重建某用户
```

## 为什么不重新解析文件

`DocChunk.content` 已经在库里，直接向量化即可。重新解析 PDF 既慢
（28344 条要几分钟）又可能因文件被删而失败。

## 幂等性

同 `chunk_id` 重复写入是 **upsert**（覆盖同 id），所以可以安全重跑。
中断后再跑一次即可补齐。

## 耗时预期

28344 条 / 500 条每批 ≈ 57 批。Mock embedding 下每批约 1-3 秒，
总计 **1-3 分钟**；真实模型会慢 5-10 倍。
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
    parser.add_argument("--batch-size", type=int, default=500, help="每批条数（默认 500）")
    parser.add_argument("--user-id", default=None, help="只重建指定用户（UUID）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    return parser.parse_args(argv)


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
        )

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"计划重建：{result.planned} 条")
        if args.dry_run:
            print("--dry-run：未写入任何向量。")
        else:
            print(f"实际写入：{result.indexed} 条")
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
