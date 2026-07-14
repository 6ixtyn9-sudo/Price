"""Deterministic candidate lifecycle controller.

This module turns research leaderboard rows and live forward evidence into an
isolated registry. It does not alter monitored_slices.csv by default. Workflow
callers may explicitly apply paper promotions and demotions, while production
activation remains a separate decision.
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


def normalize_walk_forward_patterns(frame: pd.DataFrame) -> pd.DataFrame:
    """Restore leading zeroes lost when binary fold patterns pass through CSV.

    A four-fold pattern such as ``0010`` can be read by pandas as integer 10
    and later serialized as ``10``. Fold-pattern triage must retain the exact
    width, otherwise recent-only/regime-switching classifications become
    ambiguous. The fold-count column is the width authority.
    """
    if frame is None or frame.empty or "walk_forward_pass_pattern" not in frame.columns:
        return frame
    out = frame.copy()
    fold_col = "walk_forward_folds" if "walk_forward_folds" in out.columns else "validation_n_folds"

    def _normalize(row):
        value = row.get("walk_forward_pass_pattern")
        if value is None or pd.isna(value):
            return ""
        text = str(value).strip()
        if text.endswith(".0"):
            text = text[:-2]
        try:
            width = int(float(row.get(fold_col, 0) or 0))
        except (TypeError, ValueError):
            width = 0
        if width > 0 and text and set(text).issubset({"0", "1"}):
            return text.zfill(width)[-width:]
        return text

    out["walk_forward_pass_pattern"] = out.apply(_normalize, axis=1)
    return out


def _clean(value, default="") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return default if text.lower() in {"", "nan", "none"} else text


def _book_identity(row) -> str:
    """Return the complete identity used by the monitored book."""
    return "|".join([
        _clean(row.get("symbol")).upper(),
        _clean(row.get("timeframe")),
        _clean(row.get("slice_combination")),
        _clean(row.get("side"), "long").lower(),
        _clean(row.get("bin_mode"), "insample").lower(),
    ])


def _book_base_identity(row) -> str:
    """Return the symbol/timeframe identity used to detect replacements."""
    return "|".join([
        _clean(row.get("symbol")).upper(),
        _clean(row.get("timeframe")),
    ])


def _registry_identity(row) -> str:
    """Match the candidate key used by the lifecycle registry."""
    return "|".join([
        _clean(row.get("symbol")).upper(),
        _clean(row.get("timeframe")),
        _clean(row.get("slice_combination")),
        _clean(row.get("bin_mode"), "insample").lower(),
    ])


def _book_identities(frame: pd.DataFrame | None) -> set[str]:
    if frame is None or frame.empty:
        return set()
    return {_book_identity(row) for _, row in frame.iterrows()}


def build_monitored_book_audit(
    before: pd.DataFrame | None,
    after: pd.DataFrame | None,
    registry: pd.DataFrame | None = None,
    discovery_run_id: str | None = None,
) -> dict:
    """Describe one monitored-book rewrite without changing lifecycle policy.

    The final monitored book is rebuilt by ``sync_monitored.py``. Keeping the
    audit here makes additions, removals, and replacements attributable even
    when the active paper book is intentionally dynamic.
    """
    before = before if before is not None else pd.DataFrame()
    after = after if after is not None else pd.DataFrame()
    before_keys = _book_identities(before)
    after_keys = _book_identities(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    retained = sorted(before_keys & after_keys)

    before_by_base: dict[str, set[str]] = {}
    after_by_base: dict[str, set[str]] = {}
    for _, row in before.iterrows():
        before_by_base.setdefault(_book_base_identity(row), set()).add(_book_identity(row))
    for _, row in after.iterrows():
        after_by_base.setdefault(_book_base_identity(row), set()).add(_book_identity(row))

    changed = []
    for base in sorted(set(before_by_base) & set(after_by_base)):
        old = sorted(before_by_base[base] - after_by_base[base])
        new = sorted(after_by_base[base] - before_by_base[base])
        if old and new:
            symbol, timeframe = base.split("|", 1)
            changed.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "previous": old,
                "current": new,
            })

    registry_status: dict[str, str] = {}
    if registry is not None and not registry.empty:
        for _, row in registry.iterrows():
            key = _clean(row.get("candidate_key")) or _registry_identity(row)
            registry_status[key] = _clean(row.get("status"), "unknown")

    changed_removed = {
        key
        for item in changed
        for key in item["previous"]
    }
    removal_reasons = {}
    for key in removed:
        if key in changed_removed:
            old_parts = key.split("|")
            replacement = next(
                (item for item in changed if key in item["previous"]),
                None,
            )
            replacement_keys = replacement["current"] if replacement else []
            reason = "replaced_candidate"
            if replacement_keys:
                new_parts = replacement_keys[0].split("|")
                if old_parts[2] == new_parts[2] and old_parts[4] == new_parts[4] and old_parts[3] != new_parts[3]:
                    reason = "side_changed"
                elif old_parts[3] == new_parts[3] and old_parts[4] == new_parts[4] and old_parts[2] != new_parts[2]:
                    reason = "slice_changed"
            removal_reasons[key] = reason
        else:
            registry_key = "|".join(key.split("|")[:3] + [key.split("|")[4]])
            status = registry_status.get(registry_key)
            removal_reasons[key] = (
                "decaying_suspended" if status == "decaying_suspended"
                else "not_in_current_registry" if status is None
                else f"registry_status:{status}"
            )

    promotion_reasons = {}
    for key in added:
        parts = key.split("|")
        registry_key = "|".join(parts[:3] + [parts[4]])
        promotion_reasons[key] = registry_status.get(registry_key, "added_by_sync")

    return {
        "previous_book_count": len(before_keys),
        "new_book_count": len(after_keys),
        "added_candidates": added,
        "removed_candidates": removed,
        "retained_candidates": retained,
        "changed_sides_or_slices": changed,
        "removal_reasons": removal_reasons,
        "promotion_reasons": promotion_reasons,
        "discovery_run_id": discovery_run_id,
    }


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
    if df.empty or ("fwd_ret_5b" not in df.columns and "tradeable_fwd_ret_5b" not in df.columns):
        return set()
    # New capture rows carry side-adjusted returns for short candidates. Keep
    # the raw-column fallback for legacy artifacts created before this field
    # existed.
    return_col = "tradeable_fwd_ret_5b" if "tradeable_fwd_ret_5b" in df.columns else "fwd_ret_5b"
    df[return_col] = pd.to_numeric(df[return_col], errors="coerce")
    if "bin_mode" not in df.columns:
        df["bin_mode"] = "insample"
    df = df.dropna(subset=[return_col])
    if df.empty:
        return set()
    grouped = df.groupby(["symbol", "timeframe", "slice_combination", "bin_mode"])
    return {
        "|".join(map(str, key))
        for key, group in grouped
        if len(group) >= min_completed and group[return_col].mean() <= 0
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
    leaderboard = normalize_walk_forward_patterns(leaderboard)

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
        elif eligible and enable_auto_promotion:
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
            "valid_mean_ret_costadj": row.get("valid_mean_ret_costadj"),
            "valid_p_value_nw": row.get("valid_p_value_nw"),
            "walk_forward_folds": row.get("validation_n_folds", row.get("walk_forward_folds")),
            "walk_forward_pass_count": row.get("walk_forward_pass_count"),
            "walk_forward_pass_pattern": row.get("walk_forward_pass_pattern"),
            "scenario_survived_count": row.get("scenario_survived_count"),
            "search_wide_bh_pass": row.get("search_wide_bh_pass"),
            "search_wide_bonferroni_pass": row.get("search_wide_bonferroni_pass"),
            "valid_excess_vs_baseline": row.get("valid_excess_vs_baseline"),
            "valid_excess_vs_best_parent": row.get("valid_excess_vs_best_parent"),
            "leverage_gate_pass": leverage_gate,
            "leverage_gate_reason": (
                "risk data unavailable: lifecycle has no per-candidate ATR/R input; "
                "auto-promotion remains disabled until that evidence source exists"
                if not leverage_gate else "1x/2x risk scenarios pass"
            ),
            "auto_promotion_block_reason": (
                "disabled_without_enable_auto_promotion_flag"
                if eligible and not enable_auto_promotion
                else "strict_research_gate_failed"
                if not eligible else ""
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
    promote_proposals: bool = False,
) -> pd.DataFrame:
    """Apply automatic promotions/demotions to the monitored set.

    This function is intentionally not called by the research workflow yet.
    Production activation must be an explicit operator decision. Existing
    monitored rows with no current research row are preserved; rows explicitly
    classified decaying_suspended are removed; auto_approved rows are added.
    If promote_proposals is True, paper_proposal rows are also added.
    """
    existing = pd.read_csv(monitored_path) if monitored_path.exists() else pd.DataFrame()
    rows = []
    suspended = set()
    approved = []
    if registry is not None and not registry.empty:
        suspended = set(registry.loc[registry["status"] == "decaying_suspended", "candidate_key"].astype(str))
        target_statuses = ["auto_approved", "paper_proposal"] if promote_proposals else ["auto_approved"]
        approved = registry[registry["status"].isin(target_statuses)].to_dict("records")

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
            "source_note": "auto_promoted_strict_candidate",
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
        "--promote-proposals",
        action="store_true",
        help="When applying monitored slices, promote both auto_approved and paper_proposal candidates.",
    )
    parser.add_argument(
        "--apply-monitored-slices",
        action="store_true",
        help="Apply promotions and decaying_suspended demotions.",
    )
    args = parser.parse_args()
    result = build_registry(
        args.leaderboard,
        args.output,
        live_forward_path=args.live_forward,
        enable_auto_promotion=args.enable_auto_promotion,
    )
    if args.apply_monitored_slices:
        if not args.enable_auto_promotion and not args.promote_proposals:
            raise SystemExit("--apply-monitored-slices requires --enable-auto-promotion or --promote-proposals")
        applied = apply_registry_to_monitored(result, args.monitored, promote_proposals=args.promote_proposals)
        print(f"Applied automatic lifecycle decisions to {args.monitored}: {len(applied)} monitored rows")
    print(f"Saved {len(result)} candidate lifecycle rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
