import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import pandas as pd
from price.config import DATA_DIR
from price.data_sources import fetch_alpaca_bars
from price.warehouse import load_from_warehouse

PAPER_TRADE_LOG_PATH = DATA_DIR / "paper_trade_log.csv"
LEADERBOARD_PATH = DATA_DIR / "candidate_leaderboard.csv"
MONITORED_SLICES_PATH = DATA_DIR / "monitored_slices.csv"
LIVE_FORWARD_RETURNS_PATH = DATA_DIR / "live_forward_returns.csv"
HORIZONS = [5, 20]

def _load_universe(source: str) -> Set[Tuple]:
    univ = set()
    if source in ("leaderboard", "auto") and LEADERBOARD_PATH.exists():
        lb = pd.read_csv(LEADERBOARD_PATH)
        if "triage_bucket" in lb.columns:
            clean = lb[lb["triage_bucket"].astype(str).str.startswith("clean_survivor")]
            for _, r in clean.iterrows():
                univ.add((str(r["symbol"]), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()))
    if (source == "monitored" or (source == "auto" and not univ)) and MONITORED_SLICES_PATH.exists():
        rows = pd.read_csv(MONITORED_SLICES_PATH)
        for _, r in rows.iterrows():
            univ.add((str(r["symbol"]), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()))
    return univ

def _get_exit_close(symbol, timeframe, signal_ts, horizon_bars):
    df = load_from_warehouse(symbol, timeframe)
    if df.empty: return None, True
    df = df.sort_values("bar_ts_utc").reset_index(drop=True)
    signal_ts = pd.Timestamp(signal_ts).tz_convert("UTC")
    future = df[df["bar_ts_utc"] >= signal_ts]
    if future.empty: return None, True
    idx = future.index[0] + horizon_bars
    if idx >= len(df): return None, True
    return float(df.iloc[idx]["close_adj"]), False

def run_live_capture(universe_source="auto"):
    univ = _load_universe(universe_source)
    if not univ or not PAPER_TRADE_LOG_PATH.exists(): return
    log = pd.read_csv(PAPER_TRADE_LOG_PATH)
    matched = log[(log["kind"]=="entry_signal") & (log["matched"]==True)].copy()
    matched = matched[matched.apply(lambda r: (str(r["symbol"]), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()) in univ, axis=1)]
    if matched.empty: return print("No matched signals in universe.")
    
    results = []
    for _, sig in matched.iterrows():
        row = {"symbol": sig["symbol"], "timeframe": sig["timeframe"], "slice_combination": sig["slice_combination"], "signal_ts_utc": sig["bar_ts_utc"], "signal_close": sig["close_adj"]}
        for h in HORIZONS:
            close, partial = _get_exit_close(sig["symbol"], sig["timeframe"], sig["bar_ts_utc"], h)
            row[f"fwd_ret_{h}b"] = (close / sig["close_adj"] - 1.0) if close else None
        results.append(row)
    
    pd.DataFrame(results).to_csv(LIVE_FORWARD_RETURNS_PATH, index=False)
    print(f"✅ Success: Captured returns for {len(results)} signals to {LIVE_FORWARD_RETURNS_PATH}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-source", default="auto")
    run_live_capture(parser.parse_args().universe_source)
