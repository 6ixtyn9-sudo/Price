"""Tests for scripts/live_forward_returns.py.

Four regression cases:
  A. No paper-trade log -> returns empty, no output file written.
  B. Log + empty leaderboard -> returns empty, no output file written.
  C. Real data -> one row per matched clean_survivor signal, with
     correct exit bars, partial_data flag, and idempotent rerun behavior.
  D. A matched signal for a slice NOT in the leaderboard's clean_survivor*
     set is silently skipped.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import price.warehouse as wh  # noqa: E402
import scripts.live_forward_returns as lfr  # noqa: E402


@pytest.fixture
def temp_workspace(tmp_path, monkeypatch):
    """Set up an isolated workspace with a synthetic XLF 1d warehouse."""
    wh.WAREHOUSE_DIR = tmp_path / "wh"
    (tmp_path / "wh" / "symbol=XLF" / "timeframe=1d").mkdir(parents=True, exist_ok=True)

    # 30 daily bars for XLF, starting 2026-06-01
    n = 30
    df = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-06-01", periods=n, freq="D", tz="UTC"),
        "open_raw": [50.0 + i * 0.1 for i in range(n)],
        "high_raw": [50.5 + i * 0.1 for i in range(n)],
        "low_raw": [49.5 + i * 0.1 for i in range(n)],
        "close_raw": [50.2 + i * 0.1 for i in range(n)],
        "volume_raw": [1000] * n,
        "close_adj": [50.2 + i * 0.1 for i in range(n)],
    })
    df.to_parquet(
        tmp_path / "wh" / "symbol=XLF" / "timeframe=1d" / "data.parquet",
        index=False,
    )

    log_path = tmp_path / "paper_trade_log.csv"
    lb_path = tmp_path / "candidate_leaderboard.csv"
    out_path = tmp_path / "live_forward_returns.csv"

    yield {
        "log_path": log_path,
        "lb_path": lb_path,
        "out_path": out_path,
        "warehouse_dir": wh.WAREHOUSE_DIR,
    }


def test_no_log_returns_empty(temp_workspace):
    """A) No paper-trade log at all -> empty result, no output file."""
    paths = temp_workspace
    out = lfr.run_live_capture(
        log_path=paths["log_path"],
        leaderboard_path=paths["lb_path"],
        output_path=paths["out_path"],
    )
    assert out.empty
    assert not paths["out_path"].exists()


def test_empty_leaderboard_returns_empty(temp_workspace):
    """B) Log exists but leaderboard has no clean_survivor rows -> empty."""
    paths = temp_workspace
    # Empty log + empty leaderboard
    pd.DataFrame().to_csv(paths["log_path"], index=False)
    pd.DataFrame().to_csv(paths["lb_path"], index=False)

    out = lfr.run_live_capture(
        log_path=paths["log_path"],
        leaderboard_path=paths["lb_path"],
        output_path=paths["out_path"],
    )
    assert out.empty
    assert not paths["out_path"].exists()


def test_real_data_captures_forward_returns(temp_workspace):
    """C) A matched clean_survivor signal produces one row with correct
    exit bars, partial_data flag, and idempotent rerun behavior."""
    paths = temp_workspace

    # Leaderboard with one clean_survivor slice
    pd.DataFrame([{
        "symbol": "XLF",
        "timeframe": "1d",
        "slice_combination": "state_ext=stretched_up + state_slope=flat",
        "triage_bucket": "clean_survivor_wf_strong",
    }]).to_csv(paths["lb_path"], index=False)

    # Matched signal at 2026-06-15 (idx 14, close 51.6)
    # 5 bars later: idx 19 close 52.1 -> fwd_ret = 52.1/51.6 - 1 ~= 0.00969
    # 20 bars later: idx 34 > 30 -> partial
    pd.DataFrame([{
        "kind": "entry_signal",
        "matched": True,
        "tradable": False,
        "symbol": "XLF",
        "timeframe": "1d",
        "slice_combination": "state_ext=stretched_up + state_slope=flat",
        "bar_ts_utc": "2026-06-15T00:00:00+00:00",
        "close_adj": 50.2 + 14 * 0.1,
    }]).to_csv(paths["log_path"], index=False)

    out = lfr.run_live_capture(
        log_path=paths["log_path"],
        leaderboard_path=paths["lb_path"],
        output_path=paths["out_path"],
    )
    assert len(out) == 1
    row = out.iloc[0]
    assert row["symbol"] == "XLF"
    assert row["timeframe"] == "1d"
    assert row["partial_data"]
    assert row["fwd_ret_5b"] is not None
    assert abs(row["fwd_ret_5b"] - (52.1 / 51.6 - 1)) < 1e-9
    assert row["fwd_ret_20b"] is None  # 20b exit is beyond warehouse
    assert row["exit_close_5b"] == 50.2 + 19 * 0.1

    # Idempotent rerun: same 1 row, not 2
    out2 = lfr.run_live_capture(
        log_path=paths["log_path"],
        leaderboard_path=paths["lb_path"],
        output_path=paths["out_path"],
    )
    assert len(out2) == 1
    assert out2.iloc[0]["row_key"] == out.iloc[0]["row_key"]


def test_out_of_universe_slice_is_skipped(temp_workspace):
    """A matched signal for a slice not in the leaderboard's
    clean_survivor* set is silently skipped (not captured)."""
    paths = temp_workspace

    # Leaderboard has XLF slice
    pd.DataFrame([{
        "symbol": "XLF",
        "timeframe": "1d",
        "slice_combination": "state_ext=stretched_up + state_slope=flat",
        "triage_bucket": "clean_survivor_wf_strong",
    }]).to_csv(paths["lb_path"], index=False)

    # Log has a matched signal for a DIFFERENT slice on the same symbol
    pd.DataFrame([{
        "kind": "entry_signal",
        "matched": True,
        "tradable": False,
        "symbol": "XLF",
        "timeframe": "1d",
        "slice_combination": "state_ext=other + state_slope=other",
        "bar_ts_utc": "2026-06-15T00:00:00+00:00",
        "close_adj": 51.6,
    }]).to_csv(paths["log_path"], index=False)

    out = lfr.run_live_capture(
        log_path=paths["log_path"],
        leaderboard_path=paths["lb_path"],
        output_path=paths["out_path"],
    )
    assert out.empty
    assert not paths["out_path"].exists()
