"""Tests for the direction-agnostic (long + short) infrastructure.

Covers:
  1. direction_adjusted_returns (core sign-flip + borrow drag)
  2. Side tagging in discovery (side column + tradeable_mean_fwd_ret_5)
  3. RiskLimits.allow_shorts kill-switch via check_entry
  4. Backward compatibility: all defaults behave as before (side="long")
"""

import numpy as np
import pandas as pd
import pytest

from price.validation import (
    apply_transaction_cost,
    direction_adjusted_returns,
)
from price.risk_limits import RiskLimits, check_entry


# ---------------------------------------------------------------------------
# direction_adjusted_returns
# ---------------------------------------------------------------------------

class TestDirectionAdjustedReturns:
    """Core sign-adjustment + cost logic."""

    @pytest.fixture()
    def returns(self):
        return pd.Series([0.01, -0.02, 0.005, -0.01])

    def test_long_matches_apply_transaction_cost(self, returns):
        """side='long' must reproduce the legacy path exactly."""
        expected = apply_transaction_cost(returns, cost_bps=1.0, round_trip=True)
        got = direction_adjusted_returns(returns, side="long", cost_bps=1.0)
        pd.testing.assert_series_equal(got, expected, check_names=False)

    def test_short_negates_returns(self, returns):
        """Short P&L = -(fwd_ret) - spread cost."""
        got = direction_adjusted_returns(returns, side="short", cost_bps=0.0)
        expected = -returns
        pd.testing.assert_series_equal(got, expected, check_names=False)

    def test_short_adds_borrow_cost(self, returns):
        """short_cost_bps adds extra drag on top of spread cost."""
        base = direction_adjusted_returns(
            returns, side="short", cost_bps=1.0, short_cost_bps=0.0,
        )
        with_borrow = direction_adjusted_returns(
            returns, side="short", cost_bps=1.0, short_cost_bps=5.0,
        )
        # Every observation should be lower by (5/10000)*2 per leg (round-trip)
        drag = (5.0 / 10000.0) * 2.0
        pd.testing.assert_series_equal(
            with_borrow, base - drag, check_names=False, atol=1e-12,
        )

    def test_long_ignores_short_cost_bps(self, returns):
        """Longs must be unaffected by short_cost_bps."""
        without = direction_adjusted_returns(
            returns, side="long", cost_bps=1.0, short_cost_bps=0.0,
        )
        with_borrow = direction_adjusted_returns(
            returns, side="long", cost_bps=1.0, short_cost_bps=10.0,
        )
        pd.testing.assert_series_equal(without, with_borrow, check_names=False)

    def test_promotable_short_has_positive_mean(self):
        """A negative-mean raw return slice, after negation, should have
        a positive mean (the core insight of direction-adjusted validation)."""
        raw = pd.Series([-0.02, -0.01, -0.015, 0.005])
        assert raw.mean() < 0, "precondition: raw mean is negative"
        adj = direction_adjusted_returns(raw, side="short", cost_bps=0.0)
        assert adj.mean() > 0, "after negation, the short edge is positive"


# ---------------------------------------------------------------------------
# Discovery side tagging
# ---------------------------------------------------------------------------

class TestDiscoverySideTag:
    """discovery.discover_market_slices should tag side and tradeable mean."""

    def _make_df(self, mean_ret, n=30, seed=42):
        """Build a minimal binned+eligible DataFrame with controllable mean."""
        rng = np.random.RandomState(seed)
        base_time = pd.Timestamp("2024-01-01", tz="UTC")
        return pd.DataFrame({
            "bar_ts_utc": [base_time + pd.Timedelta(hours=i) for i in range(n)],
            "symbol": "TEST",
            "timeframe": "1h",
            "close_adj": 100.0,
            "fwd_ret_5": rng.normal(mean_ret, 0.001, n),
            "fwd_mfe_5": 0.01,
            "fwd_mae_5": -0.01,
            "label_eligible": True,
            "state_slope": "downtrend",
        })

    def _patch_discovery(self, monkeypatch, df):
        """Patch warehouse + features on the discovery module namespace."""
        import price.discovery as disc
        monkeypatch.setattr(disc, "load_from_warehouse", lambda *a, **kw: df)
        monkeypatch.setattr(disc, "compute_price_features", lambda d: d)
        monkeypatch.setattr(disc, "bin_features", lambda d: d)

    def test_negative_mean_tagged_short(self, monkeypatch):
        """A slice with mean_fwd_ret < 0 should get side='short'."""
        from price.discovery import discover_market_slices

        df = self._make_df(mean_ret=-0.005)
        self._patch_discovery(monkeypatch, df)

        result = discover_market_slices("TEST", "1h", ["state_slope"], min_samples=5)
        assert not result.empty
        row = result.iloc[0]
        assert row["side"] == "short"
        assert row["tradeable_mean_fwd_ret_5"] > 0

    def test_positive_mean_tagged_long(self, monkeypatch):
        """A slice with mean_fwd_ret > 0 should get side='long'."""
        from price.discovery import discover_market_slices

        df = self._make_df(mean_ret=0.005, seed=99)
        self._patch_discovery(monkeypatch, df)

        result = discover_market_slices("TEST", "1h", ["state_slope"], min_samples=5)
        assert not result.empty
        row = result.iloc[0]
        assert row["side"] == "long"
        assert row["tradeable_mean_fwd_ret_5"] == pytest.approx(row["mean_fwd_ret_5"])

    def test_tradeable_sort_order(self, monkeypatch):
        """Slices should be sorted by tradeable_mean_fwd_ret_5 descending."""
        from price.discovery import discover_market_slices

        rng = np.random.RandomState(7)
        n = 60
        base_time = pd.Timestamp("2024-01-01", tz="UTC")
        df = pd.DataFrame({
            "bar_ts_utc": [base_time + pd.Timedelta(hours=i) for i in range(n)],
            "symbol": "TEST",
            "timeframe": "1h",
            "close_adj": 100.0,
            "fwd_ret_5": rng.normal(0.0, 0.02, n),
            "fwd_mfe_5": 0.01,
            "fwd_mae_5": -0.01,
            "label_eligible": True,
            "state_slope": rng.choice(["uptrend", "downtrend"], n),
        })

        self._patch_discovery(monkeypatch, df)

        result = discover_market_slices("TEST", "1h", ["state_slope"], min_samples=5)
        if len(result) > 1 and "tradeable_mean_fwd_ret_5" in result.columns:
            vals = result["tradeable_mean_fwd_ret_5"].tolist()
            assert vals == sorted(vals, reverse=True), "must be sorted descending"



# ---------------------------------------------------------------------------
# RiskLimits.allow_shorts
# ---------------------------------------------------------------------------

class TestAllowShortsRiskGate:
    """The risk gate must block short entries unless explicitly enabled."""

    def _base_limits(self, **kwargs):
        return RiskLimits(
            max_notional_per_position=10000,
            max_open_positions=5,
            max_daily_realized_loss=1000,
            per_symbol_cooldown_seconds=0,
            **kwargs,
        )

    def test_short_blocked_by_default(self):
        limits = self._base_limits()
        assert limits.allow_shorts is False
        result = check_entry(
            symbol="SPY", qty=10, price=100.0,
            limits=limits, open_positions=[], today_realized_pnl=0.0,
            side="short",
        )
        assert not result.allowed
        assert any("shorts not enabled" in r for r in result.reasons)

    def test_short_allowed_when_enabled(self):
        limits = self._base_limits(allow_shorts=True)
        result = check_entry(
            symbol="SPY", qty=10, price=100.0,
            limits=limits, open_positions=[], today_realized_pnl=0.0,
            side="short",
        )
        assert result.allowed, f"Expected allowed, got reasons: {result.reasons}"

    def test_long_unaffected_by_allow_shorts_false(self):
        limits = self._base_limits(allow_shorts=False)
        result = check_entry(
            symbol="SPY", qty=10, price=100.0,
            limits=limits, open_positions=[], today_realized_pnl=0.0,
            side="long",
        )
        assert result.allowed, f"Long should pass, got reasons: {result.reasons}"

    def test_side_in_details(self):
        limits = self._base_limits()
        result = check_entry(
            symbol="SPY", qty=10, price=100.0,
            limits=limits, open_positions=[], today_realized_pnl=0.0,
            side="short",
        )
        assert result.details["side"] == "short"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """All existing callers that don't pass side should behave as before."""

    def test_direction_adjusted_returns_default_is_long(self):
        r = pd.Series([0.01, -0.02])
        default = direction_adjusted_returns(r, cost_bps=1.0)
        explicit_long = direction_adjusted_returns(r, side="long", cost_bps=1.0)
        pd.testing.assert_series_equal(default, explicit_long, check_names=False)

    def test_check_entry_default_side_is_long(self):
        limits = RiskLimits(
            max_notional_per_position=10000,
            max_open_positions=5,
            max_daily_realized_loss=1000,
            per_symbol_cooldown_seconds=0,
            allow_shorts=False,
        )
        # Calling without side= should default to "long" and pass
        result = check_entry(
            symbol="SPY", qty=10, price=100.0,
            limits=limits, open_positions=[], today_realized_pnl=0.0,
        )
        assert result.allowed, f"Default (long) should pass, got: {result.reasons}"

    def test_risk_limits_to_dict_includes_allow_shorts(self):
        limits = RiskLimits()
        d = limits.to_dict()
        assert "allow_shorts" in d
        assert d["allow_shorts"] is False
