"""Phase V4 executive script: validate discovered slices against
train/valid separation, transaction costs, overlap-aware significance,
and walk-forward survival.

Reads localdata/discovered_slices.csv (produced by scripts/discover_slices.py)
and, for each slice, rebuilds the timestamped eligible-row dataset from the
local warehouse (no re-ingestion, no network calls), then runs:
  1. a chronological train/valid check (cost-adjusted, Newey-West t-stat)
  2. a walk-forward validation across N folds

Prints a consolidated scorecard and writes localdata/validated_slices.csv.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


import argparse
import contextlib
import hashlib
import io
import os
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from price.discovery import attach_cross_asset_states, apply_state_bins
from price.features import compute_price_features
from price.regime import attach_regime_labels, resolve_regime_symbol
from price.validation import (
    apply_slice_filter,
    chronological_train_valid_split,
    direction_adjusted_returns,
    evaluate_slice_train_valid,
    parse_slice_combination,
    summarize_returns,
    walk_forward_validate_slice,
)
from price.warehouse import load_from_warehouse

DISCOVERED_SLICES_PATH = "localdata/discovered_slices.csv"
VALIDATED_SLICES_PATH = "localdata/validated_slices.csv"
FEATURES_CACHE_DIR = Path("localdata/features_cache")
# Bump whenever compute_price_features / apply_state_bins semantics change.
# Without this in the cache key, a feature-code fix silently keeps serving
# stale cached features until the warehouse file's mtime happens to change.
FEATURES_SCHEMA_VERSION = 2
SCENARIO_GRID_PATH = "localdata/validation_scenario_grid.csv"
WALK_FORWARD_DIAGNOSTICS_PATH = "localdata/walk_forward_diagnostics.csv"
DATE_RANGE_DIAGNOSTICS_PATH = "localdata/date_range_diagnostics.csv"
REGIME_STRATIFIED_DIAGNOSTICS_PATH = "localdata/regime_stratified_diagnostics.csv"
CANDIDATE_LEADERBOARD_PATH = "localdata/candidate_leaderboard.csv"


def cross_symbols_from_filter(slice_filter: dict) -> dict:
    """Extract {cond_symbol: [state_fields]} from a parsed slice filter.

    Cross-asset fields are named cross_<SYM>_<state_field>, e.g.
    cross_USO_state_slope. Symbols never contain underscores; state fields
    always start with 'state_'. Returns an empty dict when there are no
    cross-asset fields.
    """
    needed: dict = {}
    for field in slice_filter:
        if not field.startswith("cross_"):
            continue
        rest = field[len("cross_"):]
        marker = "_state_"
        idx = rest.find(marker)
        if idx == -1:
            continue
        sym = rest[:idx]
        state_field = rest[idx + 1:]  # drop leading underscore -> state_*
        needed.setdefault(sym, [])
        if state_field not in needed[sym]:
            needed[sym].append(state_field)
    return needed


def build_eligible_frame(
    symbol: str, timeframe: str, cross_symbols: dict = None,
    bin_mode: str = "insample",
) -> pd.DataFrame:
    """Rebuild the timestamped, binned, forward-eligible feature frame for a
    symbol/timeframe pair, straight from the local warehouse. No network
    calls, no re-ingestion -- only reads what has already been captured.

    If cross_symbols is given ({cond_symbol: [state_fields]}), each
    conditioning symbol's most-recent-completed state is attached (backward
    as-of, no look-ahead) as cross_<SYM>_<field> columns before the
    forward-eligible rows are selected. This reconstructs, at validation
    time, exactly the cross-asset columns discovery produced.

    bin_mode controls how state_* quantile fields are binned:
      - "insample" (default): full-history pd.qcut (original behaviour;
        backward compatible). Look-ahead-prone.
      - "rolling": look-ahead-free expanding-window quantiles via
        bin_features_rolling (bar T's boundary uses only bars before T).
        The HANDOVER's V5 note names this the highest-value overfit-kill.
    bin_mode is part of the disk-cache key so the two modes never collide.
    
    Features are cached to disk to avoid recomputing rolling windows on
    repeated validation runs."""
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        return pd.DataFrame()

    warehouse_file = Path(f"localdata/warehouse/symbol={symbol}/timeframe={timeframe}/data.parquet")
    if warehouse_file.exists():
        mtime = warehouse_file.stat().st_mtime
        cache_key = hashlib.md5(
            f"{symbol}_{timeframe}_{mtime}_{bin_mode}_v{FEATURES_SCHEMA_VERSION}".encode()
        ).hexdigest()
        cache_file = FEATURES_CACHE_DIR / f"{cache_key}.parquet"
        
        if cache_file.exists():
            df_feat = pd.read_parquet(cache_file)
        else:
            df_feat = compute_price_features(df_raw)
            FEATURES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df_feat.to_parquet(cache_file, index=False)
    else:
        df_feat = compute_price_features(df_raw)

    df_binned = apply_state_bins(df_feat, bin_mode=bin_mode)

    if cross_symbols:
        for cond_sym, fields in cross_symbols.items():
            df_binned = attach_cross_asset_states(
                df_binned, cond_sym, timeframe, fields, bin_mode=bin_mode
            )

    return df_binned[df_binned["label_eligible"]].reset_index(drop=True)


def summarize_baseline_train_valid(
    df: pd.DataFrame,
    split: float = 0.7,
    target_col: str = "fwd_ret_5",
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price_col: str = "close_adj",
    min_samples: int = 15,
    side: str = "long",
    short_cost_bps: float = 0.0,
) -> dict:
    """Summarize the unconditional symbol/timeframe baseline over the same
    chronological train/valid windows used for slice validation.

    This gives each slice a fair local benchmark: did the slice beat the
    whole eligible population for that same symbol/timeframe and period, or
    is it only positive because the underlying drifted up?

    `side` direction-adjusts the baseline so a short slice is compared against
    the unconditional SHORT baseline (negate every bar's return), not the
    long drift. This is what makes "excess vs baseline" meaningful for shorts.
    """
    train_df, valid_df = chronological_train_valid_split(df, split=split)

    def summarize_window(window: pd.DataFrame) -> dict:
        if window.empty or target_col not in window.columns:
            return summarize_returns(pd.Series(dtype=float), min_samples=min_samples)

        price = window[price_col] if price_col and price_col in window.columns else None
        returns = direction_adjusted_returns(
            window[target_col],
            side=side,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            price=price,
            short_cost_bps=short_cost_bps,
        )
        return summarize_returns(returns, min_samples=min_samples)

    return {
        "train": summarize_window(train_df),
        "valid": summarize_window(valid_df),
    }


def format_slice_filter(slice_filter: dict) -> str:
    return " + ".join([f"{field}={value}" for field, value in slice_filter.items()])


def iter_parent_slice_filters(slice_filter: dict):
    """Yield all non-empty proper subset filters for a discovered slice.

    Example:
      A+B+C -> A, B, C, A+B, A+C, B+C

    These parent regimes are useful as stricter baselines: a 3D slice should
    ideally beat its simpler 1D/2D explanations, not only the unconditional
    symbol/timeframe baseline.
    """
    items = list(slice_filter.items())
    for size in range(1, len(items)):
        for combo in combinations(items, size):
            yield dict(combo)


def summarize_parent_baselines_train_valid(
    df: pd.DataFrame,
    slice_filter: dict,
    split: float = 0.7,
    target_col: str = "fwd_ret_5",
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price_col: str = "close_adj",
    min_samples: int = 15,
    side: str = "long",
    short_cost_bps: float = 0.0,
) -> dict:
    """Find the strongest parent-regime baseline in train and validation.

    The selected parent for each window is the parent filter with the highest
    cost-adjusted mean return in that same chronological window. This is a
    deliberately conservative diagnostic: if the child slice cannot beat the
    strongest simpler parent in validation, the discovered 2D/3D combination
    may not add much beyond a simpler regime.

    A parent of a short slice is itself a short, so it inherits the child's
    `side` (same direction-adjustment).
    """
    parent_results = []

    for parent_filter in iter_parent_slice_filters(slice_filter):
        tv = evaluate_slice_train_valid(
            df,
            parent_filter,
            split=split,
            target_col=target_col,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            price_col=price_col,
            min_samples=min_samples,
            side=side,
            short_cost_bps=short_cost_bps,
        )
        parent_results.append(
            {
                "filter": format_slice_filter(parent_filter),
                "train": tv["train"],
                "valid": tv["valid"],
            }
        )

    def best_parent(window: str) -> dict:
        eligible = [
            parent
            for parent in parent_results
            if not pd.isna(parent[window].get("mean_return", float("nan")))
        ]
        if not eligible:
            return {
                "filter": "",
                "sample_count": 0,
                "mean_return": float("nan"),
            }

        selected = max(eligible, key=lambda parent: parent[window]["mean_return"])
        return {
            "filter": selected["filter"],
            "sample_count": selected[window]["sample_count"],
            "mean_return": selected[window]["mean_return"],
        }

    return {
        "train": best_parent("train"),
        "valid": best_parent("valid"),
    }


def survives(summary: dict, min_samples: int, p_threshold: float) -> bool:
    if not summary.get("meets_min_samples", False):
        return False
    if summary["sample_count"] == 0:
        return False
    p_value = summary.get("p_value", float("nan"))
    mean_return = summary.get("mean_return", float("nan"))
    if pd.isna(p_value) or pd.isna(mean_return):
        return False
    return mean_return > 0 and p_value < p_threshold


def evidence_supports(summary: dict, p_threshold: float) -> bool:
    """Same directional + significance check as `survives`, but ignores the
    min_samples floor. Used to distinguish slices that are genuinely
    unsupported (wrong sign / not significant) from slices that only fail
    because the chronological split starved them below the sample floor."""
    if summary["sample_count"] == 0:
        return False
    p_value = summary.get("p_value", float("nan"))
    mean_return = summary.get("mean_return", float("nan"))
    if pd.isna(p_value) or pd.isna(mean_return):
        return False
    return mean_return > 0 and p_value < p_threshold


def classify_verdict(train_pass: bool, valid_pass: bool, train_summary: dict, valid_summary: dict, p_threshold: float) -> str:
    """Three-way verdict per HANDOVER.md V4 discipline:
      - 'survived': passes train + valid, including the min_samples floor.
      - 'provisional': directionally correct and significant on the evidence
        available, but at least one window is starved below the sample
        floor. Not yet promotable; needs more data, not evidence of failure.
      - 'rejected': wrong sign, not significant, or no eligible data.
    """
    if train_pass and valid_pass:
        return "survived"

    train_supported = evidence_supports(train_summary, p_threshold)
    valid_supported = evidence_supports(valid_summary, p_threshold)
    train_floor_only = train_supported and not train_pass
    valid_floor_only = valid_supported and not valid_pass

    if (train_pass or train_floor_only) and (valid_pass or valid_floor_only) and (train_floor_only or valid_floor_only):
        return "provisional"

    return "rejected"


def run_validation(
    slices_path: str = DISCOVERED_SLICES_PATH,
    split: float = 0.7,
    cost_bps: float = 1.0,
    cost_per_share: float = 0.0,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    short_cost_bps: float = 0.0,
    bin_mode: str = "insample",
) -> pd.DataFrame:
    try:
        discovered = pd.read_csv(slices_path)
    except FileNotFoundError:
        print(f"No discovered slices file found at {slices_path}. Run scripts/discover_slices.py first.")
        return pd.DataFrame()

    if discovered.empty:
        print(f"{slices_path} is empty. Nothing to validate.")
        return pd.DataFrame()

    frame_cache: dict = {}
    scorecard = []

    for _, row in discovered.iterrows():
        symbol = row["symbol"]
        timeframe = row["timeframe"]
        slice_combination = row["slice_combination"]
        # Direction: discovered slices now carry a `side` column. Default
        # "long" for any row that lacks it (all pre-direction-agnostic rows),
        # which keeps the change fully backward compatible.
        side = str(row.get("side", "long") or "long").lower()
        if side not in ("long", "short"):
            side = "long"

        try:
            slice_filter = parse_slice_combination(slice_combination)
        except ValueError as exc:
            print(f"  -> Could not parse slice '{slice_combination}': {exc}")
            continue

        cross_symbols = cross_symbols_from_filter(slice_filter)
        cache_key = (
            symbol,
            timeframe,
            tuple(sorted((s, tuple(f)) for s, f in cross_symbols.items())),
        )
        if cache_key not in frame_cache:
            frame_cache[cache_key] = build_eligible_frame(
                symbol, timeframe, cross_symbols=cross_symbols, bin_mode=bin_mode
            )
        eligible_df = frame_cache[cache_key]

        if eligible_df.empty:
            print(f"  -> No warehouse data for {symbol} ({timeframe}); skipping '{slice_combination}'.")
            continue

        tv = evaluate_slice_train_valid(
            eligible_df,
            slice_filter,
            split=split,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            min_samples=min_samples,
            side=side,
            short_cost_bps=short_cost_bps,
        )

        baseline = summarize_baseline_train_valid(
            eligible_df,
            split=split,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            min_samples=min_samples,
            side=side,
            short_cost_bps=short_cost_bps,
        )

        parent_baseline = summarize_parent_baselines_train_valid(
            eligible_df,
            slice_filter,
            split=split,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            min_samples=min_samples,
            side=side,
            short_cost_bps=short_cost_bps,
        )

        try:
            wf_folds = walk_forward_validate_slice(
                eligible_df,
                slice_filter,
                n_folds=n_folds,
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
                side=side,
                short_cost_bps=short_cost_bps,
            )
        except ValueError:
            wf_folds = []

        wf_valid_pass = [
            survives(fold["valid"], min_samples=min_samples, p_threshold=p_threshold)
            for fold in wf_folds
        ]
        wf_pass_count = sum(wf_valid_pass)
        wf_pass_pattern = "".join("1" if passed else "0" for passed in wf_valid_pass)
        wf_survival_rate = (wf_pass_count / len(wf_valid_pass)) if wf_valid_pass else float("nan")

        train_pass = survives(tv["train"], min_samples=min_samples, p_threshold=p_threshold)
        valid_pass = survives(tv["valid"], min_samples=min_samples, p_threshold=p_threshold)
        verdict = classify_verdict(train_pass, valid_pass, tv["train"], tv["valid"], p_threshold)

        scorecard.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": slice_combination,
                "side": side,
                "short_cost_bps": short_cost_bps,
                "train_n": tv["train"]["sample_count"],
                "train_mean_ret_costadj": tv["train"]["mean_return"],
                "train_baseline_mean_ret_costadj": baseline["train"]["mean_return"],
                "train_excess_vs_baseline": tv["train"]["mean_return"] - baseline["train"]["mean_return"],
                "train_best_parent_filter": parent_baseline["train"]["filter"],
                "train_best_parent_mean_ret_costadj": parent_baseline["train"]["mean_return"],
                "train_excess_vs_best_parent": tv["train"]["mean_return"] - parent_baseline["train"]["mean_return"],
                "train_t_stat_nw": tv["train"]["t_stat"],
                "train_pass": train_pass,
                "valid_n": tv["valid"]["sample_count"],
                "valid_mean_ret_costadj": tv["valid"]["mean_return"],
                "valid_baseline_mean_ret_costadj": baseline["valid"]["mean_return"],
                "valid_excess_vs_baseline": tv["valid"]["mean_return"] - baseline["valid"]["mean_return"],
                "valid_best_parent_filter": parent_baseline["valid"]["filter"],
                "valid_best_parent_mean_ret_costadj": parent_baseline["valid"]["mean_return"],
                "valid_excess_vs_best_parent": tv["valid"]["mean_return"] - parent_baseline["valid"]["mean_return"],
                "valid_t_stat_nw": tv["valid"]["t_stat"],
                "valid_p_value_nw": tv["valid"]["p_value"],
                "valid_pass": valid_pass,
                "walk_forward_folds": len(wf_folds),
                "walk_forward_pass_count": wf_pass_count,
                "walk_forward_pass_pattern": wf_pass_pattern,
                "walk_forward_survival_rate": wf_survival_rate,
                "survived": bool(train_pass and valid_pass),
                "verdict": verdict,
            }
        )

    scorecard_df = pd.DataFrame(scorecard)
    if scorecard_df.empty:
        print("No slices could be validated (missing warehouse data for all rows).")
        return scorecard_df

    verdict_order = pd.Categorical(
        scorecard_df["verdict"], categories=["survived", "provisional", "rejected"], ordered=True
    )
    scorecard_df = scorecard_df.assign(_verdict_order=verdict_order).sort_values(
        ["_verdict_order", "valid_mean_ret_costadj"], ascending=[True, False]
    ).drop(columns="_verdict_order").reset_index(drop=True)

    scorecard_df.to_csv(VALIDATED_SLICES_PATH, index=False)
    print(f"\nSaved validation scorecard to {VALIDATED_SLICES_PATH}")

    survivors = scorecard_df[scorecard_df["verdict"] == "survived"]
    provisional = scorecard_df[scorecard_df["verdict"] == "provisional"]
    rejected = scorecard_df[scorecard_df["verdict"] == "rejected"]

    print(
        f"\nVerdicts: {len(survivors)} survived, {len(provisional)} provisional "
        f"(starved by sample floor, not falsified), {len(rejected)} rejected "
        f"/ {len(scorecard_df)} total"
    )

    if not survivors.empty:
        print("\n== SURVIVED (passed train + valid + cost + significance + sample floor) ==")
        print(survivors.to_string(index=False))
    else:
        print("\nNo slices fully survived train/valid + cost + significance + sample-floor discipline.")

    if not provisional.empty:
        print(
            "\n== PROVISIONAL (correct sign + significant evidence, but below the "
            f"min_samples={min_samples} floor after the chronological split; "
            "needs more history before promotion, not rejected on the evidence) =="
        )
        print(provisional.to_string(index=False))

    return scorecard_df


def summarize_filter_window(
    window: pd.DataFrame,
    slice_filter: dict,
    target_col: str = "fwd_ret_5",
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price_col: str = "close_adj",
    min_samples: int = 15,
    side: str = "long",
    short_cost_bps: float = 0.0,
) -> dict:
    """Summarize one slice/filter inside a single chronological window."""
    if window.empty:
        return summarize_returns(pd.Series(dtype=float), min_samples=min_samples)

    filtered = apply_slice_filter(window, slice_filter) if slice_filter else window
    if filtered.empty or target_col not in filtered.columns:
        return summarize_returns(pd.Series(dtype=float), min_samples=min_samples)

    price = filtered[price_col] if price_col and price_col in filtered.columns else None
    returns = direction_adjusted_returns(
        filtered[target_col],
        side=side,
        cost_bps=cost_bps,
        cost_per_share=cost_per_share,
        price=price,
        short_cost_bps=short_cost_bps,
    )
    return summarize_returns(returns, min_samples=min_samples)


def best_parent_filter_window(
    window: pd.DataFrame,
    slice_filter: dict,
    target_col: str = "fwd_ret_5",
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price_col: str = "close_adj",
    min_samples: int = 15,
    side: str = "long",
    short_cost_bps: float = 0.0,
) -> dict:
    """Return the strongest simpler parent regime inside one window."""
    parent_summaries = []

    for parent_filter in iter_parent_slice_filters(slice_filter):
        summary = summarize_filter_window(
            window,
            parent_filter,
            target_col=target_col,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            price_col=price_col,
            min_samples=min_samples,
            side=side,
            short_cost_bps=short_cost_bps,
        )
        mean_return = summary.get("mean_return", float("nan"))
        if not pd.isna(mean_return):
            parent_summaries.append(
                {
                    "filter": format_slice_filter(parent_filter),
                    "sample_count": summary["sample_count"],
                    "mean_return": mean_return,
                    "p_value": summary["p_value"],
                }
            )

    if not parent_summaries:
        return {
            "filter": "",
            "sample_count": 0,
            "mean_return": float("nan"),
            "p_value": float("nan"),
        }

    return max(parent_summaries, key=lambda item: item["mean_return"])


def run_walk_forward_diagnostics(
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    cost_bps: float = 1.0,
    cost_per_share: float = 0.0,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    output_path: str = WALK_FORWARD_DIAGNOSTICS_PATH,
    diagnostic_scope: str = "current-leaders",
    top_n: int = 5,
    short_cost_bps: float = 0.0,
    bin_mode: str = "insample",
) -> pd.DataFrame:
    """Run anchored fold-by-fold diagnostics for the leading candidates.

    This answers: which chronological validation blocks work or fail, and does
    each block beat both the unconditional baseline and the strongest simpler
    parent regime?

    It is intentionally targeted at the current candidates recorded in
    HANDOVER.md rather than a broad discovery expansion.
    """
    targets = select_diagnostic_targets(
        scope=diagnostic_scope,
        top_n=top_n,
        slices_path=slices_path,
        n_folds=n_folds,
        min_samples=min_samples,
        p_threshold=p_threshold,
    )

    rows = []

    for symbol, timeframe, combo, side in targets:
        slice_filter = parse_slice_combination(combo)
        eligible_df = build_eligible_frame(
            symbol,
            timeframe,
            cross_symbols=cross_symbols_from_filter(slice_filter),
            bin_mode=bin_mode,
        )
        if eligible_df.empty:
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": combo,
                    "fold": -1,
                    "diagnostic_status": "missing_eligible_frame",
                }
            )
            continue

        slice_filter = parse_slice_combination(combo)
        sorted_df = eligible_df.sort_values("bar_ts_utc").reset_index(drop=True)

        n_blocks = n_folds + 1
        if len(sorted_df) < n_blocks:
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": combo,
                    "fold": -1,
                    "diagnostic_status": "not_enough_rows_for_folds",
                    "eligible_rows": len(sorted_df),
                }
            )
            continue

        edges = [round(i * len(sorted_df) / n_blocks) for i in range(n_blocks + 1)]
        blocks = [sorted_df.iloc[edges[i] : edges[i + 1]].reset_index(drop=True) for i in range(n_blocks)]

        for fold_idx in range(n_folds):
            train_df = pd.concat(blocks[: fold_idx + 1], ignore_index=True)
            valid_df = blocks[fold_idx + 1]

            train_summary = summarize_filter_window(
                train_df, slice_filter, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )
            valid_summary = summarize_filter_window(
                valid_df, slice_filter, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )
            valid_baseline = summarize_filter_window(
                valid_df, {}, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )
            valid_parent = best_parent_filter_window(
                valid_df, slice_filter, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )

            train_pass = survives(train_summary, min_samples=min_samples, p_threshold=p_threshold)
            valid_pass = survives(valid_summary, min_samples=min_samples, p_threshold=p_threshold)

            valid_mean = valid_summary["mean_return"]
            baseline_mean = valid_baseline["mean_return"]
            parent_mean = valid_parent["mean_return"]

            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": combo,
                    "fold": fold_idx,
                    "diagnostic_status": "ok",
                    "train_start_utc": train_df["bar_ts_utc"].min(),
                    "train_end_utc": train_df["bar_ts_utc"].max(),
                    "valid_start_utc": valid_df["bar_ts_utc"].min(),
                    "valid_end_utc": valid_df["bar_ts_utc"].max(),
                    "train_n": train_summary["sample_count"],
                    "train_mean_ret_costadj": train_summary["mean_return"],
                    "train_p_value_nw": train_summary["p_value"],
                    "train_pass": train_pass,
                    "valid_n": valid_summary["sample_count"],
                    "valid_mean_ret_costadj": valid_mean,
                    "valid_baseline_mean_ret_costadj": baseline_mean,
                    "valid_excess_vs_baseline": valid_mean - baseline_mean,
                    "valid_best_parent_filter": valid_parent["filter"],
                    "valid_best_parent_n": valid_parent["sample_count"],
                    "valid_best_parent_mean_ret_costadj": parent_mean,
                    "valid_excess_vs_best_parent": valid_mean - parent_mean,
                    "valid_p_value_nw": valid_summary["p_value"],
                    "valid_pass": valid_pass,
                }
            )

    diagnostics_df = pd.DataFrame(rows)
    diagnostics_df.to_csv(output_path, index=False)

    print(f"Saved walk-forward diagnostics to {output_path}")
    if diagnostics_df.empty:
        print("No diagnostics produced.")
    else:
        display_cols = [
            "symbol",
            "timeframe",
            "slice_combination",
            "fold",
            "valid_start_utc",
            "valid_end_utc",
            "valid_n",
            "valid_mean_ret_costadj",
            "valid_excess_vs_baseline",
            "valid_excess_vs_best_parent",
            "valid_p_value_nw",
            "valid_pass",
        ]
        available_cols = [col for col in display_cols if col in diagnostics_df.columns]
        print(diagnostics_df[available_cols].to_string(index=False))

    return diagnostics_df


DEFAULT_DIAGNOSTIC_TARGETS = [
    ("SPY", "1h", "state_session=afternoon + state_slope=downtrend", "long"),
    ("SPY", "1h", "state_session=lunch + state_slope=downtrend", "long"),
    ("QQQ", "1h", "state_session=lunch + state_slope=downtrend", "long"),
]


def select_diagnostic_targets(
    scope: str = "current-leaders",
    top_n: int = 5,
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
) -> list[tuple[str, str, str, str]]:
    """Select candidate targets for diagnostic commands.

    Returns 4-tuples: (symbol, timeframe, slice_combination, side).

    Scopes:
      - current-leaders: fixed legacy/current targets from HANDOVER.md
      - clean-survivors: leaderboard rows with triage_bucket starting with clean_survivor
      - late-emerging: leaderboard rows with triage_bucket=late_emerging_valid_supported
      - leaderboard-top: top-N leaderboard rows regardless of triage bucket
    """
    if scope == "current-leaders":
        return DEFAULT_DIAGNOSTIC_TARGETS

    if scope not in {"clean-survivors", "late-emerging", "leaderboard-top"}:
        raise ValueError(
            "diagnostic scope must be one of: current-leaders, clean-survivors, "
            "late-emerging, leaderboard-top"
        )

    with contextlib.redirect_stdout(io.StringIO()):
        leaderboard = run_candidate_leaderboard(
            slices_path=slices_path,
            n_folds=n_folds,
            min_samples=min_samples,
            p_threshold=p_threshold,
        )

    if leaderboard.empty:
        return []

    if scope == "clean-survivors":
        selected = leaderboard[
            leaderboard["triage_bucket"].astype(str).str.startswith("clean_survivor")
        ]
    elif scope == "late-emerging":
        selected = leaderboard[leaderboard["triage_bucket"] == "late_emerging_valid_supported"]
    else:
        selected = leaderboard

    selected = selected.head(top_n)
    # Carry the side through so diagnostics direction-adjust correctly. The
    # leaderboard row's `side` defaults to "long" when absent (older runs).
    out = []
    for _, r in selected.iterrows():
        s = str(r.get("side", "long") or "long").lower()
        if s not in ("long", "short"):
            s = "long"
        out.append((r["symbol"], r["timeframe"], r["slice_combination"], s))
    return out


def _filter_date_window(
    df: pd.DataFrame,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return rows in [start, end), preserving timezone-aware UTC handling."""
    if df.empty:
        return df.copy()

    out = df.copy()
    ts = pd.to_datetime(out["bar_ts_utc"])
    if getattr(ts.dtype, "tz", None) is None:
        ts = ts.dt.tz_localize("UTC")
    else:
        ts = ts.dt.tz_convert("UTC")

    mask = pd.Series(True, index=out.index)

    if start is not None:
        start_ts = pd.Timestamp(start)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize("UTC")
        else:
            start_ts = start_ts.tz_convert("UTC")
        mask &= ts >= start_ts

    if end is not None:
        end_ts = pd.Timestamp(end)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")
        mask &= ts < end_ts

    return out[mask].reset_index(drop=True)


def run_date_range_diagnostics(
    cost_bps: float = 1.0,
    cost_per_share: float = 0.0,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    output_path: str = DATE_RANGE_DIAGNOSTICS_PATH,
    diagnostic_scope: str = "current-leaders",
    top_n: int = 5,
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    short_cost_bps: float = 0.0,
    bin_mode: str = "insample",
) -> pd.DataFrame:
    """Run targeted date-range sensitivity diagnostics.

    This focuses on the current leading candidates from HANDOVER.md and asks
    whether their behavior is stable across calendar periods and recent-only
    windows. It is intentionally not a broad discovery expansion.
    """
    targets = select_diagnostic_targets(
        scope=diagnostic_scope,
        top_n=top_n,
        slices_path=slices_path,
        n_folds=n_folds,
        min_samples=min_samples,
        p_threshold=p_threshold,
    )

    rows = []

    for symbol, timeframe, combo, side in targets:
        slice_filter = parse_slice_combination(combo)
        eligible_df = build_eligible_frame(
            symbol,
            timeframe,
            cross_symbols=cross_symbols_from_filter(slice_filter),
            bin_mode=bin_mode,
        )
        if eligible_df.empty:
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": combo,
                    "window": "all",
                    "diagnostic_status": "missing_eligible_frame",
                }
            )
            continue

        eligible_df = eligible_df.sort_values("bar_ts_utc").reset_index(drop=True)
        max_ts = pd.to_datetime(eligible_df["bar_ts_utc"]).max()
        if max_ts.tzinfo is None:
            max_ts = max_ts.tz_localize("UTC")

        windows = [
            ("all", None, None),
            ("calendar_2024", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
            ("calendar_2025", pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")),
            ("calendar_2026_ytd", pd.Timestamp("2026-01-01", tz="UTC"), None),
            ("latest_12m", max_ts - pd.DateOffset(months=12), None),
            ("latest_6m", max_ts - pd.DateOffset(months=6), None),
        ]

        slice_filter = parse_slice_combination(combo)

        for window_name, start, end in windows:
            window_df = _filter_date_window(eligible_df, start=start, end=end)

            if window_df.empty:
                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "slice_combination": combo,
                        "window": window_name,
                        "diagnostic_status": "empty_window",
                    }
                )
                continue

            slice_summary = summarize_filter_window(
                window_df, slice_filter, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )
            baseline_summary = summarize_filter_window(
                window_df, {}, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )
            parent_summary = best_parent_filter_window(
                window_df, slice_filter, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )

            slice_mean = slice_summary["mean_return"]
            baseline_mean = baseline_summary["mean_return"]
            parent_mean = parent_summary["mean_return"]

            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": combo,
                    "window": window_name,
                    "diagnostic_status": "ok",
                    "window_start_utc": window_df["bar_ts_utc"].min(),
                    "window_end_utc": window_df["bar_ts_utc"].max(),
                    "window_rows": len(window_df),
                    "slice_n": slice_summary["sample_count"],
                    "slice_mean_ret_costadj": slice_mean,
                    "slice_win_rate": slice_summary["win_rate"],
                    "slice_t_stat_nw": slice_summary["t_stat"],
                    "slice_p_value_nw": slice_summary["p_value"],
                    "slice_pass": survives(slice_summary, min_samples=min_samples, p_threshold=p_threshold),
                    "baseline_n": baseline_summary["sample_count"],
                    "baseline_mean_ret_costadj": baseline_mean,
                    "excess_vs_baseline": slice_mean - baseline_mean,
                    "best_parent_filter": parent_summary["filter"],
                    "best_parent_n": parent_summary["sample_count"],
                    "best_parent_mean_ret_costadj": parent_mean,
                    "excess_vs_best_parent": slice_mean - parent_mean,
                    "best_parent_p_value_nw": parent_summary["p_value"],
                }
            )

    diagnostics_df = pd.DataFrame(rows)
    diagnostics_df.to_csv(output_path, index=False)

    print(f"Saved date-range diagnostics to {output_path}")
    if diagnostics_df.empty:
        print("No diagnostics produced.")
    else:
        display_cols = [
            "symbol",
            "timeframe",
            "slice_combination",
            "window",
            "window_start_utc",
            "window_end_utc",
            "slice_n",
            "slice_mean_ret_costadj",
            "excess_vs_baseline",
            "excess_vs_best_parent",
            "slice_p_value_nw",
            "slice_pass",
        ]
        available_cols = [col for col in display_cols if col in diagnostics_df.columns]
        print(diagnostics_df[available_cols].to_string(index=False))

    return diagnostics_df


def classify_candidate_triage(
    verdict: str,
    train_pass: bool,
    valid_pass: bool,
    valid_n: int,
    valid_excess_vs_baseline: float,
    valid_excess_vs_best_parent: float,
    valid_p_value_nw: float,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    walk_forward_pass_pattern: str = "",
) -> str:
    """Human-readable triage bucket for candidate leaderboard rows.

    This does not replace the strict validation verdict. It explains why a
    candidate is interesting or not, so research attention goes to the right
    next question.
    """
    positive_baseline = not pd.isna(valid_excess_vs_baseline) and valid_excess_vs_baseline > 0
    positive_parent = not pd.isna(valid_excess_vs_best_parent) and valid_excess_vs_best_parent > 0
    valid_significant = not pd.isna(valid_p_value_nw) and valid_p_value_nw < p_threshold
    sample_starved = valid_n < min_samples

    if verdict == "survived" and positive_parent:
        pattern = str(walk_forward_pass_pattern or "")
        pass_count = pattern.count("1")
        fold_count = len(pattern)

        if fold_count > 0:
            if pass_count == fold_count or pass_count >= max(3, int(0.75 * fold_count)):
                return "clean_survivor_wf_strong"
            if pass_count == 0:
                return "clean_survivor_wf_failed"
            return "clean_survivor_wf_mixed"

        return "clean_survivor"

    if verdict == "survived" and not positive_parent:
        return "over_specified_survivor"

    if verdict == "provisional" or sample_starved:
        if positive_baseline and positive_parent and valid_significant:
            return "provisional_sample_starved"
        return "sample_starved_unsupported"

    if (not train_pass) and valid_pass and positive_baseline and positive_parent:
        pattern = str(walk_forward_pass_pattern or "")
        pass_positions = [idx for idx, flag in enumerate(pattern) if flag == "1"]

        if pass_positions:
            latest_idx = len(pattern) - 1
            if pass_positions == [latest_idx]:
                return "late_emerging_recent_only"
            if latest_idx in pass_positions and any(idx < latest_idx for idx in pass_positions):
                return "late_emerging_regime_switching"

        return "late_emerging_valid_supported"

    if valid_pass and not positive_parent:
        return "parent_underperformed"

    if positive_baseline and positive_parent and valid_significant:
        return "interesting_but_failed_gate"

    return "rejected_unsupported"


def annotate_search_wide_significance(
    leaderboard: pd.DataFrame,
    p_threshold: float = 0.05,
    p_col: str = "valid_p_value_nw",
) -> pd.DataFrame:
    """Add search-wide multiple-testing columns to a candidate leaderboard.

    The correction family is every leaderboard row with a finite p-value.
    Adds Benjamini-Hochberg FDR (search_wide_bh_pass), Bonferroni
    (search_wide_bonferroni_pass), the ascending p rank (search_wide_rank),
    and the family size (search_wide_family_size). Read together with valid_n
    and triage_bucket: the family includes sample-starved slices whose tiny
    small-sample p-values inflate the family, so these columns are a guard
    against over-claiming, not a promotion gate.
    """
    lb = leaderboard.copy()
    p = pd.to_numeric(lb[p_col], errors="coerce")
    finite = p.notna()
    m = int(finite.sum())

    lb["search_wide_family_size"] = m
    lb["search_wide_rank"] = pd.NA
    lb["search_wide_bh_pass"] = False
    lb["search_wide_bonferroni_pass"] = False

    if m == 0:
        return lb

    order = p[finite].sort_values(kind="mergesort")
    ranks = {idx: i + 1 for i, idx in enumerate(order.index)}
    lb.loc[finite, "search_wide_rank"] = [ranks[i] for i in p[finite].index]

    bonf_thresh = p_threshold / m
    lb.loc[finite, "search_wide_bonferroni_pass"] = p[finite] <= bonf_thresh

    ranked_p = order.to_numpy()
    bh_crit = (np.arange(1, m + 1) / m) * p_threshold
    passing = np.nonzero(ranked_p <= bh_crit)[0]
    bh_cut_rank = int(passing.max() + 1) if passing.size else 0
    bh_idx = [i for i in order.index if ranks[i] <= bh_cut_rank]
    lb.loc[bh_idx, "search_wide_bh_pass"] = True

    return lb


def run_regime_stratified_diagnostics(
    cost_bps: float = 1.0,
    cost_per_share: float = 0.0,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    output_path: str = REGIME_STRATIFIED_DIAGNOSTICS_PATH,
    diagnostic_scope: str = "current-leaders",
    top_n: int = 5,
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    short_cost_bps: float = 0.0,
    bin_mode: str = "insample",
    regime_symbol: str = "",
) -> pd.DataFrame:
    """Run regime-stratified diagnostics: split each slice's bars by the
    macro regime of its (own or configured) regime symbol and report the edge
    in each regime bucket.

    This is the missing 'regime independence' test the HANDOVER's
    regime-confound finding identified as the path forward. Today's validation
    is time-stratified (train/valid/walk-forward) -- it tests TEMPORAL
    stability, not REGIME independence. A multi-year sector bull produces
    positive forward returns for dip-buying and Newey-West cannot see the
    confound. This diagnostic CAN: it splits the slice's bars into macro
    bull / bear / neutral buckets and reports the edge in each.

    How to read the output:
      - A STRUCTURAL edge is positive in bull AND bear (or at least does not
        collapse in bear).
      - A REGIME-CONDITIONAL edge is positive in bull but ~0 or negative in
        bear. This is not disqualifying (it is tradeable with the deployment
        gate) but it must be labeled honestly, not promoted as structural.
      - A slice with no bars in a regime bucket gets diagnostic_status=
        'empty_regime_window' (skip -- cannot measure, usually because that
        regime did not occur during the slice's history).

    regime_symbol: optional override for which symbol defines the macro
      regime (e.g. SPY for broad-market). When empty, the per-slice own
      symbol is used (or the slice's cross-asset conditioning symbol).

    This is a DIAGNOSTIC, not a filter: it adds information without changing
    the promotion gate. Mirrors run_walk_forward_diagnostics and
    run_date_range_diagnostics in shape.
    """
    targets = select_diagnostic_targets(
        scope=diagnostic_scope,
        top_n=top_n,
        slices_path=slices_path,
        n_folds=n_folds,
        min_samples=min_samples,
        p_threshold=p_threshold,
    )

    rows = []
    # Ordered regime buckets for display. 'regime_warmup'/'unavailable' are
    # diagnostic-only and excluded from the bull/bear/neutral reading.
    regime_order = ["all", "bull", "bear", "neutral", "regime_warmup", "regime_unavailable"]

    for symbol, timeframe, combo, side in targets:
        slice_filter = parse_slice_combination(combo)
        eligible_df = build_eligible_frame(
            symbol,
            timeframe,
            cross_symbols=cross_symbols_from_filter(slice_filter),
            bin_mode=bin_mode,
        )
        if eligible_df.empty:
            for r_label in regime_order:
                rows.append({
                    "symbol": symbol, "timeframe": timeframe,
                    "slice_combination": combo, "regime": r_label,
                    "diagnostic_status": "missing_eligible_frame",
                })
            continue

        eligible_df = eligible_df.sort_values("bar_ts_utc").reset_index(drop=True)

        # Resolve the regime symbol for this slice and attach per-bar labels.
        rsym = regime_symbol or resolve_regime_symbol(
            symbol, slice_filter, configured_regime_symbol=None
        )
        labelled = attach_regime_labels(eligible_df, rsym, timeframe=timeframe)
        if labelled is None or "regime" not in labelled.columns:
            labelled = eligible_df.copy()
            labelled["regime"] = "regime_unavailable"

        # An 'all' bucket = no regime split (the headline number).
        # Then one row per regime bucket present in the labelled frame.
        present_regimes = ["all"] + [r for r in regime_order[1:]
                                     if r in labelled["regime"].unique()]

        for r_label in present_regimes:
            if r_label == "all":
                window_df = labelled
            else:
                window_df = labelled[labelled["regime"] == r_label]

            if window_df.empty:
                rows.append({
                    "symbol": symbol, "timeframe": timeframe,
                    "slice_combination": combo, "regime": r_label,
                    "diagnostic_status": "empty_regime_window",
                    "regime_symbol": rsym,
                })
                continue

            slice_summary = summarize_filter_window(
                window_df, slice_filter, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )
            baseline_summary = summarize_filter_window(
                window_df, {}, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )
            parent_summary = best_parent_filter_window(
                window_df, slice_filter, cost_bps=cost_bps,
                cost_per_share=cost_per_share, min_samples=min_samples,
                side=side, short_cost_bps=short_cost_bps,
            )

            slice_mean = slice_summary["mean_return"]
            rows.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": combo,
                "regime": r_label,
                "regime_symbol": rsym,
                "diagnostic_status": "ok",
                "regime_window_rows": len(window_df),
                "slice_n": slice_summary["sample_count"],
                "slice_mean_ret_costadj": slice_mean,
                "slice_win_rate": slice_summary["win_rate"],
                "slice_t_stat_nw": slice_summary["t_stat"],
                "slice_p_value_nw": slice_summary["p_value"],
                "slice_pass": survives(slice_summary, min_samples=min_samples, p_threshold=p_threshold),
                "baseline_mean_ret_costadj": baseline_summary["mean_return"],
                "excess_vs_baseline": slice_mean - baseline_summary["mean_return"],
                "best_parent_filter": parent_summary["filter"],
                "best_parent_mean_ret_costadj": parent_summary["mean_return"],
                "excess_vs_best_parent": slice_mean - parent_summary["mean_return"],
            })

    diagnostics_df = pd.DataFrame(rows)
    diagnostics_df.to_csv(output_path, index=False)

    print(f"Saved regime-stratified diagnostics to {output_path}")
    if diagnostics_df.empty:
        print("No diagnostics produced.")
    else:
        display_cols = [
            "symbol", "timeframe", "slice_combination", "regime", "regime_symbol",
            "slice_n", "slice_mean_ret_costadj", "excess_vs_baseline",
            "excess_vs_best_parent", "slice_p_value_nw", "slice_pass",
        ]
        available_cols = [c for c in display_cols if c in diagnostics_df.columns]
        print(diagnostics_df[available_cols].to_string(index=False))
    return diagnostics_df


def run_candidate_leaderboard(
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    output_path: str = CANDIDATE_LEADERBOARD_PATH,
    bin_mode: str = "insample",
) -> pd.DataFrame:
    """Rank all discovered slices by validation quality and robustness.

    This is a triage tool, not a promotion engine. It compares every slice in
    the discovered-slices file across:
      - default validation verdict
      - excess vs unconditional baseline
      - excess vs best simpler parent regime
      - walk-forward survival
      - survival under common cost/split scenarios

    The goal is to choose the next candidates for deeper diagnostics instead
    of over-focusing on whichever slice was inspected first.
    """
    scenarios = [
        ("default", {}),
        ("cost2", {"cost_bps": 2.0}),
        ("cost5", {"cost_bps": 5.0}),
        ("split06", {"split": 0.6}),
        ("split08", {"split": 0.8}),
        # Short-borrow stress grid (per the direction-agnostic V5 work).
        # short_cost_bps adds drag to short-side slices only; longs are
        # unaffected, so this isolates short-edge robustness to borrow cost.
        ("short_borrow2", {"short_cost_bps": 2.0}),
        ("short_borrow5", {"short_cost_bps": 5.0}),
        ("short_borrow10", {"short_cost_bps": 10.0}),
    ]

    scenario_frames = {}

    for label, overrides in scenarios:
        params = {
            "slices_path": slices_path,
            "split": 0.7,
            "cost_bps": 1.0,
            "cost_per_share": 0.0,
            "n_folds": n_folds,
            "min_samples": min_samples,
            "p_threshold": p_threshold,
            "bin_mode": bin_mode,
        }
        params.update(overrides)

        with contextlib.redirect_stdout(io.StringIO()):
            scenario_frames[label] = run_validation(**params)

    default_df = scenario_frames["default"].copy()
    if default_df.empty:
        print("No default validation rows available for candidate leaderboard.")
        return default_df

    key_cols = ["symbol", "timeframe", "slice_combination"]

    rows = []
    for _, row in default_df.iterrows():
        key = tuple(row[col] for col in key_cols)

        scenario_verdicts = {}
        scenario_survived_count = 0
        scenario_positive_baseline_excess_count = 0
        scenario_positive_parent_excess_count = 0
        scenario_significant_count = 0

        for label, frame in scenario_frames.items():
            hit = frame[
                (frame["symbol"] == key[0])
                & (frame["timeframe"] == key[1])
                & (frame["slice_combination"] == key[2])
            ]

            if hit.empty:
                verdict = "missing"
                baseline_excess = float("nan")
                parent_excess = float("nan")
                p_value = float("nan")
            else:
                scenario_row = hit.iloc[0]
                verdict = scenario_row.get("verdict", "missing")
                baseline_excess = scenario_row.get("valid_excess_vs_baseline", float("nan"))
                parent_excess = scenario_row.get("valid_excess_vs_best_parent", float("nan"))
                p_value = scenario_row.get("valid_p_value_nw", float("nan"))

            scenario_verdicts[f"{label}_verdict"] = verdict

            if verdict == "survived":
                scenario_survived_count += 1
            if not pd.isna(baseline_excess) and baseline_excess > 0:
                scenario_positive_baseline_excess_count += 1
            if not pd.isna(parent_excess) and parent_excess > 0:
                scenario_positive_parent_excess_count += 1
            if not pd.isna(p_value) and p_value < p_threshold:
                scenario_significant_count += 1

        verdict = row["verdict"]
        train_pass = bool(row.get("train_pass", False))
        valid_pass = bool(row.get("valid_pass", False))
        valid_n = row["valid_n"]
        wf = row["walk_forward_survival_rate"]
        valid_excess_baseline = row.get("valid_excess_vs_baseline", float("nan"))
        valid_excess_parent = row.get("valid_excess_vs_best_parent", float("nan"))
        valid_p_value = row.get("valid_p_value_nw", float("nan"))
        wf_pass_count = row.get("walk_forward_pass_count", 0)
        wf_pass_pattern = str(row.get("walk_forward_pass_pattern", ""))

        default_survived = verdict == "survived"
        default_provisional = verdict == "provisional"
        positive_baseline = not pd.isna(valid_excess_baseline) and valid_excess_baseline > 0
        positive_parent = not pd.isna(valid_excess_parent) and valid_excess_parent > 0
        significant = not pd.isna(valid_p_value) and valid_p_value < p_threshold

        triage_bucket = classify_candidate_triage(
            verdict=verdict,
            train_pass=train_pass,
            valid_pass=valid_pass,
            valid_n=valid_n,
            valid_excess_vs_baseline=valid_excess_baseline,
            valid_excess_vs_best_parent=valid_excess_parent,
            valid_p_value_nw=valid_p_value,
            min_samples=min_samples,
            p_threshold=p_threshold,
            walk_forward_pass_pattern=wf_pass_pattern,
        )

        # Heuristic triage score. It is intentionally conservative about
        # parent-baseline excess and scenario survival, and it penalizes
        # sample-starved/provisional cases. Use for ordering, not promotion.
        robustness_score = 0.0
        robustness_score += 3.0 if default_survived else 0.0
        robustness_score += 1.0 if default_provisional else 0.0
        robustness_score += 1.0 if positive_baseline else 0.0
        robustness_score += 2.0 if positive_parent else 0.0
        robustness_score += 1.0 if significant else 0.0
        robustness_score += float(wf) * 2.0 if not pd.isna(wf) else 0.0
        robustness_score += scenario_survived_count * 1.0
        robustness_score += scenario_positive_parent_excess_count * 0.25
        robustness_score += scenario_significant_count * 0.25
        if valid_n < min_samples:
            robustness_score -= 2.0
        if not positive_parent:
            robustness_score -= 1.0

        rows.append(
            {
                "rank_symbol": row["symbol"],
                "rank_timeframe": row["timeframe"],
                "slice_combination": row["slice_combination"],
                "bin_mode": bin_mode,
                "side": row.get("side", "long"),
                "verdict": verdict,
                "triage_bucket": triage_bucket,
                "train_n": row["train_n"],
                "train_pass": train_pass,
                "valid_n": valid_n,
                "valid_pass": valid_pass,
                "valid_mean_ret_costadj": row["valid_mean_ret_costadj"],
                "valid_excess_vs_baseline": valid_excess_baseline,
                "valid_best_parent_filter": row.get("valid_best_parent_filter", ""),
                "valid_excess_vs_best_parent": valid_excess_parent,
                "valid_p_value_nw": valid_p_value,
                "walk_forward_pass_count": wf_pass_count,
                "walk_forward_pass_pattern": wf_pass_pattern,
                "walk_forward_survival_rate": wf,
                "scenario_survived_count": scenario_survived_count,
                "scenario_positive_baseline_excess_count": scenario_positive_baseline_excess_count,
                "scenario_positive_parent_excess_count": scenario_positive_parent_excess_count,
                "scenario_significant_count": scenario_significant_count,
                "robustness_score": robustness_score,
                **scenario_verdicts,
            }
        )

    leaderboard = pd.DataFrame(rows)
    leaderboard = leaderboard.sort_values(
        [
            "robustness_score",
            "scenario_survived_count",
            "walk_forward_survival_rate",
            "valid_excess_vs_best_parent",
            "valid_excess_vs_baseline",
            "valid_mean_ret_costadj",
        ],
        ascending=[False, False, False, False, False, False],
    ).reset_index(drop=True)

    leaderboard.insert(0, "rank", range(1, len(leaderboard) + 1))
    leaderboard = leaderboard.rename(columns={"rank_symbol": "symbol", "rank_timeframe": "timeframe"})

    leaderboard = annotate_search_wide_significance(
        leaderboard, p_threshold=p_threshold
    )
    leaderboard.to_csv(output_path, index=False)

    # Restore the default validation CSV after scenario diagnostics.
    with contextlib.redirect_stdout(io.StringIO()):
        run_validation(
            slices_path=slices_path,
            split=0.7,
            cost_bps=1.0,
            cost_per_share=0.0,
            n_folds=n_folds,
            min_samples=min_samples,
            p_threshold=p_threshold,
        )

    print(f"Saved candidate leaderboard to {output_path}")
    display_cols = [
        "rank",
        "symbol",
        "timeframe",
        "slice_combination",
        "verdict",
        "side",
        "triage_bucket",
        "train_pass",
        "valid_pass",
        "valid_n",
        "valid_mean_ret_costadj",
        "valid_excess_vs_baseline",
        "valid_excess_vs_best_parent",
        "valid_p_value_nw",
        "walk_forward_pass_pattern",
        "walk_forward_survival_rate",
        "scenario_survived_count",
        "robustness_score",
        "search_wide_rank",
        "search_wide_bh_pass",
        "search_wide_bonferroni_pass",
    ]
    print(leaderboard[display_cols].head(25).to_string(index=False))
    return leaderboard


def run_scenario_grid(
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    output_path: str = SCENARIO_GRID_PATH,
    bin_mode: str = "insample",
) -> pd.DataFrame:
    """Run a compact robustness grid for the current leading candidates.

    This is intentionally targeted, not a broad new research pass. It checks
    whether the current 2D intraday candidates survive common stress settings:
    default, moderate cost, high cost, and split sensitivity.

    The function suppresses the full validation scorecard for each scenario,
    writes a compact table to `output_path`, and restores the default
    validation output at the end so localdata/validated_slices.csv is not left
    on a non-default scenario.
    """
    targets = [
        ("SPY", "1h", "state_session=afternoon + state_slope=downtrend"),
        ("SPY", "1h", "state_session=lunch + state_slope=downtrend"),
        ("QQQ", "1h", "state_session=lunch + state_slope=downtrend"),
    ]

    scenarios = [
        ("default", {}),
        ("cost2", {"cost_bps": 2.0}),
        ("cost5", {"cost_bps": 5.0}),
        ("split06", {"split": 0.6}),
        ("split08", {"split": 0.8}),
    ]

    cols = [
        "symbol",
        "timeframe",
        "slice_combination",
        "train_n",
        "valid_n",
        "valid_mean_ret_costadj",
        "valid_baseline_mean_ret_costadj",
        "valid_excess_vs_baseline",
        "valid_best_parent_filter",
        "valid_best_parent_mean_ret_costadj",
        "valid_excess_vs_best_parent",
        "valid_p_value_nw",
        "walk_forward_survival_rate",
        "verdict",
    ]

    rows = []

    for label, overrides in scenarios:
        params = {
            "slices_path": slices_path,
            "split": 0.7,
            "cost_bps": 1.0,
            "cost_per_share": 0.0,
            "n_folds": n_folds,
            "min_samples": min_samples,
            "p_threshold": p_threshold,
            "bin_mode": bin_mode,
        }
        params.update(overrides)

        with contextlib.redirect_stdout(io.StringIO()):
            scorecard = run_validation(**params)

        for symbol, timeframe, combo in targets:
            hit = scorecard[
                (scorecard["symbol"] == symbol)
                & (scorecard["timeframe"] == timeframe)
                & (scorecard["slice_combination"] == combo)
            ]

            if hit.empty:
                row = {
                    "scenario": label,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": combo,
                    "verdict": "missing",
                }
            else:
                row = hit.iloc[0][cols].to_dict()
                row = {"scenario": label, **row}

            rows.append(row)

    scenario_df = pd.DataFrame(rows)
    scenario_df.to_csv(output_path, index=False)

    # Restore the default validation CSV after scenario diagnostics.
    with contextlib.redirect_stdout(io.StringIO()):
        run_validation(
            slices_path=slices_path,
            split=0.7,
            cost_bps=1.0,
            cost_per_share=0.0,
            n_folds=n_folds,
            min_samples=min_samples,
            p_threshold=p_threshold,
        )

    print(f"Saved scenario-grid diagnostics to {output_path}")
    print(scenario_df.to_string(index=False))
    return scenario_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate discovered market-state slices (Phase V4).")
    parser.add_argument("--slices-path", default=DISCOVERED_SLICES_PATH, help="Path to discovered_slices.csv")
    parser.add_argument("--split", type=float, default=0.7, help="Chronological train fraction (0-1)")
    parser.add_argument("--cost-bps", type=float, default=1.0, help="Round-trip cost in basis points per leg")
    parser.add_argument("--cost-per-share", type=float, default=0.0, help="Flat $/share cost per leg")
    parser.add_argument("--n-folds", type=int, default=4, help="Number of walk-forward folds")
    parser.add_argument("--min-samples", type=int, default=15, help="Minimum sample floor per fold/window")
    parser.add_argument("--p-threshold", type=float, default=0.05, help="Newey-West p-value survival threshold")
    parser.add_argument("--short-cost-bps", type=float, default=0.0, help="Extra per-leg drag (bps) for SHORT slices only (borrow + dividend).")
    parser.add_argument(
        "--bin-mode",
        default="insample",
        choices=["insample", "rolling"],
        help="How to bin quantile state fields (state_slope/state_vol/state_ret_*/etc). "
        "'insample' (default) = full-history quantiles (original behaviour, look-ahead-prone). "
        "'rolling' = look-ahead-free expanding-window quantiles (bar T's boundary uses only "
        "bars before T). Use 'rolling' end-to-end (discovery + validation + ML) for the "
        "overfit-kill. Output files are tagged with the mode to avoid cross-mode confusion.",
    )
    parser.add_argument(
        "--scenario-grid",
        action="store_true",
        help="Run targeted robustness scenarios for the current leading candidates",
    )
    parser.add_argument(
        "--scenario-grid-output",
        default=SCENARIO_GRID_PATH,
        help="Path for --scenario-grid compact CSV output",
    )
    parser.add_argument(
        "--walk-forward-diagnostics",
        action="store_true",
        help="Run anchored fold-by-fold diagnostics for the current leading candidates",
    )
    parser.add_argument(
        "--walk-forward-diagnostics-output",
        default=WALK_FORWARD_DIAGNOSTICS_PATH,
        help="Path for --walk-forward-diagnostics compact CSV output",
    )
    parser.add_argument(
        "--date-range-diagnostics",
        action="store_true",
        help="Run targeted date-range sensitivity diagnostics for the current leading candidates",
    )
    parser.add_argument(
        "--date-range-diagnostics-output",
        default=DATE_RANGE_DIAGNOSTICS_PATH,
        help="Path for --date-range-diagnostics compact CSV output",
    )
    parser.add_argument(
        "--regime-stratified-diagnostics",
        action="store_true",
        help="Run regime-stratified diagnostics: split each slice's bars by macro "
        "regime (bull/bear/neutral) and report the edge in each. This is the "
        "regime-independence test that distinguishes a structural edge (positive "
        "across regimes) from a regime-conditional one (positive only in bull).",
    )
    parser.add_argument(
        "--regime-stratified-output",
        default=REGIME_STRATIFIED_DIAGNOSTICS_PATH,
        help="Path for --regime-stratified-diagnostics CSV output",
    )
    parser.add_argument(
        "--regime-symbol",
        default="",
        help="Override the macro-regime symbol for --regime-stratified-diagnostics "
        "(e.g. SPY for broad-market regime). Empty = use each slice's own symbol "
        "(or its cross-asset conditioning symbol).",
    )
    parser.add_argument(
        "--diagnostic-scope",
        default="current-leaders",
        choices=["current-leaders", "clean-survivors", "late-emerging", "leaderboard-top"],
        help="Candidate scope for targeted diagnostics",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Maximum number of leaderboard-selected candidates for diagnostic scopes",
    )
    parser.add_argument(
        "--candidate-leaderboard",
        action="store_true",
        help="Rank all discovered slices by validation, parent-baseline, and scenario robustness",
    )
    parser.add_argument(
        "--candidate-leaderboard-output",
        default=CANDIDATE_LEADERBOARD_PATH,
        help="Path for --candidate-leaderboard CSV output",
    )
    args = parser.parse_args()

    if args.scenario_grid:
        run_scenario_grid(
            slices_path=args.slices_path,
            n_folds=args.n_folds,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            output_path=args.scenario_grid_output,
            bin_mode=args.bin_mode,
        )
    elif args.walk_forward_diagnostics:
        run_walk_forward_diagnostics(
            slices_path=args.slices_path,
            n_folds=args.n_folds,
            cost_bps=args.cost_bps,
            cost_per_share=args.cost_per_share,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            output_path=args.walk_forward_diagnostics_output,
            diagnostic_scope=args.diagnostic_scope,
            top_n=args.top_n,
            short_cost_bps=args.short_cost_bps,
            bin_mode=args.bin_mode,
        )
    elif args.date_range_diagnostics:
        run_date_range_diagnostics(
            cost_bps=args.cost_bps,
            cost_per_share=args.cost_per_share,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            output_path=args.date_range_diagnostics_output,
            diagnostic_scope=args.diagnostic_scope,
            top_n=args.top_n,
            slices_path=args.slices_path,
            n_folds=args.n_folds,
            short_cost_bps=args.short_cost_bps,
            bin_mode=args.bin_mode,
        )
    elif args.regime_stratified_diagnostics:
        run_regime_stratified_diagnostics(
            cost_bps=args.cost_bps,
            cost_per_share=args.cost_per_share,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            output_path=args.regime_stratified_output,
            diagnostic_scope=args.diagnostic_scope,
            top_n=args.top_n,
            slices_path=args.slices_path,
            n_folds=args.n_folds,
            short_cost_bps=args.short_cost_bps,
            bin_mode=args.bin_mode,
            regime_symbol=args.regime_symbol,
        )
    elif args.candidate_leaderboard:
        run_candidate_leaderboard(
            slices_path=args.slices_path,
            n_folds=args.n_folds,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            output_path=args.candidate_leaderboard_output,
            bin_mode=args.bin_mode,
        )
    else:
        run_validation(
            slices_path=args.slices_path,
            split=args.split,
            cost_bps=args.cost_bps,
            cost_per_share=args.cost_per_share,
            n_folds=args.n_folds,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            short_cost_bps=args.short_cost_bps,
        )
