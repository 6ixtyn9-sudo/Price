"""Red-Team Gauntlet Tests - Proven vulnerabilities and their fixes.

Every test here encodes a concrete exploit found during red-teaming of
the Price equities engine (WS1-4 applied).

G1: Falling knife multi-day re-entry (knife_cooldown_days)
G2: Same-scan sector cap bypass via static exposure snapshot
G3: Extreme edge magnitude bounded by clip (bulletproof)
G5: Corporate action / stale warehouse silent death
"""
import pytest
import pandas as pd

from price.risk_limits import RiskLimits, check_sector_concentration_cap
from price.stops import stopout_count_within_days, record_stopout, reset_stopout_journal
from price.monitor import _corporate_action_break_reason


# ─────────────────────────────────────────────────────────────────────────────
# G1: Knife cooldown — rolling-window journal counter
# ─────────────────────────────────────────────────────────────────────────────

def test_g1_knife_cooldown_rolling_window_counts_recent_stopout(tmp_path):
    """stopout_count_within_days correctly counts stop-outs in the last N days."""
    journal = tmp_path / "stopout_journal.json"
    record_stopout("KNIFE_SYM", path=journal)
    assert stopout_count_within_days("KNIFE_SYM", days=2, path=journal) == 1


def test_g1_knife_cooldown_ignores_clean_symbol(tmp_path):
    """stopout_count_within_days returns 0 for symbols with no stop-outs."""
    journal = tmp_path / "stopout_journal.json"
    assert stopout_count_within_days("CLEAN_SYM", days=2, path=journal) == 0


def test_g1_knife_cooldown_is_valid_risk_limits_field():
    """knife_cooldown_days must be a named field on RiskLimits."""
    limits = RiskLimits(knife_cooldown_days=2)
    assert limits.knife_cooldown_days == 2

    limits_off = RiskLimits(knife_cooldown_days=0)
    assert limits_off.knife_cooldown_days == 0


# ─────────────────────────────────────────────────────────────────────────────
# G2: Same-scan sector cap bypass (static snapshot race)
# ─────────────────────────────────────────────────────────────────────────────

def test_g2_sector_cap_blocks_third_semi_after_same_scan_commit(monkeypatch):
    """Simulates the mutable sector_commit pattern used in scan_all_slices.

    With NVDA open (1 SEMI) and KLAC approved in the same scan, the commit
    list must be updated before LRCX is evaluated, blocking the 3rd SEMI.
    """
    import price.universe as univ
    monkeypatch.setattr(
        univ,
        "get_symbol_sector",
        lambda sym: "SEMI_TECH" if sym in ("KLAC", "LRCX", "AMAT", "NVDA") else None,
    )

    # Start with 1 open position (NVDA)
    sector_commit = [{"symbol": "NVDA"}]

    # KLAC: first candidate — should pass (count 1 < max 2)
    allowed_klac = check_sector_concentration_cap("KLAC", sector_commit, max_per_sector=2)
    assert allowed_klac, "KLAC should pass when only 1 SEMI open"

    # Same-scan sector_commit append (the fix in scan_all_slices)
    sector_commit.append({"symbol": "KLAC"})

    # LRCX: second new candidate in same scan — must be blocked (count 2 >= max 2)
    allowed_lrcx = check_sector_concentration_cap("LRCX", sector_commit, max_per_sector=2)
    assert not allowed_lrcx, "LRCX (3rd SEMI) must be blocked by the sector cap after same-scan commit"

    # AMAT: same scan — also blocked
    allowed_amat = check_sector_concentration_cap("AMAT", sector_commit, max_per_sector=2)
    assert not allowed_amat, "AMAT (4th SEMI) must also be blocked"


def test_g2_static_snapshot_would_have_admitted_all_three(monkeypatch):
    """Prove the pre-fix vulnerability: a static (non-updated) snapshot lets all three through."""
    import price.universe as univ
    monkeypatch.setattr(
        univ,
        "get_symbol_sector",
        lambda sym: "SEMI_TECH" if sym in ("KLAC", "LRCX", "AMAT", "NVDA") else None,
    )

    # Bug: exposure_snapshot is materialised once and never updated
    exposure_snapshot = [{"symbol": "NVDA"}]

    results = {}
    for candidate in ("KLAC", "LRCX", "AMAT"):
        results[candidate] = check_sector_concentration_cap(
            candidate, exposure_snapshot, max_per_sector=2
        )
        # BUG: snapshot never updated, so all three pass!

    # Before the fix, all three would pass — confirm the exploit
    assert all(results.values()), (
        "Static snapshot must let all three through (this is the vulnerability being fixed)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# G3: Extreme edge magnitude is bounded (bulletproof)
# ─────────────────────────────────────────────────────────────────────────────

def test_g3_extreme_edge_clipped_to_conviction_one():
    """compute_conviction with an astronomically large edge must clip to 1.0."""
    from price.sizing import SliceEdge, compute_conviction

    big_edge = SliceEdge(
        mean_return=1e9,
        excess_vs_parent=1e9,
        walk_forward_pass_count=10,
        scenario_survived_count=10,
        valid_n=500,
        search_wide_bh_pass=True,
        search_wide_bonferroni_pass=True,
        expected_return=1e9,
    )
    result = compute_conviction(edge=big_edge)
    assert 0 < result.conviction <= 1.0, (
        f"Conviction {result.conviction} must be in (0, 1] regardless of edge magnitude"
    )


def test_g3_nan_edge_does_not_crash_sizing():
    """A SliceEdge with NaN expected_return must still yield a safe conviction."""
    from price.sizing import SliceEdge, compute_conviction

    nan_edge = SliceEdge(
        mean_return=float("nan"),
        excess_vs_parent=0.0,
        walk_forward_pass_count=0,
        scenario_survived_count=0,
        valid_n=50,
        search_wide_bh_pass=False,
        search_wide_bonferroni_pass=False,
        expected_return=float("nan"),
    )
    # Must not raise; conviction should be the minimum safe value
    result = compute_conviction(edge=nan_edge)
    assert result.conviction >= 0


# ─────────────────────────────────────────────────────────────────────────────
# G5: Corporate action / stale warehouse discontinuity guard
# ─────────────────────────────────────────────────────────────────────────────

def test_g5_split_artifact_triggers_corp_action_guard():
    """A 3:1 split causes a -66% drop in close_adj; guard must fire."""
    df = pd.DataFrame({
        "close_adj": [100.0, 33.33],
        "bar_ts_utc": ["2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"],
    })
    reason = _corporate_action_break_reason(df, "1d")
    assert reason == "corporate_action_unresolved_adjustment", (
        f"Expected corp-action guard reason, got: {reason}"
    )


def test_g5_normal_crash_does_not_trigger_corp_action_guard():
    """A genuine -18% crash must NOT be blocked by the corp action guard."""
    df = pd.DataFrame({
        "close_adj": [100.0, 82.0],   # -18%
        "bar_ts_utc": ["2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"],
    })
    reason = _corporate_action_break_reason(df, "1d")
    assert reason is None, (
        f"An 18% crash should NOT trigger the corp-action guard; got: {reason}"
    )


def test_g5_split_factor_column_triggers_corp_action_guard():
    """Explicit split_factor != 1 in the frame must also trip the guard."""
    df = pd.DataFrame({
        "close_adj": [100.0, 100.5],   # prices look fine post-adjustment
        "split_factor": [1.0, 3.0],    # but the split marker is present
        "bar_ts_utc": ["2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"],
    })
    reason = _corporate_action_break_reason(df, "1d")
    assert reason == "corporate_action_unresolved_adjustment", (
        f"split_factor=3.0 should trigger corp-action guard; got: {reason}"
    )


def test_g5_no_split_no_large_move_passes_guard():
    """Normal daily bar with no split and small moves must not be blocked."""
    df = pd.DataFrame({
        "close_adj": [100.0, 101.5, 100.8, 102.0],
        "split_factor": [1.0, 1.0, 1.0, 1.0],
        "bar_ts_utc": [
            "2026-07-28T00:00:00Z", "2026-07-29T00:00:00Z",
            "2026-07-30T00:00:00Z", "2026-07-31T00:00:00Z",
        ],
    })
    reason = _corporate_action_break_reason(df, "1d")
    assert reason is None, f"Normal bars must not trigger the guard; got: {reason}"
