"""Merge isolated research shards only after complete success."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from research_lifecycle import build_registry


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text())


def discover_manifests(shard_root: Path) -> list[tuple[Path, dict]]:
    result = []
    for path in sorted(Path(shard_root).rglob("manifest.json")):
        result.append((path.parent, _read_manifest(path)))
    return result


def merge_shards(
    shard_root: Path,
    expected_shard_ids: set[str],
    output_dir: Path,
) -> dict:
    manifests = discover_manifests(shard_root)
    found_ids = {str(manifest.get("shard_id")) for _, manifest in manifests}
    missing = sorted(expected_shard_ids - found_ids)
    failed = sorted(
        str(manifest.get("shard_id"))
        for _, manifest in manifests
        if manifest.get("status") != "success"
    )
    duplicate_ids = sorted(
        shard_id for shard_id in found_ids
        if sum(1 for _, manifest in manifests if str(manifest.get("shard_id")) == shard_id) > 1
    )
    if missing or failed or duplicate_ids:
        raise RuntimeError(json.dumps({
            "missing_shards": missing,
            "failed_shards": failed,
            "duplicate_shards": duplicate_ids,
        }, indent=2))

    output_dir.mkdir(parents=True, exist_ok=True)
    discovered_frames = []
    leaderboard_frames = []
    for shard_dir, manifest in manifests:
        discovered_path = shard_dir / "discovered_slices.csv"
        leaderboard_path = shard_dir / "candidate_leaderboard.csv"
        for path, frames in ((discovered_path, discovered_frames), (leaderboard_path, leaderboard_frames)):
            if not path.exists():
                continue
            try:
                frame = pd.read_csv(path)
            except pd.errors.EmptyDataError:
                continue
            if not frame.empty:
                frame["research_shard_id"] = manifest["shard_id"]
                frames.append(frame)

    discovered = pd.concat(discovered_frames, ignore_index=True) if discovered_frames else pd.DataFrame()
    leaderboard = pd.concat(leaderboard_frames, ignore_index=True) if leaderboard_frames else pd.DataFrame()
    if not discovered.empty:
        discovered = discovered.drop_duplicates(
            subset=[c for c in ["symbol", "timeframe", "slice_combination", "side", "bin_mode"] if c in discovered.columns]
        )
    if not leaderboard.empty:
        leaderboard = leaderboard.drop_duplicates(
            subset=[c for c in ["symbol", "timeframe", "slice_combination", "side", "bin_mode"] if c in leaderboard.columns]
        )
        # Shard-local correction is not sufficient. Recompute the multiple-
        # testing family across the complete merged hypothesis set before any
        # lifecycle classification can read search_wide_bh_pass.
        if "valid_p_value_nw" in leaderboard.columns:
            from validate_slices import annotate_search_wide_significance
            leaderboard = annotate_search_wide_significance(leaderboard)

    discovered_path = output_dir / "discovered_slices_merged.csv"
    leaderboard_path = output_dir / "candidate_leaderboard_merged.csv"
    discovered.to_csv(discovered_path, index=False)
    leaderboard.to_csv(leaderboard_path, index=False)

    registry_path = output_dir / "candidate_registry.csv"
    if not leaderboard.empty:
        build_registry(leaderboard_path, output_path=registry_path)
    else:
        pd.DataFrame().to_csv(registry_path, index=False)

    manifest = {
        "status": "success",
        "merged_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_shard_count": len(expected_shard_ids),
        "merged_shard_count": len(manifests),
        "discovered_rows": len(discovered),
        "leaderboard_rows": len(leaderboard),
        "automatic_promotion_applied": False,
        "monitored_slices_modified": False,
    }
    (output_dir / "merge_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge complete research shards.")
    parser.add_argument("--shard-root", type=Path, required=True)
    parser.add_argument("--expected-shards", type=Path, required=True,
                        help="JSON file containing {\"shard_ids\": [...]}.")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    expected = set(json.loads(args.expected_shards.read_text())["shard_ids"])
    result = merge_shards(args.shard_root, expected, args.output_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
