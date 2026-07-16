"""Isolated futures-only research lane.

Scope
-----
Research foundation only. This lane uses canonical FUT/* symbols, writes only
under localdata/research/futures/, and never touches the live equity paper
book. It is intentionally conservative: daily-first by default, no execution,
no monitored-book sync.
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
from price.futures_metadata import get_research_futures_symbols  # noqa: E402
from price.market_profiles import get_market_profile  # noqa: E402
from research_lifecycle import build_registry  # noqa: E402


_FUTURES_PROFILE = get_market_profile("futures")
DEFAULT_OUTPUT_DIR = Path(_FUTURES_PROFILE.default_output_dir)
DEFAULT_BIN_MODE = _FUTURES_PROFILE.default_bin_mode
DEFAULT_TIMEFRAMES = _FUTURES_PROFILE.default_timeframes
DEFAULT_MAX_MONITORED_CANDIDATES = 10
DEFAULT_MAX_MONITORED_PER_SYMBOL = 2
PAPER_CANDIDATE_STATUSES = (
    "structural_candidate",
    "bull_regime_candidate",
    "bear_regime_candidate",
    "neutral_regime_candidate",
)


def _resolve_effective_output_dir(output_dir: Path, timeframes: tuple[str, ...], regime_only: bool) -> Path:
    """Mirror crypto's timeframe-safe artifact namespacing for futures."""
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
                    "candidate_leaderboard_futures_rolling.csv",
                    "candidate_leaderboard_merged.csv",
                    "candidate_registry_futures_rolling.csv",
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
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


@contextmanager
def isolated_research_paths(output_dir: Path):
    discovered_path = output_dir / "discovered_slices_futures_rolling.csv"
    validated_path = output_dir / "validated_slices_futures_rolling.csv"
    leaderboard_path = output_dir / "candidate_leaderboard_futures_rolling.csv"
    date_path = output_dir / "date_range_diagnostics_futures_rolling.csv"
    regime_path = output_dir / "regime_stratified_diagnostics_futures_rolling.csv"

    state = {
        "discover": discover_slices.DISCOVERED_SLICES_PATH,
        "discovered": validate_slices.DISCOVERED_SLICES_PATH,
        "validated": validate_slices.VALIDATED_SLICES_PATH,
        "leaderboard": validate_slices.CANDIDATE_LEADERBOARD_PATH,
        "date": validate_slices.DATE_RANGE_DIAGNOSTICS_PATH,
        "regime": validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH,
    }

    discover_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
    validate_slices.VALIDATED_SLICES_PATH = str(validated_path)
    validate_slices.CANDIDATE_LEADERBOARD_PATH = str(leaderboard_path)
    validate_slices.DATE_RANGE_DIAGNOSTICS_PATH = str(date_path)
    validate_slices.REGIME_STRATIFIED_DIAGNOSTICS_PATH = str(regime_path)
    try:
        yield {
            "discovered": discovered_path,
            "validated": validated_path,
            "leaderboard": leaderboard_path,
            "date": date_path,
            "regime": regime_path,
        }
    finally:
        discover_slices.DISCOVERED_SLICES_PATH = state["discover"]
        validate_slices.DISCOVERED_SLICES_PATH = state["discovered"]
        validate_slices.VALIDATED_SLICES_PATH = state["validated"]
        validate_slices.CANDIDATE_LEADERBOARD_PATH = state["leaderboard"]
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
) -> tuple[pd.DataFrame, dict]:
    output_dir = Path(output_dir)
    regime_registry_path = output_dir / "candidate_registry_futures_regime.csv"
    regime_counts_path = output_dir / "regime_counts_futures.csv"
    regime_matrix_path = output_dir / "regime_candidate_matrix_futures.csv"

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
            pd.DataFrame().to_csv(output_dir / f"candidate_leaderboard_futures_{regime}.csv", index=False)
        return empty_registry, empty_summary

    key_cols = ["symbol", "timeframe", "slice_combination"]
    registry_cols = [c for c in ["candidate_key", "strict_gate_pass", "status", "live_decay_flag"] if c in registry.columns]
    base = leaderboard.merge(registry[key_cols + registry_cols], on=key_cols, how="left")
    base["strict_gate_pass"] = base.get("strict_gate_pass", False).fillna(False)

    diag = regime_diagnostics.copy()
    diag = diag[diag.get("diagnostic_status", "") == "ok"].copy()
    diag = diag[diag["regime"].isin(["all", "bull", "bear", "neutral"])]
    if diag.empty:
        empty_registry.to_csv(regime_registry_path, index=False)
        pd.DataFrame().to_csv(regime_counts_path, index=False)
        pd.DataFrame().to_csv(regime_matrix_path, index=False)
        for regime in ("bull", "bear", "neutral"):
            pd.DataFrame().to_csv(output_dir / f"candidate_leaderboard_futures_{regime}.csv", index=False)
        return empty_registry, empty_summary

    all_rows = diag[diag["regime"] == "all"][key_cols + ["slice_mean_ret_costadj"]].rename(
        columns={"slice_mean_ret_costadj": "all_regime_mean_ret_costadj"}
    )

    per_regime_frames = {}
    for regime in ("bull", "bear", "neutral"):
        frame = diag[diag["regime"] == regime].copy()
        if frame.empty:
            frame = pd.DataFrame()
            frame.to_csv(output_dir / f"candidate_leaderboard_futures_{regime}.csv", index=False)
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
        frame.to_csv(output_dir / f"candidate_leaderboard_futures_{regime}.csv", index=False)
        per_regime_frames[regime] = frame

    registry_rows = []
    for _, base_row in base.iterrows():
        statuses = {}
        regime_rows = []
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


def _load_existing_futures_artifacts(output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    output_dir = Path(output_dir)
    discovered_candidates = [
        output_dir / "discovered_slices_futures_rolling.csv",
        output_dir / "discovered_slices_merged.csv",
    ]
    leaderboard_candidates = [
        output_dir / "candidate_leaderboard_futures_rolling.csv",
        output_dir / "candidate_leaderboard_merged.csv",
    ]
    registry_candidates = [
        output_dir / "candidate_registry_futures_rolling.csv",
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
            "regime-only futures run requires existing artifacts: " + ", ".join(missing)
        )

    def _read(path: Path) -> pd.DataFrame:
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    return _read(discovered_path), _read(leaderboard_path), _read(registry_path)


def build_monitored_candidates(
    regime_registry: pd.DataFrame,
    leaderboard: pd.DataFrame,
    output_dir: Path,
    max_candidates: int = DEFAULT_MAX_MONITORED_CANDIDATES,
    max_per_symbol: int = DEFAULT_MAX_MONITORED_PER_SYMBOL,
) -> tuple[pd.DataFrame, dict]:
    output_dir = Path(output_dir)
    out_path = output_dir / "monitored_candidates_futures.csv"
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


def run_futures_research(
    symbols: list[str] | None = None,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    condition_symbols: tuple[str, ...] = (),
    min_samples: int = 15,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    top_n_diagnostics: int = 15,
    regime_only: bool = False,
    max_monitored_candidates: int = DEFAULT_MAX_MONITORED_CANDIDATES,
    max_monitored_per_symbol: int = DEFAULT_MAX_MONITORED_PER_SYMBOL,
) -> dict:
    output_dir = _resolve_effective_output_dir(Path(output_dir), timeframes, regime_only)
    output_dir.mkdir(parents=True, exist_ok=True)

    target_symbols = _normalize_symbols(symbols or get_research_futures_symbols())
    if not target_symbols:
        raise ValueError("No futures symbols resolved for research run.")
    conds = _normalize_symbols(condition_symbols)

    with isolated_research_paths(output_dir) as paths:
        if not regime_only:
            for path in paths.values():
                if path.exists():
                    path.unlink()

            for timeframe in timeframes:
                discover_slices.run_discovery(
                    target_symbols=target_symbols,
                    timeframe=timeframe,
                    min_samples=min_samples,
                    append=paths["discovered"].exists(),
                    cond_symbols=conds or None,
                    bin_mode=DEFAULT_BIN_MODE,
                    profile="futures",
                )

            if not paths["discovered"].exists():
                discovered = pd.DataFrame()
                leaderboard = pd.DataFrame()
                registry = pd.DataFrame()
            else:
                try:
                    discovered = pd.read_csv(paths["discovered"])
                except pd.errors.EmptyDataError:
                    discovered = pd.DataFrame()

                if discovered.empty:
                    leaderboard = pd.DataFrame()
                    registry = pd.DataFrame()
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
                        output_path=output_dir / "candidate_registry_futures_rolling.csv",
                        enable_auto_promotion=False,
                    )
        else:
            discovered, leaderboard, registry = _load_existing_futures_artifacts(output_dir)

        regime_registry = pd.DataFrame()
        regime_summary = None
        monitored_candidates = pd.DataFrame()
        monitored_summary = None
        if not leaderboard.empty:
            regime_targets = _leaderboard_targets(leaderboard)
            validate_slices.run_date_range_diagnostics(
                slices_path=str(paths["discovered"]),
                output_path=str(paths["date"]),
                diagnostic_scope="leaderboard-top",
                top_n=top_n_diagnostics,
                bin_mode=DEFAULT_BIN_MODE,
                targets=regime_targets,
            )
            regime_diagnostics = validate_slices.run_regime_stratified_diagnostics(
                slices_path=str(paths["discovered"]),
                output_path=str(paths["regime"]),
                diagnostic_scope="leaderboard-top",
                top_n=top_n_diagnostics,
                bin_mode=DEFAULT_BIN_MODE,
                targets=regime_targets,
            )
            regime_registry, regime_summary = build_regime_outputs(
                leaderboard,
                registry,
                regime_diagnostics,
                output_dir=output_dir,
                min_samples=min_samples,
            )
            monitored_candidates, monitored_summary = build_monitored_candidates(
                regime_registry,
                leaderboard,
                output_dir=output_dir,
                max_candidates=max_monitored_candidates,
                max_per_symbol=max_monitored_per_symbol,
            )

        status_counts = registry["status"].value_counts().to_dict() if not registry.empty and "status" in registry.columns else {}
        summary = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "bin_mode": DEFAULT_BIN_MODE,
            "symbols": target_symbols,
            "symbol_count": len(target_symbols),
            "condition_symbols": conds,
            "discovered_rows": int(len(discovered)),
            "leaderboard_rows": int(len(leaderboard)),
            "registry_rows": int(len(registry)),
            "strict_gate_pass_count": int(registry["strict_gate_pass"].sum()) if not registry.empty and "strict_gate_pass" in registry.columns else 0,
            "status_counts": status_counts,
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
        (output_dir / "futures_research_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated futures-only research.")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        choices=["1d", "1h", "15m"],
    )
    parser.add_argument("--condition-on", nargs="+", default=[])
    parser.add_argument("--min-samples", type=int, default=15)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-n-diagnostics", type=int, default=15)
    parser.add_argument("--regime-only", action="store_true", help="Reuse existing futures artifacts and rerun only the regime-aware phase.")
    parser.add_argument("--max-monitored-candidates", type=int, default=DEFAULT_MAX_MONITORED_CANDIDATES)
    parser.add_argument("--max-monitored-per-symbol", type=int, default=DEFAULT_MAX_MONITORED_PER_SYMBOL)
    args = parser.parse_args()

    run_futures_research(
        symbols=args.symbols,
        timeframes=tuple(args.timeframes),
        condition_symbols=tuple(args.condition_on),
        min_samples=args.min_samples,
        output_dir=args.output_dir,
        top_n_diagnostics=args.top_n_diagnostics,
        regime_only=args.regime_only,
        max_monitored_candidates=args.max_monitored_candidates,
        max_monitored_per_symbol=args.max_monitored_per_symbol,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
