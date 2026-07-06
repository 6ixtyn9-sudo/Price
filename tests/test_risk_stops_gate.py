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
