import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import argparse
from pathlib import Path
from typing import Set, Tuple
import pandas as pd
from price.config import DATA_DIR
from price.warehouse import load_from_warehouse

PAPER_TRADE_LOG_PATH = DATA_DIR / "paper_trade_log.csv"
LEADERBOARD_PATH = DATA_DIR / "candidate_leaderboard.csv"
MONITORED_SLICES_PATH = DATA_DIR / "monitored_slices.csv"
LIVE_FORWARD_RETURNS_PATH = DATA_DIR / "live_forward_returns.csv"
HORIZONS = [5, 20]

def _load_universe(source: str) -> Set[Tuple]:
    univ = set()
    # Always load monitored slices in 'auto' or 'monitored' mode
    if source in ("monitored", "auto") and MONITORED_SLICES_PATH.exists():
        rows = pd.read_csv(MONITORED_SLICES_PATH)
        for _, r in rows.iterrows():
            univ.add((str(r["symbol"]).upper(), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()))
    # Also load leaderboard if requested
    if source in ("leaderboard", "auto") and LEADERBOARD_PATH.exists():
        lb = pd.read_csv(LEADERBOARD_PATH)
        if "triage_bucket" in lb.columns:
            clean = lb[lb["triage_bucket"].astype(str).str.startswith("clean_survivor")]
            for _, r in clean.iterrows():
                univ.add((str(r["symbol"]).upper(), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()))
    return univ

def _get_exit_close(symbol, timeframe, signal_ts, horizon_bars):
    df = load_from_warehouse(symbol, timeframe)
    if df.empty: return None
    df = df.sort_values("bar_ts_utc").reset_index(drop=True)
    try:
        sig_ts = pd.to_datetime(signal_ts, utc=True)
        df["bar_ts_utc"] = pd.to_datetime(df["bar_ts_utc"], utc=True)
        future = df[df["bar_ts_utc"] >= sig_ts]
        if future.empty: return None
        idx = future.index[0] + horizon_bars
        return float(df.iloc[idx]["close_adj"]) if idx < len(df) else None
    except: return None

def run_live_capture(universe_source="auto"):
    univ = _load_universe(universe_source)
    if not univ or not PAPER_TRADE_LOG_PATH.exists():
        return print(f"Checking universe: {len(univ)} slices. Log exists: {PAPER_TRADE_LOG_PATH.exists()}")
    
    log = pd.read_csv(PAPER_TRADE_LOG_PATH)
    # Robust boolean check (handles string "True" and boolean True)
    is_matched = log["matched"].astype(str).str.lower() == "true"
    is_entry = log["kind"] == "entry_signal"
    matched = log[is_matched & is_entry].copy()
    
    # Filter by universe
    matched = matched[matched.apply(lambda r: (str(r["symbol"]).upper(), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()) in univ, axis=1)]
    
    if matched.empty:
        return print(f"Found 0 matches in log for the {len(univ)} slices in your universe.")
    
    results = []
    for _, sig in matched.iterrows():
        row = {"symbol": sig["symbol"], "timeframe": sig["timeframe"], "slice_combination": sig["slice_combination"], "signal_ts_utc": sig["bar_ts_utc"], "signal_close": sig["close_adj"]}
        for h in HORIZONS:
            close = _get_exit_close(sig["symbol"], sig["timeframe"], sig["bar_ts_utc"], h)
            row[f"fwd_ret_{h}b"] = (close / sig["close_adj"] - 1.0) if close else None
        results.append(row)
    
    df_out = pd.DataFrame(results).drop_duplicates(subset=["symbol", "signal_ts_utc", "slice_combination"])
    df_out.to_csv(LIVE_FORWARD_RETURNS_PATH, index=False)
    print(f"✅ Success: Captured {len(df_out)} realized signals to {LIVE_FORWARD_RETURNS_PATH}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-source", default="auto")
    run_live_capture(parser.parse_args().universe_source)
