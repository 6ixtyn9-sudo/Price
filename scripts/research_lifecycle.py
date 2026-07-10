"""Deterministic candidate lifecycle controller.

This module turns research leaderboard rows and live forward evidence into an
isolated registry. It does not alter monitored_slices.csv by default. Automatic
promotion is an explicit activation mode for a future production deployment;
the default is proposal-only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from price.research_leverage import evaluate_candidate_leverage


DEFAULT_REGISTRY = Path("localdata/research/candidate_registry.csv")
MONITORED_PATH = Path("localdata/monitored_slices.csv")
LIVE_FORWARD_PATH = Path("localdata/live_forward_returns.csv")


def _num(value, default=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if value == value else default


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _clean(value, default="") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return default if text.lower() in {"", "nan", "none"} else text


def _strict_candidate(row: pd.Series) -> bool:
    """Conservative automatic eligibility gate."""
    return (
        _clean(row.get("triage_bucket")).startswith("clean_survivor")
        and _num(row.get("valid_n"), 0) >= 15
        and _num(row.get("walk_forward_pass_count"), 0) >= 3
        and _num(row.get("scenario_survived_count"), 0) >= 4
        and _num(row.get("valid_excess_vs_baseline"), -1) > 0
        and _num(row.get("valid_excess_vs_best_parent"), -1) > 0
        and _truthy(row.get("search_wide_bh_pass", False))
    )


def _live_decay_keys(path: Path = LIVE_FORWARD_PATH, min_completed: int = 5) -> set[str]:
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return set()
    if df.empty or "fwd_ret_5b" not in df.columns:
        return set()
    df["fwd_ret_5b"] = pd.to_numeric(df["fwd_ret_5b"], errors="coerce")
    if "bin_mode" not in df.columns:
        df["bin_mode"] = "insample"
    df = df.dropna(subset=["fwd_ret_5b"])
    if df.empty:
        return set()
    grouped = df.groupby(["symbol", "timeframe", "slice_combination", "bin_mode"])
    return {
        "|".join(map(str, key))
        for key, group in grouped
        if len(group) >= min_completed and group["fwd_ret_5b"].mean() <= 0
    }


def build_registry(
    leaderboard_path: Path,
    output_path: Path = DEFAULT_REGISTRY,
    monitored_path: Path = MONITORED_PATH,
    live_forward_path: Path = LIVE_FORWARD_PATH,
    enable_auto_promotion: bool = False,
) -> pd.DataFrame:
    if not leaderboard_path.exists():
        return pd.DataFrame()
    try:
        leaderboard = pd.read_csv(leaderboard_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()
    if leaderboard.empty:
        return pd.DataFrame()

    decay = _live_decay_keys(live_forward_path)
    rows = []
    for _, row in leaderboard.iterrows():
        symbol = _clean(row.get("symbol"))
        timeframe = _clean(row.get("timeframe"))
        combo = _clean(row.get("slice_combination"))
        side = _clean(row.get("side"), "long")
        bin_mode = _clean(row.get("bin_mode"), "insample")
        key = "|".join([symbol, timeframe, combo, bin_mode])
        eligible = _strict_candidate(row)
        leverage = evaluate_candidate_leverage(row)
        leverage_gate = bool(leverage["leverage_auto_promotion_gate"])
        is_decaying = key in decay
        if is_decaying:
            status = "decaying_suspended"
        elif eligible and enable_auto_promotion and leverage_gate:
            status = "auto_approved"
        elif eligible:
            status = "paper_proposal"
        else:
            status = "research_only"
        rows.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_combination": combo,
            "side": side,
            "bin_mode": bin_mode,
            "candidate_key": key,
            "status": status,
            "strict_gate_pass": eligible,
            "live_decay_flag": is_decaying,
            "valid_n": row.get("valid_n"),
            "walk_forward_pass_count": row.get("walk_forward_pass_count"),
            "scenario_survived_count": row.get("scenario_survived_count"),
            "search_wide_bh_pass": row.get("search_wide_bh_pass"),
            "valid_excess_vs_baseline": row.get("valid_excess_vs_baseline"),
            "valid_excess_vs_best_parent": row.get("valid_excess_vs_best_parent"),
            "leverage_gate_pass": leverage_gate,
            "leverage_gate_reason": (
                "risk data unavailable or overnight scenario not proven"
                if not leverage_gate else "1x/2x risk scenarios pass"
            ),
            **leverage,
        })
    result = pd.DataFrame(rows).sort_values(["status", "symbol", "timeframe"]).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    return result


def apply_registry_to_monitored(
    registry: pd.DataFrame,
    monitored_path: Path = MONITORED_PATH,
) -> pd.DataFrame:
    """Apply automatic promotions/demotions to the monitored set.

    This function is intentionally not called by the research workflow yet.
    Production activation must be an explicit operator decision. Existing
    monitored rows with no current research row are preserved; rows explicitly
    classified decaying_suspended are removed; auto_approved rows are added.
    """
    existing = pd.read_csv(monitored_path) if monitored_path.exists() else pd.DataFrame()
    rows = []
    suspended = set()
    approved = []
    if registry is not None and not registry.empty:
        suspended = set(registry.loc[registry["status"] == "decaying_suspended", "candidate_key"].astype(str))
        approved = registry[registry["status"] == "auto_approved"].to_dict("records")

    if not existing.empty:
        for _, row in existing.iterrows():
            symbol = _clean(row.get("symbol"))
            timeframe = _clean(row.get("timeframe"))
            combo = _clean(row.get("slice_combination"))
            bin_mode = _clean(row.get("bin_mode"), "insample")
            key = "|".join([symbol, timeframe, combo, bin_mode])
            if key not in suspended:
                rows.append(row.to_dict())

    existing_keys = {
        "|".join([_clean(row.get("symbol")), _clean(row.get("timeframe")),
                   _clean(row.get("slice_combination")), _clean(row.get("bin_mode"), "insample")])
        for row in rows
    }
    for row in approved:
        key = str(row["candidate_key"])
        if key in existing_keys:
            continue
        rows.append({
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "slice_combination": row["slice_combination"],
            "side": row.get("side", "long"),
            "bin_mode": row.get("bin_mode", "insample"),
        })
        existing_keys.add(key)

    result = pd.DataFrame(rows)
    monitored_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(monitored_path, index=False)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build isolated automatic candidate lifecycle registry.")
    parser.add_argument("--leaderboard", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--live-forward", type=Path, default=LIVE_FORWARD_PATH)
    parser.add_argument("--monitored", type=Path, default=MONITORED_PATH)
    parser.add_argument(
        "--enable-auto-promotion",
        action="store_true",
        help="Mark strict candidates auto_approved.",
    )
    parser.add_argument(
        "--apply-monitored-slices",
        action="store_true",
        help="Apply auto_approved promotions and decaying_suspended demotions. Requires --enable-auto-promotion.",
    )
    args = parser.parse_args()
    result = build_registry(
        args.leaderboard,
        args.output,
        live_forward_path=args.live_forward,
        enable_auto_promotion=args.enable_auto_promotion,
    )
    if args.apply_monitored_slices:
        if not args.enable_auto_promotion:
            raise SystemExit("--apply-monitored-slices requires --enable-auto-promotion")
        applied = apply_registry_to_monitored(result, args.monitored)
        print(f"Applied automatic lifecycle decisions to {args.monitored}: {len(applied)} monitored rows")
    print(f"Saved {len(result)} candidate lifecycle rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
