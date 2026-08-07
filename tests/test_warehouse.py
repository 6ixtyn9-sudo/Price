import pytest
import pandas as pd
from datetime import datetime, timezone

import price.warehouse

@pytest.fixture
def temp_warehouse(tmp_path):
    old_dir = price.warehouse.WAREHOUSE_DIR
    price.warehouse.WAREHOUSE_DIR = tmp_path
    yield tmp_path
    price.warehouse.WAREHOUSE_DIR = old_dir

def test_save_and_load_warehouse(temp_warehouse):
    df = pd.DataFrame({
        'symbol': ['SPY', 'SPY'],
        'timeframe': ['1d', '1d'],
        'bar_ts_utc': [
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 2, tzinfo=timezone.utc)
        ],
        'source': ['tiingo', 'tiingo'],
        'ingested_at_utc': [datetime.now(timezone.utc), datetime.now(timezone.utc)],
        'open_raw': [100.0, 101.0],
        'high_raw': [102.0, 103.0],
        'low_raw': [99.0, 100.0],
        'close_raw': [101.5, 102.5],
        'volume_raw': [10000, 11000],
        'open_adj': [100.0, 101.0],
        'high_adj': [102.0, 103.0],
        'low_adj': [99.0, 100.0],
        'close_adj': [101.5, 102.5],
        'adj_factor': [1.0, 1.0],
        'split_factor': [1.0, 1.0],
        'dividend_cash': [0.0, 0.0]
    })
    
    price.warehouse.save_to_warehouse(df)
    partition_file = temp_warehouse / "symbol=SPY" / "timeframe=1d" / "data.parquet"
    assert partition_file.exists()
    
    loaded = price.warehouse.load_from_warehouse('SPY', '1d')
    assert len(loaded) == 2
    assert loaded.loc[0, 'close_raw'] == 101.5

def test_warehouse_revision_overwrite(temp_warehouse):
    df_v1 = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['1d'],
        'bar_ts_utc': [datetime(2026, 6, 1, tzinfo=timezone.utc)],
        'source': ['tiingo'],
        'ingested_at_utc': [datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)],
        'open_raw': [100.0], 'high_raw': [102.0], 'low_raw': [99.0], 'close_raw': [101.5], 'volume_raw': [10000],
        'open_adj': [100.0], 'high_adj': [102.0], 'low_adj': [99.0], 'close_adj': [101.5], 'adj_factor': [1.0],
        'split_factor': [1.0], 'dividend_cash': [0.0]
    })
    price.warehouse.save_to_warehouse(df_v1)
    
    df_v2 = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['1d'],
        'bar_ts_utc': [datetime(2026, 6, 1, tzinfo=timezone.utc)],
        'source': ['tiingo'],
        'ingested_at_utc': [datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)],
        'open_raw': [100.0], 'high_raw': [102.0], 'low_raw': [99.0], 'close_raw': [101.99], 'volume_raw': [10500],
        'open_adj': [100.0], 'high_adj': [102.0], 'low_adj': [99.0], 'close_adj': [101.99], 'adj_factor': [1.0],
        'split_factor': [1.0], 'dividend_cash': [0.0]
    })
    price.warehouse.save_to_warehouse(df_v2)
    
    loaded = price.warehouse.load_from_warehouse('SPY', '1d')
    assert len(loaded) == 1
    assert loaded.loc[0, 'close_raw'] == 101.99

def test_resample_15m_to_1h(temp_warehouse):
    df_15m = pd.DataFrame({
        'symbol': ['SPY'] * 4,
        'timeframe': ['15m'] * 4,
        'bar_ts_utc': [
            datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 13, 45, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 14, 15, tzinfo=timezone.utc)
        ],
        'source': ['alpaca'] * 4,
        'ingested_at_utc': [datetime.now(timezone.utc)] * 4,
        'open_raw': [100.0, 101.0, 102.0, 103.0],
        'high_raw': [102.0, 103.0, 104.0, 105.0],
        'low_raw': [99.0, 100.0, 101.0, 102.0],
        'close_raw': [101.0, 102.0, 103.0, 104.0],
        'volume_raw': [100, 200, 300, 400]
    })
    price.warehouse.save_to_warehouse(df_15m)
    
    price.warehouse.resample_15m_to_1h('SPY')
    loaded_1h = price.warehouse.load_from_warehouse('SPY', '1h')
    assert len(loaded_1h) == 2

def test_propagate_adjustment_factors(temp_warehouse):
    df_1d = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['1d'],
        'bar_ts_utc': [datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)],
        'source': ['tiingo'],
        'ingested_at_utc': [datetime.now(timezone.utc)],
        'open_raw': [100.0], 'high_raw': [102.0], 'low_raw': [99.0], 'close_raw': [101.5], 'volume_raw': [10000],
        'open_adj': [98.0], 'high_adj': [99.96], 'low_adj': [97.02], 'close_adj': [99.47],
        'adj_factor': [0.98], 'split_factor': [1.0], 'dividend_cash': [0.50]
    })
    price.warehouse.save_to_warehouse(df_1d)
    
    df_15m = pd.DataFrame({
        'symbol': ['SPY'],
        'timeframe': ['15m'],
        'bar_ts_utc': [datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)],
        'source': ['alpaca'],
        'ingested_at_utc': [datetime.now(timezone.utc)],
        'open_raw': [100.0], 'high_raw': [101.0], 'low_raw': [99.5], 'close_raw': [100.5], 'volume_raw': [500]
    })
    price.warehouse.save_to_warehouse(df_15m)
    
    price.warehouse.propagate_adjustment_factors('SPY')
    updated_15m = price.warehouse.load_from_warehouse('SPY', '15m')
    assert len(updated_15m) == 1
    
    row = updated_15m.iloc[0]
    assert row['adj_factor'] == 0.98
    assert row['close_adj'] == 100.5 * 0.98

def test_propagate_adjustment_factors_uses_daily_utc_date_for_market_session(monkeypatch):
    """Daily bars at midnight UTC must map to the same New York market date,
    not the prior New York evening.

    Regression guard for the bug where Tiingo 1d bar_ts_utc was converted to
    America/New_York before extracting the date, shifting daily adjustment
    factors one session early and creating artificial intraday price jumps.
    """
    import pandas as pd
    import price.warehouse as warehouse

    saved = {}

    daily = pd.DataFrame(
        {
            "symbol": ["XYZ"],
            "timeframe": ["1d"],
            "bar_ts_utc": pd.to_datetime(["2024-01-03 00:00:00"], utc=True),
            "adj_factor": [0.5],
            "split_factor": [1.0],
            "dividend_cash": [0.0],
        }
    )

    intraday = pd.DataFrame(
        {
            "symbol": ["XYZ"],
            "timeframe": ["15m"],
            "bar_ts_utc": pd.to_datetime(["2024-01-03 14:30:00"], utc=True),
            "source": ["test"],
            "ingested_at_utc": pd.to_datetime(["2024-01-03 15:00:00"], utc=True),
            "open_raw": [100.0],
            "high_raw": [110.0],
            "low_raw": [90.0],
            "close_raw": [104.0],
            "volume_raw": [1000],
        }
    )

    def fake_load(symbol, timeframe):
        if timeframe == "1d":
            return daily.copy()
        if timeframe == "15m":
            return intraday.copy()
        return pd.DataFrame()

    def fake_save(df):
        saved[(df["symbol"].iloc[0], df["timeframe"].iloc[0])] = df.copy()

    monkeypatch.setattr(warehouse, "load_from_warehouse", fake_load)
    monkeypatch.setattr(warehouse, "save_to_warehouse", fake_save)

    warehouse.propagate_adjustment_factors("XYZ")

    adjusted = saved[("XYZ", "15m")]
    assert adjusted["adj_factor"].iloc[0] == 0.5
    assert adjusted["close_adj"].iloc[0] == 52.0


# ─────────────────────────────────────────────────────────────────────────────
# Adjustment-book integrity gate (daily frames)
#
# A booked corporate action (split_factor != 1 and/or dividend_cash > 0) must
# move adj_factor; a material adj_factor step must carry a booked action. The
# gate lives at save_to_warehouse so every ingest path (build, capture,
# manual recovery) funnels through it. A poisoned daily frame is refused
# persistence and written to a JSONL quarantine ledger. Fail-open doctrine:
# unauditable inputs (missing columns, <2 bars, junk boundaries) are clean.
# ─────────────────────────────────────────────────────────────────────────────

import json as _json  # noqa: E402
from datetime import timedelta as _timedelta  # noqa: E402

from price.warehouse import adjustment_integrity_violations  # noqa: E402


def _adj_bar(i, adj_f=1.0, split=1.0, div=0.0, close=100.0, symbol="TEST", tf="1d"):
    ts = datetime(2026, 7, 1, tzinfo=timezone.utc) + _timedelta(days=i)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "bar_ts_utc": ts,
        "open_raw": close, "high_raw": close * 1.01,
        "low_raw": close * 0.99, "close_raw": close,
        "volume_raw": 1_000_000,
        "open_adj": close * adj_f, "high_adj": close * 1.01 * adj_f,
        "low_adj": close * 0.99 * adj_f, "close_adj": close * adj_f,
        "adj_factor": adj_f, "split_factor": split, "dividend_cash": div,
    }


def test_booked_split_with_flat_adj_factor_flagged():
    df = pd.DataFrame([_adj_bar(0), _adj_bar(1, split=3.0, close=33.33), _adj_bar(2, close=33.50)])
    reasons = adjustment_integrity_violations(df)
    assert any("booked_event_factor_mismatch" in r for r in reasons)


def test_booked_split_with_matching_factor_passes():
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0, close=99.0),
        _adj_bar(1, adj_f=3.0, split=3.0, close=33.33),
        _adj_bar(2, adj_f=3.0, close=33.50),
    ])
    assert adjustment_integrity_violations(df) == []


def test_booked_special_dividend_with_flat_adj_factor_flagged():
    # $12 special on $100: a -12% ex-date gap that sits BELOW the 35% crash
    # detector — precisely the silent-poison case this gate must catch at ingest.
    df = pd.DataFrame([
        _adj_bar(0, close=100.0),
        _adj_bar(1, div=12.0, close=88.3),
        _adj_bar(2, close=88.8),
    ])
    reasons = adjustment_integrity_violations(df)
    assert any("booked_event_factor_mismatch" in r for r in reasons)


def test_booked_dividend_with_matching_factor_passes():
    # Standard adjusted-series convention (verified against live Yahoo and
    # stored books 2026-08-07): pre-ex prices are adjusted DOWN, so as time
    # moves forward adj_factor steps UP across the ex-date by 1/(1 - dv/pc).
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0, close=100.0),
        _adj_bar(1, adj_f=1.0 / 0.88, div=12.0, close=88.3),
        _adj_bar(2, adj_f=1.0 / 0.88, close=88.8),
    ])
    assert adjustment_integrity_violations(df) == []


def test_booked_dividend_standard_convention_step_up_passes():
    # Verbatim values from the live refusal on the 2026-08-07 capture:
    # ABBV, dividend_cash=1.3, prev_close=108.53, factor step 1.0121 =
    # 1/(1 - 1.3/108.53) — the standard convention steps adj_factor UP
    # across the ex-date (pre-ex prices are adjusted down). The gate's
    # original inverted dividend term (shipped 40e53d3) computed expected
    # 0.9880 and refused every material dividend book — 37
    # reciprocal-signature refusals on the 2026-08-07 captures alone. It
    # was latent because zero-filled splits froze daily frames before the
    # gate ever saw a real dividend in production. (The dividend cash
    # exceeds the 1%-of-close materiality line, as in production.)
    df = pd.DataFrame([
        _adj_bar(0, adj_f=0.988, close=108.53),
        _adj_bar(1, adj_f=0.988 / (1 - 1.3 / 108.53), div=1.3, close=107.23),
        _adj_bar(2, adj_f=0.988 / (1 - 1.3 / 108.53), close=107.9),
    ])
    assert adjustment_integrity_violations(df) == []


def test_immaterial_dividend_flat_factor_tolerated():
    df = pd.DataFrame([
        _adj_bar(0, close=100.0),
        _adj_bar(1, div=0.50, close=99.6),
        _adj_bar(2, close=99.9),
    ])
    assert adjustment_integrity_violations(df) == []


def test_immaterial_dividend_with_matching_small_step_passes():
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0, close=100.0),
        _adj_bar(1, adj_f=0.995, div=0.50, close=99.6),
        _adj_bar(2, adj_f=0.995, close=99.9),
    ])
    assert adjustment_integrity_violations(df) == []


def test_unbooked_material_adj_factor_step_flagged():
    df = pd.DataFrame([_adj_bar(0, adj_f=1.0), _adj_bar(1, adj_f=0.90), _adj_bar(2, adj_f=0.90)])
    reasons = adjustment_integrity_violations(df)
    assert any("unbooked_adj_factor_step" in r for r in reasons)


def test_unbooked_minor_factor_noise_tolerated():
    df = pd.DataFrame([_adj_bar(0, adj_f=1.0), _adj_bar(1, adj_f=0.995), _adj_bar(2, adj_f=0.995)])
    assert adjustment_integrity_violations(df) == []


def test_clean_frame_no_violations():
    df = pd.DataFrame([_adj_bar(i, close=100.0 + i * 0.5) for i in range(5)])
    assert adjustment_integrity_violations(df) == []


def test_single_row_frame_is_unauditable_and_clean():
    df = pd.DataFrame([_adj_bar(0, split=3.0)])
    assert adjustment_integrity_violations(df) == []


def test_missing_adj_factor_column_is_unauditable_and_clean():
    df = pd.DataFrame([_adj_bar(0), _adj_bar(1, split=3.0, close=33.3)]).drop(columns=["adj_factor"])
    assert adjustment_integrity_violations(df) == []


def test_junk_factor_boundary_ignored_not_flagged():
    df = pd.DataFrame([
        _adj_bar(0, adj_f=1.0),
        _adj_bar(1, adj_f=0.0),
        _adj_bar(2, adj_f=1.0),
    ])
    assert adjustment_integrity_violations(df) == []


def test_save_to_warehouse_refuses_poisoned_daily_frame(temp_warehouse):
    poison = pd.DataFrame([
        _adj_bar(0, symbol="POISON", close=100.0),
        _adj_bar(1, symbol="POISON", div=12.0, close=88.3),
        _adj_bar(2, symbol="POISON", close=88.8),
    ])
    price.warehouse.save_to_warehouse(poison)
    loaded = price.warehouse.load_from_warehouse("POISON", "1d")
    assert loaded.empty, "poisoned daily frame must not persist"

    ledger = temp_warehouse / price.warehouse._ADJUSTMENT_QUARANTINE_LEDGER
    assert ledger.exists(), "quarantine ledger must be written"
    record = _json.loads(ledger.read_text().strip().splitlines()[-1])
    assert record["symbol"] == "POISON"
    assert any("booked_event_factor_mismatch" in v for v in record["violations"])


def test_save_to_warehouse_persists_clean_daily_frame(temp_warehouse):
    clean = pd.DataFrame([
        _adj_bar(0, symbol="CLEAN", adj_f=1.0, close=100.0),
        _adj_bar(1, symbol="CLEAN", adj_f=1.0 / 0.88, div=12.0, close=88.3),
        _adj_bar(2, symbol="CLEAN", adj_f=1.0 / 0.88, close=88.8),
    ])
    price.warehouse.save_to_warehouse(clean)
    loaded = price.warehouse.load_from_warehouse("CLEAN", "1d")
    assert len(loaded) == 3


def test_save_to_warehouse_persists_flat_dummy_frame(temp_warehouse):
    # futures/crypto staging writes adj = raw with factors 1.0 and no booked
    # events — flat and honest, must never trip the gate
    dummy = pd.DataFrame([_adj_bar(i, symbol="TEST", close=5000.0 + i) for i in range(4)])
    price.warehouse.save_to_warehouse(dummy)
    loaded = price.warehouse.load_from_warehouse("TEST", "1d")
    assert len(loaded) == 4


def test_intraday_frames_are_not_gated(temp_warehouse):
    intraday = pd.DataFrame([_adj_bar(i, tf="15m", close=100.0 + i * 0.1) for i in range(4)])
    price.warehouse.save_to_warehouse(intraday)
    loaded = price.warehouse.load_from_warehouse("TEST", "15m")
    assert len(loaded) == 4


def test_multi_symbol_save_quarantines_only_the_poisoned_symbol(temp_warehouse):
    poison = pd.DataFrame([
        _adj_bar(0, symbol="POISON", close=100.0),
        _adj_bar(1, symbol="POISON", div=12.0, close=88.3),
    ])
    clean = pd.DataFrame([
        _adj_bar(0, symbol="CLEAN", adj_f=1.0, close=100.0),
        _adj_bar(1, symbol="CLEAN", adj_f=1.0 / 0.88, div=12.0, close=88.3),
    ])
    price.warehouse.save_to_warehouse(pd.concat([poison, clean], ignore_index=True))
    assert price.warehouse.load_from_warehouse("POISON", "1d").empty
    assert len(price.warehouse.load_from_warehouse("CLEAN", "1d")) == 2


# ---------------------------------------------------------------------------
# Adjustment provenance: frame-vs-history contradictions (equity daily only)
# ---------------------------------------------------------------------------

def _adj_frame(rows, symbol):
    return pd.DataFrame([_adj_bar(*a, symbol=symbol, **kw) for a, kw in rows])


def _ledger_records(temp_warehouse):
    ledger = temp_warehouse / price.warehouse._ADJUSTMENT_QUARANTINE_LEDGER
    if not ledger.exists():
        return []
    return [_json.loads(l) for l in ledger.read_text().strip().splitlines()]


def test_dummy_frame_over_adjusted_history_quarantined(temp_warehouse):
    adjusted = _adj_frame([((0,), dict(adj_f=0.95, close=40.0)),
                           ((1,), dict(adj_f=0.95, close=40.0)),
                           ((2,), dict(adj_f=0.95, close=39.0))], "MIDBAND")
    price.warehouse.save_to_warehouse(adjusted)
    dummy = _adj_frame([((1,), dict(close=40.0)),
                        ((2,), dict(close=39.0)),
                        ((3,), dict(close=39.5))], "MIDBAND")
    price.warehouse.save_to_warehouse(dummy)

    loaded = price.warehouse.load_from_warehouse("MIDBAND", "1d")
    shared = loaded[pd.to_datetime(loaded["bar_ts_utc"]).dt.day.between(2, 3)]
    assert (pd.to_numeric(shared["adj_factor"]) == 0.95).all(), (
        "dummy frame must NOT overwrite adjusted history on shared dates")
    recs = _ledger_records(temp_warehouse)
    assert any("dummy_frame_over_adjusted_history" in v
               for r in recs if r["symbol"] == "MIDBAND" for v in r["violations"])


def test_dummy_recent_window_over_factor1_overlap_allowed(temp_warehouse):
    # Post-flip steady state (probed 2026-08-07): for a stock whose latest
    # corporate action is older than the capture window, every served bar
    # has factor exactly 1.0, adj == raw, no booked events — a frame-local
    # "full dummy" signature that is HONEST data. 65 of 123 refusals on the
    # 2026-08-07 captures were this false positive and froze healthy daily
    # books. Persisting it can only rewrite stored rows on dates it covers,
    # so when every overlapped stored row carries factor 1.0 and no booked
    # event, nothing adjusted is lost and the frame must persist.
    adjusted = _adj_frame([((0,), dict(adj_f=0.97, close=125.01)),
                           ((1,), dict(adj_f=0.97, close=124.81)),
                           ((2,), dict(adj_f=1.0, div=3.74, close=287.51)),
                           ((3,), dict(adj_f=1.0, close=287.44)),
                           ((4,), dict(adj_f=1.0, close=293.32))], "RECENT")
    price.warehouse.save_to_warehouse(adjusted)
    assert len(price.warehouse.load_from_warehouse("RECENT", "1d")) == 5
    dummy = _adj_frame([((3,), dict(close=287.44)),
                        ((4,), dict(close=293.32)),
                        ((5,), dict(close=292.68))], "RECENT")
    price.warehouse.save_to_warehouse(dummy)
    loaded = price.warehouse.load_from_warehouse("RECENT", "1d")
    assert len(loaded) == 6, (
        "dummy-looking but honest recent window must extend the book; "
        "only overlap carrying real adjustment bookkeeping may refuse")
    # the adjusted lineage and the ex-date bookkeeping stay intact
    f = pd.to_numeric(loaded["adj_factor"])
    assert (f.iloc[:2] == 0.97).all()
    assert pd.to_numeric(loaded["dividend_cash"]).iloc[2] == 3.74


def test_dummy_frame_over_event_overlap_still_refused(temp_warehouse):
    # Belt check on the overlap scope: a dummy window that covers a stored
    # bar with a BOOKED event (split/dividend) must still be refused even if
    # the stored factor there happens to be exactly 1.0 — the keep-last
    # merge would erase the event bookkeeping itself.
    stored = _adj_frame([((0,), dict(adj_f=0.05, close=2447.0)),
                         ((1,), dict(adj_f=1.0, split=20.0, close=124.79)),
                         ((2,), dict(adj_f=1.0, close=123.0))], "EVTOVERLAP")
    price.warehouse.save_to_warehouse(stored)
    assert len(price.warehouse.load_from_warehouse("EVTOVERLAP", "1d")) == 3
    dummy = _adj_frame([((1,), dict(close=124.79)),
                        ((2,), dict(close=123.0)),
                        ((3,), dict(close=121.18))], "EVTOVERLAP")
    price.warehouse.save_to_warehouse(dummy)
    loaded = price.warehouse.load_from_warehouse("EVTOVERLAP", "1d")
    assert len(loaded) == 3, "dummy over a booked-event date must not persist"
    assert pd.to_numeric(loaded["split_factor"]).iloc[1] == 20.0
    recs = _ledger_records(temp_warehouse)
    assert any("dummy_frame_over_adjusted_history" in v
               for r in recs if r["symbol"] == "EVTOVERLAP" for v in r["violations"])


def test_full_dummy_frame_persists_with_unverified_provenance_log(temp_warehouse):
    dummy = _adj_frame([((0,), dict(close=40.0)), ((1,), dict(close=40.5))], "DUMMYEQ")
    price.warehouse.save_to_warehouse(dummy)
    assert len(price.warehouse.load_from_warehouse("DUMMYEQ", "1d")) == 2
    recs = [r for r in _ledger_records(temp_warehouse) if r["symbol"] == "DUMMYEQ"]
    assert len(recs) == 1 and recs[0]["kind"] == "unverified_provenance"
    assert any("unverified_provenance" in v for v in recs[0]["violations"])


def test_adjusted_frame_over_dummy_history_is_allowed_healing(temp_warehouse):
    dummy = _adj_frame([((0,), dict(close=40.0)), ((1,), dict(close=39.0))], "HEALME")
    price.warehouse.save_to_warehouse(dummy)
    healed = _adj_frame([((0,), dict(adj_f=0.95, close=40.0)),
                         ((1,), dict(adj_f=0.95, close=39.0))], "HEALME")
    price.warehouse.save_to_warehouse(healed)
    loaded = price.warehouse.load_from_warehouse("HEALME", "1d")
    assert (pd.to_numeric(loaded["adj_factor"]) == 0.95).all()
    recs = [r for r in _ledger_records(temp_warehouse)
            if r["symbol"] == "HEALME" and r["kind"] == "quarantine"]
    assert recs == [], "healing direction must not be quarantined"


def test_adjusted_global_restatement_constant_ratio_persists(temp_warehouse):
    v1 = _adj_frame([((0,), dict(adj_f=0.95, close=40.0)),
                     ((1,), dict(adj_f=0.95, close=40.0)),
                     ((2,), dict(adj_f=0.95, close=39.0))], "RESTATE")
    price.warehouse.save_to_warehouse(v1)
    # Vendor restates the whole series after a new action: levels shift,
    # ratio across shared dates stays constant -> honest refresh, must pass.
    v2 = _adj_frame([((1,), dict(adj_f=0.93, close=40.0)),
                     ((2,), dict(adj_f=0.93, close=39.0)),
                     ((3,), dict(adj_f=0.93, close=39.5))], "RESTATE")
    price.warehouse.save_to_warehouse(v2)
    loaded = price.warehouse.load_from_warehouse("RESTATE", "1d")
    assert pd.to_numeric(loaded["adj_factor"]).iloc[-1] == 0.93


def test_adjusted_frames_disagreeing_discontinuously_quarantined(temp_warehouse):
    v1 = _adj_frame([((0,), dict(adj_f=0.95, close=40.0)),
                     ((1,), dict(adj_f=0.95, close=40.0)),
                     ((2,), dict(adj_f=0.95, close=39.0))], "CONFLICT")
    price.warehouse.save_to_warehouse(v1)
    # Same shared dates, but this source absorbed an extra event on bar 2:
    # booked dividend matches its own step (passes the booked auditor), yet
    # the adjusted ratio vs stored history STEPS at the shared boundary.
    v2 = _adj_frame([((1,), dict(adj_f=0.95, close=40.0)),
                     ((2,), dict(adj_f=0.95 / (1 - 2.1 / 40), div=2.1, close=39.0)),
                     ((3,), dict(adj_f=0.95 / (1 - 2.1 / 40), close=39.2))], "CONFLICT")
    price.warehouse.save_to_warehouse(v2)
    loaded = price.warehouse.load_from_warehouse("CONFLICT", "1d")
    assert (pd.to_numeric(loaded["adj_factor"]) == 0.95).all()
    recs = _ledger_records(temp_warehouse)
    assert any("conflicting_adjustment_sources" in v
               for r in recs if r["symbol"] == "CONFLICT" for v in r["violations"])


def test_crypto_and_futures_dummy_frames_persist_without_provenance_log(temp_warehouse):
    crypto = _adj_frame([((0,), dict(close=60000.0)),
                         ((1,), dict(close=60100.0))], "BTC/USD")
    futures = _adj_frame([((0,), dict(close=5000.0)),
                          ((1,), dict(close=5005.0))], "FUT/ES")
    price.warehouse.save_to_warehouse(pd.concat([crypto, futures], ignore_index=True))
    assert len(price.warehouse.load_from_warehouse("BTC/USD", "1d")) == 2
    assert len(price.warehouse.load_from_warehouse("FUT/ES", "1d")) == 2
    recs = _ledger_records(temp_warehouse)
    assert not any(r["symbol"] in ("BTC/USD", "FUT/ES") for r in recs), (
        "crypto/futures dummy frames are normal staging, not provenance gaps")


