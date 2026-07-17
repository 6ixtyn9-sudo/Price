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
