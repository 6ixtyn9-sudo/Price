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
import contextlib
import io
from itertools import combinations

import pandas as pd

from price.discovery import bin_features
from price.features import compute_price_features
from price.validation import (
    apply_slice_filter,
    apply_transaction_cost,
    chronological_train_valid_split,
    evaluate_slice_train_valid,
    parse_slice_combination,
    summarize_returns,
    walk_forward_validate_slice,
)
from price.warehouse import load_from_warehouse

DISCOVERED_SLICES_PATH = "localdata/discovered_slices.csv"
VALIDATED_SLICES_PATH = "localdata/validated_slices.csv"
SCENARIO_GRID_PATH = "localdata/validation_scenario_grid.csv"
WALK_FORWARD_DIAGNOSTICS_PATH = "localdata/walk_forward_diagnostics.csv"


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


def summarize_baseline_train_valid(
    df: pd.DataFrame,
    split: float = 0.7,
    target_col: str = "fwd_ret_5",
    cost_bps: float = 0.0,
    cost_per_share: float = 0.0,
    price_col: str = "close_adj",
    min_samples: int = 15,
) -> dict:
    """Summarize the unconditional symbol/timeframe baseline over the same
    chronological train/valid windows used for slice validation.

    This gives each slice a fair local benchmark: did the slice beat the
    whole eligible population for that same symbol/timeframe and period, or
    is it only positive because the underlying drifted up?
    """
    train_df, valid_df = chronological_train_valid_split(df, split=split)

    def summarize_window(window: pd.DataFrame) -> dict:
        if window.empty or target_col not in window.columns:
            return summarize_returns(pd.Series(dtype=float), min_samples=min_samples)

        price = window[price_col] if price_col and price_col in window.columns else None
        returns = apply_transaction_cost(
            window[target_col],
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            price=price,
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
) -> dict:
    """Find the strongest parent-regime baseline in train and validation.

    The selected parent for each window is the parent filter with the highest
    cost-adjusted mean return in that same chronological window. This is a
    deliberately conservative diagnostic: if the child slice cannot beat the
    strongest simpler parent in validation, the discovered 2D/3D combination
    may not add much beyond a simpler regime.
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

        baseline = summarize_baseline_train_valid(
            eligible_df,
            split=split,
            cost_bps=cost_bps,
            cost_per_share=cost_per_share,
            min_samples=min_samples,
        )

        parent_baseline = summarize_parent_baselines_train_valid(
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
        verdict = classify_verdict(train_pass, valid_pass, tv["train"], tv["valid"], p_threshold)

        scorecard.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": slice_combination,
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
) -> dict:
    """Summarize one slice/filter inside a single chronological window."""
    if window.empty:
        return summarize_returns(pd.Series(dtype=float), min_samples=min_samples)

    filtered = apply_slice_filter(window, slice_filter) if slice_filter else window
    if filtered.empty or target_col not in filtered.columns:
        return summarize_returns(pd.Series(dtype=float), min_samples=min_samples)

    price = filtered[price_col] if price_col and price_col in filtered.columns else None
    returns = apply_transaction_cost(
        filtered[target_col],
        cost_bps=cost_bps,
        cost_per_share=cost_per_share,
        price=price,
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
) -> pd.DataFrame:
    """Run anchored fold-by-fold diagnostics for the leading candidates.

    This answers: which chronological validation blocks work or fail, and does
    each block beat both the unconditional baseline and the strongest simpler
    parent regime?

    It is intentionally targeted at the current candidates recorded in
    HANDOVER.md rather than a broad discovery expansion.
    """
    targets = [
        ("SPY", "1h", "state_session=afternoon + state_slope=downtrend"),
        ("SPY", "1h", "state_session=lunch + state_slope=downtrend"),
        ("QQQ", "1h", "state_session=lunch + state_slope=downtrend"),
    ]

    rows = []

    for symbol, timeframe, combo in targets:
        eligible_df = build_eligible_frame(symbol, timeframe)
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
                train_df,
                slice_filter,
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
            )
            valid_summary = summarize_filter_window(
                valid_df,
                slice_filter,
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
            )
            valid_baseline = summarize_filter_window(
                valid_df,
                {},
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
            )
            valid_parent = best_parent_filter_window(
                valid_df,
                slice_filter,
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
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


def run_scenario_grid(
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    output_path: str = SCENARIO_GRID_PATH,
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
    args = parser.parse_args()

    if args.scenario_grid:
        run_scenario_grid(
            slices_path=args.slices_path,
            n_folds=args.n_folds,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            output_path=args.scenario_grid_output,
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
        )
