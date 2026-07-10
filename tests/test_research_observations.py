"""Tests for isolated research opportunity/fill summaries."""

import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research_observations import build_regime_opportunity_rates  # noqa: E402


def test_regime_opportunity_summary_isolated_and_exact(tmp_path):
    paper = tmp_path / "paper_trade_log.csv"
    journal = tmp_path / "trade_journal.csv"

    pd.DataFrame([
        {
            "kind": "entry_signal", "action": "enter", "symbol": "XOP",
            "timeframe": "1d", "slice_combination": "slice",
            "bin_mode": "insample", "regime": "bull", "regime_symbol": "SPY",
            "bar_ts_utc": "2026-07-01", "matched": True, "tradable": True,
            "order_id": "entry-1", "logged_at_utc": "2026-07-01T10:00:00Z",
        },
        # Same signal observed again: must count as one opportunity.
        {
            "kind": "entry_signal", "action": "enter", "symbol": "XOP",
            "timeframe": "1d", "slice_combination": "slice",
            "bin_mode": "insample", "regime": "bull", "regime_symbol": "SPY",
            "bar_ts_utc": "2026-07-01", "matched": True, "tradable": True,
            "order_id": "entry-1", "logged_at_utc": "2026-07-01T11:00:00Z",
        },
        {
            "kind": "entry_signal", "action": "block", "symbol": "XOP",
            "timeframe": "1d", "slice_combination": "slice",
            "bin_mode": "insample", "regime": "bear", "regime_symbol": "SPY",
            "bar_ts_utc": "2026-07-02", "matched": True, "tradable": False,
            "order_id": "", "logged_at_utc": "2026-07-02T10:00:00Z",
        },
    ]).to_csv(paper, index=False)

    pd.DataFrame([
        {
            "order_id": "entry-1", "symbol": "XOP", "action": "entry",
            "status": "filled", "broker_status": "filled", "qty": 10,
            "filled_qty": 10, "filled_avg_price": 100,
            "slice_label": "slice", "timeframe": "1d", "bin_mode": "insample",
            "submitted_at": "2026-07-01T10:00:00Z",
        },
        {
            "order_id": "exit-1", "symbol": "XOP", "action": "exit",
            "status": "filled", "broker_status": "filled", "qty": 10,
            "filled_qty": 10, "filled_avg_price": 101,
            "submitted_at": "2026-07-03T10:00:00Z",
        },
    ]).to_csv(journal, index=False)

    result = build_regime_opportunity_rates(paper, journal)
    bull = result[result["regime"] == "bull"].iloc[0]
    bear = result[result["regime"] == "bear"].iloc[0]

    assert bull["observed_signal_bars"] == 1
    assert bull["matched_opportunities"] == 1
    assert bull["orders_submitted"] == 1
    assert bull["orders_filled"] == 1
    assert bull["completed_round_trips"] == 1
    assert bull["order_fill_rate"] == 1.0
    assert bull["completion_rate"] == 1.0
    assert bear["risk_blocked_opportunities"] == 1
    assert bear["risk_block_rate"] == 1.0
