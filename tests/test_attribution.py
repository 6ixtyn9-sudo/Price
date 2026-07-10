"""Tests for the P&L attribution layer (lever 5).

Covers:
  - reconstruct_round_trips: FIFO entry/exit pairing, partial fills, open
    positions excluded, short-side sign.
  - SliceAttribution aggregation: win rate, mean return, total P&L,
    preliminary flag.
  - measure_realized_slippage: fill-vs-signal gap -> bps (the lever-4
    calibration).
  - attribute_pnl: full report shape, expected-vs-realized comparison,
    graceful degradation with zero round-trips.
  - format_report: human-readable output, empty-state message.

Pure unit tests with synthetic journals/logs; no network, no credentials,
no warehouse. A leaderboard is written to tmp_path for expected-return tests.
"""

import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.attribution import (  # noqa: E402
    RoundTrip,
    attribute_pnl,
    format_report,
    load_expected_returns,
    identity_key,
    measure_realized_slippage,
    reconstruct_round_trips,
)


def _journal(rows):
    """Build a trade journal DataFrame from a list of dicts."""
    base = {"order_id": "", "order_type": "market", "time_in_force": "day",
            "status": "filled", "timestamp_utc": ""}
    out = []
    for r in rows:
        d = dict(base)
        d.update(r)
        out.append(d)
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# reconstruct_round_trips
# ---------------------------------------------------------------------------

def test_no_round_trips_when_all_entries_no_exits():
    j = _journal([
        {"symbol": "XOP", "qty": 16, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-05T10:00:00Z",
         "slice_label": "state_ext=stretched_down + state_slope=downtrend"},
    ])
    assert reconstruct_round_trips(j) == []


def test_single_round_trip_long():
    j = _journal([
        {"symbol": "XLF", "qty": 10, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-01T10:00:00Z", "avg_entry_price": 100.0,
         "slice_label": "state_ext=stretched_up + state_slope=flat"},
        {"symbol": "XLF", "qty": 10, "side": "sell", "action": "exit",
         "submitted_at": "2026-07-06T10:00:00Z", "current_price": 102.0,
         "slice_label": "state_ext=stretched_up + state_slope=flat"},
    ])
    rts = reconstruct_round_trips(j)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.symbol == "XLF"
    assert rt.side == "long"
    assert rt.qty == 10
    assert rt.gross_pnl == 20.0  # (102-100)*10
    assert abs(rt.gross_return - 0.02) < 1e-9


def test_unfilled_pending_entry_is_not_a_round_trip():
    """An accepted/pending order is not a fill until broker reconciliation."""
    j = _journal([
        {"symbol": "XLK", "qty": 13, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-07T08:39:09Z", "order_type": "limit",
         "status": "pending_new", "limit_price": 183.57,
         "slice_label": "cross_TLT_state_slope=uptrend + state_ext=neutral"},
        {"symbol": "XLK", "qty": 13, "side": "close", "action": "exit",
         "submitted_at": "2026-07-07T21:17:38Z", "status": "accepted",
         "avg_entry_price": 179.98, "current_price": 179.33},
    ])
    assert reconstruct_round_trips(j) == []


def test_short_round_trip_signs_correctly():
    j = _journal([
        {"symbol": "TLT", "qty": 5, "side": "sell", "action": "entry",
         "submitted_at": "2026-07-01T10:00:00Z", "avg_entry_price": 100.0,
         "slice_label": "state_ext=stretched_up"},
        {"symbol": "TLT", "qty": 5, "side": "buy", "action": "exit",
         "submitted_at": "2026-07-06T10:00:00Z", "current_price": 98.0,
         "slice_label": "state_ext=stretched_up"},
    ])
    rts = reconstruct_round_trips(j)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.side == "short"
    # Short profits when price falls: (100-98)*5 = 10
    assert rt.gross_pnl == 10.0
    assert abs(rt.gross_return - 0.02) < 1e-9


def test_partial_fill_split_across_exits():
    """Entry 20 shares, exited in two lots of 10 -> two round-trips."""
    j = _journal([
        {"symbol": "SPY", "qty": 20, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-01T10:00:00Z", "avg_entry_price": 400.0,
         "slice_label": "sl"},
        {"symbol": "SPY", "qty": 10, "side": "sell", "action": "exit",
         "submitted_at": "2026-07-03T10:00:00Z", "current_price": 404.0,
         "slice_label": "sl"},
        {"symbol": "SPY", "qty": 10, "side": "sell", "action": "exit",
         "submitted_at": "2026-07-05T10:00:00Z", "current_price": 402.0,
         "slice_label": "sl"},
    ])
    rts = reconstruct_round_trips(j)
    assert len(rts) == 2
    assert all(rt.qty == 10 for rt in rts)
    assert rts[0].gross_pnl == 40.0   # (404-400)*10
    assert rts[1].gross_pnl == 20.0   # (402-400)*10


def test_rejected_orders_excluded():
    j = _journal([
        {"symbol": "QQQ", "qty": 5, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-01T10:00:00Z", "avg_entry_price": 300.0,
         "slice_label": "sl", "status": "rejected"},
        {"symbol": "QQQ", "qty": 5, "side": "sell", "action": "exit",
         "submitted_at": "2026-07-02T10:00:00Z", "current_price": 302.0,
         "slice_label": "sl"},
    ])
    assert reconstruct_round_trips(j) == []


def test_empty_journal_returns_empty(tmp_path, monkeypatch):
    import price.attribution as attribution

    assert reconstruct_round_trips(pd.DataFrame()) == []
    # None means "load the default trade journal". Pin that default to an
    # isolated missing file so committed/live localdata cannot leak into this
    # pure unit test.
    monkeypatch.setattr(attribution, "TRADE_JOURNAL_PATH", tmp_path / "missing.csv")
    assert reconstruct_round_trips(None) == []


# ---------------------------------------------------------------------------
# load_expected_returns
# ---------------------------------------------------------------------------

def test_load_expected_returns(tmp_path):
    lb = pd.DataFrame([{
        "symbol": "KLAC", "timeframe": "1d",
        "slice_combination": "state_ext=stretched_down + state_slope=downtrend",
        "valid_mean_ret_costadj": 0.0468,
    }])
    p = tmp_path / "lb.csv"
    lb.to_csv(p, index=False)
    exp = load_expected_returns(p)
    key = identity_key("KLAC", "1d", "state_ext=stretched_down + state_slope=downtrend")
    assert key in exp
    assert abs(exp[key] - 0.0468) < 1e-9


def test_load_expected_returns_missing_file():
    assert load_expected_returns(Path("/nonexistent/lb.csv")) == {}


# ---------------------------------------------------------------------------
# measure_realized_slippage
# ---------------------------------------------------------------------------

def test_measure_slippage_long_adverse_fill():
    """Signal close 100, filled at 100.30 -> 30bp adverse slippage for a long."""
    rts = [RoundTrip(
        symbol="XLF", slice_combination="sl", side="long", qty=10,
        entry_price=100.30, entry_ts="t1", exit_price=101.0, exit_ts="t2",
        gross_pnl=7.0, gross_return=0.007,
    )]
    log = pd.DataFrame([{
        "symbol": "XLF", "slice_combination": "sl", "matched": "True",
        "action": "enter", "close_adj": 100.0,
    }])
    slip = measure_realized_slippage(rts, paper_log_path=Path("/dev/null"))
    # Patch: write the log to tmp so it loads.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        log.to_csv(f.name, index=False)
        slip = measure_realized_slippage(rts, paper_log_path=Path(f.name))
    key = identity_key("XLF", "", "sl", "long", "insample")
    assert abs(slip[key] - 30.0) < 1e-6


def test_measure_slippage_empty_round_trips():
    assert measure_realized_slippage([], Path("/dev/null")) == {}


# ---------------------------------------------------------------------------
# attribute_pnl + format_report
# ---------------------------------------------------------------------------

def test_attribute_pnl_zero_round_trips_graceful():
    j = _journal([
        {"symbol": "XOP", "qty": 16, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-05T10:00:00Z",
         "slice_label": "state_ext=stretched_down + state_slope=downtrend"},
    ])
    report = attribute_pnl(journal=j)
    assert report["summary"]["n_round_trips"] == 0
    assert report["summary"]["total_realized_pnl"] == 0.0
    assert report["summary"]["n_open_positions"] is None
    assert report["summary"]["open_positions_source"] == "unavailable"
    assert any("--sync-broker" in n for n in report["notes"])
    assert report["by_slice"] == []


def test_broker_positions_are_authoritative_for_exposure():
    j = _journal([
        {"symbol": "KLAC", "qty": 10, "side": "buy", "action": "entry",
         "status": "accepted", "submitted_at": "2026-07-01T10:00:00Z"},
    ])
    report = attribute_pnl(journal=j, broker_positions=pd.DataFrame())
    assert report["summary"]["n_open_positions"] == 0
    assert report["summary"]["open_positions_source"] == "alpaca"


def test_same_slice_different_symbols_do_not_collide(tmp_path):
    slice_text = "state_ext=stretched_down + state_slope=downtrend"
    j = _journal([
        {"symbol": "XOP", "qty": 2, "side": "buy", "action": "entry",
         "status": "filled", "avg_entry_price": 100.0, "timeframe": "1d",
         "submitted_at": "2026-07-01T10:00:00Z", "slice_label": slice_text},
        {"symbol": "XOP", "qty": 2, "side": "sell", "action": "exit",
         "status": "filled", "current_price": 101.0,
         "submitted_at": "2026-07-02T10:00:00Z"},
        {"symbol": "KLAC", "qty": 2, "side": "buy", "action": "entry",
         "status": "filled", "avg_entry_price": 200.0, "timeframe": "1d",
         "submitted_at": "2026-07-01T11:00:00Z", "slice_label": slice_text},
        {"symbol": "KLAC", "qty": 2, "side": "sell", "action": "exit",
         "status": "filled", "current_price": 202.0,
         "submitted_at": "2026-07-02T11:00:00Z"},
    ])
    lb = pd.DataFrame([
        {"symbol": "XOP", "timeframe": "1d", "slice_combination": slice_text,
         "valid_mean_ret_costadj": 0.01},
        {"symbol": "KLAC", "timeframe": "1d", "slice_combination": slice_text,
         "valid_mean_ret_costadj": 0.02},
    ])
    lbp = tmp_path / "lb.csv"
    lb.to_csv(lbp, index=False)
    report = attribute_pnl(journal=j, leaderboard_path=lbp)
    assert len(report["by_slice"]) == 2
    by_symbol = {row["symbol"]: row for row in report["by_slice"]}
    assert by_symbol["XOP"]["expected_return"] == 0.01
    assert by_symbol["KLAC"]["expected_return"] == 0.02


def test_attribute_pnl_with_round_trips_and_expected(tmp_path):
    j = _journal([
        {"symbol": "KLAC", "qty": 10, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-01T10:00:00Z", "avg_entry_price": 100.0,
         "timeframe": "1d", "slice_label": "state_ext=stretched_down + state_slope=downtrend"},

        {"symbol": "KLAC", "qty": 10, "side": "sell", "action": "exit",
         "submitted_at": "2026-07-06T10:00:00Z", "current_price": 104.0,
         "slice_label": "state_ext=stretched_down + state_slope=downtrend"},
    ])
    lb = pd.DataFrame([{
        "symbol": "KLAC", "timeframe": "1d",
        "slice_combination": "state_ext=stretched_down + state_slope=downtrend",
        "valid_mean_ret_costadj": 0.0468,
    }])
    lbp = tmp_path / "lb.csv"
    lb.to_csv(lbp, index=False)

    report = attribute_pnl(journal=j, leaderboard_path=lbp)
    assert report["summary"]["n_round_trips"] == 1
    assert report["summary"]["total_realized_pnl"] == 40.0
    assert len(report["by_slice"]) == 1
    a = report["by_slice"][0]
    assert a["slice_combination"] == "state_ext=stretched_down + state_slope=downtrend"
    assert a["win_rate"] == 1.0
    assert abs(a["mean_gross_return"] - 0.04) < 1e-9
    assert abs(a["expected_return"] - 0.0468) < 1e-9
    assert a["preliminary"] is True  # only 1 < MIN_ROUND_TRIPS_FOR_STATS


def test_format_report_empty_state():
    report = {
        "summary": {"n_round_trips": 0, "n_open_positions": 3,
                    "total_realized_pnl": 0.0},
        "by_slice": [], "round_trips": [], "realized_slippage": {},
        "expected_returns": {},
        "notes": ["No completed round-trips yet."],
    }
    txt = format_report(report)
    assert "P&L ATTRIBUTION REPORT" in txt
    assert "Completed round-trips: 0" in txt
    assert "Open positions:        3" in txt
    assert "No completed round-trips" in txt


def test_format_report_with_slice():
    report = {
        "summary": {"n_round_trips": 1, "n_open_positions": 0,
                    "total_realized_pnl": 40.0},
        "by_slice": [{
            "slice_combination": "state_ext=stretched_down + state_slope=downtrend",
            "symbol": "KLAC", "side": "long", "n_round_trips": 1,
            "win_rate": 1.0, "mean_gross_return": 0.04, "total_gross_pnl": 40.0,
            "expected_return": 0.0468, "realized_slippage_bps": None,
            "net_of_cost_return": None, "preliminary": True,
        }],
        "round_trips": [], "realized_slippage": {},
        "expected_returns": {},
        "notes": [],
    }
    txt = format_report(report)
    assert "PER-SLICE ATTRIBUTION" in txt
    assert "KLAC" in txt
    assert "state_ext=stretched_down" in txt
    assert "4.68%" in txt  # expected return
    assert "*" in txt  # preliminary flag


def test_attribute_pnl_json_serializable(tmp_path):
    """The report must be JSON-serializable for the --json CLI path."""
    import json
    j = _journal([
        {"symbol": "XLF", "qty": 10, "side": "buy", "action": "entry",
         "submitted_at": "2026-07-01T10:00:00Z", "avg_entry_price": 100.0,
         "slice_label": "sl"},
        {"symbol": "XLF", "qty": 10, "side": "sell", "action": "exit",
         "submitted_at": "2026-07-06T10:00:00Z", "current_price": 101.0,
         "slice_label": "sl"},
    ])
    report = attribute_pnl(journal=j)
    s = json.dumps(report, default=str)
    assert json.loads(s)["summary"]["n_round_trips"] == 1
