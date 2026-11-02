"""PRISMA 主动筛选排序（纯函数模块）。

设计边界：
- 不导入 SQLAlchemy/FastAPI/数据库模型；
- 只负责二分类文本排序与稳定顺序；
- 不暴露概率，返回的 rank 是给前端展示的唯一分数形式；
- 同一输入 + 同一算法版本必须产生相同顺序。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

ALGORITHM_VERSION = "tfidf-char-logreg-v1"

PriorityStatus = Literal["ready", "insufficient_labels", "no_candidates"]


class PriorityUnavailableError(RuntimeError):
    """排序器/模型不可用；HTTP 层映射为 503，不伪装成正常空结果。"""


@dataclass(frozen=True)
class LabeledText:
    text: str
    label: Literal["positive", "negative"]


@dataclass(frozen=True)
class PriorityCandidate:
    record_id: str
    text: str


@dataclass(frozen=True)
class RankedCandidate:
    record_id: str
    rank: int


@dataclass(frozen=True)
class PriorityRanking:
    status: PriorityStatus
    items: tuple[RankedCandidate, ...]
    positive: int
    negative: int
    ignored: int
    candidates: int
    message: str
    algorithm: str = ALGORITHM_VERSION


def _vectorizer() -> TfidfVectorizer:
    """字符 n-gram 同时适配中英文；固定参数保证可复现。"""
    return TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(2, 4),
        max_features=30_000,
        min_df=1,
        sublinear_tf=True,
        lowercase=True,
    )


def _classifier() -> LogisticRegression:
    return LogisticRegression(
        C=1.0,
        class_weight="balanced",
        solver="liblinear",
        random_state=42,
        max_iter=1000,
    )


def _empty(
    status: PriorityStatus,
    *,
    positive: int,
    negative: int,
    ignored: int,
    candidates: int,
    message: str,
) -> PriorityRanking:
    return PriorityRanking(
        status=status,
        items=(),
        positive=positive,
        negative=negative,
        ignored=ignored,
        candidates=candidates,
        message=message,
    )


def rank_candidates(
    labeled: Sequence[LabeledText],
    candidates: Sequence[PriorityCandidate],
    *,
    ignored: int = 0,
) -> PriorityRanking:
    positive = sum(1 for item in labeled if item.label == "positive")
    negative = sum(1 for item in labeled if item.label == "negative")
    ignored = max(0, ignored)

    if not candidates:
        return _empty(
            "no_candidates",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=0,
            message="没有可排序的未筛记录",
        )

    if positive < 1 or negative < 1:
        return _empty(
            "insufficient_labels",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=len(candidates),
            message=f"至少需要 1 条纳入和 1 条排除决策（当前 {positive} / {negative}）",
        )

    usable = [item for item in labeled if item.text.strip()]
    positive_texts = [item.text for item in usable if item.label == "positive"]
    negative_texts = [item.text for item in usable if item.label == "negative"]
    if not positive_texts or not negative_texts:
        return _empty(
            "no_candidates",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=len(candidates),
            message="缺少可用文本，无法训练排序模型",
        )

    rankable = [item for item in candidates if item.text.strip()]
    if not rankable:
        return _empty(
            "no_candidates",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=len(candidates),
            message="候选记录缺少可用文本",
        )

    texts = positive_texts + negative_texts
    labels = [1] * len(positive_texts) + [0] * len(negative_texts)
    vectorizer = _vectorizer()
    matrix = vectorizer.fit_transform(texts)
    classifier = _classifier()
    classifier.fit(matrix, labels)

    scores = [
        float(value)
        for value in classifier.decision_function(
            vectorizer.transform([item.text for item in rankable])
        )
    ]
    paired = list(zip(rankable, scores, strict=True))
    ordered = [candidate for candidate, _ in sorted(paired, key=lambda pair: -pair[1])]

    unrankable = [item for item in candidates if not item.text.strip()]
    ordered.extend(unrankable)
    items = tuple(
        RankedCandidate(record_id=item.record_id, rank=index)
        for index, item in enumerate(ordered, start=1)
    )
    return PriorityRanking(
        status="ready",
        items=items,
        positive=positive,
        negative=negative,
        ignored=ignored,
        candidates=len(candidates),
        message=f"基于任务内 {positive} 条纳入 / {negative} 条排除决策排序",
    )
