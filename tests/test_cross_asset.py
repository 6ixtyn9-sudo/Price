import sys
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from price.discovery import align_cross_asset_states  # noqa: E402


def _ts(*args):
    return pd.Timestamp(*args, tz="UTC")


def test_align_cross_asset_states_is_backward_only():
    # Primary bars every day; conditioning symbol changes state on 2024-01-02.
    primary = pd.DataFrame(
        {
            "bar_ts_utc": [
                _ts("2024-01-01 15:00"),
                _ts("2024-01-02 15:00"),
                _ts("2024-01-03 15:00"),
            ],
            "fwd_ret_5": [0.01, 0.02, 0.03],
        }
    )
    # Conditioning states are stamped at 14:00 (before primary's 15:00 bar
    # same day) so a same-day primary bar sees that day's completed state.
    cond = pd.DataFrame(
        {
            "bar_ts_utc": [
                _ts("2024-01-01 14:00"),
                _ts("2024-01-02 14:00"),
                _ts("2024-01-03 14:00"),
            ],
            "state_slope": ["downtrend", "uptrend", "flat"],
        }
    )

    out = align_cross_asset_states(primary, cond, "USO", ["state_slope"])

    # New column is prefixed and row order preserved.
    assert "cross_USO_state_slope" in out.columns
    assert list(out["fwd_ret_5"]) == [0.01, 0.02, 0.03]

    # Each primary bar sees the most recent PRIOR-or-equal conditioning state.
    assert list(out["cross_USO_state_slope"]) == ["downtrend", "uptrend", "flat"]


def test_align_cross_asset_states_no_future_leak_when_cond_is_late():
    # Conditioning bar arrives AFTER the primary bar on the same calendar day.
    primary = pd.DataFrame(
        {
            "bar_ts_utc": [_ts("2024-01-02 13:00")],
            "fwd_ret_5": [0.05],
        }
    )
    cond = pd.DataFrame(
        {
            "bar_ts_utc": [
                _ts("2024-01-01 14:00"),  # prior day (valid)
                _ts("2024-01-02 14:00"),  # later than primary -> must NOT be used
            ],
            "state_slope": ["downtrend", "uptrend"],
        }
    )

    out = align_cross_asset_states(primary, cond, "USO", ["state_slope"])

    # Must use the prior-day state, never the same-day-but-later one.
    assert out["cross_USO_state_slope"].iloc[0] == "downtrend"


def test_align_cross_asset_states_nan_before_cond_history_starts():
    primary = pd.DataFrame(
        {
            "bar_ts_utc": [_ts("2023-12-31 15:00"), _ts("2024-01-02 15:00")],
            "fwd_ret_5": [0.01, 0.02],
        }
    )
    cond = pd.DataFrame(
        {
            "bar_ts_utc": [_ts("2024-01-02 14:00")],
            "state_slope": ["uptrend"],
        }
    )

    out = align_cross_asset_states(primary, cond, "USO", ["state_slope"])

    # First primary bar predates all conditioning history -> NaN.
    assert pd.isna(out["cross_USO_state_slope"].iloc[0])
    assert out["cross_USO_state_slope"].iloc[1] == "uptrend"
