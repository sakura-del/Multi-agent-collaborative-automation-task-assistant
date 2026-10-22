"""实验矩阵运行脚本（2026-10-22 实验矩阵 Task 3）。

## 为什么需要独立脚本而不是扩展 CLI

`python -m evaluation.cli run --adapter live` 需要**装配真实依赖**：
数据库会话、模型客户端、知识检索、演示用户。若塞进 CLI：

- `--help` 也会连带初始化数据库（慢且可能失败）；
- CI 跑 `validate` 时不该碰数据库。

所以 CLI 保持轻量，真实运行走本脚本。

## 用法

```bash
cd backend
# 小样（1 主题 × 4 画像 × 2 重复 = 8 runs）
python -m scripts.run_experiments --topic ai-rag-eval --repetitions 2

# 全量（15 主题 × 4 画像 × 2 重复 = 120 runs）
python -m scripts.run_experiments --profiles all --repetitions 2
```

## 前置条件

1. **数据库可达**（`DATABASE__URL`）；
2. **语料已入库**（`python -m evaluation.prepare_corpora` 或网页导入）；
3. **模型密钥有效**（`.env` 的 `LLM__API_KEY`）；
4. **演示用户存在**（默认 `--user demo`）。

任一缺失，脚本会**在启动时**给出可读错误，而不是跑到第 40 个 run 才炸。

## 为什么强制串行

实验指标（token 用量、耗时、并发度）必须可比。并行跑会让不同画像互相争抢
CPU/模型配额，耗时数据失去意义。`max_concurrency` 固定 1。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

# 允许 `python -m scripts.run_experiments` 与直接执行两种方式
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.run_experiments",
        description="跑实验矩阵（真实模型 + 真实数据库）",
    )
    parser.add_argument(
        "--profiles",
        default="all",
        help="all 或逗号分隔（baseline_a,baseline_b,baseline_c,full）",
    )
    parser.add_argument("--topic", default=None, help="只跑指定 topic_id（小样验证用）")
    parser.add_argument("--repetitions", type=int, default=2, help="每个 (画像,主题) 重复次数")
    parser.add_argument("--user", default="demo", help="任务归属账号（默认 demo）")
    parser.add_argument(
        "--output-root",
        default=None,
        help="结果目录（默认 evaluation/runs）",
    )
    parser.add_argument("--run-set-id", default=None, help="续跑时指定已有 run-set")
    parser.add_argument("--retry-failed", action="store_true", help="只重跑失败/未完成的 run")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="轮询间隔秒（默认 5）")
    parser.add_argument("--timeout", type=float, default=1800.0, help="单 run 超时秒（默认 1800）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只装配依赖并打印将要跑的数量，不真正执行",
    )
    return parser.parse_args()


async def _lookup_user(session_factory: Any, username: str) -> Any:
    """按用户名查用户（实验任务需要 owner）。"""
    from sqlalchemy import select

    from app.db.models.user import User

    async with session_factory() as session:
        return await session.scalar(select(User).where(User.username == username))


async def _preflight(session_factory: Any, username: str) -> Any:
    """启动前检查：数据库、用户、语料、模型密钥。

    一次性报出**所有**缺失项，而不是修一个再发现下一个。
    """
    problems: list[str] = []

    # ① 数据库 + 演示用户
    user = None
    try:
        user = await _lookup_user(session_factory, username)
        if user is None:
            problems.append(
                f"演示用户 {username!r} 不存在 → 请先在网页注册该账号，或 --user 指定已有账号"
            )
    except Exception as exc:
        problems.append(f"数据库不可达（{type(exc).__name__}: {exc}）→ 检查 DATABASE__URL")

    # ② 语料
    try:
        from sqlalchemy import func, select

        from app.db.models.knowledge import KnowledgeDoc

        async with session_factory() as session:
            doc_count = await session.scalar(select(func.count()).select_from(KnowledgeDoc))
        if not doc_count:
            problems.append(
                "知识库为空 → 请先导入语料（python -m evaluation.prepare_corpora 或网页导入）"
            )
    except Exception as exc:
        problems.append(f"无法检查知识库（{type(exc).__name__}: {exc}）")

    # ③ 模型密钥
    try:
        from app.core.config import get_settings

        llm = get_settings().llm
        # 字段名是 `openai_api_key` / `deepseek_api_key` 等（不是统一的 api_key）。
        # 任一 provider 有 key 即可 —— 实际用哪个由 default_provider + fallback_chain 决定。
        provider_keys = {
            "openai": getattr(llm, "openai_api_key", None),
            "glm": getattr(llm, "glm_api_key", None),
            "qwen": getattr(llm, "qwen_api_key", None),
            "deepseek": getattr(llm, "deepseek_api_key", None),
        }
        if not any(provider_keys.values()):
            problems.append(
                "LLM 密钥全部缺失 → 在 .env 配置 LLM__OPENAI_API_KEY"
                "（或 LLM__DEEPSEEK_API_KEY 作为保底）"
            )
    except Exception as exc:
        problems.append(f"无法读取配置（{type(exc).__name__}: {exc}）")

    if problems:
        raise SystemExit(
            "启动前检查未通过：\n"
            + "\n".join(f"  - {item}" for item in problems)
            + "\n\n修正后重试。"
        )
    return user


async def _main() -> int:
    args = _parse_args()

    from app.db.session import AsyncSessionLocal
    from app.knowledge.services.search_provider import KnowledgeSearchProvider
    from app.orchestration.runtime import RuntimeContainer
    from app.orchestration.services.task_execution import TaskExecution
    from evaluation.cli import (
        _load_selected_profiles,
        _load_selected_topics,
        build_live_executor,
    )
    from evaluation.runner import ExperimentRunner, build_matrix

    user = await _preflight(AsyncSessionLocal, args.user)
    profiles = _load_selected_profiles(args.profiles)
    topics = _load_selected_topics(args.topic)
    specs = build_matrix(profiles, topics, args.repetitions)

    print(
        f"准备就绪：{len(specs)} runs"
        f"（{len(profiles)} 画像 × {len(topics)} 主题 × {args.repetitions} 次）"
    )
    print(f"任务归属：{args.user}（{user.id}）")
    if args.dry_run:
        print("--dry-run：不执行，退出。")
        return 0

    # ⚠️ 必须先 `init_kb()`：`KnowledgeSearchProvider` 依赖全局知识库单例，
    # 未初始化时任何检索都会抛「知识库未初始化：请先在应用生命周期中调用 init_kb()」。
    #
    # 实测后果（pilot-8 首跑）：`no_rag` 画像不检索所以能过，
    # `vector` / `hybrid_rerank` 画像全部重试耗尽转 waiting_human ——
    # **恰好只有需要检索的两组失败**，很容易误判成「画像差异」。
    # 应用侧在 lifespan 里做同样的事（app/main.py:58），本脚本必须自己补上。
    from app.knowledge.services.factory import init_kb

    init_kb()

    container = RuntimeContainer(knowledge_query=KnowledgeSearchProvider())
    await container.start()
    try:
        execution = TaskExecution(container)
        executor = build_live_executor(
            execution=execution,
            user_id=user.id,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
        )

        output_root = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else (BACKEND_DIR / "evaluation" / "runs")
        )
        runner = ExperimentRunner(
            executor,
            output_root=output_root,
            run_set_id=args.run_set_id,
            logger=lambda message: print(f"  {message}", flush=True),
        )
        print(f"run-set {runner.run_set_id}")

        def _progress(index: int, total: int, result: str) -> None:
            print(f"[{index}/{total}] {result}", flush=True)

        report = await runner.run(specs, on_progress=_progress, retry_failed=args.retry_failed)
        print(report.as_dict())
        return 0 if report.ok else 1
    finally:
        await container.close()


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
