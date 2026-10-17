"""Populate expected-return coverage for conviction sizing (2026-10).

Conviction sizing can only differentiate slices that HAVE an edge row.
Historically only slices present in localdata/monitored_edge_metrics.csv
(a curated file) or candidate_leaderboard.csv got one, leaving most of
the monitored book at neutral conviction — allocation by coin flip.

This script fills the gaps: for every slice in the monitored book, if
no edge row exists yet in the output file, it copies the best matching
row from the research leaderboards (merged 1d/1h/15m first, then the
per-timeframe leaderboards). Existing rows are NEVER overwritten — the
curated file keeps operator priority. Rows are keyed by
(symbol, timeframe, slice_combination, side, bin_mode).

Idempotent. Read the --dry-run coverage report before writing.

Usage:
    python3 scripts/build_expected_returns.py --dry-run
    python3 scripts/build_expected_returns.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
from pathlib import Path

import pandas as pd

from price.config import DATA_DIR

KEY = ["symbol", "timeframe", "slice_combination", "side", "bin_mode"]


def _keyed(df: pd.DataFrame) -> dict:
    out = {}
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        key = tuple(str(r.get(c, "") or "").strip() for c in KEY)
        key = (key[0].upper(), key[1], key[2],
               (key[3] or "long").lower(), (key[4] or "insample").lower())
        if key[0] and key[2]:
            out.setdefault(key, r)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--monitored", type=Path, default=DATA_DIR / "monitored_slices.csv")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "monitored_edge_metrics.csv")
    parser.add_argument("--leaderboards", type=Path, nargs="*", default=None,
                        help="Leaderboard CSVs in priority order.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    leaderboards = args.leaderboards or [
        DATA_DIR / "research" / "merged" / "1d" / "candidate_leaderboard_merged.csv",
        DATA_DIR / "research" / "merged" / "1h" / "candidate_leaderboard_merged.csv",
        DATA_DIR / "research" / "merged" / "15m" / "candidate_leaderboard_merged.csv",
        DATA_DIR / "candidate_leaderboard_1d_tiingo_liquid236.csv",
        DATA_DIR / "candidate_leaderboard_1h_rth_liquid236.csv",
        DATA_DIR / "candidate_leaderboard_15m_rth_liquid236.csv",
        DATA_DIR / "candidate_leaderboard.csv",
    ]

    monitored = pd.read_csv(args.monitored) if args.monitored.exists() else pd.DataFrame()
    book_keys = set()
    for _, r in monitored.iterrows():
        book_keys.add((
            str(r.get("symbol", "")).upper(), str(r.get("timeframe", "")),
            str(r.get("slice_combination", "")),
            str(r.get("side", "long") or "long").lower(),
            str(r.get("bin_mode", "insample") or "insample").lower(),
        ))

    existing = pd.read_csv(args.output) if args.output.exists() else pd.DataFrame()
    existing_by_key = _keyed(existing)
    covered_before = sum(1 for k in book_keys if k in existing_by_key)

    source_rows: dict = {}
    for path in leaderboards:
        if not Path(path).exists():
            continue
        try:
            lb = pd.read_csv(path, low_memory=False)
        except Exception:  # noqa: BLE001
            continue
        for key, row in _keyed(lb).items():
            # First (highest-priority) source wins for a missing identity.
            if key in book_keys and key not in existing_by_key and key not in source_rows:
                if pd.notna(row.get("valid_mean_ret_costadj")):
                    source_rows[key] = row

    columns = list(existing.columns) if not existing.empty else None
    new_records = []
    for key, row in source_rows.items():
        rec = {c: row.get(c) for c in (columns or row.index)}
        new_records.append(rec)
    if columns is None and new_records:
        columns = list(new_records[0].keys())

    covered_after = covered_before + len(new_records)
    print(f"Monitored book: {len(book_keys)} slices")
    print(f"Edge coverage:  {covered_before} -> {covered_after} "
          f"({covered_before}/{len(book_keys) or 1} -> "
          f"{covered_after}/{len(book_keys) or 1})")
    print(f"Rows to add:    {len(new_records)} (existing rows never overwritten)")

    if args.dry_run:
        print("Dry-run; nothing written.")
        return 0
    if new_records:
        out = pd.concat([existing, pd.DataFrame(new_records, columns=columns)],
                        ignore_index=True) if not existing.empty else pd.DataFrame(new_records)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.output, index=False)
        print(f"Wrote {args.output} ({len(out)} rows)")
    else:
        print("Nothing to add.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
