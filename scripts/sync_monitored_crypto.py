#!/usr/bin/env python3
"""
Dynamic crypto monitored book sync.

Builds localdata/monitored_slices_crypto.csv from latest research outputs,
so paper trading trades fresh candidates, not static.

Two modes:
1. --candidates-path points to monitored_candidates_crypto.csv (preferred) – deterministic shortlist from regime registry.
2. --fallback-clean-mixed – when monitored_candidates is empty, take top clean_survivor_wf_mixed from candidate_leaderboard_crypto_rolling.csv as dynamic paper book.

This mirrors sync_monitored.py for equities but is substrate-isolated (crypto only).
"""
import argparse
from pathlib import Path
import pandas as pd

def build_from_candidates(candidates_path: Path, output_path: Path) -> bool:
    if not candidates_path.exists():
        print(f"No candidates file at {candidates_path}")
        return False
    try:
        df = pd.read_csv(candidates_path)
    except Exception as e:
        print(f"Failed to read {candidates_path}: {e}")
        return False
    if df.empty:
        print(f"Candidates file empty: {candidates_path}")
        return False
    # Ensure required columns
    required = {"symbol","timeframe","slice_combination"}
    missing = required - set(df.columns)
    if missing:
        print(f"Candidates missing columns {missing}")
        return False
    # Keep paper-relevant columns, ensure bin_mode, side
    keep_cols = [c for c in ["symbol","timeframe","slice_combination","side","bin_mode","overall_regime_status","source_note"] if c in df.columns]
    out = df[keep_cols].copy()
    if "side" not in out.columns:
        out["side"] = "long"
    if "bin_mode" not in out.columns:
        out["bin_mode"] = "rolling"
    out["side"] = out["side"].astype(str).str.lower()
    out["bin_mode"] = out["bin_mode"].astype(str).str.lower()
    out.to_csv(output_path, index=False)
    print(f"Wrote {len(out)} rows to {output_path} from candidates {candidates_path}")
    return True

def build_from_clean_mixed(leaderboard_path: Path, output_path: Path, top_n: int = 8) -> bool:
    if not leaderboard_path.exists():
        print(f"No leaderboard at {leaderboard_path}")
        return False
    try:
        lb = pd.read_csv(leaderboard_path)
    except Exception as e:
        print(f"Failed to read leaderboard: {e}")
        return False
    if lb.empty or "triage_bucket" not in lb.columns:
        print("Leaderboard empty or missing triage_bucket")
        return False
    # Take clean_survivor_wf_mixed as dynamic paper book (research-only, not strict)
    clean = lb[lb["triage_bucket"].astype(str).str.startswith("clean_survivor")].copy()
    if clean.empty:
        print("No clean_survivor rows")
        return False
    clean = clean.sort_values(["search_wide_bh_pass","search_wide_bonferroni_pass","robustness_score","valid_mean_ret_costadj"], ascending=[False,False,False,False])
    top = clean.head(top_n)
    out_cols = [c for c in ["symbol","timeframe","slice_combination","side","bin_mode"] if c in top.columns]
    out = top[out_cols].copy()
    if "side" not in out.columns:
        out["side"] = "long"
    if "bin_mode" not in out.columns:
        out["bin_mode"] = "rolling"
    out.to_csv(output_path, index=False)
    print(f"Wrote {len(out)} rows to {output_path} from top {top_n} clean_survivor (fallback)")
    return True

def main():
    parser = argparse.ArgumentParser(description="Sync dynamic crypto monitored book")
    parser.add_argument("--candidates-path", type=Path, default=Path("localdata/research/crypto/1d/monitored_candidates_crypto.csv"))
    parser.add_argument("--leaderboard-path", type=Path, default=Path("localdata/research/crypto/1d/candidate_leaderboard_crypto_rolling.csv"))
    parser.add_argument("--output-path", type=Path, default=Path("localdata/monitored_slices_crypto.csv"))
    parser.add_argument("--fallback-clean-mixed", action="store_true", help="If candidates empty, fallback to top clean_survivor_wf_mixed")
    parser.add_argument("--top-n", type=int, default=8)
    args = parser.parse_args()

    args.output_path.parent.mkdir(parents=True, exist_ok=True)

    ok = build_from_candidates(args.candidates_path, args.output_path)
    if not ok and args.fallback_clean_mixed:
        print("Candidates empty, trying fallback clean_mixed")
        ok = build_from_clean_mixed(args.leaderboard_path, args.output_path, top_n=args.top_n)
    if not ok:
        print("Failed to build crypto monitored book – keeping existing if any")
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
