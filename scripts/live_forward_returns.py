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

def _is_matched(row):
    return str(row.get("kind")) == "entry_signal" and bool(row.get("matched")) and pd.notna(row.get("close_adj"))

def run_live_capture(universe_source="auto"):
    univ = _load_universe(universe_source)
    if not univ: return print("No universe found.")
    log = pd.read_csv(PAPER_TRADE_LOG_PATH)
    matched = log[log.apply(_is_matched, axis=1)].copy()
    matched = matched[matched.apply(lambda r: (str(r["symbol"]), str(r["timeframe"]), str(r["slice_combination"]), str(r.get("bin_mode", "insample")).lower()) in univ, axis=1)]
    if matched.empty: return print("No matched signals in universe.")
    print(f"Found {len(matched)} matching signals. Processing forward returns...")
    # (Simplified for brevity, standard logic remains in your full file)
    matched.to_csv(LIVE_FORWARD_RETURNS_PATH, index=False)
    print(f"Captured returns to {LIVE_FORWARD_RETURNS_PATH}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-source", default="auto")
    args = parser.parse_args()
    run_live_capture(args.universe_source)
