"""Sync monitored_slices.csv and execution edge metrics from the latest registry.

The monitored set is rebuilt FROM SCRATCH on every run using only
candidate_registry.csv paper_proposal rows. The companion
monitored_edge_metrics.csv is rebuilt from the exact merged leaderboard so
conviction sizing cannot silently fall back to equal-notional after a research
refresh.
"""
import sys
from pathlib import Path
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

REGISTRY = Path("localdata/research/merged/candidate_registry.csv")
LEADERBOARD = Path("localdata/research/merged/candidate_leaderboard_merged.csv")
MONITORED = Path("localdata/monitored_slices.csv")
EDGE_METRICS = Path("localdata/monitored_edge_metrics.csv")


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
    """Write metrics for exactly the currently monitored identities.

    Prefer the merged leaderboard, which carries the full validation
    scorecard. Fall back to the registry only for a controlled degraded path;
    the registry now carries the fields sizing needs. Identity includes side
    and bin_mode so a rolling candidate cannot accidentally inherit insample
    metrics for the same slice text.
    """
    source_path = LEADERBOARD if LEADERBOARD.exists() else REGISTRY
    if not source_path.exists() or monitored.empty:
        pd.DataFrame().to_csv(EDGE_METRICS, index=False)
        print("sync_monitored: no leaderboard/registry metrics available; edge metrics empty")
        return 0

    try:
        from research_lifecycle import normalize_walk_forward_patterns
        source = normalize_walk_forward_patterns(
            _normalise_identity(pd.read_csv(source_path))
        )
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        pd.DataFrame().to_csv(EDGE_METRICS, index=False)
        print(f"sync_monitored: {source_path} is unreadable; edge metrics empty")
        return 0

    if source.empty:
        pd.DataFrame().to_csv(EDGE_METRICS, index=False)
        print("sync_monitored: metrics source empty; edge metrics empty")
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
            f"edge row in {source_path}; those candidates will use sizing fallback"
        )
    print(f"sync_monitored: wrote {len(metrics)} execution edge metric rows")
    return len(metrics)


def main() -> int:
    if not REGISTRY.exists():
        print("sync_monitored: no candidate registry found; nothing to sync")
        return 0

    reg = pd.read_csv(REGISTRY)
    before = len(pd.read_csv(MONITORED)) if MONITORED.exists() else 0

    # Start from an empty slate so only registry-qualified slices survive.
    # apply_registry_to_monitored preserves rows absent from the registry;
    # using an empty input prevents legacy candidates from getting a free pass.
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
    after = len(result)
    print(f"sync_monitored: {before} -> {after} ({after - before:+d})")
    result.to_csv(MONITORED, index=False)
    _sync_edge_metrics(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
