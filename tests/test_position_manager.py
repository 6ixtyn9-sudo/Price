"""Tests for the hybrid exit policy in price.position_manager.

Covers:
  - _count_bars_after (pure, exact counts, edge cases)
  - horizon exit fires when bars held >= horizon
  - state-break exit still fires (legacy behaviour preserved)
  - hold when within horizon and state matches
  - horizon_bars=0 disables horizon exit (state-break only)
  - no entry context -> no forced horizon exit (hold if state matches)

These run with no network / no API credentials. Warehouse state and entry
context are monkeypatched so the state-comparison path is isolated.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.position_manager import (  # noqa: E402
    ExitPolicy,
    _count_bars_after,
    _load_entry_context,
    _parse_ts,
    check_exits,
)


def _syn_warehouse(n=80, base=50.0, start="2026-01-01"):
    """Synthetic daily warehouse with adj OHLC + bar_ts_utc (survives
    compute_price_features, which needs >= 60 rows and high/low/close_adj)."""
    rng = np.arange(n)
    return pd.DataFrame({
        "bar_ts_utc": pd.date_range(start, periods=n, freq="D", tz="UTC"),
        "open_adj": base + rng,
        "high_adj": base + rng + 1.0,
        "low_adj": base + rng - 1.0,
        "close_adj": base + rng,
    })


def _positions_df(symbol="XLF"):
    return pd.DataFrame([{"symbol": symbol, "qty": 10, "side": "long"}])


SLICE = "state_ext=stretched_up + state_slope=flat"
STABLE_MATCH = {"state_ext": "stretched_up", "state_slope": "flat"}
STABLE_MISMATCH = {"state_ext": "stretched_down", "state_slope": "uptrend"}


# ---------------------------------------------------------------------------
# _count_bars_after / _parse_ts (pure helpers)
# ---------------------------------------------------------------------------

def test_count_bars_after_exact():
    df = _syn_warehouse(80)
    # Entry at the 10th bar (2026-01-11): 70 bars strictly after it.
    assert _count_bars_after("2026-01-11", df) == 69
    # Entry before any bar: all 80 bars after.
    assert _count_bars_after("2025-12-31", df) == 80
    # Entry at the last bar: 0 bars after.
    last_ts = str(df["bar_ts_utc"].iloc[-1])
    assert _count_bars_after(last_ts, df) == 0


def test_count_bars_after_none_cases():
    df = _syn_warehouse(80)
    assert _count_bars_after(None, df) is None
    assert _count_bars_after("2026-01-11", None) is None
    assert _count_bars_after("2026-01-11", pd.DataFrame()) is None
    no_ts = df.drop(columns=["bar_ts_utc"])
    assert _count_bars_after("2026-01-11", no_ts) is None
    assert _count_bars_after("not-a-timestamp", df) is None


def test_parse_ts_handles_naive_and_aware():
    assert _parse_ts(None) is None
    assert _parse_ts("garbage") is None
    aware = _parse_ts("2026-01-11")
    assert aware is not None and aware.tzinfo is not None


# ---------------------------------------------------------------------------
# check_exits (monkeypatched warehouse + entry context + state)
# ---------------------------------------------------------------------------

def _setup(monkeypatch, df, entry_ctx, state_dict):
    """Wire synthetic warehouse, entry context, and a controlled current state."""
    monkeypatch.setattr(
        "price.position_manager.load_from_warehouse", lambda *a, **k: df
    )
    monkeypatch.setattr(
        "price.position_manager._load_entry_context", lambda: entry_ctx
    )
    monkeypatch.setattr(
        "price.position_manager.current_state_to_dict",
        lambda row: dict(state_dict),
    )


def test_check_exits_empty_positions_returns_empty():
    assert check_exits(pd.DataFrame(), {}) == []
    assert check_exits(None, {}) == []


def test_horizon_exit_fires(monkeypatch):
    df = _syn_warehouse(80)
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": "2026-01-11", "submitted_at": "2026-01-11",
    }}  # 69 bars held >> 5
    _setup(monkeypatch, df, ctx, STABLE_MATCH)

    intents = check_exits(_positions_df(), {"XLF": SLICE})
    assert len(intents) == 1
    assert intents[0]["action"] == "exit"
    assert intents[0]["bars_held"] == 69
    assert "horizon reached" in intents[0]["reason"]
    # State still matches, so the only reason is horizon.
    assert "broken" not in intents[0]["reason"]


def test_hold_within_horizon(monkeypatch):
    df = _syn_warehouse(80)
    last_ts = str(df["bar_ts_utc"].iloc[-1])
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": last_ts, "submitted_at": last_ts,
    }}  # 0 bars held
    _setup(monkeypatch, df, ctx, STABLE_MATCH)

    intents = check_exits(_positions_df(), {"XLF": SLICE})
    assert intents[0]["action"] == "hold"
    assert intents[0]["bars_held"] == 0
    assert "matches" in intents[0]["reason"]


def test_state_break_exit_fires(monkeypatch):
    df = _syn_warehouse(80)
    last_ts = str(df["bar_ts_utc"].iloc[-1])
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": last_ts, "submitted_at": last_ts,
    }}  # within horizon, but state breaks
    _setup(monkeypatch, df, ctx, STABLE_MISMATCH)

    intents = check_exits(_positions_df(), {"XLF": SLICE})
    assert intents[0]["action"] == "exit"
    assert "broken" in intents[0]["reason"]
    assert "horizon" not in intents[0]["reason"]


def test_both_conditions_fire(monkeypatch):
    df = _syn_warehouse(80)
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": "2026-01-11", "submitted_at": "2026-01-11",
    }}  # horizon reached AND state broken
    _setup(monkeypatch, df, ctx, STABLE_MISMATCH)

    intents = check_exits(_positions_df(), {"XLF": SLICE})
    assert intents[0]["action"] == "exit"
    assert "broken" in intents[0]["reason"]
    assert "horizon reached" in intents[0]["reason"]


def test_horizon_disabled_is_state_break_only(monkeypatch):
    df = _syn_warehouse(80)
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": "2026-01-11", "submitted_at": "2026-01-11",
    }}  # would be horizon exit, but horizon disabled
    _setup(monkeypatch, df, ctx, STABLE_MATCH)

    intents = check_exits(_positions_df(), {"XLF": SLICE},
                          exit_policy=ExitPolicy(horizon_bars=0))
    assert intents[0]["action"] == "hold"
    assert "matches" in intents[0]["reason"]


def test_no_entry_context_no_forced_horizon_exit(monkeypatch):
    df = _syn_warehouse(80)
    _setup(monkeypatch, df, {}, STABLE_MATCH)  # no entry context at all

    intents = check_exits(_positions_df(), {"XLF": SLICE})
    assert intents[0]["action"] == "hold"
    assert intents[0]["bars_held"] is None
    assert "unknown" in intents[0]["reason"]


def test_intent_carries_audit_fields(monkeypatch):
    df = _syn_warehouse(80)
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": "2026-01-11", "submitted_at": "2026-01-11",
    }}
    _setup(monkeypatch, df, ctx, STABLE_MATCH)

    intent = check_exits(_positions_df(), {"XLF": SLICE})[0]
    for k in ("symbol", "slice_combination", "action", "reason",
              "bars_held", "horizon_bars", "timeframe", "stable_filter",
              "current_stable_state"):
        assert k in intent
    assert intent["horizon_bars"] == 5
    assert intent["timeframe"] == "1d"


def test_timeframe_resolved_from_context_overrides_heuristic(monkeypatch):
    """If the journal recorded timeframe=1h, check_exits loads the 1h
    partition even though the slice has no state_session field."""
    df = _syn_warehouse(80)
    loaded = {}

    def fake_load(symbol, timeframe):
        loaded["tf"] = timeframe
        return df

    monkeypatch.setattr("price.position_manager.load_from_warehouse", fake_load)
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1h",
        "entry_bar_ts": "2026-01-11", "submitted_at": "2026-01-11",
    }}
    monkeypatch.setattr("price.position_manager._load_entry_context", lambda: ctx)
    monkeypatch.setattr(
        "price.position_manager.current_state_to_dict",
        lambda row: dict(STABLE_MATCH),
    )

    check_exits(_positions_df(), {"XLF": SLICE})
    assert loaded["tf"] == "1h"


def test_horizon_uses_own_timeframe_bar_count(monkeypatch):
    """5 bars on 1h is NOT the same wall-clock window as 5 bars on 1d; the
    count is over the warehouse partition that was loaded for that timeframe."""
    # 80 hourly bars starting at 09:30; entry at bar 70 -> 9 bars held.
    df = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-01-05 09:30", periods=80, freq="h", tz="UTC"),
        "open_adj": np.arange(80, dtype=float),
        "high_adj": np.arange(80, dtype=float) + 1,
        "low_adj": np.arange(80, dtype=float) - 1,
        "close_adj": np.arange(80, dtype=float),
    })
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1h",
        "entry_bar_ts": str(df["bar_ts_utc"].iloc[70]),
        "submitted_at": str(df["bar_ts_utc"].iloc[70]),
    }}
    _setup(monkeypatch, df, ctx, STABLE_MATCH)

    intent = check_exits(_positions_df(), {"XLF": SLICE})[0]
    assert intent["bars_held"] == 9
    assert intent["action"] == "exit"  # 9 >= 5


def test_entry_context_bin_mode_defaults_without_type_error(monkeypatch):
    """Legacy journals without bin_mode must still produce usable exit
    context; adding the default must not call the cleaner with two args."""
    import price.trading as trading

    monkeypatch.setattr(
        trading,
        "load_trade_journal",
        lambda: pd.DataFrame([{
            "action": "entry",
            "symbol": "XLF",
            "broker_status": "filled",
            "filled_qty": 10,
            "timestamp_utc": "2026-07-13T15:00:00+00:00",
            "slice_label": SLICE,
            "timeframe": "1d",
            "entry_bar_ts": "2026-07-13T00:00:00+00:00",
            "submitted_at": "2026-07-13T15:00:00+00:00",
        }]),
    )

    context = _load_entry_context()
    assert context["XLF"]["bin_mode"] == "insample"
