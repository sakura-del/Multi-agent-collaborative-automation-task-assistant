"""实验指标（Plan 5 Task 6）——全部是纯函数。

约定（与计划一致）：
- 每个函数接收普通 Python 记录，返回 `float | None`；
- **分母未定义时返回 `None`**，绝不用 0 冒充（"没有标注"≠"得分为 0"）；
- nDCG 使用 qrels 的分级相关性；MRR 用第一个相关结果。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

MARKER_RE = re.compile(r"\[E(\d+)\]")


# ===== 检索质量 =====


def recall_at_k(
    retrieved: Sequence[str],
    relevant: Iterable[str],
    k: int = 5,
) -> float | None:
    """Recall@k：前 k 个检索结果命中的相关文档比例。

    `relevant` 为空（无标注）时返回 None。
    """
    relevant_set = {str(item) for item in relevant}
    if not relevant_set:
        return None
    if k <= 0:
        return None
    top_k = {str(item) for item in list(retrieved)[:k]}
    return len(top_k & relevant_set) / len(relevant_set)


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str]) -> float | None:
    """MRR 的单查询分量：第一个相关结果的排名倒数；无相关结果或空标注返回 None。"""
    relevant_set = {str(item) for item in relevant}
    if not relevant_set:
        return None
    for rank, item in enumerate(retrieved, start=1):
        if str(item) in relevant_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved: Sequence[str],
    qrels: dict[str, float],
    k: int = 5,
) -> float | None:
    """nDCG@k：使用 qrels 的分级相关性（0 表示不相关）。

    `qrels` 为空（无标注）或理想 DCG 为 0 时返回 None。
    """
    if not qrels or k <= 0:
        return None
    gains = [float(qrels.get(str(item), 0.0)) for item in list(retrieved)[:k]]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_gains = sorted((float(value) for value in qrels.values()), reverse=True)[:k]
    idcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(ideal_gains))
    if idcg <= 0:
        return None
    return dcg / idcg


# ===== 引用与证据质量 =====


def citation_resolvability(markdown: str, evidence_count: int) -> float | None:
    """可解析引用比例：正文 `[E{n}]` 中落在证据池范围内的比例。

    无引用标记时返回 None（"没引用"与"引用都错"是两回事）。
    """
    markers = [int(number) for number in MARKER_RE.findall(markdown or "")]
    if not markers:
        return None
    resolved = sum(1 for number in markers if 1 <= number <= evidence_count)
    return resolved / len(markers)


def evidence_coverage(markdown: str, evidence_count: int) -> float | None:
    """证据覆盖率：证据池中被正文引用到的比例；池为空返回 None。"""
    if evidence_count <= 0:
        return None
    used = {
        int(number)
        for number in MARKER_RE.findall(markdown or "")
        if 1 <= int(number) <= evidence_count
    }
    return len(used) / evidence_count


def error_citation_rate(
    cited_chunk_ids: Sequence[str], allowed_chunk_ids: Iterable[str]
) -> float | None:
    """错误引用率：引用到的分块中不属于该用户已上传文档的比例。

    没有引用时返回 None；`allowed` 为空时视为全部错误（分母仍定义）。
    """
    cited = [str(item) for item in cited_chunk_ids]
    if not cited:
        return None
    allowed = {str(item) for item in allowed_chunk_ids}
    wrong = sum(1 for chunk in cited if chunk not in allowed)
    return wrong / len(cited)


def duplicate_paragraph_ratio(markdown: str) -> float | None:
    """重复段落比例：重复出现的段落数 / 总段落数；段落数 <2 时返回 None。"""
    paragraphs = [line.strip() for line in (markdown or "").splitlines() if line.strip()]
    if len(paragraphs) < 2:
        return None
    seen: set[str] = set()
    duplicates = 0
    for paragraph in paragraphs:
        if paragraph in seen:
            duplicates += 1
        else:
            seen.add(paragraph)
    return duplicates / len(paragraphs)


# ===== 执行质量 =====


def success_rate(statuses: Sequence[str], *, success_status: str = "completed") -> float | None:
    """成功率；没有运行记录时返回 None。"""
    if not statuses:
        return None
    return sum(1 for status in statuses if status == success_status) / len(statuses)


def mean_duration(durations_ms: Sequence[float]) -> float | None:
    """平均耗时（毫秒）；无记录返回 None（不返回 0）。"""
    if not durations_ms:
        return None
    return sum(float(item) for item in durations_ms) / len(durations_ms)


def total_tokens(prompt_tokens: Sequence[int], completion_tokens: Sequence[int]) -> int | None:
    """总 token；两者都为空时返回 None。"""
    if not prompt_tokens and not completion_tokens:
        return None
    return int(sum(prompt_tokens) + sum(completion_tokens))


def retry_count(attempts: Sequence[int]) -> int | None:
    """重试总次数 = Σ(attempts - 1)（attempts 含首次）；无记录返回 None。"""
    if not attempts:
        return None
    return int(sum(max(0, int(value) - 1) for value in attempts))


def recovery_success(recovered: Sequence[bool]) -> float | None:
    """恢复成功率；没有恢复尝试时返回 None。"""
    if not recovered:
        return None
    return sum(1 for item in recovered if item) / len(recovered)


# ===== 汇总 =====


def aggregate(values: Sequence[float | None]) -> float | None:
    """对可能含 None 的指标序列求均值（忽略 None）；全部 None 时返回 None。"""
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def summarize_run(record: dict[str, Any]) -> dict[str, float | None]:
    """把单次运行记录汇总成指标字典（供 metrics.json 落盘）。

    输入字段（均可缺省；缺省即视为"未测量"→ None）：
    - markdown / evidence_count / cited_chunk_ids / allowed_chunk_ids
    - retrieved_ids / relevant_ids / qrels
    - status / duration_ms / prompt_tokens / completion_tokens / attempts
    """
    markdown = str(record.get("markdown") or "")
    evidence_count = int(record.get("evidence_count") or 0)
    durations = [record["duration_ms"]] if record.get("duration_ms") is not None else []
    return {
        "recall_at_5": recall_at_k(
            record.get("retrieved_ids") or [], record.get("relevant_ids") or []
        ),
        "mrr": reciprocal_rank(record.get("retrieved_ids") or [], record.get("relevant_ids") or []),
        "ndcg_at_5": ndcg_at_k(record.get("retrieved_ids") or [], record.get("qrels") or {}),
        "citation_resolvability": citation_resolvability(markdown, evidence_count),
        "evidence_coverage": evidence_coverage(markdown, evidence_count),
        "error_citation_rate": error_citation_rate(
            record.get("cited_chunk_ids") or [], record.get("allowed_chunk_ids") or []
        ),
        "duplicate_paragraph_ratio": duplicate_paragraph_ratio(markdown),
        "success": success_rate([record["status"]]) if record.get("status") else None,
        "duration_ms": mean_duration(durations),
        "total_tokens": total_tokens(
            [record["prompt_tokens"]] if record.get("prompt_tokens") is not None else [],
            [record["completion_tokens"]] if record.get("completion_tokens") is not None else [],
        ),
        "retries": retry_count([record["attempts"]] if record.get("attempts") is not None else []),
        # 恢复成功率（2026-10-27 接线）：此前 `recovery_success()` 函数存在，
        # 但 summarize_run 从没调用它 → metrics.json 里恒为 null。
        #
        # 语义：**只统计真正发生过重试的运行**。attempts == 1（没重试）返回 None，
        # 而不是 1.0 —— 一次没重试的运行不存在「恢复」，记 1.0 会让恢复成功率
        # 看起来完美，实际什么都没恢复。
        "recovery_success": recovery_success(
            [record.get("status") == "completed"] if int(record.get("attempts") or 1) > 1 else []
        ),
    }
