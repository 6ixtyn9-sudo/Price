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


def test_same_pass_duplicate_symbol_blocked(tmp_path, monkeypatch):
    """If multiple slices match for the exact same symbol on one scan pass,
    only the first enters; subsequent ones are blocked cleanly."""
    calls = []
    monkeypatch.setattr(paper_trade, "submit_entry", lambda *a, **k: calls.append(k.get("symbol")) or {"status": "accepted", "order_id": "123"})
    monkeypatch.setattr(paper_trade, "record_entry", lambda *a, **k: None)

    signals = [
        {"kind": "entry_signal", "symbol": "HUM", "timeframe": "1h", "slice_combination": "slice_a", "matched": True, "tradable": True, "suggested_qty": 10, "close_adj": 500.0},
        {"kind": "entry_signal", "symbol": "HUM", "timeframe": "1h", "slice_combination": "slice_b", "matched": True, "tradable": True, "suggested_qty": 10, "close_adj": 500.0},
    ]
    counts = paper_trade._handle_signals(signals, dry_run=False)
    assert counts["entry_submitted"] == 1
    assert counts["entry_blocked"] == 1
    assert len(calls) == 1
    log = pd.read_csv(paper_trade.AUDIT_LOG_PATH)
    assert (log["reason"] == "symbol_already_submitted_this_pass").any()



# ---------------------------------------------------------------------------
# Scan heartbeat + lane parking (2026-08-07): a 1-second green workflow run
# with zero evaluation rows used to be indistinguishable from a working
# lane. One scan_summary row per run is now unconditional, and a committed
# PARK_LANE_<LANE>.flag suspends a lane's scan/broker work entirely while
# keeping ingest alive in the workflows.
# ---------------------------------------------------------------------------

import time as _time_hb

import pytest as _pytest_hb


def test_lane_park_flag_path_maps_lane_names(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trade, "DATA_DIR", tmp_path)
    assert paper_trade._lane_park_flag_path("crypto") == tmp_path / "PARK_LANE_CRYPTO.flag"
    assert paper_trade._lane_park_flag_path("fut") == tmp_path / "PARK_LANE_FUTURES.flag"
    assert paper_trade._lane_park_flag_path("eq") == tmp_path / "PARK_LANE_EQUITIES.flag"


def test_scan_summary_row_shape(tmp_path):
    started = _time_hb.monotonic()
    paper_trade._emit_scan_summary(
        "scan_complete", started, "crypto",
        book_size=1, signals_total=2, entry_submitted=0, entry_blocked=0,
    )
    log = pd.read_csv(paper_trade.AUDIT_LOG_PATH)
    assert len(log) == 1
    row = log.iloc[0]
    assert row["kind"] == "scan_summary"
    assert row["action"] == "scan_complete"
    assert row["lane"] == "crypto"
    assert row["book_size"] == 1
    assert row["signals_total"] == 2
    assert row["runtime_s"] >= 0
    assert isinstance(row["logged_at_utc"], str) and row["logged_at_utc"]


def test_scan_summary_emission_never_raises(tmp_path, monkeypatch):
    # Point the audit path AT A DIRECTORY so the write must fail; the
    # heartbeat is telemetry and may never crash a trading scan.
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_PATH", tmp_path)
    paper_trade._emit_scan_summary("scan_failed", _time_hb.monotonic(), "fut", note="x")


def test_parked_lane_short_circuits_before_any_scan_or_broker_work(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trade, "AUDIT_LOG_PATH", tmp_path / "paper_trade_log_crypto.csv")
    monkeypatch.setattr(paper_trade, "DATA_DIR", tmp_path)
    (tmp_path / "PARK_LANE_CRYPTO.flag").write_text("parked for the test run\n")

    def _scan_must_not_run(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("scan_all_slices ran on a parked lane")

    def _equity_must_not_be_fetched(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("broker account queried on a parked lane")

    monkeypatch.setattr(paper_trade, "scan_all_slices", _scan_must_not_run)
    monkeypatch.setattr(paper_trade, "_resolve_sizing_equity", _equity_must_not_be_fetched)
    monkeypatch.setattr("sys.argv", ["paper_trade.py"])

    rc = paper_trade.main()
    assert rc == 0
    log = pd.read_csv(tmp_path / "paper_trade_log_crypto.csv")
    assert len(log) == 1
    row = log.iloc[0]
    assert row["kind"] == "scan_summary"
    assert row["action"] == "lane_parked"
    assert row["lane"] == "crypto"
    assert "PARK_LANE_CRYPTO.flag" in row["note"]


def test_main_completes_one_scan_pass_and_emits_heartbeat(tmp_path, monkeypatch):
    """End-to-end guard for the 2026-08-06 rupture class. A column-0 nested
    def split main() in two: every hourly paper-trade run since exited 0
    after printing its cost model, scanning nothing and writing no
    heartbeat, while the suite stayed green because it called the helpers
    directly. The adversarial net must prove the wire is connected end to
    end, not only that the lamp works when held in hand: a single main()
    invocation must execute exactly one scan pass, hand the signals to the
    handler, return 0, and leave exactly one scan_summary heartbeat row.
    """
    scan_calls = []
    handle_calls = []

    def _fake_scan(**kwargs):
        scan_calls.append(kwargs)
        return [{"kind": "entry_signal", "symbol": "XOP"}]

    def _fake_handle(signals, **kwargs):
        handle_calls.append(signals)
        return {"entries_submitted": 0}

    def _no_broker(*a, **k):
        raise AssertionError("no broker mutation allowed in this test")

    monkeypatch.setattr(paper_trade, "scan_all_slices", _fake_scan)
    monkeypatch.setattr(paper_trade, "_handle_signals", _fake_handle)
    monkeypatch.setattr(paper_trade, "submit_entry", _no_broker)
    monkeypatch.setattr(paper_trade, "close_position", _no_broker)

    import price.trading as _trading
    monkeypatch.setattr(_trading, "get_open_orders", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(_trading, "reconcile_trade_journal", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["paper_trade.py", "--sizing-equity", "100000"])

    rc = paper_trade.main()

    assert rc == 0
    assert len(scan_calls) == 1, "one monitored-book scan must run per invocation"
    assert handle_calls == [[{"kind": "entry_signal", "symbol": "XOP"}]]
    log = pd.read_csv(paper_trade.AUDIT_LOG_PATH)
    assert (log["kind"] == "scan_summary").any()
    assert (log["action"] == "scan_complete").any()
    assert (log["lane"] == "eq").any()
