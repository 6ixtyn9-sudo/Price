"""Tests for the 2026-08-10 intraday gate relaxation."""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from research_lifecycle import _tradeable_candidate


def _row(tf, wf, scen, cross=False):
    combo = "cross_USO_state_x=y + state_ext=a" if cross else "state_ext=a + state_slope=b"
    return pd.Series({
        "triage_bucket": "late_emerging", "valid_n": 30,
        "walk_forward_pass_count": wf, "scenario_survived_count": scen,
        "valid_excess_vs_baseline": 0.005, "valid_excess_vs_best_parent": 0.003,
        "search_wide_bh_pass": True, "slice_combination": combo, "timeframe": tf,
    })


def test_15m_admits_low_evidence():
    assert _tradeable_candidate(_row("15m", 1, 1)) is True


def test_1h_admits_low_evidence():
    assert _tradeable_candidate(_row("1h", 1, 1)) is True


def test_15m_cross_admits_low_evidence():
    assert _tradeable_candidate(_row("15m", 1, 1, cross=True)) is True


def test_1d_rejects_low_evidence():
    assert _tradeable_candidate(_row("1d", 1, 1)) is False


def test_1d_standalone_accepts_wf2_scen3():
    assert _tradeable_candidate(_row("1d", 2, 3)) is True


def test_1d_cross_requires_wf2():
    assert _tradeable_candidate(_row("1d", 1, 3, cross=True)) is False
    assert _tradeable_candidate(_row("1d", 2, 3, cross=True)) is True
