"""Tests for the 2026-08-10 exit-path fixes.

1. Blank/unavailable stable state must HOLD, not exit.
   A stable field whose current value is blank ("" because current_state_to_dict
   turns NaN -> "") means the state could not be classified (e.g. missing/NaN
   close). That is missing data, NOT evidence the thesis broke. Exiting on it
   manufactured whipsaw at bars_held=0.

2. Cross-timeframe exit prevention.
   A position must be exited on the timeframe of the SLICE that owns it, not on
   a faster sibling's rulebook (which cut a 1d edge after 3 hourly bars).
"""
import numpy as np
import pandas as pd

from price.position_manager import (
    ExitPolicy,
    check_exits,
    lookup_slice_parameters,
)

SLICE = "state_ext=stretched_up + state_slope=flat"
STABLE_MATCH = {"state_ext": "stretched_up", "state_slope": "flat"}
BLANK_STATE = {"state_ext": "", "state_slope": ""}       # unavailable -> hold
PARTIAL_BLANK = {"state_ext": "stretched_up", "state_slope": ""}  # one blank


def _syn_warehouse(n=80, base=50.0, start="2026-01-01"):
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


def _setup(monkeypatch, df, entry_ctx, state_dict):
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
    # default exit policy (horizon_bars=5, no pure-horizon hold)
    monkeypatch.setattr("price.position_manager.lookup_slice_parameters",
                        lambda *a, **k: {"exit_horizon": None, "stop_atr_mult": None, "timeframe": None})


# ---------------------------------------------------------------------------
# Fix 1: blank/unavailable state must HOLD, not exit
# ---------------------------------------------------------------------------

def test_blank_state_holds_not_exits(monkeypatch):
    """Fully blank current state (unavailable) must hold, never exit."""
    df = _syn_warehouse(80)
    last_ts = str(df["bar_ts_utc"].iloc[-1])
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": last_ts, "submitted_at": last_ts,
    }}
    _setup(monkeypatch, df, ctx, BLANK_STATE)
    intents = check_exits(_positions_df(), {"XLF": SLICE}, exit_policy=ExitPolicy())
    assert intents[0]["action"] == "hold", intents[0]["reason"]
    assert "broken" not in intents[0]["reason"]
    # audit should surface that we held because state was unavailable
    assert intents[0]["state_unavailable_fields"] == ["state_ext", "state_slope"]


def test_partial_blank_does_not_exit_on_blank_field(monkeypatch):
    """If one stable field is blank but the other matches, do NOT exit."""
    df = _syn_warehouse(80)
    last_ts = str(df["bar_ts_utc"].iloc[-1])
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": last_ts, "submitted_at": last_ts,
    }}
    _setup(monkeypatch, df, ctx, PARTIAL_BLANK)
    intents = check_exits(_positions_df(), {"XLF": SLICE}, exit_policy=ExitPolicy())
    # state_slope blank -> no genuine mismatch -> hold
    assert intents[0]["action"] == "hold", intents[0]["reason"]
    assert intents[0]["state_unavailable_fields"] == ["state_slope"]


def test_genuine_mismatch_still_exits(monkeypatch):
    """A real, non-blank state that differs from expected still exits."""
    df = _syn_warehouse(80)
    last_ts = str(df["bar_ts_utc"].iloc[-1])
    ctx = {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": last_ts, "submitted_at": last_ts,
    }}
    _setup(monkeypatch, df, ctx, STABLE_MISMATCH := {"state_ext": "stretched_down", "state_slope": "uptrend"})
    intents = check_exits(_positions_df(), {"XLF": SLICE}, exit_policy=ExitPolicy())
    assert intents[0]["action"] == "exit", intents[0]["reason"]
    assert "broken" in intents[0]["reason"]


# ---------------------------------------------------------------------------
# Fix 2: cross-timeframe exit prevention (slice's own timeframe wins)
# ---------------------------------------------------------------------------

def test_timeframe_prefers_slice_own_timeframe(monkeypatch):
    """The monitored slice's own timeframe must override the ctx (claiming) one."""
    df = _syn_warehouse(80)
    last_ts = str(df["bar_ts_utc"].iloc[-1])
    # ctx claims 1h (a faster sibling), but the monitored book says this slice is 1d
    ctx = {"KLAC": {
        "slice_combination": SLICE, "timeframe": "1h",
        "entry_bar_ts": last_ts, "submitted_at": last_ts,
    }}
    monkeypatch.setattr(
        "price.position_manager.load_from_warehouse", lambda *a, **k: df
    )
    monkeypatch.setattr(
        "price.position_manager._load_entry_context", lambda: ctx
    )
    monkeypatch.setattr(
        "price.position_manager.current_state_to_dict",
        lambda row: dict(STABLE_MATCH),
    )
    # monitored book says the slice belongs to the 1d rulebook
    monkeypatch.setattr(
        "price.position_manager.lookup_slice_parameters",
        lambda symbol, combo: {"exit_horizon": 5, "stop_atr_mult": None, "timeframe": "1d"},
    )
    intents = check_exits(
        _positions_df(symbol="KLAC"),
        {"KLAC": SLICE},
        exit_policy=ExitPolicy(horizon_bars=5),
    )
    # even at 5 hourly bars held, a 1d slice must NOT exit on the 1h rulebook
    # (bars_held here is tiny since daily warehouse; the key is timeframe resolution)
    assert intents[0]["timeframe"] == "1d", intents[0]


def test_lookup_slice_parameters_returns_timeframe():
    """The helper now surfaces the slice's own timeframe from the book."""
    # No book -> all None (graceful, no raise)
    import tempfile
    import os
    from pathlib import Path
    d = tempfile.mkdtemp()
    os.environ["PRICE_DATA_DIR"] = d  # point at empty dir
    try:
        res = lookup_slice_parameters("SPY", "state_ext=a")
    finally:
        os.environ.pop("PRICE_DATA_DIR", None)
    assert "timeframe" in res
