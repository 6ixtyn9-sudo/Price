import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
from pathlib import Path

import pandas as pd
from price.config import SYMBOLS
from price.discovery import discover_market_slices, precompute_binned_frame, clear_cond_bins_cache

DISCOVERED_SLICES_PATH = "localdata/discovered_slices.csv"


def _build_combinations(timeframe: str, cond_symbols=None, profile: str | None = None):
    profile = (profile or "default").lower()

    if profile == "crypto":
        combinations = [
            ["state_ext", "state_slope"],
            ["state_ext", "state_vol"],
            ["state_volume", "state_ext"],
            ["state_ret_day", "state_ext"],
            ["state_weekpart", "state_ext"],
            # T2 crypto positioning states (fixed-prior, lane-scoped).
            ["state_funding", "state_ext"],
            ["state_oi", "state_ext"],
            ["state_funding", "state_oi", "state_ext"],
        ]
        if timeframe in ["15m", "1h"]:
            combinations += [
                ["state_utc_session", "state_volume"],
                ["state_utc_session", "state_ext"],
                ["state_utc_session", "state_slope"],
                ["state_utc_session", "state_ext", "state_slope"],
                ["state_weekpart", "state_ext", "state_vol"],
                ["state_ret_day", "state_ext", "state_slope"],
                ["state_utc_session", "state_funding", "state_ext"],
            ]
    elif profile == "futures":
        combinations = [
            ["state_ext", "state_slope"],
            ["state_ext", "state_vol"],
            ["state_volume", "state_ext"],
            # T3 futures COT weekly conditioning (slow dim -- doesn't multiply space much).
            ["state_cot", "state_ext"],
            ["state_cot", "state_ext", "state_slope"],
        ]
        if timeframe in ["15m", "1h"]:
            combinations += [
                ["state_session", "state_volume"],
                ["state_session", "state_ext"],
                ["state_session", "state_slope"],
            ]
    else:
        combinations = [
            ["state_ext", "state_slope"],
            ["state_ext", "state_vol"],
            ["state_volume", "state_ext"],
            # T4 equity macro-context states.
            ["state_vix", "state_ext"],
            ["state_breadth", "state_ext"],
            ["state_vix", "state_breadth", "state_ext"],
            # ── Expanded standalone matrix ──────────────────────
            # The cross-conditioned matrix (USO/TLT) adds 11 combos
            # below; without these, standalone gets 6 shots vs 11,
            # and cross-conditioned survives validation at ~3× the
            # rate (narrower bins → fewer samples → higher apparent
            # significance).  Doubling the standalone matrix gives
            # both families equal statistical footing.
            ["state_ext", "state_ret_5"],
            ["state_ext", "state_ret_1"],
            ["state_slope", "state_vol"],
            ["state_volume", "state_slope"],
            ["state_ext", "state_slope", "state_vol"],
            ["state_volume", "state_ext", "state_slope"],
        ]
        if timeframe in ["15m", "1h"]:
            combinations.append(["state_session", "state_volume"])
            combinations.append(["state_session", "state_ext"])
            combinations.append(["state_session", "state_slope"])
            combinations.append(["state_session", "state_ext", "state_slope"])
            combinations.append(["state_vix", "state_session", "state_ext"])
            # ── Expanded hourly standalone ─────────────────────
            combinations.append(["state_session", "state_ret_5"])
            combinations.append(["state_session", "state_vol"])
            combinations.append(["state_session", "state_slope", "state_vol"])

    if cond_symbols:
        for cs in [s.upper() for s in cond_symbols]:
            combinations = combinations + [
                [f"cross_{cs}_state_slope", "state_ext"],
                [f"cross_{cs}_state_vol", "state_ext"],
                [f"cross_{cs}_state_ext", "state_ext"],
                [f"cross_{cs}_state_slope", "state_slope"],
            ]
        if len(cond_symbols) >= 2:
            cs0, cs1 = cond_symbols[0].upper(), cond_symbols[1].upper()
            combinations = combinations + [
                [f"cross_{cs0}_state_slope", f"cross_{cs1}_state_ext", "state_ext"],
                [f"cross_{cs0}_state_ext", f"cross_{cs1}_state_slope", "state_ext"],
                [f"cross_{cs0}_state_slope", f"cross_{cs1}_state_slope", "state_ext"],
            ]
    return combinations


def run_discovery(target_symbols=None, timeframe="1d", min_samples=15, append=False, cond_symbols=None, bin_mode="insample", profile=None):
    symbols = target_symbols or SYMBOLS

    combinations = _build_combinations(timeframe, cond_symbols=cond_symbols, profile=profile)

    all_slices = []

    # Clear the cross-asset conditioning cache at the start of each timeframe
    # so stale frames from a different timeframe don't leak.
    clear_cond_bins_cache()

    for symbol in symbols:
        symbol = symbol.upper()
        if cond_symbols and symbol in [s.upper() for s in cond_symbols]:
            print(f"Skipping {symbol}: cannot condition a symbol on itself.")
            continue
        print(f"\n[search] Exploring state slices for {symbol} ({timeframe})...")
        
        # KEY OPTIMISATION: compute features + bins + cross-asset states ONCE
        # per (symbol, timeframe), then reuse the cached frame for every
        # combination.  Previously each combination triggered a full
        # load→feature→bin→attach cycle — 13× redundant compute per symbol.
        # Cross-asset conditioning frames (USO, TLT) are cached globally
        # across all primary symbols, so they're loaded+featured+binned
        # exactly once, not once per primary symbol.
        try:
            binned_frame = precompute_binned_frame(
                symbol, timeframe,
                cond_symbols=cond_symbols,
                bin_mode=bin_mode,
            )
        except Exception as e:
            print(f"  [error] Failed to precompute features for {symbol}: {e}")
            continue
        
        if binned_frame.empty:
            print(f"  No data for {symbol} ({timeframe}); skipping.")
            continue
        
        for fields in combinations:
            print(f"Testing state-space combination: {fields}")
            try:
                slices = discover_market_slices(
                    symbol, timeframe, fields,
                    min_samples=min_samples,
                    cond_symbols=cond_symbols,
                    bin_mode=bin_mode,
                    _precomputed_binned=binned_frame,
                )
                if not slices.empty:
                    print(f"  -> Discovered {len(slices)} slices satisfying sample floor.")
                    all_slices.append(slices)
                else:
                    print("  -> No slices met the sample size threshold.")
            except Exception as e:
                print(f"  [error] Error exploring combination {fields}: {e}")
                
    if not all_slices:
        print("\nNo market-state slices were discovered matching the sample floor.")
        return

    final_slices = pd.concat(all_slices, ignore_index=True)
    # Rank by tradeable (direction-adjusted) P&L so the strongest edge of
    # EITHER direction -- long or short -- floats to the top. Falls back to the
    # raw mean for any rows that lack the tradeable column (defensive).
    _sort_col = "tradeable_mean_fwd_ret_5" if "tradeable_mean_fwd_ret_5" in final_slices.columns else "mean_fwd_ret_5"
    final_slices = final_slices.sort_values(_sort_col, ascending=False).reset_index(drop=True)

    output_path = Path(DISCOVERED_SLICES_PATH)

    if append and output_path.exists():
        existing = pd.read_csv(output_path)
        # Replace any prior rows for the same (symbol, timeframe) pairs covered by
        # this run, then append the fresh results, so running discovery for
        # multiple timeframes/symbols accumulates instead of clobbering.
        covered = {(s.upper(), timeframe) for s in symbols}
        existing_keep = existing[
            ~existing.apply(lambda r: (r["symbol"], r["timeframe"]) in covered, axis=1)
        ]
        final_slices = pd.concat([existing_keep, final_slices], ignore_index=True)
        _sort_col = "tradeable_mean_fwd_ret_5" if "tradeable_mean_fwd_ret_5" in final_slices.columns else "mean_fwd_ret_5"
        final_slices = final_slices.sort_values(_sort_col, ascending=False).reset_index(drop=True)

    final_slices.to_csv(output_path, index=False)
    action = "Appended to" if append else "Saved all discovered slices to"
    print(f"\n[ok] {action} {output_path}")

    print("\n== Top 10 Discovered Market Slices (by tradeable, direction-adjusted P&L) ==")
    show_cols = [c for c in ["symbol", "timeframe", "slice_combination", "side", "mean_fwd_ret_5", "sample_count"] if c in final_slices.columns]
    print(final_slices[show_cols].head(10).to_string(index=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover high-stability 3D-5D market slices.")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to explore")
    parser.add_argument("--timeframe", default="1d", choices=["15m", "1h", "1d"], help="Timeframe to explore")
    parser.add_argument("--min-samples", type=int, default=15, help="Minimum sample floor per slice")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge into the existing discovered_slices.csv instead of overwriting it "
             "(replaces only rows for the same symbol/timeframe pairs covered by this run). "
             "Use this when running discovery across multiple timeframes so earlier runs "
             "are not lost.",
    )
    parser.add_argument(
        "--condition-on",
        nargs="+",
        default=None,
        help="Optional conditioning symbol(s). Supports multiple for multi-conditioning "
             "(e.g., --condition-on USO TLT).",
    )
    parser.add_argument(
        "--bin-mode",
        default="insample",
        choices=["insample", "rolling"],
        help="How to bin quantile state fields. 'insample' (default) = full-history "
        "quantiles (original behaviour). 'rolling' = look-ahead-free expanding-window "
        "quantiles (bar T's boundary uses only bars before T). Use 'rolling' end-to-end "
        "(discovery + validation + ML) for the overfit-kill.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=["default", "crypto", "futures"],
        help="Optional substrate-specific discovery matrix. Omit/default preserves the current system. "
        "Use 'crypto' for the isolated crypto lane and 'futures' for the research-only futures lane.",
    )

    args = parser.parse_args()
    
    run_discovery(
        target_symbols=args.symbols,
        timeframe=args.timeframe,
        min_samples=args.min_samples,
        append=args.append,
        cond_symbols=args.condition_on,
        bin_mode=args.bin_mode,
        profile=args.profile,
    )
