import pytest
import sys
from unittest.mock import patch
import paper_trade

@pytest.fixture
def patch_audit(monkeypatch):
    audits = []
    def mock_append_audit(row):
        audits.append(row)
    monkeypatch.setattr(paper_trade, "_append_audit", mock_append_audit)
    return audits

def test_exit_dedup_single_symbol(patch_audit, monkeypatch):
    signals = [
        {"kind": "exit_intent", "symbol": "AAPL", "action": "exit"},
        {"kind": "exit_intent", "symbol": "AAPL", "action": "exit"}
    ]
    
    closes = []
    def mock_close_position(symbol):
        closes.append(symbol)
        return {"order_id": "123", "status": "filled", "error": None}
    
    monkeypatch.setattr(paper_trade, "close_position", mock_close_position)
    
    counts = paper_trade._handle_signals(signals, dry_run=False)
    
    assert len(closes) == 1
    assert closes[0] == "AAPL"
    assert counts["exit_submitted"] == 1
    assert counts["exit_dedup"] == 1
    
    assert len(patch_audit) == 2
    assert patch_audit[0]["action"] == "exit"
    assert patch_audit[0]["symbol"] == "AAPL"
    
    assert patch_audit[1]["action"] == "exit_dedup"
    assert patch_audit[1]["symbol"] == "AAPL"

def test_exit_dedup_single_symbol_dry_run(patch_audit, monkeypatch):
    signals = [
        {"kind": "exit_intent", "symbol": "AAPL", "action": "exit"},
        {"kind": "exit_intent", "symbol": "AAPL", "action": "exit"}
    ]
    
    closes = []
    def mock_close_position(symbol):
        closes.append(symbol)
        return {"order_id": "123", "status": "filled", "error": None}
    
    monkeypatch.setattr(paper_trade, "close_position", mock_close_position)
    
    counts = paper_trade._handle_signals(signals, dry_run=True)
    
    assert len(closes) == 0
    assert counts["exit_submitted"] == 0
    assert counts["exit_dedup"] == 1
    
    assert len(patch_audit) == 2
    assert patch_audit[0]["action"] == "would_exit"
    assert patch_audit[0]["symbol"] == "AAPL"
    
    assert patch_audit[1]["action"] == "exit_dedup"
    assert patch_audit[1]["symbol"] == "AAPL"

def test_exit_multiple_symbols(patch_audit, monkeypatch):
    signals = [
        {"kind": "exit_intent", "symbol": "AAPL", "action": "exit"},
        {"kind": "exit_intent", "symbol": "MSFT", "action": "exit"}
    ]
    
    closes = []
    def mock_close_position(symbol):
        closes.append(symbol)
        return {"order_id": "123", "status": "filled", "error": None}
    
    monkeypatch.setattr(paper_trade, "close_position", mock_close_position)
    
    counts = paper_trade._handle_signals(signals, dry_run=False)
    
    assert len(closes) == 2
    assert counts["exit_submitted"] == 2
    assert counts.get("exit_dedup", 0) == 0
    
    assert len(patch_audit) == 2
    assert patch_audit[0]["action"] == "exit"
    assert patch_audit[0]["symbol"] == "AAPL"
    
    assert patch_audit[1]["action"] == "exit"
    assert patch_audit[1]["symbol"] == "MSFT"


def test_is_nyse_rth_open_handles_dst_holidays_and_early_close():
    from datetime import datetime, timezone

    # Pre-DST fall-back (EDT, UTC-4): 13:30-20:00 UTC
    assert paper_trade.is_nyse_rth_open(datetime(2026, 10, 30, 13, 35, tzinfo=timezone.utc)) is True
    # Post-DST fall-back (EST, UTC-5): 13:26Z and 14:24Z are pre-market; 14:30Z opens
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 5, 13, 26, tzinfo=timezone.utc)) is False
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 5, 14, 24, tzinfo=timezone.utc)) is False
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)) is True
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 4, 20, 27, tzinfo=timezone.utc)) is True
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)) is False
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 5, 0, 11, tzinfo=timezone.utc)) is False
    # Thanksgiving Day 2026 (closed) & Black Friday 18:30Z (after 18:00Z early close)
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)) is False
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 27, 17, 30, tzinfo=timezone.utc)) is True
    assert paper_trade.is_nyse_rth_open(datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)) is False


def test_equity_rth_gate_defers_off_hours_equities_but_allows_crypto_and_stops(patch_audit, monkeypatch):
    from datetime import datetime, timezone

    submitted = []
    closed = []
    recorded = []
    monkeypatch.setattr(
        paper_trade,
        "submit_entry",
        lambda **kw: submitted.append(kw["symbol"]) or {"order_id": "e1", "status": "accepted"},
    )
    monkeypatch.setattr(
        paper_trade,
        "close_position",
        lambda sym: closed.append(sym) or {"order_id": "x1", "status": "filled", "error": None},
    )
    monkeypatch.setattr(paper_trade, "record_entry", lambda sym: recorded.append(sym))

    signals = [
        {"kind": "stop_intent", "action": "stop_attached", "symbol": "PM", "stop_price": 201.69},
        {
            "kind": "entry_signal",
            "symbol": "PM",
            "timeframe": "1h",
            "slice_combination": "state_ext=neutral",
            "matched": True,
            "tradable": True,
            "suggested_qty": 11,
            "close_adj": 206.92,
        },
        {
            "kind": "entry_signal",
            "symbol": "BTC/USD",
            "timeframe": "1h",
            "slice_combination": "state_ext=neutral",
            "matched": True,
            "tradable": True,
            "suggested_qty": 1,
            "close_adj": 70000.0,
        },
        {"kind": "exit_intent", "symbol": "ADI", "action": "exit", "reason": "watchdog_loser_stop"},
        {"kind": "exit_intent", "symbol": "FUT/ES", "action": "exit", "reason": "horizon_reached"},
    ]

    # Off-hours (00:11 UTC / 19:11 EST): equity entry blocked & equity exit deferred;
    # crypto entry, futures exit, and stop_intent audit still proceed.
    off_hours = datetime(2026, 11, 5, 0, 11, tzinfo=timezone.utc)
    counts = paper_trade._handle_signals(
        signals,
        dry_run=False,
        enforce_equity_rth=True,
        now_utc=off_hours,
    )
    assert submitted == ["BTC/USD"]
    assert recorded == ["BTC/USD"]
    assert closed == ["FUT/ES"]
    assert counts["entry_blocked"] == 1
    assert counts["entry_submitted"] == 1
    assert counts["exit_deferred"] == 1
    assert counts["exit_submitted"] == 1
    assert counts["stop_actions"] == 1
    assert any(r["action"] == "block" and r["symbol"] == "PM" and r["reason"] == "market_closed_rth" for r in patch_audit)
    assert any(r["action"] == "exit_deferred" and r["symbol"] == "ADI" for r in patch_audit)

    # Inside RTH (15:30 UTC / 10:30 EST): equity entry & exit execute normally.
    patch_audit.clear()
    submitted.clear()
    closed.clear()
    recorded.clear()
    in_rth = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)
    counts_rth = paper_trade._handle_signals(
        signals,
        dry_run=False,
        enforce_equity_rth=True,
        now_utc=in_rth,
    )
    assert submitted == ["PM", "BTC/USD"]
    assert closed == ["ADI", "FUT/ES"]
    assert counts_rth["entry_submitted"] == 2
    assert counts_rth["exit_submitted"] == 2
    assert counts_rth["exit_deferred"] == 0

