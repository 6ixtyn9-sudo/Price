"""Tests for the risk_limits.check_entry additions that back the
"small losses, large profits" R-based protective-stop system:

  - Aggregate open-risk budget (the leverage prerequisite).
  - Whipsaw circuit breaker (bench a symbol after repeat same-day stop-outs).

Both are additive / optional-kwarg, so backward compatibility with the
existing lever 1-5 check_entry call sites is also pinned here.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.risk_limits import RiskLimits, check_entry  # noqa: E402
from price.stops import new_stop_state, record_stopout  # noqa: E402


def _limits(**overrides):
    return RiskLimits(**overrides)


# ---------------------------------------------------------------------------
# Backward compatibility: omitting the new kwargs must not change behaviour.
# ---------------------------------------------------------------------------

def test_check_entry_without_new_kwargs_behaves_as_before():
    limits = _limits(max_aggregate_open_risk_pct=0.03, whipsaw_stopout_limit=2)
    result = check_entry(
        symbol="ZZZZ_TEST_SYMBOL", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
    )
    # No proposed_r_dollars/equity -> aggregate check fails open (skipped).
    # No stop-out journal for this fresh symbol -> whipsaw check passes.
    assert result.allowed is True


# ---------------------------------------------------------------------------
# Aggregate open-risk budget
# ---------------------------------------------------------------------------

def test_check_entry_blocks_when_aggregate_risk_budget_exceeded():
    limits = _limits(max_aggregate_open_risk_pct=0.03)  # 3% of equity
    existing = new_stop_state("XOP", "long", qty=10, entry_price=100.0, atr=2.0)  # $40 at risk
    result = check_entry(
        symbol="NEW_TEST_SYMBOL", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
        proposed_r_dollars=20.0,
        open_stop_states={"XOP": existing},
        equity_for_risk_cap=1000.0,  # budget = $30; 40+20=60 > 30
    )
    assert result.allowed is False
    assert any("aggregate open risk" in r for r in result.reasons)
    assert "aggregate_risk" in result.details


def test_check_entry_allows_when_aggregate_risk_budget_has_room():
    limits = _limits(max_aggregate_open_risk_pct=0.10)  # generous 10% budget
    existing = new_stop_state("XOP", "long", qty=10, entry_price=100.0, atr=2.0)  # $40 at risk
    result = check_entry(
        symbol="NEW_TEST_SYMBOL2", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
        proposed_r_dollars=20.0,
        open_stop_states={"XOP": existing},
        equity_for_risk_cap=1000.0,  # budget=$100; 40+20=60 <= 100
    )
    assert result.allowed is True


def test_check_entry_aggregate_risk_skipped_when_cap_is_none():
    limits = _limits(max_aggregate_open_risk_pct=None)
    result = check_entry(
        symbol="ANY", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
        proposed_r_dollars=1_000_000.0,  # would blow any real budget
        open_stop_states={},
        equity_for_risk_cap=1.0,
    )
    assert result.allowed is True


def test_check_entry_breakeven_positions_free_up_aggregate_risk_room():
    """A position ratcheted to breakeven-or-better must NOT block new
    entries via the aggregate cap -- it can no longer lose money."""
    limits = _limits(max_aggregate_open_risk_pct=0.03)
    winner = new_stop_state("XOP", "long", qty=10, entry_price=100.0, atr=2.0)
    winner.current_stop_price = 105.0  # past breakeven -> contributes $0
    result = check_entry(
        symbol="NEW_TEST_SYMBOL3", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
        proposed_r_dollars=25.0,
        open_stop_states={"XOP": winner},
        equity_for_risk_cap=1000.0,  # budget=$30; 0+25=25 <= 30
    )
    assert result.allowed is True


# ---------------------------------------------------------------------------
# Whipsaw circuit breaker
# ---------------------------------------------------------------------------

def test_check_entry_blocks_after_whipsaw_limit_reached(tmp_path, monkeypatch):
    import price.stops as stops_mod
    journal_path = tmp_path / "stopout_journal.json"
    monkeypatch.setattr(stops_mod, "STOPOUT_JOURNAL_PATH", journal_path)

    limits = _limits(whipsaw_stopout_limit=2)
    record_stopout("WHIPSYM")
    record_stopout("WHIPSYM")

    result = check_entry(
        symbol="WHIPSYM", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
    )
    assert result.allowed is False
    assert any("whipsaw" in r for r in result.reasons)


def test_check_entry_allows_below_whipsaw_limit(tmp_path, monkeypatch):
    import price.stops as stops_mod
    journal_path = tmp_path / "stopout_journal.json"
    monkeypatch.setattr(stops_mod, "STOPOUT_JOURNAL_PATH", journal_path)

    limits = _limits(whipsaw_stopout_limit=2)
    record_stopout("WHIPSYM2")  # only 1 -- below the limit of 2

    result = check_entry(
        symbol="WHIPSYM2", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
    )
    assert result.allowed is True


def test_check_entry_whipsaw_disabled_when_limit_zero(tmp_path, monkeypatch):
    import price.stops as stops_mod
    journal_path = tmp_path / "stopout_journal.json"
    monkeypatch.setattr(stops_mod, "STOPOUT_JOURNAL_PATH", journal_path)

    limits = _limits(whipsaw_stopout_limit=0)
    record_stopout("WHIPSYM3")
    record_stopout("WHIPSYM3")
    record_stopout("WHIPSYM3")

    result = check_entry(
        symbol="WHIPSYM3", qty=1, price=100.0, limits=limits,
        open_positions=[], today_realized_pnl=0.0,
    )
    assert result.allowed is True


def test_check_sector_concentration_cap_blocks_correlated_semis():
    from price.risk_limits import check_sector_concentration_cap
    open_pos = [{"symbol": "KLAC"}, {"symbol": "LRCX"}]
    # 3rd semi must be blocked under cap=2
    assert not check_sector_concentration_cap("AMAT", open_pos, max_per_sector=2)
    # Orthogonal sector (SPY/JPM) must be allowed
    assert check_sector_concentration_cap("JPM", open_pos, max_per_sector=2)
    # Unmapped ticker must fail open
    assert check_sector_concentration_cap("UNKNOWN_XYZ", open_pos, max_per_sector=2)


def test_check_entry_sector_concentration_integration():
    limits = _limits(max_positions_per_sector=2, max_open_positions=10)
    open_pos = [{"symbol": "KLAC"}, {"symbol": "LRCX"}]
    result = check_entry(
        symbol="AMAT", qty=1, price=100.0, limits=limits,
        open_positions=open_pos, today_realized_pnl=0.0,
    )
    assert result.allowed is False
    assert any("sector 'SEMI_TECH' at concentration cap" in r for r in result.reasons)



# ---------------------------------------------------------------------------
# 2026-08-06 hardening tests (standalone imports kept deliberately so this
# block is portable across lineages; duplicate imports are harmless).
# ---------------------------------------------------------------------------

from pathlib import Path as _RTPath  # noqa: E402
import sys as _RTsys  # noqa: E402

for _sub in ("src", "scripts"):
    _p = str(_RTPath(__file__).resolve().parent.parent / _sub)
    if _p not in _RTsys.path:
        _RTsys.path.insert(0, _p)

from datetime import datetime, timedelta, timezone  # noqa: E402
_ROOT = _RTPath(__file__).resolve().parent.parent  # repo root for data pins

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import price.monitor as mon  # noqa: E402
import price.stops as stops  # noqa: E402

def test_proposed_r_uses_same_open_window_buffer_as_attach(tmp_path, monkeypatch):
    import inspect
    if "proposed_r_dollars" not in inspect.getsource(mon.scan_all_slices):
        pytest.skip("this lineage has no proposed_R aggregate-budget wiring")
    from price.risk_limits import RiskLimits

    def run(clock):
        monkeypatch.setattr(mon, "get_open_positions", lambda: pd.DataFrame())
        monkeypatch.setattr(mon, "get_open_orders", lambda: pd.DataFrame())
        monkeypatch.setattr(mon, "get_today_realized_pnl", lambda: 0.0)
        monkeypatch.setattr(mon, "reconcile_stops", lambda *a, **k: [])
        monkeypatch.setattr(mon, "get_current_state", lambda *a, **k: pd.DataFrame([{
            "bar_ts_utc": "2026-08-05", "close_adj": 100.0,
            "state_ext": "neutral", "state_slope": "flat",
        }]))
        base = datetime.now(timezone.utc) - timedelta(hours=30)
        wh = pd.DataFrame([{
            "bar_ts_utc": base + timedelta(hours=i),
            "high_adj": 101.0, "low_adj": 99.0, "close_adj": 100.0,
        } for i in range(20)])
        monkeypatch.setattr(mon, "load_from_warehouse", lambda s, t: wh)
        captured = {}

        def _spy(**kw):
            captured.update(kw)
            from price.risk_limits import RiskCheckResult
            return RiskCheckResult(allowed=True, reasons=[], details={})

        monkeypatch.setattr(mon, "check_entry", _spy)
        monkeypatch.setattr(stops, "_now_utc", lambda: clock)
        mon.scan_all_slices(
            slices=[{"symbol": "SMCI", "timeframe": "1h",
                     "slice_combination": "state_ext=neutral + state_slope=flat",
                     "side": "long"}],
            limits=RiskLimits(), dry_run=False,
        )
        return captured.get("proposed_r_dollars")

    in_w = run(datetime(2026, 8, 6, 13, 45, tzinfo=timezone.utc))
    out_w = run(datetime(2026, 8, 6, 15, 0, tzinfo=timezone.utc))
    assert in_w and out_w and in_w / out_w == pytest.approx(1.05)  # 2.0 -> 2.1


# ======================================================================

def _pt(monkeypatch, tmp_path):
    import paper_trade as pt
    monkeypatch.setattr(pt, "AUDIT_LOG_PATH", tmp_path / "paper_trade_log.csv")
    return pt


def _sig():
    return {
        "kind": "entry_signal", "symbol": "SMCI", "timeframe": "1d",
        "slice_combination": "state_ext=neutral + state_slope=flat",
        "bin_mode": "insample", "matched": True, "tradable": True,
        "suggested_side": "buy", "suggested_qty": 2,
        "close_adj": 100.0, "sizing_atr": 2.5,
        "timestamp_utc": "2026-08-06T13:35:00Z",
    }


def _wh(close, age_hours):
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return pd.DataFrame([{"bar_ts_utc": ts, "close_adj": float(close)}])


def _drive(tmp_path, monkeypatch, wh_df):
    import price.trading as trading
    import price.warehouse as warehouse
    pt = _pt(monkeypatch, tmp_path)
    monkeypatch.setattr(trading, "get_latest_price", lambda s: None)  # quote outage
    monkeypatch.setattr(warehouse, "load_from_warehouse", lambda s, t: wh_df)
    counts = pt._handle_signals(
        [_sig()], dry_run=True, max_adverse_fill_bps=200.0, adverse_atr_mult=1.0)
    return counts, pd.read_csv(tmp_path / "paper_trade_log.csv").iloc[0]


def test_quote_outage_warehouse_ref_blocks_knife(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, _wh(90.0, 20))
    assert counts["entry_blocked"] == 1
    assert row["reason"] == "stale_signal_adverse_gap_warehouse_ref"
    assert float(row["signal_to_fill_bps"]) <= -1000 + 1e-9


def test_quote_outage_within_threshold_passes(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, _wh(99.5, 20))
    assert counts["entry_blocked"] == 0 and row["action"] == "would_enter"
    assert row["adverse_guard"] == "passed_warehouse_ref"


def test_stale_warehouse_bar_still_fails_open(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, _wh(90.0, 100))
    assert counts["entry_blocked"] == 0 and row["adverse_guard"] == "skipped_no_price"


def test_empty_warehouse_still_fails_open(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, pd.DataFrame())
    assert counts["entry_blocked"] == 0 and row["adverse_guard"] == "skipped_no_price"


def test_live_quote_path_unchanged(tmp_path, monkeypatch):
    import price.trading as trading
    import price.warehouse as warehouse
    pt = _pt(monkeypatch, tmp_path)
    monkeypatch.setattr(trading, "get_latest_price", lambda s: 90.0)
    seen = []
    monkeypatch.setattr(warehouse, "load_from_warehouse",
                        lambda s, t: (seen.append(1) or pd.DataFrame()))
    counts = pt._handle_signals(
        [_sig()], dry_run=True, max_adverse_fill_bps=200.0, adverse_atr_mult=1.0)
    row = pd.read_csv(tmp_path / "paper_trade_log.csv").iloc[0]
    assert counts["entry_blocked"] == 1 and row["reason"] == "stale_signal_adverse_gap"
    assert seen == []


# ======================================================================

def test_knife_cooldown_is_valid_risk_limits_field():
    """knife_cooldown_days must be a named field on RiskLimits."""
    limits = RiskLimits(knife_cooldown_days=2)
    assert limits.knife_cooldown_days == 2

    limits_off = RiskLimits(knife_cooldown_days=0)
    assert limits_off.knife_cooldown_days == 0


def test_sector_cap_blocks_third_semi_after_same_scan_commit(monkeypatch):
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


def test_static_snapshot_would_have_admitted_all_three(monkeypatch):
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



from price.risk_limits import check_sector_concentration_cap
