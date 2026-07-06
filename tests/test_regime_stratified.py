"""Tests for regime-stratified validation diagnostics.

Covers attach_regime_labels (the per-bar regime labeller) and the
run_regime_stratified_diagnostics function -- the regime-independence test
the HANDOVER's regime-confound finding identified as the path forward.

The decisive test: a slice whose edge is positive in bull but collapses in
bear (the regime-conditional fingerprint) is correctly shown by the
diagnostic as bull-positive / bear-negative. A structural edge passes both.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from price.regime import attach_regime_labels  # noqa: E402


def _series(closes, start="2024-01-01"):
    n = len(closes)
    return pd.DataFrame({
        "bar_ts_utc": pd.to_datetime(
            pd.date_range(start, periods=n, freq="D", tz="UTC")),
        "close_adj": closes,
    })


# ---------------------------------------------------------------------------
# attach_regime_labels (per-bar regime classification, look-ahead-free)
# ---------------------------------------------------------------------------

def test_labels_bull_on_uptrend(monkeypatch):
    # Steady 400-bar uptrend -> every bar after LONG_MA(200) warmup is bull.
    s = _series(np.linspace(100, 200, 400))
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: s)
    labelled = attach_regime_labels(s.copy(), "SPY")
    assert "regime" in labelled.columns
    post_warmup = labelled["regime"].iloc[200:]
    assert (post_warmup == "bull").mean() > 0.95


def test_labels_bear_on_downtrend(monkeypatch):
    s = _series(np.linspace(200, 100, 400))
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: s)
    labelled = attach_regime_labels(s.copy(), "SPY")
    post_warmup = labelled["regime"].iloc[200:]
    assert (post_warmup == "bear").mean() > 0.95


def test_insufficient_history_returns_no_regime_column(monkeypatch):
    # < SHORT_MA(50) bars -> no regime column (graceful degradation).
    s = _series(np.linspace(100, 110, 30))
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: s)
    labelled = attach_regime_labels(s.copy(), "SPY")
    assert "regime" not in labelled.columns  # unchanged, no crash


def test_missing_regime_symbol_returns_df_unchanged(monkeypatch):
    s = _series(np.linspace(100, 200, 400))
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: pd.DataFrame())
    labelled = attach_regime_labels(s.copy(), "NOPE")
    assert "regime" not in labelled.columns


def test_no_overlap_in_time_gets_unavailable(monkeypatch):
    primary = _series(np.linspace(100, 200, 400), start="2024-01-01")
    regime = _series(np.linspace(100, 200, 400), start="2030-01-01")
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: regime)
    labelled = attach_regime_labels(primary, "SPY")
    assert (labelled["regime"] == "regime_unavailable").all()


# ---------------------------------------------------------------------------
# run_regime_stratified_diagnostics (the regime-independence test)
# ---------------------------------------------------------------------------

def _eligible_frame(n=400, fwd_ret=None):
    """Synthetic eligible frame; slice fires on every bar (stretched_up)."""
    ts = pd.to_datetime(pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"))
    close = 100 + np.arange(n, dtype=float) * 0.01
    df = pd.DataFrame({
        "bar_ts_utc": ts,
        "open_adj": close, "high_adj": close + 1, "low_adj": close - 1,
        "close_adj": close,
        "feat_ext_vs_ma_20": 0.02,  # > 0.015 -> always stretched_up
        "feat_trend_slope_20": 0.0001,
        "feat_realized_vol_20": 0.01,
        "feat_session_bucket": 2,
        "feat_dow": 0,
        "feat_ret_1": 0.0,
        "state_ext": "stretched_up",  # pre-binned so apply_slice_filter works
        "state_slope": "flat",
        "fwd_ret_5": fwd_ret if fwd_ret is not None else np.full(n, 0.02),
        "fwd_mfe_5": np.full(n, 0.03),
        "fwd_mae_5": np.full(n, 0.01),
        "label_eligible": True,
    })
    return df


def test_regime_diagnostics_runs_and_writes_csv(monkeypatch, tmp_path):
    import validate_slices as vs

    monkeypatch.setattr(
        vs, "select_diagnostic_targets",
        lambda **kw: [("TEST", "1d", "state_ext=stretched_up", "long")],
    )
    monkeypatch.setattr(vs, "build_eligible_frame", lambda *a, **k: _eligible_frame(400))
    regime_df = _series(np.linspace(100, 200, 400))
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: regime_df)

    out = tmp_path / "regime_diag.csv"
    df = vs.run_regime_stratified_diagnostics(
        min_samples=1, output_path=str(out), regime_symbol="SPY",
    )
    assert out.exists()
    assert not df.empty
    assert "all" in df["regime"].values
    assert "bull" in df["regime"].values
    for col in ["slice_mean_ret_costadj", "slice_n", "regime"]:
        assert col in df.columns


def test_regime_conditional_edge_shows_bull_positive_bear_negative(monkeypatch, tmp_path):
    """The decisive test: an edge +2% in bull, -2% in bear shows up as
    positive in bull and negative in bear. This is the regime-conditional
    fingerprint that time-stratified validation could not see."""
    import validate_slices as vs

    n = 400
    # V-shaped regime: 200 bars up (bull), 200 bars down (bear).
    regime_closes = np.concatenate([
        np.linspace(100, 200, 200), np.linspace(200, 100, 200),
    ])
    regime_df = _series(regime_closes)
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: regime_df)

    # Forward return: +2% in bars 200-399-bull-half, -2% in bear-half.
    # After LONG_MA warmup (~200 bars), bars 200-299 are bull, 300-399 are bear.
    fwd = np.zeros(n)
    fwd[200:300] = 0.02   # bull window -> positive edge
    fwd[300:] = -0.02     # bear window -> edge collapses
    eligible = _eligible_frame(n, fwd_ret=fwd)
    monkeypatch.setattr(vs, "build_eligible_frame", lambda *a, **k: eligible)
    monkeypatch.setattr(vs, "select_diagnostic_targets",
                        lambda **kw: [("TEST", "1d", "state_ext=stretched_up", "long")])

    out = tmp_path / "regime_diag.csv"
    df = vs.run_regime_stratified_diagnostics(
        min_samples=1, output_path=str(out), regime_symbol="SPY",
    )
    by_regime = {r["regime"]: r for _, r in df.iterrows() if r["diagnostic_status"] == "ok"}
    assert "bull" in by_regime, f"bull missing from {list(by_regime.keys())}"
    assert "bear" in by_regime, f"bear missing from {list(by_regime.keys())}"
    assert by_regime["bull"]["slice_mean_ret_costadj"] > 0
    assert by_regime["bear"]["slice_mean_ret_costadj"] < 0


def test_missing_regime_data_does_not_crash(monkeypatch, tmp_path):
    import validate_slices as vs
    import price.regime as rm
    monkeypatch.setattr(rm, "load_from_warehouse", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(vs, "build_eligible_frame", lambda *a, **k: _eligible_frame(400))
    monkeypatch.setattr(vs, "select_diagnostic_targets",
                        lambda **kw: [("TEST", "1d", "state_ext=stretched_up", "long")])
    out = tmp_path / "regime_diag.csv"
    df = vs.run_regime_stratified_diagnostics(
        min_samples=1, output_path=str(out), regime_symbol="NOPE",
    )
    assert not df.empty
    assert "all" in df["regime"].values
