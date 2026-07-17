"""Tests for slice-context recovery in position_manager.

Covers:
  1. Missing label recovered from paper_trade_log (enter row).
  2. Missing label recovered from paper_trade_log (any row with slice).
  3. monitored_slices fallback works when exactly ONE slice for symbol.
  4. monitored_slices fallback REFUSES to guess when MULTIPLE slices for symbol.
  5. Unrecovered case emits metadata_missing audit fields.
  6. Recovered context enables normal state-break evaluation (doesn't hold blindly).
"""

import os
import textwrap
from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import price.position_manager as pm
from price.position_manager import (
    _recover_entry_context_from_monitored_slices,
    _recover_entry_context_from_paper_trade_log,
    recover_entry_context_for_symbol,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_log_csv(rows: list[dict]) -> str:
    """Return CSV text from a list of dicts."""
    return pd.DataFrame(rows).to_csv(index=False)


# ---------------------------------------------------------------------------
# 1. Recover from paper_trade_log – explicit 'enter' row
# ---------------------------------------------------------------------------

def test_recover_from_paper_trade_log_enter_row(tmp_path, monkeypatch):
    log = _make_log_csv([
        {
            "action": "enter",
            "symbol": "ETN",
            "slice_label": "state_ext=neutral + state_vol=mid_vol",
            "slice_combination": "state_ext=neutral + state_vol=mid_vol",
            "timeframe": "1d",
            "bin_mode": "rolling",
            "bar_ts_utc": "2026-07-10 00:00:00+00:00",
            "timestamp_utc": "2026-07-10T14:00:00+00:00",
            "order_id": "abc123",
            "order_status": "filled",
        }
    ])
    log_path = tmp_path / "paper_trade_log.csv"
    log_path.write_text(log)
    result = _recover_entry_context_from_paper_trade_log("ETN", _log_path=str(log_path))
    assert result is not None
    assert result["slice_combination"] == "state_ext=neutral + state_vol=mid_vol"
    assert result["timeframe"] == "1d"
    assert result["bin_mode"] == "rolling"
    assert result["context_source"] == "paper_trade_log_enter"


# ---------------------------------------------------------------------------
# 2. Recover from paper_trade_log – would_enter / any row with slice label
# ---------------------------------------------------------------------------

def test_recover_from_paper_trade_log_refuses_any_row(tmp_path, monkeypatch):
    log = _make_log_csv([
        {
            "action": "stop_adopted",
            "symbol": "ETN",
            "slice_label": None,
            "slice_combination": None,
            "timeframe": None,
            "bin_mode": None,
            "timestamp_utc": "2026-07-17T15:00:00+00:00",
        },
        {
            "action": "would_enter",
            "symbol": "ETN",
            "slice_label": "state_ext=neutral + state_vol=mid_vol",
            "slice_combination": "state_ext=neutral + state_vol=mid_vol",
            "timeframe": "1d",
            "bin_mode": "rolling",
            "bar_ts_utc": "2026-07-09 00:00:00+00:00",
            "timestamp_utc": "2026-07-09T14:00:00+00:00",
        },
    ])
    log_path = tmp_path / "paper_trade_log.csv"
    log_path.write_text(log)
    result = _recover_entry_context_from_paper_trade_log("ETN", _log_path=str(log_path))
    assert result is None, "Should refuse to recover from would_enter"


# ---------------------------------------------------------------------------
# 3. Ledger Recovery
# ---------------------------------------------------------------------------

@patch("glob.glob")
def test_recover_from_ledger(mock_glob, tmp_path):
    ledger = pd.DataFrame([{
        "lane": "eq",
        "symbol": "AAPL",
        "side": "long",
        "qty": 10,
        "entry_order_id": "123",
        "client_order_id": "price-eq-AAPL-1d-long-hash",
        "slice_combination": "state_slope=uptrend",
        "timeframe": "1d",
        "bin_mode": "rolling",
        "entry_bar_ts": "2026-07-10",
        "submitted_at_utc": "2026-07-10T14:00:00Z",
        "status": "open"
    }])
    path = tmp_path / "open_position_context_eq.csv"
    ledger.to_csv(path, index=False)
    mock_glob.return_value = [str(path)]
    
    result = pm._recover_entry_context_from_ledger("AAPL")
    assert result is not None
    assert result["slice_combination"] == "state_slope=uptrend"
    assert result["timeframe"] == "1d"
    assert result["context_source"] == "open_position_context_file"

# ---------------------------------------------------------------------------
# 4. Broker Recovery
# ---------------------------------------------------------------------------

@patch("price.position_manager._hash_matches_slice")
@patch("price.trading.get_recent_orders")
def test_recover_from_broker_orders(mock_get_orders, mock_hash_matches):
    mock_get_orders.return_value = [
        {"client_order_id": "price-eq-AAPL-1d-long-abcdef12"},
        {"client_order_id": "some-other-id"}
    ]
    mock_hash_matches.return_value = {
        "slice_combination": "state_slope=uptrend",
        "timeframe": "1d",
        "bin_mode": "rolling"
    }
    
    result = pm._recover_entry_context_from_broker_orders("AAPL")
    assert result is not None
    assert result["slice_combination"] == "state_slope=uptrend"
    assert result["context_source"] == "broker_client_order_id"
    mock_hash_matches.assert_called_with("AAPL", "abcdef12")


# ---------------------------------------------------------------------------
# 3. monitored_slices fallback – exactly one slice: succeeds
# ---------------------------------------------------------------------------

def test_recover_monitored_slices_single_slice(tmp_path, monkeypatch):
    slices = pd.DataFrame([
        {
            "symbol": "XOP",
            "timeframe": "1d",
            "slice_combination": "state_slope=uptrend + state_vol=low_vol",
            "side": "long",
            "bin_mode": "rolling",
        }
    ])
    path = tmp_path / "monitored_slices.csv"
    slices.to_csv(path, index=False)
    result = _recover_entry_context_from_monitored_slices("XOP", _slices_path=str(path))
    assert result is not None
    assert result["slice_combination"] == "state_slope=uptrend + state_vol=low_vol"
    assert result["context_source"] == "monitored_slices_single"


# ---------------------------------------------------------------------------
# 4. monitored_slices fallback – multiple slices: REFUSES
# ---------------------------------------------------------------------------

def test_recover_monitored_slices_refuses_multiple(tmp_path, monkeypatch):
    slices = pd.DataFrame([
        {
            "symbol": "ETN",
            "timeframe": "1d",
            "slice_combination": "state_ext=neutral + state_vol=mid_vol",
            "side": "long",
            "bin_mode": "rolling",
        },
        {
            "symbol": "ETN",
            "timeframe": "1h",
            "slice_combination": "cross_TLT_state_slope=flat + state_slope=downtrend",
            "side": "long",
            "bin_mode": "rolling",
        },
    ])
    path = tmp_path / "monitored_slices.csv"
    slices.to_csv(path, index=False)
    result = _recover_entry_context_from_monitored_slices("ETN", _slices_path=str(path))
    assert result is None, "Should refuse when multiple slices exist for symbol"


# ---------------------------------------------------------------------------
# 5. Fully unrecovered: check_exits emits metadata_missing fields
# ---------------------------------------------------------------------------

def _make_position_df(symbol="GHOST", qty=10.0, current_price=100.0):
    return pd.DataFrame([{
        "symbol": symbol,
        "qty": qty,
        "current_price": current_price,
        "avg_entry_price": 90.0,
        "unrealized_pl": (current_price - 90.0) * qty,
    }])


@patch("price.position_manager._load_entry_context", return_value={})
@patch("price.position_manager.recover_entry_context_for_symbol", return_value=None)
def test_unrecovered_position_emits_metadata_fields(mock_recover, mock_ctx):
    pos = _make_position_df("GHOST")
    results = pm.check_exits(pos, {})
    assert len(results) == 1
    r = results[0]
    assert r["action"] == "hold"
    assert r["metadata_missing"] is True
    assert r["metadata_recovery_attempted"] is True
    assert r["metadata_recovery_source"] is None


# ---------------------------------------------------------------------------
# 6. Recovered context enables normal check_exits flow (not a blind hold)
# ---------------------------------------------------------------------------

@patch("price.position_manager._load_entry_context", return_value={})
@patch("price.position_manager.recover_entry_context_for_symbol")
@patch("price.position_manager.load_from_warehouse")
@patch("price.position_manager.compute_price_features")
@patch("price.position_manager.apply_state_bins")
def test_recovered_context_enables_state_break_check(
    mock_bins, mock_feat, mock_warehouse, mock_recover, mock_ctx
):
    """A position recovered from paper_trade_log should proceed to state-break
    evaluation rather than returning a blind 'hold'."""
    recovered = {
        "slice_combination": "state_slope=uptrend",
        "timeframe": "1d",
        "bin_mode": "rolling",
        "entry_bar_ts": None,
        "submitted_at": None,
        "context_source": "paper_trade_log_enter",
    }
    mock_recover.return_value = recovered

    # Fake warehouse data
    bars = pd.DataFrame({
        "bar_ts_utc": pd.date_range("2026-01-01", periods=70, freq="D", tz="UTC"),
        "open": [100.0] * 70,
        "high": [105.0] * 70,
        "low": [95.0] * 70,
        "close": [101.0] * 70,
        "volume": [1_000_000] * 70,
    })
    mock_warehouse.return_value = bars
    mock_feat.return_value = bars
    # Return a df whose last row has state_slope != uptrend → triggers state-break
    last_row = bars.iloc[-1:].copy()
    last_row["state_slope"] = "downtrend"
    mock_bins.return_value = pd.concat([bars.iloc[:-1], last_row])

    pos = _make_position_df("XOP")
    results = pm.check_exits(pos, {})
    assert len(results) == 1
    r = results[0]
    # Should NOT be the blind "no slice label" hold
    assert "metadata_missing" not in r
    assert r["context_source"] == "paper_trade_log_enter"
    # State-break should fire because current state doesn't match slice
    assert r["action"] == "exit"
    assert "stable filter broken" in r["reason"]
