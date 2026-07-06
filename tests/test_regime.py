"""Tests for the regime deployment gate (price.regime).

Covers:
  - assess_regime: SMA crossover classification (bull/bear/neutral/unknown)
  - resolve_regime_symbol: per-slice symbol resolution (own / cross / configured)
  - check_regime: enabled/disabled pass-through + fail-open on missing data
  - RegimeState.favourable: the gate decision (bear blocks, others allow)
  - The semantic invariant: a bear regime blocks dip-buying slices.

Pure unit tests on synthetic warehouse frames; no network, no credentials.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.regime import (  # noqa: E402
    RegimeState,
    assess_regime,
    check_regime,
    resolve_regime_symbol,
)


def _wh(monkeypatch, tmp_path, symbol, closes, freq="D"):
    """Write a synthetic 1d warehouse for `symbol` and monkeypatch the loader."""
    n = len(closes)
    df = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC"),
        "open_adj": closes, "high_adj": closes, "low_adj": closes, "close_adj": closes,
    })
    d = tmp_path / "wh" / f"symbol={symbol}" / "timeframe=1d"
    d.mkdir(parents=True, exist_ok=True)
    df.to_parquet(d / "data.parquet", index=False)
    # Monkeypatch the loader used inside assess_regime.
    import price.regime as regime_mod

    def fake_load(sym, tf="1d"):
        if sym == symbol:
            return df
        return pd.DataFrame()
    monkeypatch.setattr(regime_mod, "load_from_warehouse", fake_load)
    return df


# ---------------------------------------------------------------------------
# assess_regime (the SMA classifier)
# ---------------------------------------------------------------------------

def test_bull_regime_when_short_above_long(monkeypatch, tmp_path):
    # Rising series: SMA50 above SMA200, price above both.
    closes = list(np.linspace(100, 200, 250))  # steady uptrend, 250 bars
    _wh(monkeypatch, tmp_path, "SPY", closes)
    r = assess_regime("SPY", "1d")
    assert r.regime == "bull"
    assert r.favourable() is True
    assert r.short_ma > r.long_ma


def test_bear_regime_when_short_below_long(monkeypatch, tmp_path):
    # Falling series: SMA50 below SMA200, price below both.
    closes = list(np.linspace(200, 100, 250))
    _wh(monkeypatch, tmp_path, "SPY", closes)
    r = assess_regime("SPY", "1d")
    assert r.regime == "bear"
    assert r.favourable() is False
    assert r.short_ma < r.long_ma


def test_short_history_falls_back_to_50_only(monkeypatch, tmp_path):
    # 80 bars (< 200): uses short-MA only. Rising -> bull-ish.
    closes = list(np.linspace(100, 150, 80))
    _wh(monkeypatch, tmp_path, "SPY", closes)
    r = assess_regime("SPY", "1d")
    assert r.regime in ("bull", "neutral")
    assert r.long_ma is None  # not enough history for the 200-MA


def test_unknown_on_missing_data(monkeypatch, tmp_path):
    import price.regime as regime_mod

    monkeypatch.setattr(regime_mod, "load_from_warehouse",
                        lambda *a, **k: pd.DataFrame())
    r = assess_regime("NOPE", "1d")
    assert r.regime == "unknown"
    assert r.favourable() is True  # fail-open


def test_unknown_on_warehouse_exception(monkeypatch, tmp_path):
    import price.regime as regime_mod

    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(regime_mod, "load_from_warehouse", boom)
    r = assess_regime("SPY", "1d")
    assert r.regime == "unknown"
    assert r.favourable() is True  # fail-open


def test_unknown_on_insufficient_history(monkeypatch, tmp_path):
    closes = list(np.linspace(100, 110, 20))  # < SHORT_MA(50)
    _wh(monkeypatch, tmp_path, "SPY", closes)
    r = assess_regime("SPY", "1d")
    assert r.regime == "unknown"
    assert r.favourable() is True  # fail-open


# ---------------------------------------------------------------------------
# resolve_regime_symbol (per-slice resolution)
# ---------------------------------------------------------------------------

def test_explicit_configured_symbol_wins():
    assert resolve_regime_symbol("XOP", {}, "SPY") == "SPY"


def test_falls_back_to_slice_own_symbol():
    assert resolve_regime_symbol("XOP", {}) == "XOP"


def test_uses_cross_asset_symbol_when_conditioned():
    # A slice conditioning on USO uses USO as its regime symbol.
    sf = {"cross_USO_state_slope": "uptrend", "state_ext": "stretched_down"}
    assert resolve_regime_symbol("XLE", sf) == "USO"


def test_explicit_overrides_cross():
    sf = {"cross_USO_state_slope": "uptrend"}
    assert resolve_regime_symbol("XLE", sf, "SPY") == "SPY"


# ---------------------------------------------------------------------------
# check_regime (the full gate, enabled/disabled + fail-open)
# ---------------------------------------------------------------------------

def test_check_regime_disabled_is_passthrough():
    r = check_regime("SPY", enabled=False)
    assert r.regime == "gate_disabled"
    assert r.favourable() is True


def test_check_regime_enabled_fails_open_on_missing(monkeypatch, tmp_path):
    import price.regime as regime_mod
    monkeypatch.setattr(regime_mod, "load_from_warehouse",
                        lambda *a, **k: pd.DataFrame())
    r = check_regime("NOPE", enabled=True)
    assert r.regime == "unknown"
    assert r.favourable() is True  # never blocks on missing data


def test_check_regime_enabled_uses_own_symbol(monkeypatch, tmp_path):
    closes = list(np.linspace(200, 100, 250))  # bear
    _wh(monkeypatch, tmp_path, "XOP", closes)
    r = check_regime("XOP", enabled=True)
    assert r.regime == "bear"
    assert r.favourable() is False


def test_check_regime_respects_configured_symbol(monkeypatch, tmp_path):
    # XOP is rising (bull) but configured regime symbol SPY is falling (bear).
    xop_closes = list(np.linspace(100, 200, 250))
    spy_closes = list(np.linspace(200, 100, 250))
    df_xop = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2024-01-01", periods=250, freq="D", tz="UTC"),
        "open_adj": xop_closes, "high_adj": xop_closes, "low_adj": xop_closes,
        "close_adj": xop_closes,
    })
    df_spy = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2024-01-01", periods=250, freq="D", tz="UTC"),
        "open_adj": spy_closes, "high_adj": spy_closes, "low_adj": spy_closes,
        "close_adj": spy_closes,
    })
    import price.regime as regime_mod

    def fake_load(sym, tf="1d"):
        return {"XOP": df_xop, "SPY": df_spy}.get(sym, pd.DataFrame())
    monkeypatch.setattr(regime_mod, "load_from_warehouse", fake_load)
    r = check_regime("XOP", configured_regime_symbol="SPY", enabled=True)
    assert r.regime == "bear"
    assert r.favourable() is False
    assert r.symbol == "SPY"


# ---------------------------------------------------------------------------
# RegimeState audit + favourable semantics
# ---------------------------------------------------------------------------

def test_audit_dict_is_flat_and_csv_safe():
    r = RegimeState(symbol="SPY", regime="bull", close=100.0,
                    short_ma=99.0, long_ma=98.0, reason="SMA50/200")
    d = r.to_audit_dict()
    for k in ["regime_symbol", "regime", "regime_favourable", "regime_close",
              "regime_short_ma", "regime_long_ma"]:
        assert k in d
    assert all(not isinstance(v, (dict, list)) for v in d.values())


def test_favourable_allows_bull_neutral_unknown_blocks_bear():
    assert RegimeState("X", "bull").favourable() is True
    assert RegimeState("X", "neutral").favourable() is True
    assert RegimeState("X", "unknown").favourable() is True
    assert RegimeState("X", "bear").favourable() is False
