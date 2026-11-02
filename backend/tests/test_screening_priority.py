"""主动筛选纯排序器测试（不碰数据库/HTTP）。"""

from __future__ import annotations

from typing import Literal

from app.knowledge.services.screening_priority import (
    ALGORITHM_VERSION,
    LabeledText,
    PriorityCandidate,
    rank_candidates,
)


def _label(text: str, label: Literal["positive", "negative"]) -> LabeledText:
    return LabeledText(text=text, label=label)


def _candidate(record_id: str, text: str) -> PriorityCandidate:
    return PriorityCandidate(record_id=record_id, text=text)


def test_ranks_relevant_candidate_first() -> None:
    labeled = [
        _label("retrieval augmented generation evaluation evidence benchmark", "positive"),
        _label("clinical cancer trial patient treatment outcome", "negative"),
    ]
    candidates = [
        _candidate("negative", "clinical cancer patient trial outcome treatment"),
        _candidate("positive", "retrieval augmented generation benchmark evidence"),
    ]

    ranking = rank_candidates(labeled, candidates)

    assert ranking.status == "ready"
    assert ranking.algorithm == ALGORITHM_VERSION
    assert [item.record_id for item in ranking.items] == ["positive", "negative"]
    assert [item.rank for item in ranking.items] == [1, 2]
    assert ranking.positive == 1
    assert ranking.negative == 1


def test_chinese_text_is_rankable() -> None:
    labeled = [
        _label("检索增强生成的证据评估方法", "positive"),
        _label("临床肿瘤试验患者治疗结果", "negative"),
    ]
    candidates = [
        _candidate("negative", "临床肿瘤患者试验治疗结果"),
        _candidate("positive", "检索增强生成证据评估方法"),
    ]

    ranking = rank_candidates(labeled, candidates)

    assert ranking.status == "ready"
    assert ranking.items[0].record_id == "positive"


def test_same_input_is_deterministic() -> None:
    labeled = [
        _label("graph neural network message passing", "positive"),
        _label("marine biology coral reef ecology", "negative"),
    ]
    candidates = [
        _candidate("b", "coral reef marine biology"),
        _candidate("a", "graph neural network message"),
    ]

    first = rank_candidates(labeled, candidates)
    second = rank_candidates(labeled, candidates)

    assert first == second
    assert [item.rank for item in first.items] == [1, 2]


def test_ties_keep_input_order() -> None:
    labeled = [
        _label("active learning screening evidence", "positive"),
        _label("unrelated topic completely different", "negative"),
    ]
    candidates = [
        _candidate("first", "active learning screening evidence"),
        _candidate("second", "active learning screening evidence"),
    ]

    ranking = rank_candidates(labeled, candidates)

    assert [item.record_id for item in ranking.items] == ["first", "second"]


def test_insufficient_labels_returns_empty_ranking() -> None:
    ranking = rank_candidates(
        [_label("only positive", "positive")],
        [_candidate("c1", "only positive")],
    )

    assert ranking.status == "insufficient_labels"
    assert ranking.items == ()
    assert ranking.positive == 1
    assert ranking.negative == 0


def test_no_candidates_wins_over_insufficient_labels() -> None:
    ranking = rank_candidates([_label("only positive", "positive")], [])

    assert ranking.status == "no_candidates"
    assert ranking.items == ()
    assert ranking.candidates == 0


def test_empty_text_returns_no_candidates() -> None:
    ranking = rank_candidates(
        [_label("", "positive"), _label("", "negative")],
        [_candidate("c1", "")],
    )

    assert ranking.status == "no_candidates"
    assert ranking.items == ()
    assert "文本" in ranking.message


def test_ignored_count_is_preserved() -> None:
    ranking = rank_candidates(
        [
            _label("positive text about screening", "positive"),
            _label("negative text about other work", "negative"),
        ],
        [_candidate("c1", "positive text about screening")],
        ignored=2,
    )

    assert ranking.ignored == 2
    assert ranking.status == "ready"
