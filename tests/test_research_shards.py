"""Tests for research shard planning and complete-result merging."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from research_merge import merge_shards  # noqa: E402
from research_shard import split_symbols  # noqa: E402


def test_split_symbols_is_deterministic():
    assert split_symbols(["a", "b", "c", "d", "e"], batch_size=2) == [
        ["A", "B"], ["C", "D"], ["E"]
    ]


def _write_shard(root, shard_id, status="success"):
    path = root / shard_id
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({"shard_id": shard_id, "status": status}))
    pd.DataFrame([{
        "symbol": shard_id,
        "timeframe": "1d",
        "slice_combination": "state_ext=stretched_down",
        "side": "long",
        "bin_mode": "rolling",
        "valid_p_value_nw": 0.001,
    }]).to_csv(path / "candidate_leaderboard.csv", index=False)
    pd.DataFrame([{
        "symbol": shard_id,
        "timeframe": "1d",
        "slice_combination": "state_ext=stretched_down",
        "side": "long",
        "bin_mode": "rolling",
    }]).to_csv(path / "discovered_slices.csv", index=False)


def test_merge_requires_every_successful_shard(tmp_path):
    root = tmp_path / "shards"
    _write_shard(root, "1d-00")
    with pytest.raises(RuntimeError, match="missing_shards"):
        merge_shards(root, {"1d-00", "1d-01"}, tmp_path / "merged")


def test_merge_writes_registry_only_after_complete_shards(tmp_path):
    root = tmp_path / "shards"
    _write_shard(root, "1d-00")
    _write_shard(root, "1d-01")
    result = merge_shards(root, {"1d-00", "1d-01"}, tmp_path / "merged")
    assert result["merged_shard_count"] == 2
    assert result["leaderboard_rows"] == 2
    merged = pd.read_csv(tmp_path / "merged" / "candidate_leaderboard_merged.csv")
    assert set(merged["search_wide_family_size"]) == {2}
    assert (tmp_path / "merged" / "candidate_registry.csv").exists()
    manifest = json.loads((tmp_path / "merged" / "merge_manifest.json").read_text())
    assert manifest["monitored_slices_modified"] is False


def test_split_symbols_rejects_shell_metacharacters():
    with pytest.raises(ValueError, match="invalid research symbol"):
        split_symbols(["SPY", "BAD; echo pwned"], batch_size=2)
