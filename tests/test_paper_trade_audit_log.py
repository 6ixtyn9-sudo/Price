"""Tests for paper_trade's audit-log size cap.

The audit CSV (paper_trade_log.csv) is append-only and committed to
git by the live workflows. GitHub rejects files over 100 MB; once the
log crossed that (103.84 MB on 2026-10-20) every hourly "Commit
workflow outputs" step failed GH001 and the trade journal froze on
main while trading continued through the Actions cache. The cap drops
the oldest rows on append so the file always stays committable.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import paper_trade  # noqa: E402


@pytest.fixture()
def audit_path(tmp_path, monkeypatch):
    path = tmp_path / "paper_trade_log.csv"
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_PATH", path)
    return path


def test_append_creates_then_accumulates(audit_path):
    paper_trade._append_audit({"action": "enter", "symbol": "AAA"})
    paper_trade._append_audit({"action": "exit", "symbol": "AAA"})
    df = pd.read_csv(audit_path)
    assert df["action"].tolist() == ["enter", "exit"]
    assert "logged_at_utc" in df.columns


def test_append_trims_oldest_rows_when_over_cap(audit_path, monkeypatch):
    seed = pd.DataFrame(
        {
            "action": ["scan"] * 200,
            "symbol": [f"S{i:03d}" for i in range(200)],
            "pad": ["x" * 200] * 200,
        }
    )
    seed.to_csv(audit_path, index=False)
    assert audit_path.stat().st_size > 40_000
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_MAX_BYTES", 20_000)

    paper_trade._append_audit({"action": "enter", "symbol": "NEWEST"})

    out = pd.read_csv(audit_path)
    assert audit_path.stat().st_size <= 20_000
    assert out.iloc[-1]["symbol"] == "NEWEST"
    assert out.iloc[-1]["action"] == "enter"
    # Oldest rows dropped, survivors are the most recent seed rows,
    # in original order.
    assert "S000" not in set(out["symbol"])
    seed_survivors = [s for s in out["symbol"] if isinstance(s, str) and s.startswith("S")]
    assert seed_survivors == sorted(seed_survivors)
    assert seed_survivors[-1] == "S199"


def test_append_under_cap_keeps_everything(audit_path, monkeypatch):
    seed = pd.DataFrame({"action": ["scan"] * 5, "symbol": list("ABCDE")})
    seed.to_csv(audit_path, index=False)
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_MAX_BYTES", 10 * 1024 * 1024)
    paper_trade._append_audit({"action": "enter", "symbol": "F"})
    out = pd.read_csv(audit_path)
    assert out["symbol"].tolist() == ["A", "B", "C", "D", "E", "F"]


def test_trim_helper_noop_when_frame_fits():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = paper_trade._trim_audit_frame(df, max_bytes=10**9, bytes_per_row=10.0)
    assert len(out) == 3


def test_trim_helper_keeps_newest_and_at_least_one():
    df = pd.DataFrame({"a": list(range(100))})
    out = paper_trade._trim_audit_frame(df, max_bytes=100, bytes_per_row=1000.0)
    assert len(out) == 1
    assert out.iloc[0]["a"] == 99


def test_default_cap_is_safely_below_github_limit():
    assert 0 < paper_trade.AUDIT_LOG_MAX_BYTES < 100 * 1024 * 1024


def test_append_mode_aligns_missing_columns(audit_path, monkeypatch):
    # Under-cap appends must land values in the right columns even when
    # a row omits fields present in the file (append-mode alignment).
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_MAX_BYTES", 10 * 1024 * 1024)
    paper_trade._append_audit({"action": "enter", "symbol": "AAA", "qty": 3})
    paper_trade._append_audit({"action": "exit", "symbol": "BBB"})
    df = pd.read_csv(audit_path)
    assert df["symbol"].tolist() == ["AAA", "BBB"]
    assert df.iloc[0]["qty"] == 3
    assert pd.isna(df.iloc[1]["qty"])


def test_append_mode_new_column_extends_schema(audit_path, monkeypatch):
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_MAX_BYTES", 10 * 1024 * 1024)
    paper_trade._append_audit({"action": "enter", "symbol": "AAA"})
    paper_trade._append_audit({"action": "exit", "symbol": "BBB", "new_field": "z"})
    df = pd.read_csv(audit_path)
    assert df["symbol"].tolist() == ["AAA", "BBB"]
    assert df.iloc[1]["new_field"] == "z"
    assert pd.isna(df.iloc[0]["new_field"])
    # Subsequent appends align against the extended schema.
    paper_trade._append_audit({"action": "enter", "symbol": "CCC"})
    df = pd.read_csv(audit_path)
    assert df["symbol"].tolist() == ["AAA", "BBB", "CCC"]


def test_append_mode_does_not_rewrite_existing_rows(audit_path, monkeypatch):
    # O(1) append: appending must not parse/rewrite history -- the raw
    # bytes of the pre-existing content stay a strict prefix.
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_MAX_BYTES", 10 * 1024 * 1024)
    paper_trade._append_audit({"action": "enter", "symbol": "AAA"})
    before = audit_path.read_bytes()
    paper_trade._append_audit({"action": "exit", "symbol": "BBB"})
    after = audit_path.read_bytes()
    assert after.startswith(before)
    assert len(after) > len(before)
