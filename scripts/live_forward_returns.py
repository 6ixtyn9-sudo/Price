import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import argparse
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from price.config import DATA_DIR
from price.data_sources import fetch_universal_bars
from price.warehouse import load_from_warehouse

PAPER_TRADE_LOG_PATH = DATA_DIR / "paper_trade_log.csv"
LEADERBOARD_PATH = DATA_DIR / "candidate_leaderboard.csv"
MONITORED_SLICES_PATH = DATA_DIR / "monitored_slices.csv"
LIVE_FORWARD_RETURNS_PATH = DATA_DIR / "live_forward_returns.csv"

def _load_universe(source):
    univ = set()
    paths = []
    if source in ("monitored", "auto"): paths.append(MONITORED_SLICES_PATH)
    if source in ("leaderboard", "auto"): paths.append(LEADERBOARD_PATH)
    for path in paths:
        if path.exists():
            df = pd.read_csv(path)
            for _, r in df.iterrows():
                if "symbol" in r:
                    univ.add((str(r["symbol"]).upper(), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()))
    return univ

def _get_exit_close(symbol, timeframe, signal_ts, horizon_bars):
    df = load_from_warehouse(symbol, timeframe)
    sig_ts = pd.to_datetime(signal_ts, utc=True)
    if not df.empty:
        df = df.sort_values("bar_ts_utc").reset_index(drop=True)
        df["bar_ts_utc"] = pd.to_datetime(df["bar_ts_utc"], utc=True)
        future = df[df["bar_ts_utc"] >= sig_ts]
        if not future.empty:
            idx = future.index[0] + horizon_bars
            if idx < len(df): return float(df.iloc[idx]["close_adj"])
    
    # Fallback to Universal Router (Tiingo 1d / RTH Alpaca Intraday)
    try:
        # Request enough bars to cover the horizon
        lookback = 45 if timeframe == "1d" else 14
        df_api = fetch_universal_bars(symbol, timeframe, sig_ts, datetime.now(timezone.utc))
        if df_api is not None and not df_api.empty:
            df_api = df_api.sort_values("bar_ts_utc").reset_index(drop=True)
            # Filter for bars strictly after the signal
            future_api = df_api[df_api["bar_ts_utc"] >= sig_ts]
            if not future_api.empty:
                api_idx = future_api.index[0] + horizon_bars
                if api_idx < len(df_api):
                    # For 1d use adjusted close from Tiingo; for intraday use raw/adj (identical)
                    return float(df_api.iloc[api_idx]["close_adj"])
    except: pass
    return None

def run_live_capture(universe_source="auto"):
    univ = _load_universe(universe_source)
    if not PAPER_TRADE_LOG_PATH.exists(): return
    log = pd.read_csv(PAPER_TRADE_LOG_PATH)
    is_matched = log["matched"].astype(str).str.lower() == "true"
    is_entry = log["kind"] == "entry_signal"
    matched = log[is_matched & is_entry].copy()
    
    matched = matched[matched.apply(lambda r: (str(r["symbol"]).upper(), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()) in univ, axis=1)]
    if matched.empty: return print("No signals found in universe.")
    
    results = []
    for _, sig in matched.iterrows():
        res = {"symbol": sig["symbol"], "timeframe": sig["timeframe"], "slice_combination": sig["slice_combination"], "signal_ts_utc": sig["bar_ts_utc"], "signal_close": sig["close_adj"]}
        for h in [5, 20]:
            close = _get_exit_close(sig["symbol"], sig["timeframe"], sig["bar_ts_utc"], h)
            res[f"fwd_ret_{h}b"] = (close / sig["close_adj"] - 1.0) if close else None
        results.append(res)
    
    df_out = pd.DataFrame(results).drop_duplicates(subset=["symbol", "signal_ts_utc", "slice_combination"])
    df_out.to_csv(LIVE_FORWARD_RETURNS_PATH, index=False)
    print(f"✅ Success: Captured {len(df_out)} signals (Tiingo/RTH aware).")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-source", default="auto")
    run_live_capture(parser.parse_args().universe_source)
EOF
