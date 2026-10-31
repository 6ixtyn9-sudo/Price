"""Tests for the 2026-10 ROI pass: shadow gating, tail watchdog,
calibration, pool gate, cost-negated sizing, expired-order forwards.

Pure unit tests; no network, no credentials, no warehouse.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.roi_guards import (  # noqa: E402
    WatchdogConfig,
    calibration_stats,
    compute_pool_stats,
    compute_shadow_stats,
    forward_return_from_bars,
    shadow_key,
    shadow_verdict,
    stale_lots,
    update_demotion_state,
    watchdog_exit_reason,
)
from price.risk_limits import RiskLimits, check_entry  # noqa: E402


def _live(rows):
    return pd.DataFrame(rows)


def _sig(symbol="AAA", tf="1h", side="long", ret5=-0.01, ret20=-0.02, n=1, **kw):
    base = {"symbol": symbol, "timeframe": tf, "side": side,
            "slice_combination": "state_ext=neutral", "bin_mode": "rolling",
            "tradeable_fwd_ret_5b": ret5, "tradeable_fwd_ret_20b": ret20,
            "signal_ts_utc": "2026-10-01"}
    base.update(kw)
    return [dict(base) for _ in range(n)]


# ---------------------------------------------------------------------------
# Shadow stats / verdict / two-strike ledger
# ---------------------------------------------------------------------------

def test_shadow_stats_and_demote_candidate():
    live = _live(_sig(n=40) + _sig(symbol="BBB", ret5=0.01, ret20=0.02, n=40))
    stats = {r["symbol"]: r for r in compute_shadow_stats(live)}
    assert stats["AAA"]["n_5b"] == 40
    assert abs(stats["AAA"]["mean_5b_bps"] - (-100.0)) < 1e-6
    assert shadow_verdict(stats["AAA"]) == "demote_candidate"
    assert shadow_verdict(stats["BBB"]) == "ok"


def test_shadow_verdict_requires_both_horizons_and_min_n():
    live = _live(_sig(n=40, ret20=0.01))  # 20b contradicts the bleed
    row = compute_shadow_stats(live)[0]
    assert shadow_verdict(row) == "watch"
    small = compute_shadow_stats(_live(_sig(n=5)))[0]
    assert shadow_verdict(small) == "insufficient"
    mild = compute_shadow_stats(_live(_sig(n=40, ret5=-0.0005, ret20=-0.0005)))[0]
    assert shadow_verdict(mild) == "watch"  # negative but inside cost drag


def test_pool_stats_flags_1h_short_shape():
    live = _live(_sig(tf="1h", side="short", n=50, ret5=-0.002, ret20=-0.001)
                 + _sig(tf="1h", side="long", n=50, ret5=0.002, ret20=0.003))
    pools = {(p["timeframe"], p["side"]): p for p in compute_pool_stats(live)}
    assert pools[("1h", "short")]["mean_5b_bps"] < 0
    assert pools[("1h", "long")]["mean_5b_bps"] > 0


def test_two_strikes_need_distinct_cycles():
    key = shadow_key("AAA", "1h", "state_ext=neutral", "long", "rolling")
    state, demoted = update_demotion_state({}, {key: "demote_candidate"}, "2026-10-01")
    assert demoted == []
    # Same cycle re-run: no new strike.
    state, demoted = update_demotion_state(state, {key: "demote_candidate"}, "2026-10-01")
    assert demoted == []
    # New cycle: second strike demotes.
    state, demoted = update_demotion_state(state, {key: "demote_candidate"}, "2026-10-08")
    assert demoted == [key]
    # Recovery forgives.
    state, demoted = update_demotion_state(state, {key: "ok"}, "2026-10-15")
    assert demoted == []


# ---------------------------------------------------------------------------
# Watchdog (pure)
# ---------------------------------------------------------------------------

def test_watchdog_max_hold_and_winner_exemption():
    cfg = WatchdogConfig(max_horizon_multiple=3.0, hard_loss_r=2.0)
    assert watchdog_exit_reason(bars_held=16, horizon_bars=5, r_multiple=0.2, config=cfg)
    assert watchdog_exit_reason(bars_held=16, horizon_bars=5, r_multiple=None, config=cfg)
    # Confirmed winner: trailing stop is the watchdog, time cap exempt.
    assert watchdog_exit_reason(bars_held=99, horizon_bars=5, r_multiple=1.5, config=cfg) is None
    # Within cap: nothing.
    assert watchdog_exit_reason(bars_held=10, horizon_bars=5, r_multiple=0.0, config=cfg) is None


def test_watchdog_loser_stop_fires_only_underwater_at_fraction():
    cfg = WatchdogConfig(loser_stop_fraction=0.5)
    # horizon 20 -> trigger at ceil(0.5*20)=10 bars
    reason = watchdog_exit_reason(bars_held=10, horizon_bars=20, r_multiple=-0.4, config=cfg)
    assert reason is not None and reason.startswith("watchdog_loser_stop")
    # before the fraction: no fire
    assert watchdog_exit_reason(bars_held=9, horizon_bars=20, r_multiple=-0.4, config=cfg) is None
    # winners and flat positions are exempt at any age
    assert watchdog_exit_reason(bars_held=19, horizon_bars=20, r_multiple=0.3, config=cfg) is None
    assert watchdog_exit_reason(bars_held=19, horizon_bars=20, r_multiple=0.0, config=cfg) is None
    # unknown R never fires the loser stop (other rules own that case)
    assert watchdog_exit_reason(bars_held=19, horizon_bars=20, r_multiple=None, config=cfg) is None
    # ceil behaviour on odd horizons: 0.5 x 5 -> 3 bars
    assert watchdog_exit_reason(bars_held=2, horizon_bars=5, r_multiple=-0.1, config=cfg) is None
    assert watchdog_exit_reason(bars_held=3, horizon_bars=5, r_multiple=-0.1, config=cfg) is not None


def test_watchdog_loser_stop_disabled_by_default_and_hard_loss_precedence():
    assert WatchdogConfig().enabled is False
    assert WatchdogConfig(loser_stop_fraction=0.5).enabled is True
    assert watchdog_exit_reason(bars_held=99, horizon_bars=5, r_multiple=-0.5,
                                config=WatchdogConfig()) is None
    # hard-loss still outranks the loser stop when both apply
    cfg = WatchdogConfig(hard_loss_r=2.0, loser_stop_fraction=0.5)
    reason = watchdog_exit_reason(bars_held=10, horizon_bars=20, r_multiple=-2.5, config=cfg)
    assert reason is not None and reason.startswith("watchdog_hard_loss")


def test_watchdog_hard_loss_fires_even_for_managed_trade():
    cfg = WatchdogConfig(max_horizon_multiple=3.0, hard_loss_r=2.0)
    reason = watchdog_exit_reason(bars_held=1, horizon_bars=5, r_multiple=-2.5, config=cfg)
    assert reason and "watchdog_hard_loss" in reason
    assert watchdog_exit_reason(bars_held=99, horizon_bars=5, r_multiple=-9.0,
                                config=WatchdogConfig()) is None  # disabled


def test_stale_lots_age_filter():
    lots = [{"symbol": "A", "entry_ts": "2026-07-01", "age_days": 80.0},
            {"symbol": "B", "entry_ts": "2026-10-10", "age_days": 5.0}]
    assert [l["symbol"] for l in stale_lots(lots, max_age_days=30)] == ["A"]


# ---------------------------------------------------------------------------
# Forward returns + calibration
# ---------------------------------------------------------------------------

def test_forward_return_from_bars_direction_adjusted():
    bars = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC"),
        "close_adj": [100.0] * 5 + [110.0] * 5,
    })
    assert abs(forward_return_from_bars(bars, "2026-01-01", 5, "long") - 0.10) < 1e-9
    assert abs(forward_return_from_bars(bars, "2026-01-01", 5, "short") + 0.10) < 1e-9
    assert forward_return_from_bars(bars, "2026-01-01", 20, "long") is None
    assert forward_return_from_bars(None, "2026-01-01", 5, "long") is None


def test_calibration_stats_verdicts():
    weak = calibration_stats([(0.01 * i, -0.001 * i, 5) for i in range(1, 15)])
    assert weak["verdict"] == "uncalibrated_freeze_conviction"
    strong = calibration_stats([(0.005 * i, 0.004 * i, 5) for i in range(1, 15)])
    assert strong["verdict"] == "calibrated"
    few = calibration_stats([(0.01, 0.01, 3)] * 4)
    assert few["verdict"] == "insufficient"


# ---------------------------------------------------------------------------
# Risk gate: short pool gate
# ---------------------------------------------------------------------------

def _limits(**kw):
    base = dict(max_notional_per_position=2500.0, max_open_positions=20,
                max_daily_realized_loss=500.0, per_symbol_cooldown_seconds=0,
                allow_shorts=True, whipsaw_stopout_limit=0,
                max_aggregate_open_risk_pct=None, margin_cushion_pct=None)
    base.update(kw)
    return RiskLimits(**base)


def test_short_pool_gate_blocks_1h_short_only():
    limits = _limits(short_blocked_timeframes=frozenset({"1h"}))
    blocked = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="short",
                          timeframe="1h", slice_combination="sl")
    assert not blocked.allowed
    assert any("pool gate" in r for r in blocked.reasons)
    ok_tf = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="short",
                        timeframe="15m", slice_combination="sl")
    assert ok_tf.allowed
    ok_long = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="long",
                          timeframe="1h", slice_combination="sl")
    assert ok_long.allowed
    # Legacy callers without timeframe context fail open.
    legacy = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="short")
    assert legacy.allowed


def test_short_pool_gate_exemption():
    limits = _limits(
        short_blocked_timeframes=frozenset({"1h"}),
        short_exempt_slices=frozenset({"AAA|1h|sl"}),
    )
    res = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="short",
                      timeframe="1h", slice_combination="sl")
    assert res.allowed


def test_long_pool_gate_blocks_1d_long_only():
    limits = _limits(long_blocked_timeframes=frozenset({"1d"}))
    blocked = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="long",
                          timeframe="1d", slice_combination="sl")
    assert not blocked.allowed
    assert any("long entries blocked on 1d by pool gate" in r for r in blocked.reasons)
    ok_tf = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="long",
                        timeframe="1h", slice_combination="sl")
    assert ok_tf.allowed
    ok_short = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="short",
                           timeframe="1d", slice_combination="sl")
    assert ok_short.allowed
    legacy = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="long")
    assert legacy.allowed


def test_long_pool_gate_exemption():
    limits = _limits(
        long_blocked_timeframes=frozenset({"1d"}),
        long_exempt_slices=frozenset({"AAA|1d|sl"}),
    )
    res = check_entry("AAA", 10, 100.0, limits, [], 0.0, side="long",
                      timeframe="1d", slice_combination="sl")
    assert res.allowed


# ---------------------------------------------------------------------------
# Sizing: cost-negated = zero
# ---------------------------------------------------------------------------

def test_cost_negated_edge_sizes_to_zero(tmp_path):
    from price.sizing import compute_position_size
    lb = pd.DataFrame([{
        "symbol": "AAA", "timeframe": "1d", "slice_combination": "sl",
        "valid_mean_ret_costadj": 0.001,  # 10bp < 13bp default drag
        "valid_excess_vs_best_parent": 0.005, "valid_n": 100,
        "walk_forward_pass_count": 4, "scenario_survived_count": 8,
    }])
    p = tmp_path / "lb.csv"
    lb.to_csv(p, index=False)
    size = compute_position_size("AAA", "1d", "sl", 100.0, _limits(),
                                 leaderboard_path=p)
    assert size.sizing_mode == "cost_negated"
    assert size.qty == 0
    assert size.qty_notional >= 1  # would-be size stays in the audit trail


# ---------------------------------------------------------------------------
# Monitor: demotion loader
# ---------------------------------------------------------------------------

def test_load_shadow_demotions_fails_open(tmp_path):
    from price.monitor import _load_shadow_demotions
    assert _load_shadow_demotions(tmp_path / "missing.csv") == set()
    p = tmp_path / "demoted.csv"
    pd.DataFrame([{"symbol": "AAA", "timeframe": "1h", "bin_mode": "rolling",
                   "side": "short", "slice_combination": "sl"}]).to_csv(p, index=False)
    keys = _load_shadow_demotions(p)
    assert shadow_key("AAA", "1h", "sl", "short", "rolling") in keys


def test_per_slice_cycle_keys_prevent_unrelated_slice_from_manufacturing_strike():
    # AAA has 35 losing signals on Oct 01; SPY gets a fresh 15m signal on Oct 02.
    # AAA's own data has NOT changed, so AAA must stay at strike 1 until AAA
    # itself accrues new resolved observations.
    live_cycle_1 = _live(
        _sig(symbol="AAA", tf="1d", n=35, signal_ts_utc="2026-10-01T16:00:00Z")
        + _sig(symbol="SPY", tf="15m", ret5=0.01, ret20=0.02, n=35, signal_ts_utc="2026-10-01 16:00:00+00:00")
    )
    stats_1 = compute_shadow_stats(live_cycle_1)
    verdicts_1 = {r["key"]: shadow_verdict(r) for r in stats_1}
    cycles_1 = {r["key"]: r["cycle_key"] for r in stats_1}
    aaa_key = shadow_key("AAA", "1d", "state_ext=neutral", "long", "rolling")

    state, demoted = update_demotion_state(
        {}, verdicts_1, "2026-10-01T16:00:00+00:00", slice_cycle_keys=cycles_1
    )
    assert state["strikes"][aaa_key] == 1
    assert demoted == []

    # Unrelated SPY 15m signal advances global timestamp (space format > 'T' if
    # parsed chronologically, not lexicographically), while AAA is unchanged.
    live_cycle_2 = _live(
        _sig(symbol="AAA", tf="1d", n=35, signal_ts_utc="2026-10-01T16:00:00Z")
        + _sig(symbol="SPY", tf="15m", ret5=0.01, ret20=0.02, n=36, signal_ts_utc="2026-10-02 14:15:00+00:00")
    )
    stats_2 = compute_shadow_stats(live_cycle_2)
    verdicts_2 = {r["key"]: shadow_verdict(r) for r in stats_2}
    cycles_2 = {r["key"]: r["cycle_key"] for r in stats_2}
    state, demoted = update_demotion_state(
        state, verdicts_2, "2026-10-02T14:15:00+00:00", slice_cycle_keys=cycles_2
    )
    assert state["strikes"][aaa_key] == 1
    assert demoted == []

    # Now AAA itself accrues new losing evidence -> strike 2 -> demoted.
    live_cycle_3 = _live(
        _sig(symbol="AAA", tf="1d", n=36, signal_ts_utc="2026-10-03 20:00:00+00:00")
        + _sig(symbol="SPY", tf="15m", ret5=0.01, ret20=0.02, n=36, signal_ts_utc="2026-10-02 14:15:00+00:00")
    )
    stats_3 = compute_shadow_stats(live_cycle_3)
    verdicts_3 = {r["key"]: shadow_verdict(r) for r in stats_3}
    cycles_3 = {r["key"]: r["cycle_key"] for r in stats_3}
    state, demoted = update_demotion_state(
        state, verdicts_3, "2026-10-03T20:00:00+00:00", slice_cycle_keys=cycles_3
    )
    assert state["strikes"][aaa_key] == 2
    assert demoted == [aaa_key]


def test_loser_stop_deadband_and_full_horizon_guard():
    cfg = WatchdogConfig(loser_stop_fraction=0.5)
    # 1-cent / spread noise (-0.01R inside 0.05R deadband) does not prematurely exit.
    assert watchdog_exit_reason(bars_held=3, horizon_bars=5, r_multiple=-0.01, config=cfg) is None
    # Materially underwater (-0.08R) fires.
    assert watchdog_exit_reason(bars_held=3, horizon_bars=5, r_multiple=-0.08, config=cfg) is not None
    # Degenerate horizon_bars=1 (trigger == horizon_bars) leaves exit labeling to normal horizon rule.
    assert watchdog_exit_reason(bars_held=1, horizon_bars=1, r_multiple=-0.4, config=cfg) is None


def test_pool_gate_exemption_normalizes_case_and_whitespace():
    limits = _limits(
        long_blocked_timeframes=frozenset({"1d"}),
        long_exempt_slices=frozenset({" aapl | 1d | sl "}),
    )
    res = check_entry("AAPL", 10, 100.0, limits, [], 0.0, side="long",
                      timeframe="1d", slice_combination="sl")
    assert res.allowed

