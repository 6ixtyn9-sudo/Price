import os
import pandas as pd
import requests
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from datetime import datetime, timedelta, timezone

# Load environment variables from .env
load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")

def fetch_alpaca():
    print("--- Testing Alpaca ---")
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("Alpaca keys missing in .env")
        return None
    
    try:
        client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        
        # Free-tier basic API key requires feed=DataFeed.IEX to prevent 403 Forbidden errors
        request_params = StockBarsRequest(
            symbol_or_symbols="SPY",
            timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=30),
            feed=DataFeed.IEX
        )
        
        bars = client.get_stock_bars(request_params)
        df = bars.df
        if df is None or df.empty:
            print("Alpaca returned empty dataframe.")
            return None
            
        print(f"Alpaca SPY Daily (last 30 days) - Head:\n{df.head(3)}")
        return df
    except Exception as e:
        print(f"Alpaca error: {e}")
        return None

def test_alpaca():
    fetch_alpaca()

def fetch_tiingo():
    print("\n--- Testing Tiingo ---")
    if not TIINGO_API_KEY:
        print("Tiingo API key missing in .env")
        return None
    
    symbol = "SPY"
    start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
    url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices?startDate={start_date}&token={TIINGO_API_KEY}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        df = pd.DataFrame(data)
        if df.empty:
            print("Tiingo returned empty data.")
            return None
            
        print(f"Tiingo SPY Daily (last 30 days) - Head:\n{df.head(3)}")
        return df
    except Exception as e:
        print(f"Tiingo error: {e}")
        return None

def test_tiingo():
    fetch_tiingo()

if __name__ == "__main__":
    alpaca_df = fetch_alpaca()
    tiingo_df = fetch_tiingo()
    
    if alpaca_df is not None and tiingo_df is not None:
        print("\n--- Comparison ---")
        # Alpaca index is MultiIndex (symbol, timestamp). Let's reset index and parse timestamp to date.
        alpaca_clean = alpaca_df.reset_index()
        alpaca_clean['date'] = pd.to_datetime(alpaca_clean['timestamp']).dt.date
        alpaca_compare = alpaca_clean[['date', 'close', 'volume']].rename(
            columns={'close': 'alpaca_close', 'volume': 'alpaca_volume'}
        )
        
        # Tiingo date column is a string / datetime object. Let's parse to date.
        tiingo_clean = tiingo_df.copy()
        tiingo_clean['date'] = pd.to_datetime(tiingo_clean['date']).dt.date
        tiingo_compare = tiingo_clean[['date', 'close', 'adjClose', 'volume']].rename(
            columns={
                'close': 'tiingo_close_raw',
                'adjClose': 'tiingo_close_adj',
                'volume': 'tiingo_volume'
            }
        )
        
        # Merge on date
        merged = pd.merge(alpaca_compare, tiingo_compare, on='date', how='inner')
        if merged.empty:
            print("Could not align daily data on dates.")
        else:
            print(f"Aligned data count: {len(merged)} rows")
            print("\nFirst 5 aligned rows:")
            print(merged.head(5).to_string(index=False))
            
            # Compare raw closes (should be identical since daily raw close is unadjusted)
            close_diff = (merged['alpaca_close'] - merged['tiingo_close_raw']).abs().max()
            print(f"\nMax discrepancy in unadjusted daily close: ${close_diff:.4f}")
            if close_diff < 0.01:
                print("SUCCESS: Unadjusted closing prices are in perfect agreement!")
            else:
                print("WARNING: Discrepancy detected in unadjusted closing prices.")
                
            # Compare volume (Alpaca IEX vs Tiingo consolidated)
            print("\nVolume comparison (Alpaca IEX vs Tiingo Consolidated):")
            for idx, row in merged.head(3).iterrows():
                ratio = (row['alpaca_volume'] / row['tiingo_volume']) * 100 if row['tiingo_volume'] > 0 else 0
                print(f"Date {row['date']}: Alpaca Vol = {int(row['alpaca_volume']):,}, Tiingo Vol = {int(row['tiingo_volume']):,}, IEX Share = {ratio:.2f}%")


def test_resolve_universal_source_uses_yfinance_for_equity_daily(monkeypatch):
    """yfinance is the primary source for equity daily and hourly bars;
    the source-label helper must match that path so workflow logs don't lie."""
    import price.data_sources as ds

    monkeypatch.setattr(ds, "TIINGO_API_KEY", "dummy-token")

    assert ds.resolve_universal_source("XOP", "1d") == "yfinance"
    assert ds.resolve_universal_source("KLAC", "1d") == "yfinance"
    assert ds.resolve_universal_source("XOP", "1h") == "yfinance"
    assert ds.resolve_universal_source("XOP", "15m") == "alpaca"
    assert ds.resolve_universal_source("BTC/USD", "1d") == "yfinance_crypto"
    assert ds.resolve_universal_source("FUT/ES", "1d") == "yfinance_futures"


def test_resolve_universal_source_yfinance_works_without_tiingo_key(monkeypatch):
    """yfinance does not require a Tiingo key — it remains primary even when
    the Tiingo key is absent (Tiingo is only a fallback now)."""
    import price.data_sources as ds

    monkeypatch.setattr(ds, "TIINGO_API_KEY", None)

    assert ds.resolve_universal_source("XOP", "1d") == "yfinance"


def test_capture_bars_logs_universal_router_source(monkeypatch, capsys):
    """capture_bars should print the same first-attempt source as the router,
    not the old ETF-only Tiingo heuristic."""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import capture_bars as cb

    monkeypatch.setattr(cb, "resolve_universal_source", lambda symbol, tf: "tiingo")
    monkeypatch.setattr(cb, "load_from_warehouse", lambda symbol, tf: pd.DataFrame())
    monkeypatch.setattr(cb, "fetch_universal_bars", lambda symbol, tf, start, end: pd.DataFrame())

    cb.capture_bars(target_symbols=["XOP"], target_timeframes=["1d"], days_lookback=1)

    out = capsys.readouterr().out
    assert "Ingesting XOP (1d) from TIINGO" in out


def test_zero_filled_stock_splits_normalised_to_no_event():
    """Vendor change of ~2026-08-03: Yahoo began zero-filling the 'Stock
    Splits' actions column server-side (verified 2026-08-07 across yfinance
    clients 1.4.1 / 1.5.1 / 1.5.2, so no client pin can fix it — the
    normalisation has to live at our ingest boundary). A 0.0 split factor
    is a phantom ratio-zero split: it fired the incremental-pull split
    detector on all 139 symbols, full re-pulls then violated the warehouse
    adjustment-book gate on every bar, and daily frames froze at 2026-08-03.
    Malformed values (0, negative, NaN, non-finite) must normalise to 1.0
    ("no event"); genuine ratios must survive.
    """
    import price.data_sources as ds

    raw = pd.DataFrame({
        "Date": pd.date_range("2026-08-03", periods=4, freq="D", tz="UTC"),
        "Open": [10.0, 10.05, 10.1, 20.3],
        "High": [10.1, 10.15, 10.2, 20.4],
        "Low": [9.95, 10.0, 10.05, 20.1],
        "Close": [10.0, 10.1, 10.2, 20.3],
        "Adj Close": [10.0, 10.1, 10.2, 20.3],
        "Volume": [1_000_000] * 4,
        "Dividends": [0.0, 0.0, -1.5, 0.0],   # negative dividend: malformed
        "Stock Splits": [0.0, 0.0, 0.0, 2.0], # zeros: vendor junk; 2.0: real split
    })

    out = ds._build_yfinance_canonical(raw, "TEST", "1d")

    assert out["split_factor"].tolist() == [1.0, 1.0, 1.0, 2.0]
    assert (out["dividend_cash"] == 0.0).all()
