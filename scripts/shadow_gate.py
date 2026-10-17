"""Live shadow-expectancy demotion ledger (2026-10 ROI pass).

Reads localdata/live_forward_returns.csv — the direction-adjusted
forward returns of every matched signal, traded or not (~10x the
sample of realized round-trips) — and maintains:

  localdata/shadow_gate_state.json     two-strike ledger (per data cycle)
  localdata/shadow_demoted_slices.csv  slices currently demoted
  localdata/shadow_gate_report.json    full per-slice + per-pool stats

A slice becomes a demotion CANDIDATE when, on >= --min-n signals, its
mean tradeable 5-bar forward return is below -cost-drag (default 13bp,
the measured round-trip drag), confirmed on the 20-bar horizon when
measurable. It is DEMOTED (entries blocked when paper_trade runs with
--shadow-gate) only after --strikes consecutive DISTINCT data cycles
as a candidate — a cycle is keyed by the newest signal timestamp, so
re-running on unchanged data cannot manufacture strikes.

This script places no orders and blocks nothing by itself; it only
writes the ledger the monitor's gate reads. Fails open: missing/empty
input writes an empty demotion list.

Usage:
    python3 scripts/shadow_gate.py               # advance one cycle
    python3 scripts/shadow_gate.py --dry-run     # report only
    python3 scripts/shadow_gate.py --reset       # clear strikes
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import json
from pathlib import Path

import pandas as pd

from price.config import DATA_DIR
from price.roi_guards import (
    DEFAULT_COST_DRAG_BPS,
    DEFAULT_MIN_SHADOW_N,
    DEFAULT_REQUIRED_STRIKES,
    compute_pool_stats,
    compute_shadow_stats,
    shadow_verdict,
    update_demotion_state,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live-forward", type=Path,
                        default=DATA_DIR / "live_forward_returns.csv")
    parser.add_argument("--state", type=Path,
                        default=DATA_DIR / "shadow_gate_state.json")
    parser.add_argument("--demoted-out", type=Path,
                        default=DATA_DIR / "shadow_demoted_slices.csv")
    parser.add_argument("--report-out", type=Path,
                        default=DATA_DIR / "shadow_gate_report.json")
    parser.add_argument("--min-n", type=int, default=DEFAULT_MIN_SHADOW_N)
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_DRAG_BPS)
    parser.add_argument("--strikes", type=int, default=DEFAULT_REQUIRED_STRIKES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    live = pd.DataFrame()
    if args.live_forward.exists():
        try:
            live = pd.read_csv(args.live_forward, low_memory=False)
        except Exception as exc:  # noqa: BLE001
            print(f"warning: could not read {args.live_forward}: {exc}", file=sys.stderr)

    stats = compute_shadow_stats(live)
    pools = compute_pool_stats(live)
    verdicts = {}
    for row in stats:
        row["verdict"] = shadow_verdict(
            row, min_n=args.min_n, cost_drag_bps=args.cost_bps)
        verdicts[row["key"]] = row["verdict"]

    cycle_key = ""
    if not live.empty and "signal_ts_utc" in live.columns:
        cycle_key = str(live["signal_ts_utc"].astype(str).max())

    state = {}
    if args.state.exists() and not args.reset:
        try:
            state = json.loads(args.state.read_text())
        except Exception:  # noqa: BLE001
            state = {}
    state, demoted_keys = update_demotion_state(
        state, verdicts, cycle_key, required_strikes=args.strikes)
    demoted_set = set(demoted_keys)
    for row in stats:
        row["strikes"] = int((state.get("strikes") or {}).get(row["key"], 0))
        row["demoted"] = row["key"] in demoted_set

    report = {
        "cycle_key": cycle_key,
        "n_signals": int(len(live)),
        "n_slices_scored": len(stats),
        "n_demote_candidates": sum(1 for v in verdicts.values() if v == "demote_candidate"),
        "n_demoted": len(demoted_keys),
        "pools": pools,
        "slices": stats,
    }

    if not args.dry_run:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2))
        demoted_rows = [r for r in stats if r["demoted"]]
        pd.DataFrame(demoted_rows, columns=[
            "symbol", "timeframe", "bin_mode", "side", "slice_combination",
            "n_5b", "mean_5b_bps", "n_20b", "mean_20b_bps", "strikes",
        ]).to_csv(args.demoted_out, index=False)
        args.report_out.write_text(json.dumps(report, indent=2, default=str))

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Shadow gate: {len(live)} signals, {len(stats)} slices scored, "
              f"cycle={cycle_key or 'n/a'}")
        print("\nPools (timeframe x side):")
        for p in pools:
            m5 = p["mean_5b_bps"]
            print(f"  {p['timeframe']:4} {p['side']:5} n5={p['n_5b']:5d} "
                  f"mean5={m5:8.1f}bp" if m5 is not None else
                  f"  {p['timeframe']:4} {p['side']:5} n5={p['n_5b']:5d} mean5=   n/a")
        cands = [r for r in stats if r["verdict"] == "demote_candidate"]
        if cands:
            print(f"\nDemote candidates ({len(cands)}):")
            for r in cands[:20]:
                print(f"  {r['symbol']:6} {r['timeframe']:4} {r['side']:5} "
                      f"n={r['n_5b']:3d} mean5={r['mean_5b_bps']:.1f}bp "
                      f"strikes={r['strikes']} demoted={r['demoted']} "
                      f"{r['slice_combination'][:50]}")
        print(f"\nDemoted now: {len(demoted_keys)} slice(s)"
              + (" (dry-run; nothing written)" if args.dry_run else
                 f" -> {args.demoted_out}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
