"""RED-TEAM PORT PACK - standalone acceptance tests (2026-08-06).

Self-contained: no imports from any other test file. Covers the three
vulnerable gauntlets' fixes as accepted on the adjudicated lineage:

  G1  falling-knife guard quote-outage fallback (paper_trade)
  G4  opening-window session stop buffer, capped floor-lift form
      (stops.effective_stop_atr_mult + stop_manager attach + monitor budget)
  G5  corporate-action quarantine gate (monitor._corporate_action_anomaly)

Apply the production patches first; these are the red->green acceptance
harness. Expected transitions are listed in APPLY_NOTES.md.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for p in (SRC, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import price.monitor as mon  # noqa: E402
import price.stops as stops  # noqa: E402
import price.stop_manager as stop_manager  # noqa: E402


# ======================================================================
# G4 - pure helper semantics (capped floor-lift)
# ======================================================================

from price.stops import effective_stop_atr_mult  # noqa: E402

_IN = datetime(2026, 8, 6, 13, 45, tzinfo=timezone.utc)    # first RTH hour
_OUT = datetime(2026, 8, 6, 15, 0, tzinfo=timezone.utc)    # regular session
_E0 = datetime(2026, 8, 6, 13, 30, tzinfo=timezone.utc)    # window opens
_E1 = datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc)    # window closes


def test_g4_floor_slice_lifts_to_2p1_at_open():
    # THE pinned consumers: LNG 1.500, MU 1.524, UBER 1.519 all land at ~2.1x.
    assert effective_stop_atr_mult(1.5, "1h", now=_IN) == pytest.approx(2.1)
    assert effective_stop_atr_mult(1.524313, "15m", now=_IN) == pytest.approx(2.1, abs=1e-3)
    assert effective_stop_atr_mult(1.518789, "15m", now=_IN) == pytest.approx(2.1, abs=1e-3)


def test_g4_subfloor_scales_by_buffer_below_cap():
    # 1.2 x 1.4 = 1.68 (< 2.1 cap) -> pure multiplier behavior under the floor.
    assert effective_stop_atr_mult(1.2, "15m", now=_IN) == pytest.approx(1.68)


def test_g4_midwidth_rises_only_to_caps_and_wide_untouched():
    # 2.0 -> 2.1 (NOT 2.8); already-wide 2.8 is NEVER over-widened.
    assert effective_stop_atr_mult(2.0, "1h", now=_IN) == pytest.approx(2.1)
    assert effective_stop_atr_mult(2.796041, "1h", now=_IN) == pytest.approx(2.796041)


def test_g4_noop_outside_window_and_for_daily_bars():
    assert effective_stop_atr_mult(1.5, "15m", now=_OUT) == pytest.approx(1.5)
    assert effective_stop_atr_mult(1.5, "1d", now=_IN) == pytest.approx(1.5)
    assert effective_stop_atr_mult(1.5, "1h", now=_IN, buffer_mult=1.0) == pytest.approx(1.5)


def test_g4_window_edges():
    assert effective_stop_atr_mult(1.5, "1h", now=_E0) == pytest.approx(2.1)
    assert effective_stop_atr_mult(1.5, "1h", now=_E1) == pytest.approx(1.5)


def test_g4_fail_safe_on_junk():
    assert effective_stop_atr_mult(1.5, "1h", now="bad") == pytest.approx(1.5)
    assert effective_stop_atr_mult("junk", "1h", now=_IN) == "junk"


# ======================================================================
# G4 - real attach path with pinned clock (binds production, not a copy)
# ======================================================================

def _attach(tmp_path, monkeypatch, limits, tframe, hour, minute):
    monkeypatch.setattr(stop_manager, "_resolve_atr_for_symbol", lambda s, t: 3.0)
    monkeypatch.setattr(
        stops, "_now_utc",
        lambda: datetime(2026, 8, 6, hour, minute, tzinfo=timezone.utc),
    )
    calls = []

    def _submit(symbol, qty, stop_price, side):
        calls.append((symbol, qty, stop_price, side))
        return {"order_id": "ord-1", "status": "accepted"}

    def _replace(order_id, new_stop_price):
        return {"order_id": order_id, "status": "replaced"}

    positions = pd.DataFrame([{
        "symbol": "LNG", "side": "long", "qty": 16,
        "avg_entry_price": 154.47, "current_price": 154.47,
    }])
    stop_manager.reconcile_stops(
        positions, limits,
        entry_context={"LNG": {"timeframe": tframe, "stop_atr_mult": 1.5}},
        submit_protective_stop_fn=_submit,
        replace_protective_stop_fn=_replace,
        stop_state_path=tmp_path / "stop_state.json",
        stopout_journal_path=tmp_path / "stopout_journal.json",
        get_orders_for_symbol_fn=lambda s, status="open": pd.DataFrame(),
    )
    return calls


class _Buffer:
    stop_atr_multiple = 2.0
    target_leverage_multiple = 1.0
    intraday_open_stop_buffer_mult = 1.4


class _NoBuffer:
    stop_atr_multiple = 2.0
    target_leverage_multiple = 1.0
    intraday_open_stop_buffer_mult = 1.0


def test_g4_attach_1h_at_open_places_2p1_floor_stop(tmp_path, monkeypatch):
    calls = _attach(tmp_path, monkeypatch, _Buffer(), "1h", 13, 45)
    assert calls == [("LNG", 16.0, pytest.approx(154.47 - 2.1 * 3.0), "long")]


def test_g4_attach_midday_unbuffered(tmp_path, monkeypatch):
    calls = _attach(tmp_path, monkeypatch, _Buffer(), "1h", 15, 0)
    assert calls == [("LNG", 16.0, pytest.approx(154.47 - 1.5 * 3.0), "long")]


def test_g4_attach_dial_off_noop_even_at_open(tmp_path, monkeypatch):
    calls = _attach(tmp_path, monkeypatch, _NoBuffer(), "1h", 13, 45)
    assert calls == [("LNG", 16.0, pytest.approx(154.47 - 1.5 * 3.0), "long")]


def test_g4_attach_daily_bar_never_buffered(tmp_path, monkeypatch):
    calls = _attach(tmp_path, monkeypatch, _Buffer(), "1d", 13, 45)
    assert calls == [("LNG", 16.0, pytest.approx(154.47 - 1.5 * 3.0), "long")]


# ======================================================================
# G4 - live book pin (book churn forces an explicit decision)
# ======================================================================

def test_g4_live_near_floor_intraday_slices_pinned():
    path = ROOT / "localdata" / "monitored_slices.csv"
    df = pd.read_csv(path)
    exposed = set(df.loc[
        df["timeframe"].isin(["1h", "15m"])
        & (pd.to_numeric(df["stop_atr_mult"], errors="coerce") < 1.55),
        "symbol",
    ].str.upper())
    assert {"LNG", "MU", "UBER"} <= exposed, f"book changed: {exposed}"


# ======================================================================
# G4 - budget coherence (skip if this lineage has no proposed_R wiring)
# ======================================================================

def test_g4_monitor_proposed_r_matches_attach(tmp_path, monkeypatch):
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
# G1 - quote-outage warehouse-reference fallback
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


def test_g1_quote_outage_warehouse_ref_blocks_knife(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, _wh(90.0, 20))
    assert counts["entry_blocked"] == 1
    assert row["reason"] == "stale_signal_adverse_gap_warehouse_ref"
    assert float(row["signal_to_fill_bps"]) <= -1000 + 1e-9


def test_g1_quote_outage_within_threshold_passes(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, _wh(99.5, 20))
    assert counts["entry_blocked"] == 0 and row["action"] == "would_enter"
    assert row["adverse_guard"] == "passed_warehouse_ref"


def test_g1_stale_warehouse_bar_still_fails_open(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, _wh(90.0, 100))
    assert counts["entry_blocked"] == 0 and row["adverse_guard"] == "skipped_no_price"


def test_g1_empty_warehouse_still_fails_open(tmp_path, monkeypatch):
    counts, row = _drive(tmp_path, monkeypatch, pd.DataFrame())
    assert counts["entry_blocked"] == 0 and row["adverse_guard"] == "skipped_no_price"


def test_g1_live_quote_path_unchanged(tmp_path, monkeypatch):
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
# G5 - corporate-action quarantine (canonical warehouse schema)
# ======================================================================

def _frame(raw, age_last=20, extra=None):
    last = datetime.now(timezone.utc) - timedelta(hours=age_last)
    n = len(raw)
    rows = []
    for i, c in enumerate(raw):
        row = {"bar_ts_utc": last - timedelta(hours=24 * (n - 1 - i)),
               "open_raw": float(c), "high_raw": float(c) * 1.003,
               "low_raw": float(c) * 0.997, "close_raw": float(c),
               "volume_raw": 1_000_000.0}
        if extra:
            row.update({k: v[i] for k, v in extra.items()})
        rows.append(row)
    return pd.DataFrame(rows)


def _phantom():
    return [100.0] * 69 + [67.0, 22.0]


def _die(*a, **k):
    raise RuntimeError("features must not run on a quarantined frame")


def test_g5_phantom_split_flagged():
    c = _phantom()
    df = _frame(c, extra={"close_adj": list(c), "split_factor": [1.0] * len(c),
                          "adj_factor": [1.0] * len(c)})
    reason = mon._corporate_action_anomaly(df, "1d")
    assert reason and reason.startswith("corporate_action_unadjusted_move")


def test_g5_gate_quarantined_and_audited(monkeypatch):
    if not hasattr(mon, "_state_unavailable_context"):
        pytest.skip("lineage has no Workstream-4 audit ctx (P5 skipped per APPLY_NOTES)")
    c = _phantom()
    df = _frame(c, extra={"close_adj": list(c), "split_factor": [1.0] * len(c),
                          "adj_factor": [1.0] * len(c)})
    monkeypatch.setattr(mon, "load_from_warehouse", lambda s, t: df)
    monkeypatch.setattr(mon, "compute_price_features", _die)
    assert mon.get_current_state("SMCI", "1d") is None
    ctx = mon._state_unavailable_context("SMCI", "1d")
    assert ctx["reason"].startswith("corporate_action_unadjusted_move")


def test_g5_71_5h_bar_inside_stale_window_still_quarantined(monkeypatch):
    df = _frame(_phantom(), age_last=71.5,
                extra={"split_factor": [1.0] * 71, "adj_factor": [1.0] * 71})
    monkeypatch.setattr(mon, "load_from_warehouse", lambda s, t: df)
    monkeypatch.setattr(mon, "compute_price_features", _die)
    assert mon._stale_warehouse_reason(df, "1d") is None  # 71.5h < 72h window
    assert mon.get_current_state("SMCI", "1d") is None


def test_g5_earnings_gap_passes():
    c = [100.0] * 69 + [100.0, 88.0]
    df = _frame(c, extra={"close_adj": list(c), "split_factor": [1.0] * len(c),
                          "adj_factor": [1.0] * len(c)})
    assert mon._corporate_action_anomaly(df, "1d") is None


def test_g5_booked_split_passes():
    c = [67.0] * 69 + [67.0, 22.0]
    df = _frame(c, extra={"close_adj": list(c),
                          "split_factor": [1.0] * 70 + [3.0],
                          "adj_factor": [1.0] * len(c)})
    assert mon._corporate_action_anomaly(df, "1d") is None


def test_g5_properly_adjusted_split_passes():
    raw = [300.0] * 69 + [300.0, 100.0]
    adj = [100.0] * 69 + [100.0, 100.0]
    df = _frame(raw, extra={"close_adj": adj,
                            "split_factor": [1.0] * 70 + [3.0],
                            "adj_factor": [a / r for a, r in zip(adj, raw)]})
    assert mon._corporate_action_anomaly(df, "1d") is None


def test_g5_stale_precedence_preserved(monkeypatch):
    if not hasattr(mon, "_state_unavailable_context"):
        pytest.skip("lineage has no Workstream-4 audit ctx (P5 skipped per APPLY_NOTES)")
    c = _phantom()
    df = _frame(c, age_last=100, extra={"close_adj": list(c),
                                        "split_factor": [1.0] * len(c),
                                        "adj_factor": [1.0] * len(c)})
    monkeypatch.setattr(mon, "load_from_warehouse", lambda s, t: df)
    monkeypatch.setattr(mon, "compute_price_features", _die)
    assert mon.get_current_state("SMCI", "1d") is None
    assert mon._state_unavailable_context("SMCI", "1d")["reason"].startswith(
        "stale_warehouse_bar_too_old")


def test_g5_degenerate_frames_clean():
    assert mon._corporate_action_anomaly(pd.DataFrame(), "1d") is None
    assert mon._corporate_action_anomaly(None, "1d") is None
    assert mon._corporate_action_anomaly(_frame([100.0]), "1d") is None
