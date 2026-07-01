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
DATE_RANGE_DIAGNOSTICS_PATH = "localdata/date_range_diagnostics.csv"
CANDIDATE_LEADERBOARD_PATH = "localdata/candidate_leaderboard.csv"


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
    diagnostic_scope: str = "current-leaders",
    top_n: int = 5,
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


DEFAULT_DIAGNOSTIC_TARGETS = [
    ("SPY", "1h", "state_session=afternoon + state_slope=downtrend"),
    ("SPY", "1h", "state_session=lunch + state_slope=downtrend"),
    ("QQQ", "1h", "state_session=lunch + state_slope=downtrend"),
]


def select_diagnostic_targets(
    scope: str = "current-leaders",
    top_n: int = 5,
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
) -> list[tuple[str, str, str]]:
    """Select candidate targets for diagnostic commands.

    Scopes:
      - current-leaders: fixed legacy/current targets from HANDOVER.md
      - clean-survivors: leaderboard rows with triage_bucket=clean_survivor
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
        selected = leaderboard[leaderboard["triage_bucket"] == "clean_survivor"]
    elif scope == "late-emerging":
        selected = leaderboard[leaderboard["triage_bucket"] == "late_emerging_valid_supported"]
    else:
        selected = leaderboard

    selected = selected.head(top_n)
    return list(zip(selected["symbol"], selected["timeframe"], selected["slice_combination"]))


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

    for symbol, timeframe, combo in targets:
        eligible_df = build_eligible_frame(symbol, timeframe)
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
                window_df,
                slice_filter,
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
            )
            baseline_summary = summarize_filter_window(
                window_df,
                {},
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
            )
            parent_summary = best_parent_filter_window(
                window_df,
                slice_filter,
                cost_bps=cost_bps,
                cost_per_share=cost_per_share,
                min_samples=min_samples,
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


def run_candidate_leaderboard(
    slices_path: str = DISCOVERED_SLICES_PATH,
    n_folds: int = 4,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    output_path: str = CANDIDATE_LEADERBOARD_PATH,
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
    ]
    print(leaderboard[display_cols].head(25).to_string(index=False))
    return leaderboard


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
        )
    elif args.candidate_leaderboard:
        run_candidate_leaderboard(
            slices_path=args.slices_path,
            n_folds=args.n_folds,
            min_samples=args.min_samples,
            p_threshold=args.p_threshold,
            output_path=args.candidate_leaderboard_output,
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
