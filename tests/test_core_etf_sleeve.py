"""Tests for the Low-DOF Core ETF Sleeve, ETF execution cost tiering,
sector-relative features, validation external-state attachment, and DSR
effective-sample outlier hardening."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from price.core_etf_sleeve import (  # noqa: E402
    CORE_ETF_SLEEVE_SOURCE_NOTE,
    CORE_ETF_TEMPLATES,
    load_core_etf_sleeve_exemptions,
    merge_long_exempt_slices,
    select_core_etf_sleeve,
)
from price.cost_model import (  # noqa: E402
    CostModel,
    core_etf_cost_model,
    cost_model_for_lane,
    equity_execution_cost_model,
)
from price.discovery import apply_state_bins, discover_market_slices  # noqa: E402
from price.external_data import attach_lane_externals, reset_breadth_cache  # noqa: E402
from price.features import compute_price_features  # noqa: E402
from price.market_profiles import get_market_profile  # noqa: E402
from price.risk_limits import RiskLimits, check_entry  # noqa: E402
from price.universe import (  # noqa: E402
    CORE_LIQUID_ETFS,
    get_symbol_sector,
    get_symbol_sector_etf,
    is_core_liquid_etf,
)


def _synthetic_ohlcv(n: int = 80, start_price: float = 100.0, drift: float = 0.003, freq: str = "1D") -> pd.DataFrame:
    ts = pd.date_range("2025-01-01", periods=n, freq=freq, tz="UTC")
    idx = np.arange(n, dtype=float)
    close = start_price * np.exp(drift * idx + 0.01 * np.sin(idx / 2.0))
    return pd.DataFrame(
        {
            "bar_ts_utc": ts,
            "open_adj": close * 0.998,
            "high_adj": close * 1.008,
            "low_adj": close * 0.992,
            "close_adj": close,
            "open_raw": close * 0.998,
            "high_raw": close * 1.008,
            "low_raw": close * 0.992,
            "close_raw": close,
            "volume": np.full(n, 1_000_000.0),
        }
    )


def test_universe_gics_sectors_and_core_etf_lookup():
    assert len(CORE_LIQUID_ETFS) >= 20
    assert is_core_liquid_etf("spy")
    assert is_core_liquid_etf("XLK")
    assert is_core_liquid_etf("TLT")
    assert not is_core_liquid_etf("AAPL")
    assert not is_core_liquid_etf("BTC/USD")

    # Existing contracts preserved + expanded 11-sector coverage.
    assert get_symbol_sector("KLAC") == "SEMI_TECH"
    assert get_symbol_sector("JPM") == "FIN"
    assert get_symbol_sector("LLY") == "HLTH"
    assert get_symbol_sector("PG") == "CONS_STAP"
    assert get_symbol_sector("NEE") == "UTIL"
    assert get_symbol_sector("PLD") == "REIT"
    assert get_symbol_sector("UNKNOWN_XYZ") is None

    # Single stocks map to their GICS sector SPDR ETF; Core ETFs & unknowns map to SPY.
    assert get_symbol_sector_etf("AMAT") == "XLK"
    assert get_symbol_sector_etf("JPM") == "XLF"
    assert get_symbol_sector_etf("LLY") == "XLV"
    assert get_symbol_sector_etf("XLK") == "SPY"
    assert get_symbol_sector_etf("SPY") == "SPY"
    assert get_symbol_sector_etf("UNKNOWN_XYZ") == "SPY"


def test_equity_execution_cost_model_tiers_core_etfs_and_preserves_overrides():
    # Lane baseline contract stays 13.0 bps RT.
    assert abs(cost_model_for_lane("equity").round_trip_drag() - 0.0013) < 1e-9
    assert abs(core_etf_cost_model().round_trip_drag() - 0.0004) < 1e-9

    # Default / generic CostModel() -> 4.0 bps RT on Core Liquid ETFs, 13.0 bps on stocks.
    assert abs(equity_execution_cost_model("SPY", None).round_trip_drag() - 0.0004) < 1e-9
    assert abs(equity_execution_cost_model("XLK", CostModel()).round_trip_drag() - 0.0004) < 1e-9
    assert abs(equity_execution_cost_model("AAPL", CostModel()).round_trip_drag() - 0.0013) < 1e-9

    # Explicit custom CostModel override is respected even on a Core Liquid ETF.
    custom = CostModel(commission_bps=0.0, spread_bps=2.0, slippage_bps=3.0)  # 10 bps RT
    assert abs(equity_execution_cost_model("XLF", custom).round_trip_drag() - 0.0010) < 1e-9


def test_select_core_etf_sleeve_enforces_low_dof_orthogonality_and_cost_hurdle():
    lb = pd.DataFrame(
        [
            # XLP passes all gates; stronger template wins dedupe.
            {
                "symbol": "XLP",
                "timeframe": "1d",
                "slice_combination": CORE_ETF_TEMPLATES[4],
                "side": "long",
                "bin_mode": "rolling",
                "verdict": "survived",
                "valid_n": 55,
                "valid_n_eff": 35.0,
                "best_fwd_horizon": 20,
                "best_stop_atr_mult": 5.0,
                "walk_forward_survival_rate": 0.75,
                "valid_sharpe": 0.72,
                "valid_mean_ret_costadj": 0.0125,
                "valid_excess_vs_baseline": 0.0110,
                "valid_in_bull": False,
                "valid_in_neutral": True,
                "valid_in_bear": True,
            },
            # Second XLP template must be deduped (max 1 per ETF).
            {
                "symbol": "XLP",
                "timeframe": "1d",
                "slice_combination": CORE_ETF_TEMPLATES[3],
                "side": "long",
                "bin_mode": "rolling",
                "verdict": "survived",
                "valid_n": 90,
                "valid_n_eff": 45.0,
                "best_fwd_horizon": 20,
                "best_stop_atr_mult": 5.0,
                "walk_forward_survival_rate": 0.75,
                "valid_sharpe": 0.42,
                "valid_mean_ret_costadj": 0.0075,
                "valid_excess_vs_baseline": 0.0055,
                "valid_in_bull": True,
                "valid_in_neutral": True,
                "valid_in_bear": False,
            },
            # SPY passes.
            {
                "symbol": "SPY",
                "timeframe": "1d",
                "slice_combination": CORE_ETF_TEMPLATES[3],
                "side": "long",
                "bin_mode": "rolling",
                "verdict": "survived",
                "valid_n": 110,
                "valid_n_eff": 54.0,
                "best_fwd_horizon": 20,
                "best_stop_atr_mult": 5.0,
                "walk_forward_survival_rate": 0.50,
                "valid_sharpe": 0.35,
                "valid_mean_ret_costadj": 0.0082,
                "valid_excess_vs_baseline": 0.0052,
            },
            # Single stock with identical stats MUST be excluded.
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "slice_combination": CORE_ETF_TEMPLATES[4],
                "side": "long",
                "bin_mode": "rolling",
                "verdict": "survived",
                "valid_n": 80,
                "valid_n_eff": 50.0,
                "best_fwd_horizon": 20,
                "best_stop_atr_mult": 5.0,
                "walk_forward_survival_rate": 1.0,
                "valid_sharpe": 0.90,
                "valid_mean_ret_costadj": 0.0200,
                "valid_excess_vs_baseline": 0.0150,
            },
            # Noisy 5-day horizon ETF row MUST be excluded (min_exit_horizon=10).
            {
                "symbol": "QQQ",
                "timeframe": "1d",
                "slice_combination": CORE_ETF_TEMPLATES[0],
                "side": "long",
                "bin_mode": "rolling",
                "verdict": "survived",
                "valid_n": 120,
                "valid_n_eff": 90.0,
                "best_fwd_horizon": 5,
                "best_stop_atr_mult": 3.0,
                "walk_forward_survival_rate": 0.75,
                "valid_sharpe": 0.50,
                "valid_mean_ret_costadj": 0.0060,
                "valid_excess_vs_baseline": 0.0045,
            },
        ]
    )

    out = select_core_etf_sleeve(lb, include_diagnostics=True)
    assert list(out["symbol"]) == ["XLP", "SPY"]
    assert set(out["source_note"]) == {CORE_ETF_SLEEVE_SOURCE_NOTE}
    assert list(out["exit_horizon"]) == [20, 20]
    assert out.loc[out["symbol"] == "XLP", "slice_combination"].iloc[0] == CORE_ETF_TEMPLATES[4]
    assert out.loc[out["symbol"] == "XLP", "valid_regimes"].iloc[0] == '["neutral", "bear"]'


def test_core_etf_sleeve_exemptions_allow_only_tagged_etf_1d_slices(tmp_path):
    mon_path = tmp_path / "monitored_slices.csv"
    pd.DataFrame(
        [
            {
                "symbol": "SPY",
                "timeframe": "1d",
                "slice_combination": CORE_ETF_TEMPLATES[3],
                "side": "long",
                "source_note": CORE_ETF_SLEEVE_SOURCE_NOTE,
                "bin_mode": "rolling",
            },
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "slice_combination": CORE_ETF_TEMPLATES[3],
                "side": "long",
                "source_note": "auto_promoted_tradeable",
                "bin_mode": "rolling",
            },
        ]
    ).to_csv(mon_path, index=False)

    loaded = load_core_etf_sleeve_exemptions(mon_path)
    assert loaded == frozenset({f"SPY|1d|rolling|{CORE_ETF_TEMPLATES[3]}"})

    exempt = merge_long_exempt_slices([], lane="equity", monitored_path=mon_path, allow_core_etf_sleeve=True)
    limits = RiskLimits(
        long_blocked_timeframes=frozenset({"1d", "15m"}),
        long_exempt_slices=exempt,
    )

    # Core ETF 1d sleeve slice passes the 1d pool gate.
    res_etf = check_entry(
        symbol="SPY",
        qty=2,
        price=500.0,
        limits=limits,
        open_positions=[],
        today_realized_pnl=0.0,
        side="long",
        timeframe="1d",
        slice_combination=CORE_ETF_TEMPLATES[3],
        bin_mode="rolling",
    )
    assert res_etf.allowed is True

    # Single-stock 1d slice with the exact same combo remains blocked.
    res_stock = check_entry(
        symbol="AAPL",
        qty=5,
        price=200.0,
        limits=limits,
        open_positions=[],
        today_realized_pnl=0.0,
        side="long",
        timeframe="1d",
        slice_combination=CORE_ETF_TEMPLATES[3],
        bin_mode="rolling",
    )
    assert res_stock.allowed is False
    assert any("slice not exempt" in r for r in res_stock.reasons)

    # --no-core-etf-sleeve and non-equity lanes disable automatic exemptions.
    assert merge_long_exempt_slices([], lane="equity", monitored_path=mon_path, allow_core_etf_sleeve=False) == frozenset()
    assert merge_long_exempt_slices([], lane="crypto", monitored_path=mon_path, allow_core_etf_sleeve=True) == frozenset()


def test_sector_relative_features_causal_and_validation_attaches_externals(monkeypatch):
    import validate_slices

    reset_breadth_cache()
    spy_df = _synthetic_ohlcv(90, start_price=400.0, drift=0.002)
    xlk_df = _synthetic_ohlcv(90, start_price=200.0, drift=0.003)
    # AMAT dips sharply over the last 10 bars while XLK and SPY stay in uptrends.
    amat_df = _synthetic_ohlcv(90, start_price=150.0, drift=0.004)
    amat_df.loc[75:82, "close_adj"] *= 0.88
    amat_df.loc[75:82, "high_adj"] *= 0.89
    amat_df.loc[75:82, "low_adj"] *= 0.87

    store = {("SPY", "1d"): spy_df, ("XLK", "1d"): xlk_df, ("AMAT", "1d"): amat_df}

    def _fake_load(sym: str, tf: str) -> pd.DataFrame:
        return store.get((sym.upper(), tf), pd.DataFrame()).copy()

    monkeypatch.setattr("price.warehouse.load_from_warehouse", _fake_load)
    monkeypatch.setattr("price.discovery.load_from_warehouse", _fake_load)
    monkeypatch.setattr(validate_slices, "load_from_warehouse", _fake_load)
    monkeypatch.setattr("price.external_data.fetch_vix_daily", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr("price.external_data.fetch_dxy_daily", lambda *a, **k: pd.DataFrame())

    feat = attach_lane_externals(compute_price_features(amat_df), "AMAT")
    binned = apply_state_bins(feat, bin_mode="rolling")

    # During AMAT's idiosyncratic dip (bar 78 vs bar 73) while XLK & SPY > SMA20:
    dip_row = binned.iloc[78]
    assert dip_row["state_sector_regime"] == "sector_uptrend"
    assert dip_row["state_rel_ext_sector"] == "rel_ext_oversold"
    assert dip_row["state_rel_ret_sector"] == "rel_ret_lagging"

    # SPY self-relative return/extension stays neutral while its trend regime works.
    spy_binned = apply_state_bins(attach_lane_externals(compute_price_features(spy_df), "SPY"), bin_mode="rolling")
    assert set(spy_binned["state_rel_ret_sector"].dropna().unique()) == {"rel_ret_inline"}
    assert set(spy_binned["state_rel_ext_sector"].dropna().unique()) == {"rel_ext_inline"}
    assert "sector_uptrend" in set(spy_binned["state_sector_regime"].dropna().unique())

    # build_eligible_frame in validate_slices attaches external & sector-relative states
    # so external-state slices never silently validate with valid_n=0.
    elig = validate_slices.build_eligible_frame("AMAT", "1d", bin_mode="rolling")
    assert not elig.empty
    assert "sector_uptrend" in set(elig["state_sector_regime"].unique())

    # Standalone discover_market_slices (without _precomputed_binned) also attaches externals.
    disc = discover_market_slices("AMAT", "1d", ["state_sector_regime", "state_rel_ext_sector"], min_samples=3, bin_mode="rolling")
    assert not disc.empty
    reset_breadth_cache()


def test_dsr_excludes_low_effective_n_cluster_outliers_from_sr_std():
    from price.validation import deflated_sharpe
    from validate_slices import annotate_search_wide_significance

    lb = pd.DataFrame(
        [
            {"symbol": "A", "valid_p_value_nw": 0.001, "valid_n": 100, "valid_n_eff": 80.0, "valid_sharpe": 0.45, "valid_skew": 0.0, "valid_kurtosis": 3.0},
            {"symbol": "B", "valid_p_value_nw": 0.020, "valid_n": 90, "valid_n_eff": 70.0, "valid_sharpe": 0.25, "valid_skew": 0.0, "valid_kurtosis": 3.0},
            {"symbol": "C", "valid_p_value_nw": 0.250, "valid_n": 75, "valid_n_eff": 60.0, "valid_sharpe": 0.05, "valid_skew": 0.0, "valid_kurtosis": 3.0},
            # Overlapped 20-bar cluster: raw valid_n=22 >= 15, but effective N is only 3.0
            # with a pathological Sharpe=35.0. Must count in N=4 trials, NOT in sr_std.
            {"symbol": "CLUSTER_OUTLIER", "valid_p_value_nw": 0.010, "valid_n": 22, "valid_n_eff": 3.0, "valid_sharpe": 35.0, "valid_skew": 0.0, "valid_kurtosis": 3.0},
        ]
    )
    out = annotate_search_wide_significance(lb, min_samples=15)
    expected_a = deflated_sharpe(
        [0.45, 0.25, 0.05],
        n_trials=4,
        observed_sr=0.45,
        n_obs=80.0,
        skewness=0.0,
        kurtosis=3.0,
    )
    assert int(out["search_wide_family_size"].iloc[0]) == 4
    assert abs(float(out.loc[out["symbol"] == "A", "search_wide_dsr"].iloc[0]) - expected_a) < 1e-6


def test_equity_default_conditioning_and_capture_sector_benchmarks(tmp_path):
    import capture_bars
    import discover_slices

    assert get_market_profile("equity").default_condition_symbols == ("SPY", "QQQ")

    # Equity default combos include sector-relative rules; crypto/futures never do.
    eq_combos = discover_slices._build_combinations("1d", profile="default")
    assert ["state_sector_regime", "state_rel_ext_sector"] in eq_combos
    for non_eq in ("crypto", "futures"):
        non_eq_combos = discover_slices._build_combinations("1d", profile=non_eq)
        flat = {f for c in non_eq_combos for f in c}
        assert "state_sector_regime" not in flat
        assert "state_rel_ext_sector" not in flat

    # capture_bars augments equity batches with SPY + required sector ETFs when
    # monitored_slices.csv uses sector-relative or cross_QQQ_ states.
    mon = tmp_path / "monitored_slices.csv"
    pd.DataFrame(
        [
            {"symbol": "AMAT", "timeframe": "1h", "slice_combination": "state_sector_regime=sector_uptrend + state_rel_ext_sector=rel_ext_oversold"},
            {"symbol": "JPM", "timeframe": "1h", "slice_combination": "cross_QQQ_state_slope=uptrend + state_ext=stretched_down"},
        ]
    ).to_csv(mon, index=False)

    augmented = capture_bars.augment_with_sector_benchmark_etfs(["AMAT", "JPM"], data_dir=tmp_path)
    assert set(augmented) == {"AMAT", "JPM", "SPY", "QQQ", "XLK"}

    # Crypto batches are never polluted by equity benchmark ETFs.
    assert capture_bars.augment_with_sector_benchmark_etfs(["BTC/USD", "ETH/USD"], data_dir=tmp_path) == ["BTC/USD", "ETH/USD"]


def test_intraday_sector_context_daily_fallback_blocks_same_day_lookahead(monkeypatch):
    reset_breadth_cache()
    # Daily SPY & XLK flip from downtrend to uptrend ONLY on the final UTC day (day 39).
    spy_1d = _synthetic_ohlcv(40, start_price=400.0, drift=-0.005, freq="1D")
    xlk_1d = _synthetic_ohlcv(40, start_price=200.0, drift=-0.005, freq="1D")
    spy_1d.loc[39, "close_adj"] = 600.0
    xlk_1d.loc[39, "close_adj"] = 300.0

    # Intraday 1h bars for AMAT on day 39 (no 1h benchmark bars in warehouse -> 1d fallback).
    day39 = spy_1d["bar_ts_utc"].iloc[39]
    amat_1h = _synthetic_ohlcv(40, start_price=150.0, drift=0.001, freq="1h")
    amat_1h["bar_ts_utc"] = pd.date_range(day39 - pd.Timedelta(hours=25), periods=40, freq="1h", tz="UTC")

    store = {("SPY", "1d"): spy_1d, ("XLK", "1d"): xlk_1d}
    monkeypatch.setattr("price.warehouse.load_from_warehouse", lambda s, t: store.get((s.upper(), t), pd.DataFrame()).copy())

    feat = attach_lane_externals(compute_price_features(amat_1h), "AMAT")
    same_day_rows = feat[feat["bar_ts_utc"].dt.date == day39.date()]
    assert not same_day_rows.empty
    # Same-day intraday bars on day 39 must see day 38's downtrend (-1.0), NOT day 39's EOD jump (+1.0)!
    assert set(same_day_rows["feat_sector_regime"].dropna().unique()) == {-1.0}
    # And daily ret_5 / atr_ext scales are never mixed into 1h bars.
    assert same_day_rows["feat_rel_ret_sector_z"].isna().all()
    assert same_day_rows["feat_rel_ext_sector_atr"].isna().all()
    reset_breadth_cache()


def test_run_discovery_discovers_spy_without_self_conditioning(monkeypatch, tmp_path):
    import discover_slices
    from price.discovery import clear_cond_bins_cache

    reset_breadth_cache()
    clear_cond_bins_cache()
    spy_df = _synthetic_ohlcv(90, start_price=400.0, drift=0.002)
    qqq_df = _synthetic_ohlcv(90, start_price=350.0, drift=0.003)
    store = {("SPY", "1d"): spy_df, ("QQQ", "1d"): qqq_df}

    monkeypatch.setattr("price.warehouse.load_from_warehouse", lambda s, t: store.get((s.upper(), t), pd.DataFrame()).copy())
    monkeypatch.setattr("price.discovery.load_from_warehouse", lambda s, t: store.get((s.upper(), t), pd.DataFrame()).copy())
    out_csv = tmp_path / "discovered.csv"
    monkeypatch.setattr(discover_slices, "DISCOVERED_SLICES_PATH", str(out_csv))

    discover_slices.run_discovery(
        target_symbols=["SPY"],
        timeframe="1d",
        min_samples=5,
        cond_symbols=["SPY", "QQQ"],
        bin_mode="rolling",
    )
    assert out_csv.exists()
    disc = pd.read_csv(out_csv)
    assert not disc.empty
    assert set(disc["symbol"].unique()) == {"SPY"}
    assert not disc["slice_combination"].astype(str).str.contains("cross_SPY_").any()
    assert disc["slice_combination"].astype(str).str.contains("cross_QQQ_").any()
    clear_cond_bins_cache()
    reset_breadth_cache()

