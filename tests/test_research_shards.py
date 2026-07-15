"""Tests for research shard planning and complete-result merging."""

import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from research_merge import merge_shards  # noqa: E402
import research_shard  # noqa: E402
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


def test_run_shard_passes_profile_to_discovery(monkeypatch, tmp_path: Path):
    called = {}

    fake_discover = types.SimpleNamespace(DISCOVERED_SLICES_PATH="")
    fake_validate = types.SimpleNamespace(
        DISCOVERED_SLICES_PATH="",
        VALIDATED_SLICES_PATH="",
        CANDIDATE_LEADERBOARD_PATH="",
    )

    def fake_run_discovery(**kwargs):
        called.update(kwargs)
        Path(fake_discover.DISCOVERED_SLICES_PATH).write_text(
            "symbol,timeframe,slice_combination\nBTC/USD,1h,a\n"
        )

    def fake_run_candidate_leaderboard(**kwargs):
        Path(kwargs["output_path"]).write_text(
            "symbol,timeframe,slice_combination\nBTC/USD,1h,a\n"
        )

    fake_discover.run_discovery = fake_run_discovery
    fake_validate.run_candidate_leaderboard = fake_run_candidate_leaderboard

    monkeypatch.setitem(sys.modules, "discover_slices", fake_discover)
    monkeypatch.setitem(sys.modules, "validate_slices", fake_validate)

    manifest = research_shard.run_shard(
        symbols=["BTC/USD"],
        timeframe="1h",
        shard_id="crypto-1h-00",
        output_dir=tmp_path,
        condition_symbols=("BTC/USD", "ETH/USD"),
        bin_mode="rolling",
        profile="crypto",
    )

    assert manifest["status"] == "success"
    assert called["profile"] == "crypto"
