"""reindex --reconcile CLI 参数边界。"""

from __future__ import annotations

import pytest

from scripts.reindex_vectors import _parse_args


def test_accepts_reconcile() -> None:
    args = _parse_args(["--reconcile"])
    assert args.reconcile is True


def test_reconcile_rejects_limit(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--reconcile", "--limit", "1"])
    assert "--reconcile 不能与 --limit 同时使用" in capsys.readouterr().err


def test_reconcile_rejects_user_id(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--reconcile", "--user-id", "00000000-0000-0000-0000-000000000000"])
    assert "--reconcile 不能与 --user-id 同时使用" in capsys.readouterr().err
