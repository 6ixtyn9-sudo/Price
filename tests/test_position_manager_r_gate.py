"""Tests for the R-multiple horizon-suppression gate in check_exits
(price.position_manager): once a trade has confirmed to +1R, the 5-bar
time-stop is suppressed and the trade is left under trailing-stop
management instead -- the "small losses, large profits" design.

Mirrors the setup style in test_position_manager.py (synthetic warehouse
+ monkeypatched entry context + controlled current state), plus a
monkeypatched price.stops.load_stop_states so no real localdata/ file is
touched.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.position_manager import ExitPolicy, check_exits  # noqa: E402
from price.stops import new_stop_state  # noqa: E402


def _syn_warehouse(n=80, base=50.0, start="2026-01-01"):
    rng = np.arange(n)
    return pd.DataFrame({
        "bar_ts_utc": pd.date_range(start, periods=n, freq="D", tz="UTC"),
        "open_adj": base + rng,
        "high_adj": base + rng + 1.0,
        "low_adj": base + rng - 1.0,
        "close_adj": base + rng,
    })


def _positions_df(symbol="XLF", current_price=None):
    row = {"symbol": symbol, "qty": 10, "side": "long"}
    if current_price is not None:
        row["current_price"] = current_price
    return pd.DataFrame([row])


SLICE = "state_ext=stretched_up + state_slope=flat"
STABLE_MATCH = {"state_ext": "stretched_up", "state_slope": "flat"}


def _setup(monkeypatch, df, entry_ctx, state_dict):
    monkeypatch.setattr("price.position_manager.load_from_warehouse", lambda *a, **k: df)
    monkeypatch.setattr("price.position_manager._load_entry_context", lambda: entry_ctx)
    monkeypatch.setattr("price.position_manager.current_state_to_dict", lambda row: dict(state_dict))


def _far_past_horizon_ctx():
    return {"XLF": {
        "slice_combination": SLICE, "timeframe": "1d",
        "entry_bar_ts": "2026-01-11", "submitted_at": "2026-01-11",
    }}  # 69 bars held on an 80-bar warehouse >> horizon of 5


def test_horizon_exit_still_fires_when_no_tracked_stop_state(monkeypatch):
    """No StopState for this symbol -> the R-gate is inert; original
    unconditional horizon-exit behaviour is preserved exactly."""
    monkeypatch.setattr("price.stops.load_stop_states", lambda *a, **k: {})
    df = _syn_warehouse(80)
    _setup(monkeypatch, df, _far_past_horizon_ctx(), STABLE_MATCH)

    intents = check_exits(_positions_df(current_price=55.0), {"XLF": SLICE})
    assert intents[0]["action"] == "exit"
    assert "horizon reached" in intents[0]["reason"]
    assert intents[0]["r_multiple_suppressed_horizon"] is False


def test_horizon_exit_suppressed_when_trade_past_1r(monkeypatch):
    """Tracked StopState shows the trade is past +1R -> horizon exit is
    suppressed; the position is held (left to the trailing stop)."""
    state = new_stop_state("XLF", "long", qty=10, entry_price=50.0, atr=2.0)  # R=4
    monkeypatch.setattr("price.stops.load_stop_states", lambda *a, **k: {"XLF": state})
    df = _syn_warehouse(80)
    _setup(monkeypatch, df, _far_past_horizon_ctx(), STABLE_MATCH)

    intents = check_exits(_positions_df(current_price=55.0), {"XLF": SLICE})  # +1.25R
    assert intents[0]["action"] == "hold"
    assert intents[0]["r_multiple_suppressed_horizon"] is True
    assert "trailing-stop" in intents[0]["reason"]


def test_horizon_exit_still_fires_when_below_1r(monkeypatch):
    """Tracked StopState exists but trade has NOT yet reached +1R -> the
    horizon exit still fires normally (the gate only suppresses AFTER
    confirmation, never before)."""
    state = new_stop_state("XLF", "long", qty=10, entry_price=50.0, atr=2.0)  # R=4
    monkeypatch.setattr("price.stops.load_stop_states", lambda *a, **k: {"XLF": state})
    df = _syn_warehouse(80)
    _setup(monkeypatch, df, _far_past_horizon_ctx(), STABLE_MATCH)

    intents = check_exits(_positions_df(current_price=51.0), {"XLF": SLICE})  # +0.25R
    assert intents[0]["action"] == "exit"
    assert "horizon reached" in intents[0]["reason"]
    assert intents[0]["r_multiple_suppressed_horizon"] is False


def test_state_break_exit_fires_even_when_r_gate_would_suppress_horizon(monkeypatch):
    """The R-gate only suppresses the HORIZON condition. A stable-filter
    break must still fire the exit regardless of R state -- the thesis
    invalidation is a separate, unconditional signal."""
    state = new_stop_state("XLF", "long", qty=10, entry_price=50.0, atr=2.0)
    monkeypatch.setattr("price.stops.load_stop_states", lambda *a, **k: {"XLF": state})
    df = _syn_warehouse(80)
    broken_state = {"state_ext": "stretched_down", "state_slope": "downtrend"}
    _setup(monkeypatch, df, _far_past_horizon_ctx(), broken_state)

    intents = check_exits(_positions_df(current_price=55.0), {"XLF": SLICE})  # +1.25R
    assert intents[0]["action"] == "exit"
    assert "stable filter broken" in intents[0]["reason"]


def test_respect_r_multiple_gate_false_restores_legacy_unconditional_horizon(monkeypatch):
    """ExitPolicy(respect_r_multiple_gate=False) restores the original
    behaviour: horizon exit fires even for a confirmed (+1R) trade."""
    state = new_stop_state("XLF", "long", qty=10, entry_price=50.0, atr=2.0)
    monkeypatch.setattr("price.stops.load_stop_states", lambda *a, **k: {"XLF": state})
    df = _syn_warehouse(80)
    _setup(monkeypatch, df, _far_past_horizon_ctx(), STABLE_MATCH)

    policy = ExitPolicy(horizon_bars=5, respect_r_multiple_gate=False)
    intents = check_exits(_positions_df(current_price=55.0), {"XLF": SLICE}, exit_policy=policy)
    assert intents[0]["action"] == "exit"
    assert "horizon reached" in intents[0]["reason"]


def test_r_gate_inert_when_current_price_missing(monkeypatch):
    """No current_price column on the position row -> the R-gate can't be
    evaluated, so it fails to its inert (non-suppressing) default and the
    original unconditional horizon exit still fires. Never crashes."""
    state = new_stop_state("XLF", "long", qty=10, entry_price=50.0, atr=2.0)
    monkeypatch.setattr("price.stops.load_stop_states", lambda *a, **k: {"XLF": state})
    df = _syn_warehouse(80)
    _setup(monkeypatch, df, _far_past_horizon_ctx(), STABLE_MATCH)

    intents = check_exits(_positions_df(current_price=None), {"XLF": SLICE})
    assert intents[0]["action"] == "exit"
    assert "horizon reached" in intents[0]["reason"]


def test_r_gate_helper_handles_missing_stops_module_state_gracefully(monkeypatch):
    """If price.stops.load_stop_states itself raises, the gate must fail
    to its inert default rather than crashing check_exits."""
    def _raise(*a, **k):
        raise RuntimeError("disk error")
    monkeypatch.setattr("price.stops.load_stop_states", _raise)
    df = _syn_warehouse(80)
    _setup(monkeypatch, df, _far_past_horizon_ctx(), STABLE_MATCH)

    intents = check_exits(_positions_df(current_price=55.0), {"XLF": SLICE})
    assert intents[0]["action"] == "exit"  # falls back to normal horizon exit
