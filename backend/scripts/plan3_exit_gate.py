"""Plan 3 Exit Gate：离线真实模型验收脚本。

在**一次真实模型运行**上验证 Plan 3 的三条硬性要求，并输出 JSON 证据：

1. 引用全可回溯：每个节点正文里的 `[E{n}]` 都能解析到该节点的证据条目，且每条证据的
   `chunk_id` 都能在数据库中查到对应的 `DocChunk`（属于同一 owner）；
2. 重启恢复保留已完成节点：新建容器调用 `TaskExecution.recover()` 后，任务状态与
   节点尝试次数不变，不产生新事件；
3. 产物哈希：Markdown 与 DOCX 均存在，重算 sha256 与任务记录一致。

运行前提（与 `scripts/plan2_exit_gate.py` 相同）：
    export APP_ENV=test
    export DATABASE__URL="postgresql+asyncpg://..."
    export REDIS__URL="redis://..."
    export JWT_SECRET_KEY="..."
    export RAG_EMBEDDING_MOCK=true RAG_RERANKER_MOCK=true
    python scripts/plan3_exit_gate.py --max-documents 10

脚本自身不做 LLM 调用以外的网络访问（语料为本地冻结清单，`--offline`）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

DEMO_DIR = BACKEND_DIR / "demo_data" / "literature_review"
MANIFEST_PATH = DEMO_DIR / "manifest.json"

MARKER_RE = re.compile(r"\[E(\d+)\]")


def extract_markers(content: str) -> list[int]:
    """提取正文中的 `[E{n}]` 编号。"""
    return [int(number) for number in MARKER_RE.findall(content or "")]


def unresolved_markers(content: str, evidence_count: int) -> list[int]:
    """返回越界（无法解析）的标记编号。"""
    return sorted({n for n in extract_markers(content) if n < 1 or n > evidence_count})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_cli_summary(stdout: str) -> dict[str, Any] | None:
    """从 CLI 输出中解析最后的 JSON 摘要（容忍前置日志）。"""
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "task_id" in candidate and "status" in candidate:
            return candidate
    return None


def run_demo_cli(args: argparse.Namespace) -> tuple[int, dict[str, Any] | None, str]:
    """以子进程方式运行离线演示（验收入口就是 CLI 本身）。"""
    command = [
        sys.executable,
        "-m",
        "app.cli",
        "run-literature-demo",
        "--offline",
        "--max-documents",
        str(args.max_documents),
        "--username",
        args.username,
        "--timeout",
        str(args.timeout),
        "--data-root",
        args.data_root,
    ]
    completed = subprocess.run(
        command,
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        timeout=args.timeout + 120,
        env=os.environ.copy(),
        check=False,
    )
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    return completed.returncode, parse_cli_summary(completed.stdout or ""), output


async def verify_task(task_id: str) -> dict[str, Any]:
    """校验引用可回溯、产物哈希与重启恢复。"""
    from sqlalchemy import select

    from app.db.models.knowledge import DocChunk, KnowledgeDoc
    from app.db.models.task import Subtask, Task
    from app.db.session import AsyncSessionLocal

    report: dict[str, Any] = {"task_id": task_id, "citations": [], "artifacts": []}

    async with AsyncSessionLocal() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task is None:
            raise AssertionError(f"任务不存在: {task_id}")
        report["status"] = task.status
        report["artifact_count"] = len(task.artifacts or [])

        subtasks = (
            (await session.execute(select(Subtask).where(Subtask.task_id == task.id)))
            .scalars()
            .all()
        )
        attempts_before = {sub.node_id: sub.retry_count for sub in subtasks}

        # 1) 引用可回溯
        stored_chunks = set(
            (
                await session.execute(
                    select(DocChunk.id)
                    .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
                    .where(KnowledgeDoc.user_id == task.user_id)
                )
            )
            .scalars()
            .all()
        )
        for sub in subtasks:
            output = (sub.output_data or {}).get("output") or {}
            execution = output.get("execution_result") or {}
            content = str(execution.get("content", "") or output.get("result", ""))
            cited = execution.get("evidence") or []
            # 标记编号对应「节点证据池」（校验器同样按池校验）；execution.evidence 只是被引用子集
            pool = output.get("evidence_pool") or cited
            unresolved = unresolved_markers(content, len(pool))
            missing = [
                item.get("chunk_id")
                for item in cited
                if item.get("chunk_id") not in {str(chunk) for chunk in stored_chunks}
            ]
            report["citations"].append(
                {
                    "node_id": sub.node_id,
                    "status": sub.status,
                    "markers": len(extract_markers(content)),
                    "evidence_pool": len(pool),
                    "evidence_cited": len(cited),
                    "unresolved": unresolved,
                    "missing_chunks": missing,
                }
            )
            if unresolved or missing:
                raise AssertionError(
                    f"节点 {sub.node_id} 引用不可回溯: 越界={unresolved} 缺块={missing}"
                )

        # 2) 产物哈希
        from app.core.config import get_settings
        from app.tools.literature.exporter import LiteratureExporter

        exporter = LiteratureExporter(get_settings().app.artifact_root)
        for item in task.artifacts or []:
            artifact_id = str(item.get("artifact_id"))
            try:
                path, download_name = exporter.resolve_for_user(task.user_id, task.id, artifact_id)
            except Exception as exc:
                raise AssertionError(f"产物 {artifact_id} 无法解析: {exc}") from exc
            digest = sha256_file(path)
            report["artifacts"].append(
                {
                    "kind": item.get("kind"),
                    "artifact_id": artifact_id,
                    "download_name": download_name,
                    "sha256_recorded": item.get("sha256"),
                    "sha256_actual": digest,
                    "size": path.stat().st_size,
                }
            )
            if digest != item.get("sha256"):
                raise AssertionError(f"产物哈希不一致: {artifact_id}")
        if len(report["artifacts"]) < 2:
            raise AssertionError("产物不足（需要 Markdown + DOCX）")

        report["attempts_before"] = attempts_before
        report["events_before"] = await _event_count(task.id)

    # 3) 重启恢复：新容器 recover() 后状态与尝试次数不变
    from app.orchestration.runtime import RuntimeContainer
    from app.orchestration.services.task_execution import TaskExecution

    container = RuntimeContainer()
    await container.start()
    try:
        await TaskExecution(container).recover()
    finally:
        await container.close()
    await asyncio.sleep(1.0)

    async with AsyncSessionLocal() as session:
        task_after = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        subtasks_after = (
            (await session.execute(select(Subtask).where(Subtask.task_id == task_id)))
            .scalars()
            .all()
        )
        attempts_after = {sub.node_id: sub.retry_count for sub in subtasks_after}
        events_after = await _event_count(task_id)

    report["status_after_recovery"] = task_after.status
    report["attempts_after"] = attempts_after
    report["events_after"] = events_after
    if task_after.status != report["status"]:
        raise AssertionError("重启恢复改变了任务状态")
    if attempts_after != attempts_before:
        raise AssertionError("重启恢复重跑了已完成节点")
    if events_after != report["events_before"]:
        raise AssertionError("重启恢复产生了新事件")
    return report


async def _event_count(task_id: str) -> int:
    from sqlalchemy import func, select

    from app.db.models.task import ExecutionLog
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ExecutionLog)
                    .where(ExecutionLog.task_id == task_id)
                )
            ).scalar_one()
        )


def verify_corpus(manifest_path: Path) -> dict[str, Any]:
    """校验冻结语料：10 条记录、哈希一致。"""
    from app.tools.literature.offline import load_offline_corpus

    corpus = load_offline_corpus(manifest_path)
    corpus.verify_hashes()
    if len(corpus.records) != 10:
        raise AssertionError(f"语料记录数不是 10：{len(corpus.records)}")
    return {"records": len(corpus.records), "manifest": str(manifest_path)}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"ok": False, "phases": [], "failures": []}

    # 上传/产物根目录：与 CLI 子进程保持一致（容器内默认 /app/data/* 在宿主机不可写）
    from app.core.config import get_settings

    data_root = Path(args.data_root).resolve()
    settings = get_settings()
    settings.app.upload_root = str(data_root / "uploads")
    settings.app.artifact_root = str(data_root / "artifacts")
    report["data_root"] = str(data_root)

    def phase(name: str) -> dict[str, Any]:
        entry: dict[str, Any] = {"phase": name}
        report["phases"].append(entry)
        return entry

    # 阶段 1：语料
    entry = phase("corpus")
    try:
        entry.update(verify_corpus(Path(args.manifest)))
        entry["ok"] = True
    except Exception as exc:
        entry["ok"] = False
        entry["error"] = str(exc)
        report["failures"].append(f"corpus: {exc}")
        return report

    # 阶段 2：离线真实模型演示（CLI 即验收入口）
    entry = phase("offline_demo")
    if args.task_id:
        entry.update({"task_id": args.task_id, "skipped": True, "ok": True})
        task_id = args.task_id
    else:
        code, summary, output = run_demo_cli(args)
        entry["cli_exit_code"] = code
        entry["summary"] = summary
        entry["ok"] = code == 0 and summary is not None and summary.get("status") == "completed"
        if not entry["ok"]:
            entry["error"] = "CLI 未成功完成"
            report["failures"].append("offline_demo: CLI 未成功完成")
            report["cli_output_tail"] = output[-2000:]
            return report
        task_id = str(summary["task_id"])
        entry["task_id"] = task_id

    # 阶段 3：引用/产物/恢复
    entry = phase("verify_task")
    try:
        entry.update(await verify_task(task_id))
        entry["ok"] = True
    except Exception as exc:
        entry["ok"] = False
        entry["error"] = str(exc)
        report["failures"].append(f"verify_task: {exc}")
        return report

    report["ok"] = not report["failures"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan 3 Exit Gate（离线真实模型验收）")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--max-documents", type=int, default=10)
    parser.add_argument("--username", default="literature-gate")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--task-id", default=None, help="复用既有任务，只做校验（不跑演示）")
    parser.add_argument(
        "--data-root",
        default=str(BACKEND_DIR / ".runtime"),
        help="宿主机可写的数据根目录（上传与产物），需与 CLI 一致",
    )
    parser.add_argument("--report", default=None, help="把 JSON 报告写入该路径")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.report:
        Path(args.report).write_text(payload, encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
