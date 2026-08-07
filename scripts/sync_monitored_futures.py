#!/usr/bin/env python3
"""
Dynamic futures monitored book sync.

Builds localdata/monitored_slices_futures.csv from latest research outputs,
so paper trading trades fresh candidates, not static.

This mirrors sync_monitored_crypto.py but for the futures substrate.
Unlike crypto, futures has no fallback mode — if the gate produces zero
candidates, the book stays empty. No leaderboard bypass.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT / "scripts"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import research_freshness  # noqa: E402

BOOK_COLUMNS = ["symbol", "timeframe", "slice_combination", "side", "bin_mode"]


def _write_header_only(output_path: Path) -> None:
    pd.DataFrame(columns=BOOK_COLUMNS).to_csv(output_path, index=False)


def build_from_candidates(candidates_path: Path, output_path: Path, *, now: Optional[datetime] = None, max_age_hours: float = research_freshness.DEFAULT_MAX_AGE_HOURS) -> bool:
    if not candidates_path.exists():
        print(f"No candidates file at {candidates_path}")
        return False
    stale_reason = research_freshness.research_stale_reason(candidates_path, max_age_hours=max_age_hours, now=now)
    if stale_reason is not None:
        print(f"::notice::futures book sync refused: {stale_reason}")
        _write_header_only(output_path)
        return False
    try:
        df = pd.read_csv(candidates_path)
    except Exception as e:
        print(f"Failed to read {candidates_path}: {e}")
        return False
    if df.empty:
        print(f"Candidates file empty: {candidates_path} — gate produced zero tradeable candidates. Book stays empty.")
        # Write header-only to clear any stale entries
        _write_header_only(output_path)
        return False
    required = {"symbol", "timeframe", "slice_combination"}
    missing = required - set(df.columns)
    if missing:
        print(f"Candidates missing columns {missing}")
        return False
    keep_cols = [c for c in ["symbol", "timeframe", "slice_combination", "side", "bin_mode", "overall_regime_status", "source_note"] if c in df.columns]
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


def main():
    parser = argparse.ArgumentParser(description="Sync dynamic futures monitored book")
    parser.add_argument(
        "--candidates-path",
        type=Path,
        default=Path("localdata/research/futures/monitored_candidates_futures.csv"),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=Path("localdata/monitored_slices_futures.csv"),
    )
    parser.add_argument("--max-research-age-hours", type=float, default=research_freshness.DEFAULT_MAX_AGE_HOURS,
                        help="Refuse (and wipe the book to header-only) when the research summary stamping the candidates is older than this many hours. Default 72.")
    args = parser.parse_args()

    args.output_path.parent.mkdir(parents=True, exist_ok=True)

    ok = build_from_candidates(args.candidates_path, args.output_path, max_age_hours=args.max_research_age_hours)
    if not ok:
        print("Futures monitored book is empty — gate produced no tradeable candidates.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
