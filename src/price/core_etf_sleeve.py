"""Low-DOF Core Macro/Sector ETF Pullback-in-Uptrend Sleeve.

Evaluates a small, fixed pre-specified family (`N = 6`) of daily dip/pullback
state templates across `CORE_LIQUID_ETFS` under ETF-realistic execution costs
(`4.0 bps` round-trip) rather than forcing broad-index/sector ETFs to clear a
232k-trial single-stock data-mining hurdle (`SR_0 ~ 0.91`) and `13 bps` stock
execution drag.

Selected rows are tagged with `source_note = "core_etf_sleeve_v1"` in
`monitored_slices.csv` so `paper_trade.py` can grant targeted `long_exempt_slices`
entries on `1d` without reopening mined single-stock `1d` slices, while keeping
all risk caps (`max_per_group`, `max_per_sector`, `shadow_gate`, tail watchdog)
active.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from price.cost_model import core_etf_cost_model
from price.roi_guards import normalize_exempt_slice_key
from price.universe import is_core_liquid_etf
from price.validation import expected_max_sharpe, probabilistic_sharpe

CORE_ETF_SLEEVE_SOURCE_NOTE = "core_etf_sleeve_v1"

# Pre-specified N=6 daily pullback / capitulation templates on liquid index,
# GICS sector, duration, credit, and commodity ETFs. Keep this tuple fixed so
# family size N stays low-DOF (`SR_0` reflects 6 templates, not 232k trials).
CORE_ETF_TEMPLATES: tuple[str, ...] = (
    "state_ext=neutral + state_slope=uptrend",
    "state_ext=stretched_down + state_slope=uptrend",
    "state_ext=stretched_down + state_vol=low_vol",
    "state_ext=stretched_down + state_vol=mid_vol",
    "state_volume=vol_surge + state_ext=stretched_down",
    "state_ext=stretched_down + state_ret_5=ret_down",
)

# Leaderboard rows in research_shard default to cost_bps=1.0 per leg (2.0 bps RT).
_LEADERBOARD_DEFAULT_RT_DRAG = 0.0002


def _truthy(value) -> bool:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes"}


def _row_valid_regimes_json(row) -> str:
    if isinstance(row.get("valid_regimes"), str) and row.get("valid_regimes").strip():
        return row["valid_regimes"].strip()
    regimes = []
    for label, col in (
        ("bull", "valid_in_bull"),
        ("neutral", "valid_in_neutral"),
        ("bear", "valid_in_bear"),
    ):
        if _truthy(row.get(col)):
            regimes.append(label)
    return json.dumps(regimes)


def select_core_etf_sleeve(
    leaderboard: pd.DataFrame,
    *,
    min_valid_n: int = 30,
    min_exit_horizon: int = 10,
    min_wf_survival: float = 0.50,
    min_excess_bh: float = 0.0020,
    min_sleeve_dsr: float = 0.50,
    include_diagnostics: bool = False,
) -> pd.DataFrame:
    """Select at most one validated 1d long pullback rule per Core Liquid ETF.

    Parameters
    ----------
    leaderboard : pd.DataFrame
        Merged candidate leaderboard (`candidate_leaderboard_merged.csv`).
    min_valid_n : int
        Minimum out-of-sample validation fire count (default 30).
    min_exit_horizon : int
        Minimum holding horizon in daily bars (default 10 -> selects 20d holds).
    min_wf_survival : float
        Minimum walk-forward fold survival fraction (default 0.50).
    min_excess_bh : float
        Minimum per-trade net excess return over buy-and-hold after full
        `4.0 bps` Core ETF round-trip drag (default +20 bps / trade).
    min_sleeve_dsr : float
        Minimum Deflated Sharpe Ratio under the `N = 6` template family.
    include_diagnostics : bool
        When True, retain `etf_net_mean`, `etf_excess_bh`, `valid_sharpe`,
        `valid_n`, `walk_forward_survival_rate`, and `sleeve_dsr` columns.
    """
    base_cols = [
        "symbol",
        "timeframe",
        "slice_combination",
        "side",
        "source_note",
        "bin_mode",
        "exit_horizon",
        "stop_atr_mult",
        "valid_regimes",
    ]
    if leaderboard is None or leaderboard.empty:
        return pd.DataFrame(columns=base_cols)

    df = leaderboard.copy()
    required = {"symbol", "timeframe", "slice_combination", "valid_mean_ret_costadj", "valid_n"}
    if not required.issubset(df.columns):
        return pd.DataFrame(columns=base_cols)

    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["timeframe"] = df["timeframe"].astype(str).str.strip()
    df["slice_combination"] = df["slice_combination"].astype(str).str.strip()
    df["side"] = df.get("side", pd.Series("long", index=df.index)).fillna("long").astype(str).str.strip().str.lower()
    df["bin_mode"] = (
        df.get("bin_mode", pd.Series("rolling", index=df.index))
        .fillna("rolling")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    pool = df[
        (df["timeframe"] == "1d")
        & (df["side"] == "long")
        & (df["bin_mode"] == "rolling")
        & (df["symbol"].map(is_core_liquid_etf))
        & (df["slice_combination"].isin(CORE_ETF_TEMPLATES))
    ].copy()
    if pool.empty:
        return pd.DataFrame(columns=base_cols)

    # Estimate cross-template Sharpe dispersion across sample-sufficient rows in
    # the low-DOF Core ETF pool.
    sr_series = pd.to_numeric(
        pool["valid_sharpe"] if "valid_sharpe" in pool.columns else pd.Series(np.nan, index=pool.index),
        errors="coerce",
    )
    n_series = pd.to_numeric(pool["valid_n"], errors="coerce")
    n_eff_series = (
        pd.to_numeric(pool["valid_n_eff"], errors="coerce").fillna(n_series)
        if "valid_n_eff" in pool.columns
        else n_series
    )
    stable_mask = sr_series.notna() & (n_series >= 15) & (n_eff_series >= 15)
    stable_sr = sr_series[stable_mask].to_numpy(dtype=float)
    sr_std = float(np.std(stable_sr, ddof=0)) if stable_sr.size > 1 else 0.15

    n_trials = len(CORE_ETF_TEMPLATES)
    sr0 = expected_max_sharpe(sr_std, float(n_trials))
    skew_series = (
        pd.to_numeric(pool["valid_skew"], errors="coerce").fillna(0.0)
        if "valid_skew" in pool.columns
        else pd.Series(0.0, index=pool.index)
    )
    kurt_series = (
        pd.to_numeric(pool["valid_kurtosis"], errors="coerce").fillna(3.0)
        if "valid_kurtosis" in pool.columns
        else pd.Series(3.0, index=pool.index)
    )

    dsrs: list[float] = []
    for sr_val, n_obs, sk, ku in zip(sr_series, n_eff_series, skew_series, kurt_series):
        if not np.isfinite(sr_val) or not np.isfinite(n_obs) or n_obs <= 1.0:
            dsrs.append(0.0)
            continue
        val = probabilistic_sharpe(
            float(sr_val),
            benchmark_sr=sr0,
            n_obs=float(n_obs),
            skewness=float(sk),
            kurtosis=float(ku),
        )
        dsrs.append(float(val) if np.isfinite(val) else 0.0)
    pool["sleeve_dsr"] = dsrs

    etf_rt_drag = core_etf_cost_model().round_trip_drag()  # 4.0 bps RT
    extra_drag_vs_lb = max(0.0, etf_rt_drag - _LEADERBOARD_DEFAULT_RT_DRAG)
    valid_mean = pd.to_numeric(pool["valid_mean_ret_costadj"], errors="coerce")
    pool["etf_net_mean"] = valid_mean - extra_drag_vs_lb

    if "valid_excess_vs_baseline" in pool.columns:
        raw_excess = pd.to_numeric(pool["valid_excess_vs_baseline"], errors="coerce")
    elif "valid_baseline_mean_ret_costadj" in pool.columns:
        raw_excess = valid_mean - pd.to_numeric(pool["valid_baseline_mean_ret_costadj"], errors="coerce")
    else:
        raw_excess = valid_mean
    # Charge full 4.0 bps tactical RT drag against zero-turnover buy-and-hold.
    pool["etf_excess_bh"] = raw_excess - etf_rt_drag

    if "exit_horizon" in pool.columns:
        pool["exit_horizon"] = pd.to_numeric(pool["exit_horizon"], errors="coerce").fillna(5).astype(int)
    elif "best_fwd_horizon" in pool.columns:
        pool["exit_horizon"] = pd.to_numeric(pool["best_fwd_horizon"], errors="coerce").fillna(5).astype(int)
    elif "optimal_horizon" in pool.columns:
        pool["exit_horizon"] = pd.to_numeric(pool["optimal_horizon"], errors="coerce").fillna(5).astype(int)
    else:
        pool["exit_horizon"] = 5

    if "stop_atr_mult" not in pool.columns:
        if "best_stop_atr_mult" in pool.columns:
            pool["stop_atr_mult"] = pd.to_numeric(pool["best_stop_atr_mult"], errors="coerce")
        else:
            pool["stop_atr_mult"] = None

    wf_rate = (
        pd.to_numeric(pool["walk_forward_survival_rate"], errors="coerce").fillna(0.0)
        if "walk_forward_survival_rate" in pool.columns
        else pd.Series(0.0, index=pool.index)
    )
    verdict = pool.get("verdict", pd.Series("survived", index=pool.index)).astype(str).str.strip().str.lower()

    keep = (
        (verdict == "survived")
        & (n_series >= min_valid_n)
        & (pool["exit_horizon"] >= min_exit_horizon)
        & (wf_rate >= min_wf_survival)
        & (pool["etf_excess_bh"] >= min_excess_bh)
        & (pool["sleeve_dsr"] >= min_sleeve_dsr)
    )
    survivors = pool[keep].copy()
    if survivors.empty:
        return pd.DataFrame(columns=base_cols)

    survivors = survivors.sort_values(
        ["sleeve_dsr", "etf_excess_bh", "symbol"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    # Orthogonal sleeve rule: at most 1 template per Core Liquid ETF.
    survivors = survivors.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    survivors["source_note"] = CORE_ETF_SLEEVE_SOURCE_NOTE
    survivors["valid_regimes"] = [_row_valid_regimes_json(row) for _, row in survivors.iterrows()]

    if include_diagnostics:
        diag_cols = base_cols + [
            c
            for c in (
                "valid_n",
                "etf_net_mean",
                "etf_excess_bh",
                "valid_sharpe",
                "walk_forward_survival_rate",
                "sleeve_dsr",
            )
            if c in survivors.columns
        ]
        return survivors[diag_cols]
    return survivors[base_cols]


def core_etf_sleeve_exemptions_from_frame(monitored: pd.DataFrame) -> frozenset[str]:
    """Build normalized 4-part `long_exempt_slices` keys for monitored rows
    belonging to `CORE_ETF_SLEEVE_SOURCE_NOTE`."""
    if monitored is None or monitored.empty:
        return frozenset()
    required = {"symbol", "timeframe", "slice_combination", "source_note"}
    if not required.issubset(monitored.columns):
        return frozenset()

    keys: set[str] = set()
    for _, row in monitored.iterrows():
        note = str(row.get("source_note", "") or "").strip()
        if note != CORE_ETF_SLEEVE_SOURCE_NOTE:
            continue
        sym = str(row.get("symbol", "") or "").strip().upper()
        tf = str(row.get("timeframe", "") or "").strip()
        side = str(row.get("side", "long") or "long").strip().lower()
        combo = str(row.get("slice_combination", "") or "").strip()
        bin_mode = str(row.get("bin_mode", "rolling") or "rolling").strip().lower()
        if not sym or tf != "1d" or side != "long" or not combo or not is_core_liquid_etf(sym):
            continue
        keys.add(normalize_exempt_slice_key(f"{sym}|{tf}|{bin_mode}|{combo}"))
    return frozenset(keys)


def load_core_etf_sleeve_exemptions(
    monitored_path: Path | str = Path("localdata/monitored_slices.csv"),
) -> frozenset[str]:
    """Load `long_exempt_slices` keys for active Core ETF sleeve rows on disk."""
    path = Path(monitored_path)
    if not path.exists():
        return frozenset()
    try:
        df = pd.read_csv(path)
    except Exception:
        return frozenset()
    return core_etf_sleeve_exemptions_from_frame(df)


def merge_long_exempt_slices(
    cli_exemptions: Iterable[str],
    *,
    lane: str = "equity",
    monitored_path: Path | str = Path("localdata/monitored_slices.csv"),
    allow_core_etf_sleeve: bool = True,
) -> frozenset[str]:
    """Combine explicit CLI `--allow-long-slice` entries with active Core ETF
    sleeve exemptions on the equity lane."""
    manual = {
        normalize_exempt_slice_key(s)
        for s in (cli_exemptions or ())
        if str(s).strip()
    }
    if str(lane or "equity").strip().lower() == "equity" and allow_core_etf_sleeve:
        manual |= set(load_core_etf_sleeve_exemptions(monitored_path))
    return frozenset(manual)
