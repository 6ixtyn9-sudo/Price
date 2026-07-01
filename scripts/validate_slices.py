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

import argparse

import pandas as pd

from price.discovery import bin_features
from price.features import compute_price_features
from price.validation import (
    evaluate_slice_train_valid,
    parse_slice_combination,
    walk_forward_validate_slice,
)
from price.warehouse import load_from_warehouse

DISCOVERED_SLICES_PATH = "localdata/discovered_slices.csv"
VALIDATED_SLICES_PATH = "localdata/validated_slices.csv"


def build_eligible_frame(symbol: str, timeframe: str) -> pd.DataFrame:
    """Rebuild the timestamped, binned, forward-eligible feature frame for a
    symbol/timeframe pair, straight from the local warehouse. No network
    calls, no re-ingestion -- only reads what has already been captured."""
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        return pd.DataFrame()

    df_feat = compute_price_features(df_raw)
    df_binned = bin_features(df_feat)
    return df_binned[df_binned["label_eligible"]].reset_index(drop=True)


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


def run_validation(
    slices_path: str = DISCOVERED_SLICES_PATH,
    split: float = 0.7,
    cost_bps: float = 1.0,
    cost_per_share: float = 0.0,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
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

        cache_key = (symbol, timeframe)
        if cache_key not in frame_cache:
            frame_cache[cache_key] = build_eligible_frame(symbol, timeframe)
        eligible_df = frame_cache[cache_key]

        if eligible_df.empty:
            print(f"  -> No warehouse data for {symbol} ({timeframe}); skipping '{slice_combination}'.")
            continue

        try:
            slice_filter = parse_slice_combination(slice_combination)
        except ValueError as exc:
            print(f"  -> Could not parse slice '{slice_combination}': {exc}")
            continue

        tv = evaluate_slice_train_valid(
            eligible_df,
            slice_filter,
            split=split,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            min_samples=min_samples,
        )

        try:
            wf_folds = walk_forward_validate_slice(
                eligible_df,
                slice_filter,
                n_folds=n_folds,
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
            )
        except ValueError:
            wf_folds = []

        wf_valid_pass = [
            survives(fold["valid"], min_samples=min_samples, p_threshold=p_threshold)
            for fold in wf_folds
        ]
        wf_survival_rate = (sum(wf_valid_pass) / len(wf_valid_pass)) if wf_valid_pass else float("nan")

        train_pass = survives(tv["train"], min_samples=min_samples, p_threshold=p_threshold)
        valid_pass = survives(tv["valid"], min_samples=min_samples, p_threshold=p_threshold)

        scorecard.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": slice_combination,
                "train_n": tv["train"]["sample_count"],
                "train_mean_ret_costadj": tv["train"]["mean_return"],
                "train_t_stat_nw": tv["train"]["t_stat"],
                "train_pass": train_pass,
                "valid_n": tv["valid"]["sample_count"],
                "valid_mean_ret_costadj": tv["valid"]["mean_return"],
                "valid_t_stat_nw": tv["valid"]["t_stat"],
                "valid_p_value_nw": tv["valid"]["p_value"],
                "valid_pass": valid_pass,
                "walk_forward_folds": len(wf_folds),
                "walk_forward_survival_rate": wf_survival_rate,
                "survived": bool(train_pass and valid_pass),
            }
        )

    scorecard_df = pd.DataFrame(scorecard)
    if scorecard_df.empty:
        print("No slices could be validated (missing warehouse data for all rows).")
        return scorecard_df

    scorecard_df = scorecard_df.sort_values(
        ["survived", "valid_mean_ret_costadj"], ascending=[False, False]
    ).reset_index(drop=True)

    scorecard_df.to_csv(VALIDATED_SLICES_PATH, index=False)
    print(f"\nSaved validation scorecard to {VALIDATED_SLICES_PATH}")

    survivors = scorecard_df[scorecard_df["survived"]]
    print(f"\nSurviving slices: {len(survivors)} / {len(scorecard_df)}")
    if not survivors.empty:
        print(survivors.to_string(index=False))
    else:
        print("No slices passed train/valid + cost + significance discipline.")

    return scorecard_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate discovered market-state slices (Phase V4).")
    parser.add_argument("--slices-path", default=DISCOVERED_SLICES_PATH, help="Path to discovered_slices.csv")
    parser.add_argument("--split", type=float, default=0.7, help="Chronological train fraction (0-1)")
    parser.add_argument("--cost-bps", type=float, default=1.0, help="Round-trip cost in basis points per leg")
    parser.add_argument("--cost-per-share", type=float, default=0.0, help="Flat $/share cost per leg")
    parser.add_argument("--n-folds", type=int, default=4, help="Number of walk-forward folds")
    parser.add_argument("--min-samples", type=int, default=15, help="Minimum sample floor per fold/window")
    parser.add_argument("--p-threshold", type=float, default=0.05, help="Newey-West p-value survival threshold")
    args = parser.parse_args()

    run_validation(
        slices_path=args.slices_path,
        split=args.split,
        cost_bps=args.cost_bps,
        cost_per_share=args.cost_per_share,
        n_folds=args.n_folds,
        min_samples=args.min_samples,
        p_threshold=args.p_threshold,
    )
