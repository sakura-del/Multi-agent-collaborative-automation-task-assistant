"""实验 runner（Plan 5 Task 6）——resume-safe 的 120-run 矩阵执行。

设计要点：
- 每次运行一个目录：`runs/<run-set-id>/<profile>/<topic-id>/<repetition>/`，含
  `config.json` / `task.json` / `events.jsonl` / `result.md` / `review.json` /
  `metrics.json`；**全部文件齐全且任务状态终态**才视为完成，resume 时跳过；
- 写入是原子的：先写 `.tmp` 再 `replace()`，中途崩溃不会留下"看起来完整"的半成品；
- 失败运行**保留日志且不覆盖**，需显式 `--retry-failed` 才重跑；
- 执行器可注入（`fixed` 适配器用于离线演练；`live` 走真实模型），
  实验专用开关只在本模块内生效，不暴露到公开 API。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from evaluation.metrics import summarize_run
from evaluation.models import EvaluationTopic, ExperimentConfig

# 一次运行必须落齐的文件（判定"完成"的依据）
REQUIRED_ARTIFACTS = (
    "config.json",
    "task.json",
    "events.jsonl",
    "result.md",
    "review.json",
    "metrics.json",
)
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "waiting_human"}


@dataclass
class RunSpec:
    """一次运行的完整标识与输入。"""

    run_id: str
    profile: ExperimentConfig
    topic: EvaluationTopic
    repetition: int

    @property
    def relative_dir(self) -> Path:
        return Path(self.profile.name) / self.topic.topic_id / f"rep-{self.repetition}"


@dataclass
class RunOutcome:
    """一次运行的结果（缓存到磁盘供指标汇总）。"""

    status: str
    record: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class RunExecutor(Protocol):
    """执行器协议：给定画像与课题，返回一次运行的结果。"""

    async def execute(self, spec: RunSpec) -> RunOutcome: ...


# ===== 矩阵构造 =====


def build_matrix(
    profiles: Sequence[ExperimentConfig],
    topics: Sequence[EvaluationTopic],
    repetitions: int,
) -> list[RunSpec]:
    """构造 4 组 × 15 课题 × N 次的运行矩阵（顺序稳定，便于复现与断点续跑）。"""
    if repetitions < 1:
        raise ValueError("repetitions 必须 ≥ 1")
    run_set_id = datetime.now(UTC).strftime("rs-%Y%m%dT%H%M%SZ")
    specs: list[RunSpec] = []
    for profile in profiles:
        for topic in topics:
            for repetition in range(1, repetitions + 1):
                specs.append(
                    RunSpec(
                        run_id=f"{run_set_id}:{profile.name}:{topic.topic_id}:{repetition}",
                        profile=profile,
                        topic=topic,
                        repetition=repetition,
                    )
                )
    return specs


# ===== 原子写入 =====


def write_json_atomic(path: Path, payload: Any) -> None:
    """先写临时文件再替换，避免半成品被误判为完成。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_events_atomic(path: Path, events: Iterable[dict[str, Any]]) -> None:
    """写 JSONL 事件流。

    无事件的运行会写入一个占位事件，保证 `events.jsonl` 非空——
    这样"文件存在且非空"才是可靠的完成判据（空文件可能是中途崩溃）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        count = 0
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            count += 1
        if count == 0:
            handle.write(json.dumps({"event_type": "no_events"}, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# ===== 完成判定 =====


def run_is_complete(run_dir: Path) -> bool:
    """目录内必需文件齐全且 metrics.json 可解析 → 视为已完成。"""
    for name in REQUIRED_ARTIFACTS:
        candidate = run_dir / name
        if not candidate.is_file() or candidate.stat().st_size == 0:
            return False
    try:
        json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return True


# ===== Runner =====


@dataclass
class RunReport:
    """一次 run-set 的执行摘要。"""

    run_set_id: str
    root: Path
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    total: int = 0
    failed_runs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and (self.completed + self.skipped) == self.total

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_set_id": self.run_set_id,
            "root": str(self.root),
            "total": self.total,
            "completed": self.completed,
            "skipped": self.skipped,
            "failed": self.failed,
            "ok": self.ok,
        }


class ExperimentRunner:
    """按矩阵执行并落盘；resume 与失败重试语义见模块文档。"""

    def __init__(
        self,
        executor: RunExecutor,
        *,
        output_root: Path,
        run_set_id: str | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.executor = executor
        self.run_set_id = run_set_id or datetime.now(UTC).strftime("rs-%Y%m%dT%H%M%SZ")
        self.root = Path(output_root) / self.run_set_id
        self._log = logger or (lambda message: None)

    def run_dir(self, spec: RunSpec) -> Path:
        return self.root / spec.relative_dir

    async def run_one(self, spec: RunSpec, *, retry_failed: bool = False) -> str:
        """执行单次运行，返回 `completed` / `skipped` / `failed`。"""
        target = self.run_dir(spec)
        complete = run_is_complete(target)
        if complete and not retry_failed:
            self._log(f"skip {spec.run_id}")
            return "skipped"
        if target.exists() and (retry_failed or not complete):
            # 已有现场（失败残留或显式重试）：归档上次结果到 attempts/，绝不覆盖
            stale = [
                candidate
                for candidate in target.iterdir()
                if candidate.is_file() and candidate.name != "config.json"
            ]
            if stale:
                archive = target / "attempts" / datetime.now(UTC).strftime("retry-%Y%m%dT%H%M%S%f")
                archive.mkdir(parents=True, exist_ok=True)
                for candidate in stale:
                    shutil.move(str(candidate), str(archive / candidate.name))

        started = time.perf_counter()
        write_json_atomic(target / "config.json", spec.profile.model_dump(mode="json"))
        write_json_atomic(
            target / "task.json",
            {
                "run_id": spec.run_id,
                "run_set_id": self.run_set_id,
                "profile": spec.profile.name,
                "topic_id": spec.topic.topic_id,
                "domain": spec.topic.domain.value,
                "topic_zh": spec.topic.topic_zh,
                "query_en": spec.topic.query_en,
                "repetition": spec.repetition,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )

        try:
            outcome = await self.executor.execute(spec)
        except Exception as exc:
            write_text_atomic(target / "error.txt", f"{type(exc).__name__}: {exc}\n")
            write_json_atomic(
                target / "metrics.json",
                {"run_id": spec.run_id, "status": "failed", "error": str(exc)},
            )
            write_events_atomic(target / "events.jsonl", [])
            write_text_atomic(target / "result.md", "")
            write_json_atomic(target / "review.json", {})
            self._log(f"failed {spec.run_id}: {exc}")
            return "failed"

        record = dict(outcome.record or {})
        record.setdefault("duration_ms", (time.perf_counter() - started) * 1000.0)
        record["status"] = outcome.status
        if outcome.error:
            record["error"] = outcome.error

        write_events_atomic(target / "events.jsonl", record.get("events") or [])
        write_text_atomic(target / "result.md", str(record.get("markdown") or ""))
        write_json_atomic(target / "review.json", record.get("review") or {})
        write_json_atomic(
            target / "metrics.json", {**summarize_run(record), **strip_payload(record)}
        )

        if outcome.status == "completed":
            self._log(f"completed {spec.run_id}")
            return "completed"
        self._log(f"failed {spec.run_id}: {outcome.error or outcome.status}")
        return "failed"

    async def run(
        self,
        specs: Sequence[RunSpec],
        *,
        retry_failed: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> RunReport:
        """执行整个矩阵；单个 run 失败不中断其余 run。"""
        report = RunReport(run_set_id=self.run_set_id, root=self.root, total=len(specs))
        for index, spec in enumerate(specs, start=1):
            result = await self.run_one(spec, retry_failed=retry_failed)
            if result == "completed":
                report.completed += 1
            elif result == "skipped":
                report.skipped += 1
            else:
                report.failed += 1
                report.failed_runs.append(spec.run_id)
            if on_progress is not None:
                on_progress(index, len(specs), result)

        write_json_atomic(
            self.root / "run_set.json",
            {
                **report.as_dict(),
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        return report


def strip_payload(record: dict[str, Any]) -> dict[str, Any]:
    """metrics.json 只保留标量与摘要，避免把整篇正文再存一份。"""
    keep = {
        "status",
        "duration_ms",
        "prompt_tokens",
        "completion_tokens",
        "attempts",
        "error",
        "evidence_count",
        "node_count",
        "event_count",
        "document_count",
    }
    return {key: record.get(key) for key in keep if key in record}


# ===== 命令行可用的执行器 =====


class FixedAdapterExecutor:
    """离线确定性执行器：不调用真实模型，供 dry run / CI 使用。

    产出的正文含可解析引用（`[E1]`），因此指标链路能被真实打通。
    """

    def __init__(self, *, delay_ms: int = 0) -> None:
        self.delay_ms = delay_ms
        self.calls: list[str] = []

    async def execute(self, spec: RunSpec) -> RunOutcome:
        import asyncio

        self.calls.append(spec.run_id)
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000.0)

        evidence_count = 3
        markdown = (
            f"## 摘要\n针对「{spec.topic.topic_zh}」的离线演练输出 [E1][E2]。\n\n"
            "## 主要发现\n证据表明相关方法在评估维度上存在差异 [E1]。\n\n"
            "## 局限性\n仅覆盖离线语料，未做跨语言验证。\n"
        )
        events = [
            {"event_type": "task_created", "status": "created", "event_id": 1},
            {
                "event_type": "node_result",
                "status": "completed",
                "node_id": "n1",
                "event_id": 2,
                "attempts": 1,
            },
            {"event_type": "task_completed", "status": "completed", "event_id": 3},
        ]
        # 四种画像的检索能力不同：无 RAG 的两组没有检索命中
        retrieved = ["doc-a", "doc-b"] if spec.profile.retrieval_mode.value != "none" else []
        record: dict[str, Any] = {
            "markdown": markdown,
            "evidence_count": evidence_count,
            "cited_chunk_ids": ["chunk-1", "chunk-2"],
            "allowed_chunk_ids": ["chunk-1", "chunk-2", "chunk-3"],
            "retrieved_ids": retrieved,
            "relevant_ids": ["doc-a", "doc-c"],
            "qrels": {"doc-a": 2.0, "doc-c": 1.0},
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "attempts": 1,
            "events": events,
            "review": {"final_markdown": markdown, "scores": {"citation_traceability": 1.0}},
            "node_count": 5,
            "event_count": len(events),
            "document_count": 10,
        }
        return RunOutcome(status="completed", record=record)


class TaskExecutionExecutor:
    """真实执行器：通过 TaskExecution 跑完整文献综述任务。

    需要注入已启动的 `TaskExecution` 与演示用户；语料须事先入库。
    实验专用开关（validation_enabled / review_enabled / retrieval_mode）
    以 `config` 形式传给任务，由 runner 侧决定是否启用对应能力。
    """

    def __init__(
        self,
        execution: Any,
        *,
        user_id: uuid.UUID,
        poll_interval: float = 1.0,
        timeout: float = 1800.0,
    ) -> None:
        self.execution = execution
        self.user_id = user_id
        self.poll_interval = poll_interval
        self.timeout = timeout

    async def execute(self, spec: RunSpec) -> RunOutcome:
        import asyncio

        from app.orchestration.contracts import LiteratureReviewRequest, TaskStatus

        request = LiteratureReviewRequest(
            topic=spec.topic.topic_zh,
            keywords=[spec.topic.query_en],
            max_documents=10,
            offline_dataset=True,
        )
        task_id = await self.execution.submit(self.user_id, request)
        deadline = time.monotonic() + self.timeout
        snapshot = await self.execution.get_snapshot(self.user_id, task_id)
        while snapshot.status == TaskStatus.WAITING_APPROVAL.value:
            await self.execution.approve_plan(self.user_id, task_id, snapshot.revision)
            snapshot = await self.execution.get_snapshot(self.user_id, task_id)
            break
        while time.monotonic() < deadline:
            snapshot = await self.execution.get_snapshot(self.user_id, task_id)
            if snapshot.status in TERMINAL_STATUSES:
                break
            await asyncio.sleep(self.poll_interval)

        events = await self.execution.events_after(self.user_id, task_id, 0)
        review = snapshot.review_report or {}
        markdown = str(review.get("final_markdown") or "")
        evidence_count = 0
        attempts = 0
        for subtask in snapshot.subtasks:
            output = subtask.output or {}
            execution_payload = output.get("execution_result") or {}
            evidence_count = max(evidence_count, len(execution_payload.get("evidence") or []))
            attempts = max(attempts, int(subtask.retry_count or 0) + 1)
        return RunOutcome(
            status=snapshot.status,
            record={
                "markdown": markdown,
                "evidence_count": evidence_count,
                "status": snapshot.status,
                "attempts": attempts,
                "events": [event.model_dump(mode="json") for event in events],
                "review": review,
                "node_count": len(snapshot.subtasks),
                "event_count": len(events),
                "document_count": int((review.get("stats") or {}).get("documents") or 0),
            },
            error=None if snapshot.status == "completed" else f"最终状态 {snapshot.status}",
        )
