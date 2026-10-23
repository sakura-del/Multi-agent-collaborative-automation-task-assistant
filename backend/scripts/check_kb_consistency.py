"""向量库一致性检查与重建 CLI（2026-10-22 向量一致性）。

## 两个命令

```bash
# 检查（只读，可随时在线上跑）
python -m scripts.check_kb_consistency

# 重建（保持 chunk_id 不变，幂等 upsert）
python -m scripts.reindex_vectors --dry-run   # 先看规模
python -m scripts.reindex_vectors             # 实际重建
```

## 为什么要独立脚本而不是 API

重建 28344 条向量需要几分钟到几十分钟。做成 HTTP 端点会：

- 阻塞一个 worker；
- 超时后客户端不知道是否成功；
- 无法在维护窗口里从 shell 操作。

运维脚本可以 nohup 后台跑、可以看日志、可以中断重跑（幂等）。

## 前置条件

`init_kb()`（建向量库客户端）。本脚本自己调，不依赖 Web 进程。
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
        prog="scripts.check_kb_consistency",
        description="检查向量库与数据库是否一致（只读）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（便于脚本判断）")
    parser.add_argument(
        "--skip-ids",
        action="store_true",
        help="只比数量（快，但会漏检「数量相等、id 全不同」的漂移）",
    )
    return parser.parse_args(argv)


async def _main() -> int:
    args = _parse_args()

    from app.db.session import AsyncSessionLocal
    from app.knowledge.services.consistency import check_vector_consistency
    from app.knowledge.services.factory import get_index_manager, init_kb

    init_kb()
    # 注意：`get_kb()` 返回 KnowledgeBase 门面，**没有** size 属性。
    # 向量库本体在 IndexManager 里（与 get_stats() 同源）。
    index_manager = get_index_manager()
    vector_store = index_manager._vector_store
    async with AsyncSessionLocal() as session:
        report = await check_vector_consistency(
            session=session, vector_store=vector_store, verify_ids=not args.skip_ids
        )

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"文档（ready）：{report.document_count}")
        print(f"分块（期望向量数）：{report.expected_vectors}")
        print(f"向量库实际条数：{report.actual_vectors}")
        if report.consistent:
            print("结论：**一致**")
        else:
            print(f"结论：**不一致**（缺 {report.missing_vectors}，孤儿 {report.orphan_vectors}）")
            if report.ids_verified and report.missing_ids:
                print(f"缺失 id 样本（前 {len(report.missing_ids)}）：{report.missing_ids[:5]}")
            if report.ids_verified and report.orphan_ids:
                print(f"孤儿 id 样本（前 {len(report.orphan_ids)}）：{report.orphan_ids[:5]}")
            print()
            print(report.hint)

    return 0 if report.consistent else 1


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
