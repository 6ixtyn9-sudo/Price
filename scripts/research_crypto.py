"""Isolated crypto-only research lane.

Purpose
-------
Give crypto its own search/validation family without touching the live equity
paper book. This script is intentionally additive and isolated:

- targets only crypto symbols
- defaults conditioning to BTC/USD and ETH/USD
- writes only under localdata/research/crypto/
- never modifies monitored_slices.csv
- never places orders

Why it exists
-------------
Crypto is already inside the warehouse/discovery substrate, but in the mixed
236-symbol family it is still being judged through an equity-heavy lens:
search-wide correction pooled with equities, conditioning on USO/TLT, and no
clean way to inspect crypto on its own. This script isolates crypto so we can
answer the research question honestly before touching the live book.

Red-team safety design
----------------------
- No shared live artifacts are written.
- validate_slices/discover_slices globals are temporarily redirected and then
  restored, so running this cannot clobber the current system's default
  localdata/discovered_slices.csv / candidate_leaderboard.csv.
- BTC/USD and ETH/USD are used as conditioning symbols for alts, but BTC and
  ETH themselves are still researched via self-exclusion batches (BTC
  conditioned on ETH only, ETH conditioned on BTC only) rather than being
  accidentally skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import discover_slices  # noqa: E402
import validate_slices  # noqa: E402
from price.config import ALLOWLIST_CACHE_PATH, CRYPTO_SYMBOLS  # noqa: E402
from price.market_profiles import get_market_profile  # noqa: E402
from research_lifecycle import build_registry  # noqa: E402


_CRYPTO_PROFILE = get_market_profile("crypto")
DEFAULT_OUTPUT_DIR = Path(_CRYPTO_PROFILE.default_output_dir)
DEFAULT_BIN_MODE = _CRYPTO_PROFILE.default_bin_mode
DEFAULT_TIMEFRAMES = _CRYPTO_PROFILE.default_timeframes
DEFAULT_CONDITION_SYMBOLS = _CRYPTO_PROFILE.default_condition_symbols
DEFAULT_MAX_REGIME_TARGETS = 150
DEFAULT_MAX_REGIME_PER_SYMBOL = 15
DEFAULT_REGIME_TARGET_TRIAGE_BUCKETS = (
    "clean_survivor_wf_strong",
    "clean_survivor_wf_mixed",
    "clean_survivor_wf_failed",
    "late_emerging_recent_only",
    "late_emerging_regime_switching",
    "late_emerging_valid_supported",
)
DEFAULT_MAX_MONITORED_CANDIDATES = 15
DEFAULT_MAX_MONITORED_PER_SYMBOL = 2
PAPER_CANDIDATE_STATUSES = (
    "structural_candidate",
    "bull_regime_candidate",
    "bear_regime_candidate",
    "neutral_regime_candidate",
)


def _resolve_effective_output_dir(output_dir: Path, timeframes: tuple[str, ...], regime_only: bool) -> Path:
    """Choose a stable artifact namespace.

    Full single-timeframe runs should not clobber artifacts from another
    timeframe in the same substrate directory, so they default to
    <output_dir>/<timeframe> unless the caller already gave a scoped path.

    Regime-only reruns search for existing artifacts first:
      1. base output_dir (used by merged shard workflows)
      2. scoped <output_dir>/<timeframe> (used by local single-timeframe runs)
    """
    base = Path(output_dir)
    if len(timeframes) != 1:
        return base

    tf = timeframes[0]
    scoped = base / tf
    if regime_only:
        for candidate in (base, scoped):
            if any(
                (candidate / name).exists()
                for name in (
                    "candidate_leaderboard_crypto_rolling.csv",
                    "candidate_leaderboard_merged.csv",
                    "candidate_registry_crypto_rolling.csv",
                    "candidate_registry.csv",
                )
            ):
                return candidate
        return scoped

    if base.name == tf or base.name.endswith(f"_{tf}"):
        return base
    return scoped


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for symbol in symbols:
        s = str(symbol).strip().upper()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def load_crypto_symbols(path: Path | None = None) -> list[str]:
    """Load the active crypto research universe.

    Preference order:
      1. localdata/explicit_allowlist.json -> .crypto
      2. localdata/explicit_allowlist.json -> filter .all for slash pairs
      3. static CRYPTO_SYMBOLS fallback
    """
    allowlist_path = Path(path) if path else Path(ALLOWLIST_CACHE_PATH)
    if allowlist_path.exists():
        try:
            payload = json.loads(allowlist_path.read_text())
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            crypto = _normalize_symbols(payload.get("crypto", []))
            if crypto:
                return crypto
            all_symbols = _normalize_symbols(payload.get("all", []))
            filtered = [s for s in all_symbols if "/" in s]
            if filtered:
                return filtered
    return _normalize_symbols(CRYPTO_SYMBOLS)


def build_discovery_batches(
    target_symbols: Iterable[str],
    condition_symbols: Iterable[str],
) -> list[dict]:
    """Build safe discovery batches that avoid self-conditioning skips.

    discover_slices.run_discovery uses one shared cond_symbols list per run and
    skips a symbol completely if it appears in that conditioning list. For
    crypto we want alts conditioned on BTC+ETH, while still allowing BTC and
    ETH themselves to be researched. So we split the universe into:

      - non-conditioning symbols: conditioned on all requested symbols
      - each conditioning symbol: conditioned on the OTHER conditioning symbols

    Example:
      targets = [BTC/USD, ETH/USD, SOL/USD], cond = [BTC/USD, ETH/USD]
      -> [SOL/USD] with [BTC/USD, ETH/USD]
      -> [BTC/USD] with [ETH/USD]
      -> [ETH/USD] with [BTC/USD]
    """
    targets = _normalize_symbols(target_symbols)
    conds = _normalize_symbols(condition_symbols)
    cond_set = set(conds)
    batches: list[dict] = []

    non_cond = [symbol for symbol in targets if symbol not in cond_set]
    if non_cond:
        batches.append({
            "label": "alts",
            "symbols": non_cond,
            "condition_symbols": conds,
        })

    for cond_symbol in conds:
        if cond_symbol not in targets:
            continue
        other_conds = [symbol for symbol in conds if symbol != cond_symbol]
        batches.append({
            "label": cond_symbol.replace("/", "-"),
            "symbols": [cond_symbol],
            "condition_symbols": other_conds,
        })

    return batches


@contextmanager
def isolated_research_paths(output_dir: Path):
    """Temporarily redirect discovery/validation globals into output_dir."""
    discovered_path = output_dir / "discovered_slices_crypto_rolling.csv"
    validated_path = output_dir / "validated_slices_crypto_rolling.csv"
    leaderboard_path = output_dir / "candidate_leaderboard_crypto_rolling.csv"
    scenario_path = output_dir / "validation_scenario_grid_crypto_rolling.csv"
    walk_path = output_dir / "walk_forward_diagnostics_crypto_rolling.csv"
    date_path = output_dir / "date_range_diagnostics_crypto_rolling.csv"
    regime_path = output_dir / "regime_stratified_diagnostics_crypto_rolling.csv"

    state = {
        "discover": discover_slices.DISCOVERED_SLICES_PATH,
        "discovered": validate_slices.DISCOVERED_SLICES_PATH,
        "validated": validate_slices.VALIDATED_SLICES_PATH,
        "leaderboard": validate_slices.CANDIDATE_LEADERBOARD_PATH,
        "scenario": validate_slices.SCENARIO_GRID_PATH,
        "walk": validate_slices.WALK_FORWARD_DIAGNOSTICS_PATH,
        "date": validate_slices.DATE_RANGE_DIAGNOSTICS_PATH,
        "regime": validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH,
    }

    discover_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.VALIDATED_SLICES_PATH = str(validated_path)
    validate_slices.CANDIDATE_LEADERBOARD_PATH = str(leaderboard_path)
    validate_slices.SCENARIO_GRID_PATH = str(scenario_path)
    validate_slices.WALK_FORWARD_DIAGNOSTICS_PATH = str(walk_path)
    validate_slices.DATE_RANGE_DIAGNOSTICS_PATH = str(date_path)
    validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH = str(regime_path)
    try:
        yield {
            "discovered": discovered_path,
            "validated": validated_path,
            "leaderboard": leaderboard_path,
            "scenario": scenario_path,
            "walk": walk_path,
            "date": date_path,
            "regime": regime_path,
        }
    finally:
        discover_slices.DISCOVERED_SLICES_PATH = state["discover"]
        validate_slices.DISCOVERED_SLICES_PATH = state["discovered"]
        validate_slices.VALIDATED_SLICES_PATH = state["validated"]
        validate_slices.CANDIDATE_LEADERBOARD_PATH = state["leaderboard"]
        validate_slices.SCENARIO_GRID_PATH = state["scenario"]
        validate_slices.WALK_FORWARD_DIAGNOSTICS_PATH = state["walk"]
        validate_slices.DATE_RANGE_DIAGNOSTICS_PATH = state["date"]
        validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH = state["regime"]


def _top_rows(frame: pd.DataFrame, columns: list[str], n: int = 15) -> list[dict]:
    if frame is None or frame.empty:
        return []
    keep = [col for col in columns if col in frame.columns]
    out = frame[keep].head(n).copy()
    if "walk_forward_pass_pattern" in out.columns:
        out["walk_forward_pass_pattern"] = out["walk_forward_pass_pattern"].astype(str)
    return out.to_dict("records")


def _leaderboard_targets(leaderboard: pd.DataFrame) -> list[tuple[str, str, str, str]]:
    if leaderboard is None or leaderboard.empty:
        return []
    out = []
    seen = set()
    for _, row in leaderboard.iterrows():
        side = str(row.get("side", "long") or "long").lower()
        if side not in {"long", "short"}:
            side = "long"
        key = (
            str(row["symbol"]),
            str(row["timeframe"]),
            str(row["slice_combination"]),
            side,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _load_existing_crypto_artifacts(output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    output_dir = Path(output_dir)
    discovered_candidates = [
        output_dir / "discovered_slices_crypto_rolling.csv",
        output_dir / "discovered_slices_merged.csv",
    ]
    leaderboard_candidates = [
        output_dir / "candidate_leaderboard_crypto_rolling.csv",
        output_dir / "candidate_leaderboard_merged.csv",
    ]
    registry_candidates = [
        output_dir / "candidate_registry_crypto_rolling.csv",
        output_dir / "candidate_registry.csv",
    ]

    def _pick(candidates: list[Path]) -> Path | None:
        for path in candidates:
            if path.exists():
                return path
        return None

    discovered_path = _pick(discovered_candidates)
    leaderboard_path = _pick(leaderboard_candidates)
    registry_path = _pick(registry_candidates)

    missing = []
    if discovered_path is None:
        missing.append(discovered_candidates[0].name)
    if leaderboard_path is None:
        missing.append(leaderboard_candidates[0].name)
    if registry_path is None:
        missing.append(registry_candidates[0].name)
    if missing:
        raise FileNotFoundError(
            "regime-only crypto run requires existing artifacts: " + ", ".join(missing)
        )

    def _read(path: Path) -> pd.DataFrame:
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    return _read(discovered_path), _read(leaderboard_path), _read(registry_path)


def _select_regime_targets(
    leaderboard: pd.DataFrame,
    max_targets: int = DEFAULT_MAX_REGIME_TARGETS,
    max_per_symbol: int = DEFAULT_MAX_REGIME_PER_SYMBOL,
    allowed_buckets: tuple[str, ...] = DEFAULT_REGIME_TARGET_TRIAGE_BUCKETS,
) -> tuple[pd.DataFrame, list[tuple[str, str, str, str]]]:
    if leaderboard is None or leaderboard.empty:
        return pd.DataFrame(), []

    selected = leaderboard[
        leaderboard["triage_bucket"].astype(str).isin(allowed_buckets)
    ].copy()
    if selected.empty:
        return pd.DataFrame(), []

    sort_cols = [
        "search_wide_bonferroni_pass",
        "search_wide_bh_pass",
        "robustness_score",
        "valid_mean_ret_costadj",
        "walk_forward_survival_rate",
    ]
    selected = selected.sort_values(sort_cols, ascending=[False, False, False, False, False])

    capped_rows = []
    per_symbol_counts: dict[tuple[str, str], int] = {}
    for _, row in selected.iterrows():
        key = (str(row["symbol"]), str(row["timeframe"]))
        if per_symbol_counts.get(key, 0) >= max_per_symbol:
            continue
        capped_rows.append(row)
        per_symbol_counts[key] = per_symbol_counts.get(key, 0) + 1
        if len(capped_rows) >= max_targets:
            break

    capped = pd.DataFrame(capped_rows).reset_index(drop=True)
    return capped, _leaderboard_targets(capped)


def _write_regime_target_manifest(selected: pd.DataFrame, output_dir: Path) -> None:
    output_dir = Path(output_dir)
    cols = [
        col for col in [
            "symbol", "timeframe", "slice_combination", "side",
            "triage_bucket", "robustness_score",
            "search_wide_bh_pass", "search_wide_bonferroni_pass",
            "valid_mean_ret_costadj",
        ] if col in selected.columns
    ]
    selected[cols].to_csv(output_dir / "regime_target_manifest.csv", index=False)


def _annotate_regime_search_wide(frame: pd.DataFrame, p_threshold: float = 0.05) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(frame.columns) if frame is not None else [])
    work = frame.copy()
    p_col = "__regime_p_value_nw__"
    work[p_col] = pd.to_numeric(work["slice_p_value_nw"], errors="coerce")
    work = validate_slices.annotate_search_wide_significance(
        work,
        p_threshold=p_threshold,
        p_col=p_col,
    )
    work = work.drop(columns=[p_col])
    work = work.rename(
        columns={
            "search_wide_rank": "search_wide_rank_regime",
            "search_wide_bh_pass": "search_wide_bh_pass_regime",
            "search_wide_bonferroni_pass": "search_wide_bonferroni_pass_regime",
            "search_wide_family_size": "search_wide_family_size_regime",
        }
    )
    return work


def _classify_regime_candidate_status(row: pd.Series, min_samples: int = 15) -> str:
    if bool(row.get("strict_gate_pass", False)):
        return "structural_candidate"

    positive = (
        int(row.get("slice_n", 0) or 0) >= min_samples
        and bool(row.get("slice_pass", False))
        and float(row.get("excess_vs_baseline", 0) or 0) > 0
        and float(row.get("excess_vs_best_parent", 0) or 0) > 0
        and float(row.get("regime_excess_vs_all", 0) or 0) > 0
    )
    if positive and bool(row.get("search_wide_bh_pass_regime", False)):
        return f"{row['regime']}_regime_candidate"

    if bool(row.get("not_regime_evaluated", False)):
        return "not_regime_evaluated"

    weak_positive = (
        int(row.get("slice_n", 0) or 0) >= min_samples
        and float(row.get("slice_mean_ret_costadj", 0) or 0) > 0
        and float(row.get("excess_vs_baseline", 0) or 0) > 0
    )
    if weak_positive:
        return "regime_switching_research_only"

    return "unsupported"


def build_regime_outputs(
    leaderboard: pd.DataFrame,
    registry: pd.DataFrame,
    regime_diagnostics: pd.DataFrame,
    output_dir: Path,
    min_samples: int = 15,
    p_threshold: float = 0.05,
    selected_targets_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    output_dir = Path(output_dir)
    regime_registry_path = output_dir / "candidate_registry_crypto_regime.csv"
    regime_counts_path = output_dir / "regime_counts_crypto.csv"
    regime_matrix_path = output_dir / "regime_candidate_matrix_crypto.csv"

    empty_registry = pd.DataFrame()
    empty_summary = {
        "regime_status_counts": {},
        "regime_leaderboard_rows": {"bull": 0, "bear": 0, "neutral": 0},
        "regime_candidate_count": 0,
        "top_regime_candidates": [],
    }
    if leaderboard is None or leaderboard.empty or regime_diagnostics is None or regime_diagnostics.empty:
        empty_registry.to_csv(regime_registry_path, index=False)
        pd.DataFrame().to_csv(regime_counts_path, index=False)
        pd.DataFrame().to_csv(regime_matrix_path, index=False)
        for regime in ("bull", "bear", "neutral"):
            pd.DataFrame().to_csv(output_dir / f"candidate_leaderboard_crypto_{regime}.csv", index=False)
        return empty_registry, empty_summary

    key_cols = ["symbol", "timeframe", "slice_combination"]
    selected_keys = set()
    if selected_targets_df is not None and not selected_targets_df.empty:
        for _, row in selected_targets_df.iterrows():
            selected_keys.add((str(row["symbol"]), str(row["timeframe"]), str(row["slice_combination"])))

    registry_cols = [c for c in ["candidate_key", "strict_gate_pass", "status", "live_decay_flag"] if c in registry.columns]
    base = leaderboard.merge(registry[key_cols + registry_cols], on=key_cols, how="left")
    base["strict_gate_pass"] = base.get("strict_gate_pass", False).fillna(False)
    base["not_regime_evaluated"] = False

    diag = regime_diagnostics.copy()
    diag = diag[diag.get("diagnostic_status", "") == "ok"].copy()
    diag = diag[diag["regime"].isin(["all", "bull", "bear", "neutral"])]
    if diag.empty:
        empty_registry.to_csv(regime_registry_path, index=False)
        pd.DataFrame().to_csv(regime_counts_path, index=False)
        pd.DataFrame().to_csv(regime_matrix_path, index=False)
        for regime in ("bull", "bear", "neutral"):
            pd.DataFrame().to_csv(output_dir / f"candidate_leaderboard_crypto_{regime}.csv", index=False)
        return empty_registry, empty_summary

    all_rows = diag[diag["regime"] == "all"][key_cols + ["slice_mean_ret_costadj"]].rename(
        columns={"slice_mean_ret_costadj": "all_regime_mean_ret_costadj"}
    )

    per_regime_frames = {}
    for regime in ("bull", "bear", "neutral"):
        frame = diag[diag["regime"] == regime].copy()
        if frame.empty:
            frame = pd.DataFrame()
            frame.to_csv(output_dir / f"candidate_leaderboard_crypto_{regime}.csv", index=False)
            per_regime_frames[regime] = frame
            continue
        frame = frame.merge(base, on=key_cols, how="left", suffixes=("", "_all"))
        frame = frame.merge(all_rows, on=key_cols, how="left")
        frame["regime_excess_vs_all"] = frame["slice_mean_ret_costadj"] - frame["all_regime_mean_ret_costadj"]
        frame = _annotate_regime_search_wide(frame, p_threshold=p_threshold)
        frame["regime_candidate_status"] = frame.apply(
            _classify_regime_candidate_status, axis=1, min_samples=min_samples
        )
        frame = frame.sort_values(
            [
                "search_wide_bh_pass_regime",
                "slice_pass",
                "slice_mean_ret_costadj",
                "excess_vs_best_parent",
                "slice_p_value_nw",
            ],
            ascending=[False, False, False, False, True],
        ).reset_index(drop=True)
        frame.to_csv(output_dir / f"candidate_leaderboard_crypto_{regime}.csv", index=False)
        per_regime_frames[regime] = frame

    status_priority = {
        "structural_candidate": 5,
        "bull_regime_candidate": 4,
        "bear_regime_candidate": 4,
        "neutral_regime_candidate": 4,
        "regime_switching_research_only": 3,
        "unsupported": 1,
    }

    registry_rows = []
    for _, base_row in base.iterrows():
        base_key = (str(base_row["symbol"]), str(base_row["timeframe"]), str(base_row["slice_combination"]))
        statuses = {}
        regime_rows = []

        if selected_keys and base_key not in selected_keys and not bool(base_row.get("strict_gate_pass", False)):
            statuses = {"bull": "not_regime_evaluated", "bear": "not_regime_evaluated", "neutral": "not_regime_evaluated"}
            best_row = None
            overall_status = "not_regime_evaluated"
            base_row = base_row.copy()
            base_row["not_regime_evaluated"] = True
        else:
            for regime, frame in per_regime_frames.items():
                if frame.empty:
                    statuses[regime] = "unsupported"
                    continue
                hit = frame[
                    (frame["symbol"] == base_row["symbol"])
                    & (frame["timeframe"] == base_row["timeframe"])
                    & (frame["slice_combination"] == base_row["slice_combination"])
                ]
                if hit.empty:
                    statuses[regime] = "unsupported"
                    continue
                row = hit.iloc[0]
                statuses[regime] = row["regime_candidate_status"]
                regime_rows.append(row)

            if bool(base_row.get("strict_gate_pass", False)):
                overall_status = "structural_candidate"
                best_row = None
            else:
                candidate_rows = [r for r in regime_rows if str(r["regime_candidate_status"]).endswith("_regime_candidate")]
                if candidate_rows:
                    best_row = max(candidate_rows, key=lambda r: (float(r["slice_mean_ret_costadj"]), -float(r["slice_p_value_nw"])))
                    overall_status = best_row["regime_candidate_status"]
                else:
                    weak_rows = [r for r in regime_rows if r["regime_candidate_status"] == "regime_switching_research_only"]
                    if weak_rows:
                        best_row = max(weak_rows, key=lambda r: float(r["slice_mean_ret_costadj"]))
                        overall_status = "regime_switching_research_only"
                    else:
                        best_row = None
                        overall_status = "unsupported"

        registry_rows.append(
            {
                "symbol": base_row["symbol"],
                "timeframe": base_row["timeframe"],
                "slice_combination": base_row["slice_combination"],
                "side": base_row.get("side", "long"),
                "bin_mode": DEFAULT_BIN_MODE,
                "all_regime_status": base_row.get("status", "research_only"),
                "strict_gate_pass": bool(base_row.get("strict_gate_pass", False)),
                "overall_regime_status": overall_status,
                "best_regime": best_row["regime"] if best_row is not None else "",
                "best_regime_mean_ret_costadj": best_row.get("slice_mean_ret_costadj") if best_row is not None else None,
                "best_regime_p_value_nw": best_row.get("slice_p_value_nw") if best_row is not None else None,
                "bull_status": statuses.get("bull", "unsupported"),
                "bear_status": statuses.get("bear", "unsupported"),
                "neutral_status": statuses.get("neutral", "unsupported"),
                "valid_mean_ret_costadj": base_row.get("valid_mean_ret_costadj"),
                "valid_p_value_nw": base_row.get("valid_p_value_nw"),
                "walk_forward_pass_pattern": base_row.get("walk_forward_pass_pattern", ""),
                "search_wide_bh_pass": base_row.get("search_wide_bh_pass", False),
                "search_wide_bonferroni_pass": base_row.get("search_wide_bonferroni_pass", False),
            }
        )

    regime_registry = pd.DataFrame(registry_rows).sort_values(
        ["strict_gate_pass", "overall_regime_status", "best_regime_mean_ret_costadj"],
        ascending=[False, True, False],
    ).reset_index(drop=True)
    regime_registry.to_csv(regime_registry_path, index=False)

    counts_rows = []
    for regime, frame in per_regime_frames.items():
        if frame.empty:
            continue
        for status, count in frame["regime_candidate_status"].value_counts().items():
            counts_rows.append({"regime": regime, "status": status, "count": int(count)})
    pd.DataFrame(counts_rows).to_csv(regime_counts_path, index=False)

    matrix_cols = ["symbol", "timeframe", "slice_combination", "side", "overall_regime_status", "bull_status", "bear_status", "neutral_status", "best_regime"]
    regime_registry[matrix_cols].to_csv(regime_matrix_path, index=False)

    top_candidates = regime_registry[
        regime_registry["overall_regime_status"].isin(
            ["structural_candidate", "bull_regime_candidate", "bear_regime_candidate", "neutral_regime_candidate"]
        )
    ]

    summary = {
        "regime_status_counts": regime_registry["overall_regime_status"].value_counts().to_dict(),
        "regime_leaderboard_rows": {regime: int(len(frame)) for regime, frame in per_regime_frames.items()},
        "regime_candidate_count": int(len(top_candidates)),
        "regime_target_count": int(len(selected_targets_df)) if selected_targets_df is not None else int(len(leaderboard)),
        "regime_not_evaluated_count": int((regime_registry["overall_regime_status"] == "not_regime_evaluated").sum()),
        "regime_evaluated_symbol_count": int(regime_registry.loc[regime_registry["overall_regime_status"] != "not_regime_evaluated", "symbol"].nunique()),
        "top_regime_candidates": _top_rows(
            top_candidates,
            [
                "symbol", "timeframe", "slice_combination", "side",
                "overall_regime_status", "best_regime", "best_regime_mean_ret_costadj",
                "best_regime_p_value_nw", "walk_forward_pass_pattern",
            ],
        ),
    }
    return regime_registry, summary


def build_summary(
    symbols: list[str],
    condition_symbols: list[str],
    discovered: pd.DataFrame,
    leaderboard: pd.DataFrame,
    registry: pd.DataFrame,
    output_dir: Path,
    regime_summary: dict | None = None,
    monitored_summary: dict | None = None,
) -> dict:
    strict_gate_pass = int(registry["strict_gate_pass"].sum()) if not registry.empty and "strict_gate_pass" in registry.columns else 0
    status_counts = registry["status"].value_counts().to_dict() if not registry.empty and "status" in registry.columns else {}
    timeframe_counts = (
        leaderboard.groupby("timeframe").size().to_dict()
        if not leaderboard.empty and "timeframe" in leaderboard.columns
        else {}
    )
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bin_mode": DEFAULT_BIN_MODE,
        "symbols": symbols,
        "symbol_count": len(symbols),
        "condition_symbols": condition_symbols,
        "discovered_rows": int(len(discovered)),
        "leaderboard_rows": int(len(leaderboard)),
        "registry_rows": int(len(registry)),
        "strict_gate_pass_count": strict_gate_pass,
        "status_counts": status_counts,
        "timeframe_counts": timeframe_counts,
        "paper_proposal_count": int(status_counts.get("paper_proposal", 0)),
        "auto_approved_count": int(status_counts.get("auto_approved", 0)),
        "output_dir": str(output_dir),
        "top_candidates": _top_rows(
            leaderboard,
            [
                "rank", "symbol", "timeframe", "slice_combination", "side",
                "triage_bucket", "valid_n", "valid_mean_ret_costadj",
                "valid_p_value_nw", "walk_forward_pass_pattern",
                "search_wide_bh_pass", "search_wide_bonferroni_pass",
            ],
        ),
    }
    if regime_summary:
        summary.update(regime_summary)
    if monitored_summary:
        summary.update(monitored_summary)
    return summary


def build_monitored_candidates(
    regime_registry: pd.DataFrame,
    leaderboard: pd.DataFrame,
    output_dir: Path,
    max_candidates: int = DEFAULT_MAX_MONITORED_CANDIDATES,
    max_per_symbol: int = DEFAULT_MAX_MONITORED_PER_SYMBOL,
) -> tuple[pd.DataFrame, dict]:
    output_dir = Path(output_dir)
    out_path = output_dir / "monitored_candidates_crypto.csv"

    columns = [
        "symbol",
        "timeframe",
        "slice_combination",
        "side",
        "bin_mode",
        "overall_regime_status",
        "best_regime",
        "best_regime_mean_ret_costadj",
        "best_regime_p_value_nw",
        "triage_bucket",
        "valid_n",
        "valid_mean_ret_costadj",
        "valid_p_value_nw",
        "walk_forward_pass_pattern",
        "search_wide_bh_pass",
        "search_wide_bonferroni_pass",
        "source_note",
    ]

    empty = pd.DataFrame(columns=columns)
    empty_summary = {
        "monitored_candidate_count": 0,
        "monitored_candidate_symbols": [],
        "monitored_candidate_status_counts": {},
        "top_monitored_candidates": [],
    }

    if regime_registry is None or regime_registry.empty or leaderboard is None or leaderboard.empty:
        empty.to_csv(out_path, index=False)
        return empty, empty_summary

    key_cols = ["symbol", "timeframe", "slice_combination"]
    lb_cols = [
        c for c in [
            "triage_bucket",
            "valid_n",
            "valid_mean_ret_costadj",
            "valid_p_value_nw",
            "walk_forward_pass_pattern",
            "search_wide_bh_pass",
            "search_wide_bonferroni_pass",
            "bin_mode",
        ] if c in leaderboard.columns
    ]
    # Avoid bin_mode collision: regime_registry already carries bin_mode (DEFAULT).
    lb_cols_no_bin = [c for c in lb_cols if c != "bin_mode"]
    joined = regime_registry.merge(
        leaderboard[key_cols + lb_cols_no_bin] if lb_cols_no_bin else leaderboard[key_cols].head(0),
        on=key_cols,
        how="left",
        suffixes=("", "_lb"),
    )
    if "bin_mode" not in joined.columns:
        if "bin_mode_lb" in joined.columns:
            joined["bin_mode"] = joined["bin_mode_lb"]
        else:
            joined["bin_mode"] = DEFAULT_BIN_MODE
    else:
        if "bin_mode_lb" in joined.columns:
            joined["bin_mode"] = joined["bin_mode"].fillna(joined["bin_mode_lb"])
        joined["bin_mode"] = joined["bin_mode"].fillna(DEFAULT_BIN_MODE)

    selected = joined[joined["overall_regime_status"].isin(PAPER_CANDIDATE_STATUSES)].copy()
    if selected.empty:
        empty.to_csv(out_path, index=False)
        return empty, empty_summary

    selected["source_note"] = selected["overall_regime_status"].astype(str)
    if "bin_mode" in selected.columns:
        selected["bin_mode"] = selected["bin_mode"].astype(str).replace({"nan": DEFAULT_BIN_MODE, "None": DEFAULT_BIN_MODE, "": DEFAULT_BIN_MODE})
        selected["bin_mode"] = selected["bin_mode"].fillna(DEFAULT_BIN_MODE)
    else:
        selected["bin_mode"] = DEFAULT_BIN_MODE

    status_priority = {
        "structural_candidate": 4,
        "bull_regime_candidate": 3,
        "bear_regime_candidate": 3,
        "neutral_regime_candidate": 2,
    }
    selected["_status_priority"] = selected["overall_regime_status"].map(status_priority).fillna(0)
    selected = selected.sort_values(
        [
            "_status_priority",
            "best_regime_p_value_nw",
            "best_regime_mean_ret_costadj",
            "search_wide_bh_pass",
            "valid_mean_ret_costadj",
        ],
        ascending=[False, True, False, False, False],
    )

    kept_rows = []
    per_symbol_counts: dict[tuple[str, str], int] = {}
    for _, row in selected.iterrows():
        key = (str(row["symbol"]), str(row["timeframe"]))
        if per_symbol_counts.get(key, 0) >= max_per_symbol:
            continue
        kept_rows.append(row)
        per_symbol_counts[key] = per_symbol_counts.get(key, 0) + 1
        if len(kept_rows) >= max_candidates:
            break

    monitored = pd.DataFrame(kept_rows)
    monitored = monitored[columns].reset_index(drop=True)
    monitored.to_csv(out_path, index=False)

    summary = {
        "monitored_candidate_count": int(len(monitored)),
        "monitored_candidate_symbols": sorted(monitored["symbol"].astype(str).unique().tolist()),
        "monitored_candidate_status_counts": monitored["overall_regime_status"].value_counts().to_dict(),
        "top_monitored_candidates": _top_rows(
            monitored,
            [
                "symbol", "timeframe", "slice_combination", "side",
                "overall_regime_status", "best_regime",
                "best_regime_mean_ret_costadj", "best_regime_p_value_nw",
                "triage_bucket", "walk_forward_pass_pattern",
            ],
        ),
    }
    return monitored, summary


def run_crypto_research(
    symbols: list[str] | None = None,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    condition_symbols: tuple[str, ...] = DEFAULT_CONDITION_SYMBOLS,
    min_samples: int = 15,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    top_n_diagnostics: int = 25,
    regime_only: bool = False,
    max_regime_targets: int = DEFAULT_MAX_REGIME_TARGETS,
    max_regime_per_symbol: int = DEFAULT_MAX_REGIME_PER_SYMBOL,
    max_monitored_candidates: int = DEFAULT_MAX_MONITORED_CANDIDATES,
    max_monitored_per_symbol: int = DEFAULT_MAX_MONITORED_PER_SYMBOL,
) -> dict:
    output_dir = _resolve_effective_output_dir(Path(output_dir), timeframes, regime_only)
    output_dir.mkdir(parents=True, exist_ok=True)

    target_symbols = _normalize_symbols(symbols or load_crypto_symbols())
    if not target_symbols:
        raise ValueError("No crypto symbols resolved for research run.")
    conds = _normalize_symbols(condition_symbols)

    batches = build_discovery_batches(target_symbols, conds)
    if not batches and not regime_only:
        raise ValueError("No crypto discovery batches could be built.")

    with isolated_research_paths(output_dir) as paths:
        if not regime_only:
            print(f"CRYPTO RESEARCH: full rebuild for {len(target_symbols)} symbols across {timeframes}")
            for path in paths.values():
                if path.exists():
                    path.unlink()

            for timeframe in timeframes:
                print(f"CRYPTO RESEARCH: running discovery for timeframe={timeframe}")
                for batch in batches:
                    discover_slices.run_discovery(
                        target_symbols=batch["symbols"],
                        timeframe=timeframe,
                        min_samples=min_samples,
                        append=paths["discovered"].exists(),
                        cond_symbols=batch["condition_symbols"] or None,
                        bin_mode=DEFAULT_BIN_MODE,
                        profile="crypto",
                    )

            if not paths["discovered"].exists():
                discovered = pd.DataFrame()
                leaderboard = pd.DataFrame()
                registry = pd.DataFrame()
                regime_registry = pd.DataFrame()
                regime_summary = None
            else:
                try:
                    discovered = pd.read_csv(paths["discovered"])
                except pd.errors.EmptyDataError:
                    discovered = pd.DataFrame()

                if discovered.empty:
                    leaderboard = pd.DataFrame()
                    registry = pd.DataFrame()
                    regime_registry = pd.DataFrame()
                    regime_summary = None
                    pd.DataFrame().to_csv(paths["validated"], index=False)
                    pd.DataFrame().to_csv(paths["leaderboard"], index=False)
                else:
                    leaderboard = validate_slices.run_candidate_leaderboard(
                        slices_path=str(paths["discovered"]),
                        output_path=str(paths["leaderboard"]),
                        bin_mode=DEFAULT_BIN_MODE,
                    )
                    registry = build_registry(
                        paths["leaderboard"],
                        output_path=output_dir / "candidate_registry_crypto_rolling.csv",
                        enable_auto_promotion=False,
                    )
        else:
            print("CRYPTO RESEARCH: regime-only rerun from existing artifacts")
            discovered, leaderboard, registry = _load_existing_crypto_artifacts(output_dir)
            regime_registry = pd.DataFrame()
            regime_summary = None

        regime_registry = pd.DataFrame()
        regime_summary = None
        monitored_candidates = pd.DataFrame()
        monitored_summary = None
        if not leaderboard.empty:
            print("CRYPTO RESEARCH: selecting regime-aware target subset")
            selected_targets_df, regime_targets = _select_regime_targets(
                leaderboard,
                max_targets=max_regime_targets,
                max_per_symbol=max_regime_per_symbol,
            )
            _write_regime_target_manifest(selected_targets_df, output_dir)
            print(
                f"CRYPTO RESEARCH: regime targets={len(regime_targets)} "
                f"across {selected_targets_df['symbol'].nunique() if not selected_targets_df.empty else 0} symbols"
            )
            print("CRYPTO RESEARCH: running date-range diagnostics on regime targets")
            validate_slices.run_date_range_diagnostics(
                slices_path=str(paths["discovered"]),
                output_path=str(paths["date"]),
                diagnostic_scope="leaderboard-top",
                top_n=top_n_diagnostics,
                bin_mode=DEFAULT_BIN_MODE,
                targets=regime_targets,
            )
            print("CRYPTO RESEARCH: running regime-stratified diagnostics on regime targets")
            regime_diagnostics = validate_slices.run_regime_stratified_diagnostics(
                slices_path=str(paths["discovered"]),
                output_path=str(paths["regime"]),
                diagnostic_scope="leaderboard-top",
                top_n=len(regime_targets),
                bin_mode=DEFAULT_BIN_MODE,
                regime_symbol_policy="crypto",
                targets=regime_targets,
            )
            print("CRYPTO RESEARCH: building regime-aware leaderboards and registry")
            regime_registry, regime_summary = build_regime_outputs(
                leaderboard,
                registry,
                regime_diagnostics,
                output_dir=output_dir,
                min_samples=min_samples,
                selected_targets_df=selected_targets_df,
            )
            print("CRYPTO RESEARCH: building monitored paper candidates")
            monitored_candidates, monitored_summary = build_monitored_candidates(
                regime_registry,
                leaderboard,
                output_dir=output_dir,
                max_candidates=max_monitored_candidates,
                max_per_symbol=max_monitored_per_symbol,
            )

        print("CRYPTO RESEARCH: writing summary")
        summary = build_summary(
            target_symbols,
            conds,
            discovered,
            leaderboard,
            registry,
            output_dir,
            regime_summary=regime_summary,
            monitored_summary=monitored_summary,
        )
        summary_path = output_dir / "crypto_research_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated crypto-only research.")
    parser.add_argument("--symbols", nargs="+", default=None, help="Explicit crypto symbols to research.")
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        choices=["1d", "1h", "15m"],
        help="Timeframes to include in the crypto-only lane.",
    )
    parser.add_argument(
        "--condition-on",
        nargs="+",
        default=list(DEFAULT_CONDITION_SYMBOLS),
        help="Conditioning symbols for crypto discovery (default: BTC/USD ETH/USD).",
    )
    parser.add_argument("--min-samples", type=int, default=15)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-n-diagnostics", type=int, default=25)
    parser.add_argument("--regime-only", action="store_true", help="Reuse existing crypto artifacts and rerun only the regime-aware phase.")
    parser.add_argument("--max-regime-targets", type=int, default=DEFAULT_MAX_REGIME_TARGETS)
    parser.add_argument("--max-regime-per-symbol", type=int, default=DEFAULT_MAX_REGIME_PER_SYMBOL)
    parser.add_argument("--max-monitored-candidates", type=int, default=DEFAULT_MAX_MONITORED_CANDIDATES)
    parser.add_argument("--max-monitored-per-symbol", type=int, default=DEFAULT_MAX_MONITORED_PER_SYMBOL)
    args = parser.parse_args()

    run_crypto_research(
        symbols=args.symbols,
        timeframes=tuple(args.timeframes),
        condition_symbols=tuple(args.condition_on),
        min_samples=args.min_samples,
        output_dir=args.output_dir,
        top_n_diagnostics=args.top_n_diagnostics,
        regime_only=args.regime_only,
        max_regime_targets=args.max_regime_targets,
        max_regime_per_symbol=args.max_regime_per_symbol,
        max_monitored_candidates=args.max_monitored_candidates,
        max_monitored_per_symbol=args.max_monitored_per_symbol,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
