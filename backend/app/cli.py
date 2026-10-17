"""文献综述离线演示 CLI（Plan 3 Task 6）。

薄封装：只负责装配依赖（知识库、离线清单源、RuntimeContainer）并调用
`TaskExecution` 的公开接口，不复制任何工作流逻辑。

    python -m app.cli run-literature-demo --offline --max-documents 10
    python -m app.cli run-literature-demo --offline --check     # 只做清单与 LLM 连通性检查

离线运行前提：先执行 `python scripts/prepare_demo_corpus.py` 生成 10 篇冻结语料。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo_data" / "literature_review"
MANIFEST_PATH = DEMO_DIR / "manifest.json"
QUERY_PATH = DEMO_DIR / "query.json"
DEFAULT_USERNAME = "literature-demo"
TERMINAL_STATUSES = {"completed", "waiting_human", "failed", "cancelled"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="多智能体协作助手 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("run-literature-demo", help="运行离线文献综述演示任务")
    demo.add_argument("--offline", action="store_true", help="使用冻结清单，不访问 arXiv")
    demo.add_argument("--max-documents", type=int, default=10, help="入库与引用文献数")
    demo.add_argument("--topic", default=None, help="覆盖冻结查询的主题")
    demo.add_argument("--username", default=DEFAULT_USERNAME, help="演示账号")
    demo.add_argument("--timeout", type=float, default=900.0, help="等待任务完成的秒数")
    demo.add_argument("--check", action="store_true", help="只检查清单与 LLM 连通性")
    demo.add_argument(
        "--mock-embedding",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="知识库使用确定性 Mock 向量（默认开启，保证离线可复现）",
    )
    return parser


async def run_literature_demo(args: argparse.Namespace) -> int:
    if not MANIFEST_PATH.is_file():
        print(
            f"缺少离线语料清单 {MANIFEST_PATH}；请先运行 "
            "`python scripts/prepare_demo_corpus.py`",
            file=sys.stderr,
        )
        return 2

    from app.knowledge.services.factory import init_kb
    from app.orchestration.runtime import RuntimeContainer
    from app.orchestration.services.task_execution import TaskExecution
    from app.tools.literature.offline import ManifestLiteratureSource

    frozen = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    topic = args.topic or str(frozen["topic"])

    source = ManifestLiteratureSource(MANIFEST_PATH)
    source.corpus.verify_hashes()
    print(f"离线语料: {len(source.records)} 篇（哈希校验通过，主题: {topic}）")

    init_kb(
        force_mock_embedding=args.mock_embedding,
        force_mock_reranker=True,
        chroma_persist_dir="",
    )

    if args.check:
        return await _connectivity_check()

    from app.orchestration.contracts import LiteratureReviewRequest, TaskStatus

    user_id = await _ensure_user(args.username)
    await _ingest_corpus(user_id, source, max_documents=args.max_documents)

    container = RuntimeContainer(literature_source=source)
    await container.start()
    try:
        execution = TaskExecution(container)
        task_id = await execution.submit(
            user_id,
            LiteratureReviewRequest(
                topic=topic,
                keywords=[str(frozen["query"])],
                max_documents=args.max_documents,
                offline_dataset=True,
            ),
        )
        print(f"任务已创建: {task_id}")

        snapshot = await _wait_for(
            execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}, 300.0
        )
        await execution.approve_plan(user_id, task_id, snapshot.revision)
        print("规划已确认，开始执行…")

        final = await _wait_for(execution, user_id, task_id, TERMINAL_STATUSES, args.timeout)
        events = await execution.events_after(user_id, task_id, 0)
        report = final.review_report or {}
        print(f"最终状态: {final.status}；事件数: {len(events)}")
        summary = {
            "task_id": str(task_id),
            "status": final.status,
            "artifacts": [
                {
                    "kind": item.get("kind"),
                    "artifact_id": item.get("artifact_id"),
                    "sha256": item.get("sha256"),
                    "size": item.get("size"),
                }
                for item in (final.artifacts or [])
            ],
            "citation_traceability": report.get("scores", {}).get("citation_traceability"),
            "documents": report.get("stats", {}).get("documents"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if final.status == TaskStatus.COMPLETED.value else 1
    finally:
        await container.close()


async def _connectivity_check() -> int:
    """LLM 连通性验证（Plan 3 §6 前置要求：普通 chat 与流式各一次）。"""
    from app.llm.factory import get_llm_manager
    from app.llm.models import Message, MessageRole

    manager = get_llm_manager()
    try:
        llm = manager.get_primary()
    except Exception as exc:
        print(f"LLM 初始化失败: {exc}", file=sys.stderr)
        return 3

    try:
        response = await manager.chat(
            [Message(role=MessageRole.USER, content="ping")], max_tokens=8
        )
    except Exception as exc:
        print(f"LLM 连通性验证失败: {exc}", file=sys.stderr)
        return 3

    streams = 0
    try:
        async for _chunk in manager.stream_chat(
            [Message(role=MessageRole.USER, content="ping")], max_tokens=8
        ):
            streams += 1
    except Exception as exc:
        print(f"LLM 流式验证失败: {exc}", file=sys.stderr)
        return 3

    preview = (response.content or "").strip().replace("\n", " ")[:80]
    print(
        f"LLM 连通性 OK: {getattr(llm, 'model', 'unknown')} -> {preview!r}"
        f"（流式分片 {streams} 个）"
    )
    return 0


async def _ensure_user(username: str) -> uuid.UUID:
    """按用户名取或建演示账号（幂等）。"""
    from sqlalchemy import select

    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash="cli-demo-no-login",
            role="user",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _ingest_corpus(user_id: uuid.UUID, source, *, max_documents: int) -> None:
    """把语料 PDF 入库到演示账号知识库（已入库则跳过）。"""
    from sqlalchemy import select
    from starlette.datastructures import UploadFile

    from app.db.models.knowledge import KnowledgeDoc
    from app.db.session import AsyncSessionLocal
    from app.knowledge.models.document import ChunkStrategy
    from app.knowledge.services.document_service import KnowledgeDocumentService
    from app.knowledge.services.factory import get_index_manager, get_kb, get_storage

    async with AsyncSessionLocal() as session:
        ready = (
            (
                await session.execute(
                    select(KnowledgeDoc.id).where(
                        KnowledgeDoc.user_id == user_id,
                        KnowledgeDoc.index_status == "ready",
                    )
                )
            )
            .scalars()
            .all()
        )
    if len(ready) >= max_documents:
        print(f"知识库已含 {len(ready)} 篇文档，跳过入库")
        return

    for record in source.records[:max_documents]:
        path = source.pdf_path(record.source_id)
        async with AsyncSessionLocal() as session:
            service = KnowledgeDocumentService(
                session,
                storage=get_storage(),
                index_manager=get_index_manager(),
                kb=get_kb(),
            )
            with path.open("rb") as handle:
                upload = UploadFile(file=handle, filename=f"{record.source_id}.pdf")
                await service.ingest(
                    user_id,
                    upload,
                    title=record.title,
                    strategy=ChunkStrategy.RECURSIVE,
                )
        print(f"入库 {record.source_id}")


async def _wait_for(execution, user_id, task_id, statuses: set[str], timeout: float):

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await execution.get_snapshot(user_id, task_id)
        if last.status in statuses:
            return last
        await asyncio.sleep(1.0)
    status = last.status if last else "unknown"
    raise TimeoutError(f"等待任务状态 {statuses} 超时，当前 {status}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run-literature-demo":  # pragma: no cover - argparse 保证
        return 2
    if not args.offline:
        print("当前仅支持 --offline（在线模式走 HTTP 任务接口）", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run_literature_demo(args))
    except TimeoutError as exc:
        print(f"演示超时: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
