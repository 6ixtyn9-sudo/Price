"""Sync monitored_slices.csv and execution edge metrics from ALL available
timeframe-specific merged registries.

The monitored set is rebuilt FROM SCRATCH on every run using only
candidate_registry.csv paper_proposal rows. The companion
monitored_edge_metrics.csv is rebuilt from the union of all merged
leaderboards so conviction sizing cannot silently fall back to
equal-notional after a research refresh.

CRITICAL: merged artifacts now live in timeframe-specific subdirectories
so a 1h merge cannot bulldoze a 1d merge that committed first. This
script discovers all available registries under merged/*/ and unions
them before building the book.
"""
import json
import os
import sys
from pathlib import Path
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

MERGED_ROOT = Path("localdata/research/merged")
REGISTRY = MERGED_ROOT / "candidate_registry.csv"       # legacy fallback
LEADERBOARD = MERGED_ROOT / "candidate_leaderboard_merged.csv"  # legacy
MONITORED = Path("localdata/monitored_slices.csv")
EDGE_METRICS = Path("localdata/monitored_edge_metrics.csv")
BOOK_LIFECYCLE = Path("localdata/research/monitored_book_lifecycle.json")

# ── Multi-timeframe artifact discovery ──────────────────────────────
TIMEFRAME_SUBDIRS = [MERGED_ROOT / d for d in ("1d", "1h", "15m")]


def _discover_registry_paths() -> list[Path]:
    """Return all available candidate_registry.csv paths, preferring
    timeframe-specific subdirectories over the legacy root."""
    paths = []
    for subdir in TIMEFRAME_SUBDIRS:
        candidate = subdir / "candidate_registry.csv"
        if candidate.exists():
            paths.append(candidate)
    if not paths and REGISTRY.exists():
        paths.append(REGISTRY)
    return paths


def _discover_leaderboard_paths() -> list[Path]:
    """Return all available leaderboard paths, preferring
    timeframe-specific subdirectories over the legacy root."""
    paths = []
    for subdir in TIMEFRAME_SUBDIRS:
        candidate = subdir / "candidate_leaderboard_merged.csv"
        if candidate.exists():
            paths.append(candidate)
    if not paths and LEADERBOARD.exists():
        paths.append(LEADERBOARD)
    return paths


def _load_union_registry() -> pd.DataFrame:
    """Load and union all available candidate registries, deduplicating
    by candiate key and keeping the first occurrence."""
    path_list = _discover_registry_paths()
    if not path_list:
        return pd.DataFrame()
    frames = []
    for path in path_list:
        try:
            frames.append(pd.read_csv(path))
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    if "candidate_key" in result.columns:
        result = result.drop_duplicates(subset=["candidate_key"], keep="first")
    print(f"sync_monitored: unioned {len(path_list)} registr(y|ies) → {len(result)} rows")
    return result


def _load_union_leaderboard() -> pd.DataFrame:
    """Load and union all available merged leaderboards."""
    path_list = _discover_leaderboard_paths()
    if not path_list:
        return pd.DataFrame()
    frames = []
    for path in path_list:
        try:
            frames.append(pd.read_csv(path))
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    key_cols = ["symbol", "timeframe", "slice_combination"]
    present = [c for c in key_cols if c in result.columns]
    if present:
        result = result.drop_duplicates(subset=present, keep="first")
    print(f"sync_monitored: unioned {len(path_list)} leaderboard(s) → {len(result)} rows")
    return result


def _normalise_identity(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if "symbol" in frame.columns:
        frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    if "timeframe" in frame.columns:
        frame["timeframe"] = frame["timeframe"].astype(str).str.strip()
    if "slice_combination" in frame.columns:
        frame["slice_combination"] = frame["slice_combination"].astype(str).str.strip()
    if "side" not in frame.columns:
        frame["side"] = "long"
    frame["side"] = frame["side"].fillna("long").astype(str).str.strip().str.lower()
    if "bin_mode" not in frame.columns:
        frame["bin_mode"] = "insample"
    frame["bin_mode"] = frame["bin_mode"].fillna("insample").astype(str).str.strip().str.lower()
    frame.loc[~frame["bin_mode"].isin({"insample", "rolling"}), "bin_mode"] = "insample"
    return frame


def _sync_edge_metrics(monitored: pd.DataFrame) -> int:
    if monitored.empty:
        pd.DataFrame().to_csv(EDGE_METRICS, index=False)
        print("sync_monitored: monitored set empty; edge metrics empty")
        return 0

    source = _load_union_leaderboard()
    if source.empty:
        pd.DataFrame().to_csv(EDGE_METRICS, index=False)
        print("sync_monitored: no leaderboard metrics available; edge metrics empty")
        return 0

    try:
        from research_lifecycle import normalize_walk_forward_patterns
        source = normalize_walk_forward_patterns(_normalise_identity(source))
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        pd.DataFrame().to_csv(EDGE_METRICS, index=False)
        print("sync_monitored: leaderboard unreadable; edge metrics empty")
        return 0

    key_cols = ["symbol", "timeframe", "slice_combination", "side", "bin_mode"]
    source = source.drop_duplicates(subset=key_cols, keep="first")
    lookup = source.set_index(key_cols, drop=False)

    rows = []
    missing = []
    for _, monitored_row in monitored.iterrows():
        key = tuple(monitored_row[col] for col in key_cols)
        if key not in lookup.index:
            missing.append("|".join(map(str, key)))
            continue
        rows.append(lookup.loc[key].to_dict())

    metrics = pd.DataFrame(rows)
    metrics.to_csv(EDGE_METRICS, index=False)
    if missing:
        print(
            f"sync_monitored: {len(missing)} monitored candidates have no exact "
            f"edge row in the union leaderboard; those candidates will use sizing fallback"
        )
    print(f"sync_monitored: wrote {len(metrics)} execution edge metric rows")
    return len(metrics)


def main() -> int:
    reg = _load_union_registry()
    if reg.empty:
        print("sync_monitored: no candidate registry found; nothing to sync")
        return 0

    baseline_path = BOOK_LIFECYCLE.parent / ".monitored_book_before.csv"
    if baseline_path.exists():
        before_frame = pd.read_csv(baseline_path)
        baseline_path.unlink(missing_ok=True)
    else:
        before_frame = pd.read_csv(MONITORED) if MONITORED.exists() else pd.DataFrame()
    before = len(before_frame)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        empty = Path(tmp.name)
    pd.DataFrame(columns=["symbol", "timeframe", "slice_combination", "side", "source_note", "bin_mode"]).to_csv(
        empty, index=False
    )

    try:
        sys.path.insert(0, "scripts")
        from research_lifecycle import apply_registry_to_monitored

        result = apply_registry_to_monitored(reg, monitored_path=empty, promote_proposals=True)
    finally:
        empty.unlink(missing_ok=True)

    result = _normalise_identity(result)
    # Ensure exit_horizon is present for every slice.  Slices promoted
    # from the registry carry it; legacy slices that survived through
    # apply_registry_to_monitored's "preserve rows absent from registry"
    # path default to 5 (the fwd_ret_5 validation horizon — faithful
    # to the original measured edge).
    if "exit_horizon" not in result.columns:
        result["exit_horizon"] = 5
    result["exit_horizon"] = result["exit_horizon"].fillna(5).astype(int)
    after = len(result)
    print(f"sync_monitored: {before} -> {after} ({after - before:+d})")
    result.to_csv(MONITORED, index=False)

    from research_lifecycle import build_monitored_book_audit
    lifecycle = build_monitored_book_audit(
        before_frame,
        result,
        registry=reg,
        discovery_run_id=os.getenv("DISCOVERY_RUN_ID") or None,
    )
    BOOK_LIFECYCLE.parent.mkdir(parents=True, exist_ok=True)
    BOOK_LIFECYCLE.write_text(json.dumps(lifecycle, indent=2) + "\n")
    print(
        "sync_monitored: lifecycle "
        f"added={len(lifecycle['added_candidates'])} "
        f"removed={len(lifecycle['removed_candidates'])} "
        f"retained={len(lifecycle['retained_candidates'])}"
    )
    _sync_edge_metrics(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
