"""Tests for paper_trade._resolve_sizing_equity: the helper that decides
whether the volatility rail / aggregate-risk-budget / leverage checks use
a manually-set --sizing-equity value or a live-fetched Alpaca equity
figure (--auto-sizing-equity).

This is the fix for the "dormant lever" gap identified in the ROI/risk
audit: without it, Stage B sizing and the new risk budgets never
activate unless an operator hand-maintains a --sizing-equity number,
which silently drifts stale as the account's P&L moves.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import paper_trade  # noqa: E402


def test_manual_mode_returns_manual_value_untouched():
    result = paper_trade._resolve_sizing_equity(auto=False, manual=12345.0)
    assert result == 12345.0


def test_manual_mode_never_calls_the_fetch_function():
    calls = []

    def _fetch():
        calls.append("called")
        return {"equity": 999.0}

    result = paper_trade._resolve_sizing_equity(auto=False, manual=500.0, get_account_info_fn=_fetch)
    assert result == 500.0
    assert calls == []


def test_manual_mode_returns_none_when_manual_is_none():
    assert paper_trade._resolve_sizing_equity(auto=False, manual=None) is None


def test_auto_mode_fetches_live_equity():
    def _fetch():
        return {"equity": 98765.43, "buying_power": 0.0}

    result = paper_trade._resolve_sizing_equity(auto=True, manual=100.0, get_account_info_fn=_fetch)
    assert result == pytest.approx(98765.43)


def test_auto_mode_falls_back_to_manual_on_fetch_exception():
    def _raising_fetch():
        raise RuntimeError("API credentials missing")

    result = paper_trade._resolve_sizing_equity(auto=True, manual=2500.0, get_account_info_fn=_raising_fetch)
    assert result == 2500.0


def test_auto_mode_falls_back_to_manual_on_missing_equity_key():
    def _fetch_missing_key():
        return {"cash": 100.0}  # no "equity" key -> KeyError inside the helper

    result = paper_trade._resolve_sizing_equity(auto=True, manual=777.0, get_account_info_fn=_fetch_missing_key)
    assert result == 777.0


def test_auto_mode_default_fetch_fn_is_price_trading_get_account_info(monkeypatch):
    """When no get_account_info_fn is injected, the helper must resolve
    the real price.trading.get_account_info -- pinned via monkeypatch so
    this never makes a real network/broker call."""
    import price.trading as trading

    monkeypatch.setattr(trading, "get_account_info", lambda: {"equity": 42.0})
    result = paper_trade._resolve_sizing_equity(auto=True, manual=1.0)
    assert result == 42.0
