"""live 执行器必须记录 token / 检索 id / 可引用分块，否则实验指标恒为 null。

## 缺陷（2026-10-27 跑完 60 run 实测）

真实 60 个 run 的 `summary.csv` 里，这些列 **0/60 非空**：

```
error_citation_rate   0/60
recall_at_5           0/60
ndcg_at_5             0/60
total_tokens          0/60
recovery_success      0/60
```

（`duration_ms` 是 60/60 有值的 —— 它由 `runner` 用 `perf_counter` 兜底，
所以本文件不测它。）

## 根因（两处，都已按生产端代码确认，不是猜）

1. **`TaskExecutionExecutor.execute()` 的 `record` 不含 token / 检索字段**。
   `evaluation/runner.py` 的返回只有 markdown / evidence_count / status /
   attempts / events / review / node_count / event_count / document_count。

   而 `evaluation/metrics.py::summarize_run` 要算：
   - `total_tokens` ← `record["prompt_tokens"]` + `record["completion_tokens"]`
   - `recall_at_5` / `mrr` / `ndcg_at_5` ← `record["retrieved_ids"]` + `relevant_ids`
   - `error_citation_rate` ← `record["cited_chunk_ids"]` + `allowed_chunk_ids`

   一个都没填 → 全 null。

2. **`recovery_success` 根本没有被 `summarize_run` 调用**。
   `metrics.py::recovery_success()` 函数存在，但 `summarize_run` 的返回字典里
   没有这个键 → 恒为 null。

## 载荷形状依据（**照抄**，不是自造）

`app/orchestration/services/task_execution.py::sum_actual_tokens` 的文档字符串
写明了生产载荷：token 在 `output["execution_result"]` **里面**：

```python
{"node_id": ..., "result": ..., "status": "completed",
 "execution_result": {"prompt_tokens": N, "completion_tokens": M, ...}}
```

而 `execution_result` 是 `contracts.ExecutionResult` 的序列化，含
`prompt_tokens` / `completion_tokens` / `duration_ms` / `evidence` / `node_type`。
`contracts.Evidence` 含 `doc_id` / `chunk_id`。

**AGENTS §5 #16 / #33 的教训**：这两次都是「测试载荷扁平、生产载荷嵌套」导致的
假绿。所以本文件的夹具**必须**用嵌套形状。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from evaluation.models import EvaluationTopic, ExperimentConfig
from evaluation.runner import RunSpec, TaskExecutionExecutor


def _profile() -> ExperimentConfig:
    return ExperimentConfig(
        name="multi_agent_vector_rag",
        agent_mode="multi",
        retrieval_mode="vector",
        validation_enabled=True,
        review_enabled=True,
    )


def _topic() -> EvaluationTopic:
    return EvaluationTopic(
        topic_id="ai-rag-eval",
        domain="ai",
        topic_zh="检索增强生成系统的质量评估方法",
        query_en="retrieval augmented generation quality evaluation metrics",
        corpus_manifest="evaluation/datasets/corpora/ai.json",
    )


def _spec() -> RunSpec:
    return RunSpec(run_id="r1", profile=_profile(), topic=_topic(), repetition=1)


class _Subtask:
    """最小 subtask 替身：只有执行器真正读取的两个字段。"""

    def __init__(self, output: dict[str, Any], retry_count: int = 0) -> None:
        self.output = output
        self.retry_count = retry_count


class _Snapshot:
    def __init__(self, status: str, subtasks: list[_Subtask]) -> None:
        self.status = status
        self.revision = 1
        self.review_report: dict[str, Any] = {
            "final_markdown": "# 综述\n\n结论 [E1]\n",
            "stats": {"documents": 3},
        }
        self.subtasks = subtasks


class _Execution:
    """直接返回已完成的快照（不经过审批流程，那些已有测试覆盖）。"""

    def __init__(self, snapshot: _Snapshot) -> None:
        self._snapshot = snapshot
        self._task_id = uuid.uuid4()

    async def submit(self, user_id: Any, request: Any) -> uuid.UUID:
        return self._task_id

    async def get_snapshot(self, user_id: Any, task_id: Any) -> _Snapshot:
        return self._snapshot

    async def approve_plan(self, user_id: Any, task_id: Any, revision: int) -> None:
        return None

    async def events_after(self, user_id: Any, task_id: Any, event_id: int) -> list[Any]:
        return []


def _search_subtask(
    doc_ids: list[str], chunk_ids: list[str], *, tokens: tuple[int, int]
) -> _Subtask:
    """生产形状：token / evidence 都在 `execution_result` 里面。"""
    return _Subtask(
        {
            "node_id": "n1",
            "result": "ok",
            "status": "completed",
            "execution_result": {
                "node_id": "n1",
                "content": "检索到若干文献",
                "node_type": "search",
                "duration_ms": 1200,
                "prompt_tokens": tokens[0],
                "completion_tokens": tokens[1],
                "evidence": [
                    {
                        "doc_id": doc_id,
                        "chunk_id": chunk_id,
                        "excerpt": "原文片段",
                        "kind": "excerpt",
                    }
                    for doc_id, chunk_id in zip(doc_ids, chunk_ids, strict=True)
                ],
            },
        }
    )


def _write_subtask(tokens: tuple[int, int]) -> _Subtask:
    return _Subtask(
        {
            "node_id": "n2",
            "result": "ok",
            "status": "completed",
            "execution_result": {
                "node_id": "n2",
                "content": "正文",
                "node_type": "extract",
                "duration_ms": 800,
                "prompt_tokens": tokens[0],
                "completion_tokens": tokens[1],
                "evidence": [],
            },
        }
    )


async def _run(execution: _Execution) -> dict[str, Any]:
    executor = TaskExecutionExecutor(
        execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
    )
    outcome = await executor.execute(_spec())
    return outcome.record


DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"
CHUNK_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CHUNK_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestExecutorRecordsTokenUsage:
    @pytest.mark.asyncio
    async def test_tokens_are_read_from_nested_execution_result(self) -> None:
        """核心：token 在生产载荷的 `execution_result` 里面，不能被漏掉。"""
        execution = _Execution(
            _Snapshot("completed", [_search_subtask([DOC_A], [CHUNK_A], tokens=(120, 30))])
        )

        record = await _run(execution)

        assert record.get("prompt_tokens") == 120, f"未读到 prompt_tokens，record={record}"
        assert record.get("completion_tokens") == 30

    @pytest.mark.asyncio
    async def test_tokens_are_summed_across_subtasks(self) -> None:
        """多个节点要**累加**，不是取最后一个。"""
        execution = _Execution(
            _Snapshot(
                "completed",
                [
                    _search_subtask([DOC_A], [CHUNK_A], tokens=(100, 20)),
                    _write_subtask((200, 50)),
                ],
            )
        )

        record = await _run(execution)

        assert record.get("prompt_tokens") == 300
        assert record.get("completion_tokens") == 70

    @pytest.mark.asyncio
    async def test_tokens_missing_yields_no_key_not_zero(self) -> None:
        """没有用量就**不要造 0**：0 会被误读成「真的没消耗」。"""
        execution = _Execution(
            _Snapshot("completed", [_Subtask({"node_id": "n1", "execution_result": {}})])
        )

        record = await _run(execution)

        assert record.get("prompt_tokens") is None
        assert record.get("completion_tokens") is None


class TestExecutorRecordsRetrievalIds:
    @pytest.mark.asyncio
    async def test_retrieved_doc_ids_come_from_search_node_evidence(self) -> None:
        """检索到的文档 = search 节点证据里的 doc_id。"""
        execution = _Execution(
            _Snapshot(
                "completed",
                [_search_subtask([DOC_A, DOC_B], [CHUNK_A, CHUNK_B], tokens=(10, 5))],
            )
        )

        record = await _run(execution)

        assert record.get("retrieved_ids") == [DOC_A, DOC_B]

    @pytest.mark.asyncio
    async def test_allowed_chunk_ids_collects_all_evidence_chunks(self) -> None:
        """可引用分块 = 全部节点证据里的 chunk_id（error_citation_rate 的合法集）。"""
        execution = _Execution(
            _Snapshot(
                "completed",
                [_search_subtask([DOC_A, DOC_B], [CHUNK_A, CHUNK_B], tokens=(10, 5))],
            )
        )

        record = await _run(execution)

        assert record.get("allowed_chunk_ids") == [CHUNK_A, CHUNK_B]

    @pytest.mark.asyncio
    async def test_duplicate_ids_are_deduplicated(self) -> None:
        """同一文档被多个节点引用时不能重复计入（否则 recall 分母失真）。"""
        execution = _Execution(
            _Snapshot(
                "completed",
                [
                    _search_subtask([DOC_A], [CHUNK_A], tokens=(10, 5)),
                    _search_subtask([DOC_A], [CHUNK_A], tokens=(10, 5)),
                ],
            )
        )

        record = await _run(execution)

        assert record.get("retrieved_ids") == [DOC_A]
        assert record.get("allowed_chunk_ids") == [CHUNK_A]

    @pytest.mark.asyncio
    async def test_no_rag_profile_yields_empty_retrieval(self) -> None:
        """无检索画像没有 search 证据 → 空列表（而不是崩）。"""
        execution = _Execution(_Snapshot("completed", [_write_subtask((10, 5))]))

        record = await _run(execution)

        assert record.get("retrieved_ids") == []


class TestSummarizeRunEmitsMetrics:
    def test_total_tokens_is_computed_from_executor_record(self) -> None:
        """端到端：执行器记录 → summarize_run 必须产出非 null 的 total_tokens。"""
        from evaluation.metrics import summarize_run

        metrics = summarize_run(
            {
                "status": "completed",
                "markdown": "# x\n",
                "prompt_tokens": 300,
                "completion_tokens": 70,
            }
        )

        assert metrics["total_tokens"] == 370

    def test_recovery_success_is_emitted_when_retry_happened(self) -> None:
        """`recovery_success` 之前**根本没被 summarize_run 调用**，恒为 null。"""
        from evaluation.metrics import summarize_run

        metrics = summarize_run({"status": "completed", "attempts": 3})

        assert "recovery_success" in metrics, "summarize_run 未产出 recovery_success"
        assert metrics["recovery_success"] == 1.0, "重试 2 次后成功 = 恢复成功"

    def test_recovery_success_zero_when_retry_failed(self) -> None:
        from evaluation.metrics import summarize_run

        metrics = summarize_run({"status": "waiting_human", "attempts": 3})

        assert metrics["recovery_success"] == 0.0

    def test_recovery_success_none_without_retry(self) -> None:
        """没有重试 = 没有「恢复」可言 → None，不是 1.0。

        记成 1.0 会让「恢复成功率」看起来完美，而其实什么都没恢复。
        """
        from evaluation.metrics import summarize_run

        metrics = summarize_run({"status": "completed", "attempts": 1})

        assert metrics["recovery_success"] is None
