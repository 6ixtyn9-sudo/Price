"""Tests for paper_trade.py's handling of kind=stop_intent signals.

scan_all_slices already performs the actual broker call (attach/ratchet)
inside reconcile_stops before emitting the signal; _handle_signals's job
for this kind is purely to audit-log it and count it, never to call
trading again. This is pinned separately from the stop_manager/monitor
integration tests because it is glue-script behaviour, not stop logic.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import paper_trade  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_PATH", tmp_path / "paper_trade_log.csv")
    return tmp_path


def test_stop_intent_is_audited_and_counted(tmp_path):
    signals = [{
        "kind": "stop_intent",
        "action": "stop_attached",
        "symbol": "XOP",
        "stop_price": 148.5,
        "r_dollars": 96.0,
    }]

    counts = paper_trade._handle_signals(signals, dry_run=False)

    assert counts["stop_actions"] == 1
    log = pd.read_csv(paper_trade.AUDIT_LOG_PATH)
    assert (log["action"] == "stop_attached").any()
    assert (log["symbol"] == "XOP").any()


def test_stop_intent_never_calls_trading_again(tmp_path, monkeypatch):
    """_handle_signals must not re-invoke submit_entry/close_position for a
    stop_intent -- the broker call already happened in reconcile_stops."""
    calls = []
    monkeypatch.setattr(paper_trade, "submit_entry", lambda *a, **k: calls.append("submit_entry"))
    monkeypatch.setattr(paper_trade, "close_position", lambda *a, **k: calls.append("close_position"))

    signals = [{"kind": "stop_intent", "action": "stop_ratcheted", "symbol": "XLF"}]
    paper_trade._handle_signals(signals, dry_run=False)

    assert calls == []


def test_multiple_stop_intents_all_counted():
    signals = [
        {"kind": "stop_intent", "action": "stop_attached", "symbol": "XOP"},
        {"kind": "stop_intent", "action": "stop_unchanged", "symbol": "XLF"},
        {"kind": "stop_intent", "action": "stop_pending", "symbol": "KLAC"},
    ]
    counts = paper_trade._handle_signals(signals, dry_run=False)
    assert counts["stop_actions"] == 3
