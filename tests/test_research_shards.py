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
    leaderboard_called = {}

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
        leaderboard_called.update(kwargs)
        Path(kwargs["output_path"]).write_text(
            "symbol,timeframe,slice_combination\nBTC/USD,1h,a\n"
        )

    fake_discover.run_discovery = fake_run_discovery
    fake_validate.run_candidate_leaderboard = fake_run_candidate_leaderboard

    monkeypatch.setitem(sys.modules, "discover_slices", fake_discover)
    monkeypatch.setitem(sys.modules, "validate_slices", fake_validate)

    manifest = research_shard.run_shard(
        symbols=["BTC/USD"],
        timeframe="15m",
        shard_id="crypto-15m-00",
        output_dir=tmp_path,
        condition_symbols=("BTC/USD", "ETH/USD"),
        bin_mode="rolling",
        profile="crypto",
        min_samples=30,
    )

    assert manifest["status"] == "success"
    assert called["profile"] == "crypto"
    assert called["min_samples"] == 30
    assert leaderboard_called["min_samples"] == 30


def test_merge_shards_propagates_min_samples_for_15m_dsr_dispersion(tmp_path):
    root = tmp_path / "shards"
    for idx, (sym, sr, vn) in enumerate([("A", 0.65, 35), ("B", 0.10, 35), ("OUTLIER", 99.0, 20)]):
        sid = f"15m-0{idx}"
        p = root / sid
        p.mkdir(parents=True)
        (p / "manifest.json").write_text(
            json.dumps({"shard_id": sid, "status": "success", "timeframe": "15m", "min_samples": 30})
        )
        pd.DataFrame([{
            "symbol": sym,
            "timeframe": "15m",
            "slice_combination": "state_ext=stretched_down",
            "side": "long",
            "bin_mode": "rolling",
            "valid_p_value_nw": 0.001,
            "valid_sharpe": sr,
            "valid_n": vn,
            "valid_n_eff": float(vn),
            "valid_skew": 0.0,
            "valid_kurtosis": 3.0,
        }]).to_csv(p / "candidate_leaderboard.csv", index=False)
        pd.DataFrame([{
            "symbol": sym,
            "timeframe": "15m",
            "slice_combination": "state_ext=stretched_down",
            "side": "long",
            "bin_mode": "rolling",
        }]).to_csv(p / "discovered_slices.csv", index=False)

    merge_shards(root, {"15m-00", "15m-01", "15m-02"}, tmp_path / "merged")
    merged = pd.read_csv(tmp_path / "merged" / "candidate_leaderboard_merged.csv")
    row_a = merged[merged["symbol"] == "A"].iloc[0]
    # With min_samples=30 propagated, the valid_n=20 outlier (SR=99) is excluded
    # from V[SR], so A's DSR stays high instead of collapsing to ~0.
    assert bool(row_a["search_wide_dsr_pass"]) is True

