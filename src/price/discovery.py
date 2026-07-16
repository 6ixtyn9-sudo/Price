import pandas as pd
import numpy as np
from typing import Dict, List

from price.warehouse import load_from_warehouse
from price.features import compute_price_features

# Canonical mapping from a raw ML feature name to its binned state_* field.
# This is the vocabulary the ML discovery path (ml_discovery.py) uses to
# translate raw feature interactions (e.g. "feat_ext_vs_ma_20 + feat_ret_3")
# into the state_*=value slice format the validation pipeline consumes.
# Every value here MUST be a column produced by bin_features().
ML_FEATURE_TO_STATE: Dict[str, str] = {
    "feat_ext_vs_ma_20": "state_ext",
    "feat_trend_slope_20": "state_slope",
    "feat_realized_vol_20": "state_vol",
    "feat_session_bucket": "state_session",
    "feat_dow": "state_dow",
    "feat_ret_1": "state_ret_1",
    "feat_ret_3": "state_ret_3",
    "feat_ret_5": "state_ret_5",
    "feat_ret_10": "state_ret_10",
    "feat_ret_20": "state_ret_20",
    "feat_atr_norm_ext": "state_atr_ext",
    "feat_vol_regime": "state_vol_regime",
    "feat_trend_strength_20": "state_trend_strength",
    "feat_gap": "state_gap",
    "feat_range_position": "state_range_pos",
    "feat_ret_day_equiv": "state_ret_day",
    "feat_realized_vol_day_equiv": "state_vol_day",
    "feat_volume_rel": "state_volume",
    # T2 crypto positioning
    "feat_funding_z20": "state_funding",
    "feat_oi_change_5": "state_oi",
    # T3 futures COT
    "feat_cot_mm_z52": "state_cot",
    # T4 equity macro context
    "feat_vix_ext": "state_vix",
    "feat_breadth_pct": "state_breadth",
    "feat_dxy_slope": "state_dxy",
}

# Ordered state labels (low -> high) for the ML feature -> state mapping.
# evaluate_interactions defines the promising region as the high-quantile
# (>= q75) side of each feature, so each ML feature maps to its HIGHEST state
# bucket here. All fields are ordinal, so "highest" is well defined. This dict
# MUST stay in sync with the label lists in bin_features(); test_state_labels
# guards against drift.
STATE_LABELS: Dict[str, tuple] = {
    "state_ext": ("stretched_down", "neutral", "stretched_up"),
    "state_slope": ("downtrend", "flat", "uptrend"),
    "state_vol": ("low_vol", "mid_vol", "high_vol"),
    "state_volume": ("vol_quiet", "vol_normal", "vol_surge"),
    "state_session": ("morning", "lunch", "afternoon"),
    "state_dow": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "state_ret_1": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_3": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_5": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_10": ("ret_down", "ret_flat", "ret_up"),
    "state_ret_20": ("ret_down", "ret_flat", "ret_up"),
    "state_atr_ext": ("atr_down", "atr_neutral", "atr_up"),
    "state_vol_regime": ("vol_regime_low", "vol_regime_mid", "vol_regime_high"),
    "state_trend_strength": ("weak_trend", "mod_trend", "strong_trend"),
    "state_gap": ("gap_down", "gap_flat", "gap_up"),
    "state_range_pos": ("range_low", "range_mid", "range_high"),
    "state_ret_day": ("ret_day_down", "ret_day_flat", "ret_day_up"),
    "state_vol_day": ("vol_day_low", "vol_day_mid", "vol_day_high"),
    "state_utc_session": ("utc_asia", "utc_europe", "utc_us"),
    "state_weekpart": ("weekday", "weekend"),
    # T2 crypto positioning states
    "state_funding": ("funding_short", "funding_neutral", "funding_long"),
    "state_oi": ("oi_collapsing", "oi_flat", "oi_building"),
    # T3 futures COT
    "state_cot": ("cot_short", "cot_neutral", "cot_long"),
    # T4 equity macro context
    "state_vix": ("vix_low", "vix_mid", "vix_high"),
    "state_breadth": ("breadth_weak", "breadth_mixed", "breadth_strong"),
    "state_dxy": ("dxy_weak", "dxy_flat", "dxy_strong"),
}


def _qcut_state(series: pd.Series, labels: List[str], fallback: str):
    """Bin a numeric feature into len(labels) equal-frequency state buckets.

    Mirrors the existing state_slope / state_vol binning style: tries a clean
    quantile cut and falls back to a constant bucket when the data is too
    sparse / degenerate to produce non-duplicate edges (e.g. tiny fixtures).
    """
    try:
        return pd.qcut(series, q=len(labels), labels=labels)
    except Exception:
        return fallback


def _expanding_qcut(series: pd.Series, labels: List[str], min_periods: int, fallback: str):
    """Time-respecting equal-frequency bins (look-ahead-free).

    For bar T, the quantile boundaries are computed using ONLY bars strictly
    before T (bars [0..T-1]), via an expanding window over the shift(1)'d
    series. This removes the two failure modes of the full-history pd.qcut
    used by bin_features():
      1. look-ahead bias -- a bar at T is no longer binned using future bars.
      2. in-sample fit -- the boundary at T is not fit on T's own value
         (T is a test point against its forward return; the boundary must
         exclude it).

    Bars before min_periods get NaN (dropped from evaluation -- they are early
    history). Falls back to `fallback` when the series is too short or all-NaN
    to produce stable boundaries. This is the single highest-value overfit-kill
    flagged by the HANDOVER's V5 methodological note.
    """
    n = len(labels)
    s = pd.Series(series).astype(float)
    if s.dropna().shape[0] < max(min_periods, n):
        return fallback
    # Shift by 1 so bar T's boundary uses only [0..T-1] (strictly excludes T).
    prior = s.shift(1)
    qs = [prior.expanding(min_periods=min_periods).quantile((i + 1) / n) for i in range(n - 1)]
    valid = s.notna()
    for q in qs:
        valid = valid & q.notna()
    result = pd.Series(np.nan, index=s.index, dtype=object)
    for i in range(n):
        lower = qs[i - 1] if i > 0 else pd.Series(-np.inf, index=s.index)
        upper = qs[i] if i < n - 1 else pd.Series(np.inf, index=s.index)
        mask = valid & (s >= lower) & (s < upper)
        result[mask] = labels[i]
    return result


# Default min_periods for rolling-bin state fields. 200 matches the monitor's
# lookback and is large enough that the expanding quantiles are stable on both
# daily (~1250 bars) and intraday (thousands) histories while only dropping
# early history that contributes little to validation anyway.
DEFAULT_ROLLING_MIN_PERIODS = 200


def bin_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df_binned = df.copy()

    def bin_ext(val):
        if pd.isna(val):
            return np.nan
        if val < -0.015:
            return "stretched_down"
        elif val > 0.015:
            return "stretched_up"
        else:
            return "neutral"

    if 'feat_ext_vs_ma_20' in df_binned.columns:
        df_binned['state_ext'] = df_binned['feat_ext_vs_ma_20'].apply(bin_ext)
    else:
        df_binned['state_ext'] = np.nan

    if 'feat_trend_slope_20' in df_binned.columns and not df_binned['feat_trend_slope_20'].dropna().empty:
        try:
            df_binned['state_slope'] = pd.qcut(
                df_binned['feat_trend_slope_20'],
                q=3,
                labels=["downtrend", "flat", "uptrend"]
            )
        except Exception:
            df_binned['state_slope'] = "flat"
    else:
        df_binned['state_slope'] = "flat"

    if 'feat_realized_vol_20' in df_binned.columns and not df_binned['feat_realized_vol_20'].dropna().empty:
        try:
            df_binned['state_vol'] = pd.qcut(
                df_binned['feat_realized_vol_20'],
                q=3,
                labels=["low_vol", "mid_vol", "high_vol"]
            )
        except Exception:
            df_binned['state_vol'] = "mid_vol"
    else:
        df_binned['state_vol'] = "mid_vol"

    # Traded-volume participation state ("who showed up on this bar"), from
    # the time-of-day-normalised relative volume computed in features.
    # state_vol is realized VOLATILITY -- a different signal.
    #
    # FIXED thresholds (<0.7x quiet, >1.5x surge), NOT quantiles, for two
    # reasons:
    #   1. true surge events are rare, which makes relative volume mass at
    #      ~1.0 with a sparse right tail -- quantile binning collapses on the
    #      duplicate edges and silently degrades every bar to "vol_normal";
    #   2. state_ext (the other fixed-prior state) is precisely what clears
    #      the search-wide BH/Bonferroni gate where quantile-fitted states
    #      keep failing; fixed thresholds are domain priors, not in-sample
    #      fits, so this state has no look-ahead to remove in rolling mode.
    if 'feat_volume_rel' in df_binned.columns and not df_binned['feat_volume_rel'].dropna().empty:
        def bin_volume(val):
            if pd.isna(val):
                return np.nan
            if val < 0.7:
                return "vol_quiet"
            if val > 1.5:
                return "vol_surge"
            return "vol_normal"
        df_binned['state_volume'] = df_binned['feat_volume_rel'].apply(bin_volume)
    else:
        df_binned['state_volume'] = "vol_normal"

    session_map = {0: "morning", 1: "lunch", 2: "afternoon"}
    if 'feat_session_bucket' in df_binned.columns:
        df_binned['state_session'] = df_binned['feat_session_bucket'].map(session_map)
    else:
        df_binned['state_session'] = np.nan

    dow_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    if 'feat_dow' in df_binned.columns:
        df_binned['state_dow'] = df_binned['feat_dow'].map(dow_map)
    else:
        df_binned['state_dow'] = np.nan

    utc_session_map = {0: "utc_asia", 1: "utc_europe", 2: "utc_us"}
    if 'feat_utc_session_bucket' in df_binned.columns:
        df_binned['state_utc_session'] = df_binned['feat_utc_session_bucket'].map(utc_session_map)
    else:
        df_binned['state_utc_session'] = np.nan

    weekpart_map = {0: "weekday", 1: "weekend"}
    if 'feat_weekpart' in df_binned.columns:
        df_binned['state_weekpart'] = df_binned['feat_weekpart'].map(weekpart_map)
    else:
        df_binned['state_weekpart'] = np.nan

    # Additional state bins used by the ML discovery path. These translate
    # the raw feat_* features LightGBM ranks highly (notably the return
    # features that dominate ML candidate interactions) into the same
    # state_*=value slice vocabulary the combinatorial discovery and
    # validation pipeline already speak. Purely additive: the combinatorial
    # discovery combinations never reference these columns, so existing
    # discovery/leaderboard behaviour is unchanged.
    for _period in [1, 3, 5, 10, 20]:
        _feat = f"feat_ret_{_period}"
        if _feat in df_binned.columns:
            df_binned[f"state_ret_{_period}"] = _qcut_state(
                df_binned[_feat], ["ret_down", "ret_flat", "ret_up"], "ret_flat"
            )
    if "feat_atr_norm_ext" in df_binned.columns:
        df_binned["state_atr_ext"] = _qcut_state(
            df_binned["feat_atr_norm_ext"], ["atr_down", "atr_neutral", "atr_up"], "atr_neutral"
        )
    if "feat_vol_regime" in df_binned.columns:
        df_binned["state_vol_regime"] = _qcut_state(
            df_binned["feat_vol_regime"], ["vol_regime_low", "vol_regime_mid", "vol_regime_high"],
            "vol_regime_mid",
        )
    if "feat_trend_strength_20" in df_binned.columns:
        df_binned["state_trend_strength"] = _qcut_state(
            df_binned["feat_trend_strength_20"], ["weak_trend", "mod_trend", "strong_trend"],
            "mod_trend",
        )
    if "feat_gap" in df_binned.columns:
        df_binned["state_gap"] = _qcut_state(
            df_binned["feat_gap"], ["gap_down", "gap_flat", "gap_up"], "gap_flat"
        )
    if "feat_range_position" in df_binned.columns:
        df_binned["state_range_pos"] = _qcut_state(
            df_binned["feat_range_position"], ["range_low", "range_mid", "range_high"], "range_mid"
        )
    if "feat_ret_day_equiv" in df_binned.columns:
        df_binned["state_ret_day"] = _qcut_state(
            df_binned["feat_ret_day_equiv"], ["ret_day_down", "ret_day_flat", "ret_day_up"], "ret_day_flat"
        )
    if "feat_realized_vol_day_equiv" in df_binned.columns:
        df_binned["state_vol_day"] = _qcut_state(
            df_binned["feat_realized_vol_day_equiv"], ["vol_day_low", "vol_day_mid", "vol_day_high"], "vol_day_mid"
        )

    # ── T2 crypto positioning states ─────────────────────────────────
    # state_funding: fixed thresholds on annualised funding z-score (<-1
    # shorts paying => long-squeeze pressure; >+1 longs paying => overheated).
    def _bin_funding(val):
        if pd.isna(val):
            return np.nan
        if val < -1.0:
            return "funding_short"
        if val > 1.0:
            return "funding_long"
        return "funding_neutral"
    if "feat_funding_z20" in df_binned.columns and not df_binned["feat_funding_z20"].dropna().empty:
        df_binned["state_funding"] = df_binned["feat_funding_z20"].apply(_bin_funding)
    else:
        df_binned["state_funding"] = "funding_neutral"
    # state_oi: OI change over 5 bars (short horizon) binned via fixed
    # thresholds because OI change has heavy tails that break qcut.
    def _bin_oi(val):
        if pd.isna(val):
            return np.nan
        if val < -0.03:
            return "oi_collapsing"
        if val > 0.03:
            return "oi_building"
        return "oi_flat"
    if "feat_oi_change_5" in df_binned.columns and not df_binned["feat_oi_change_5"].dropna().empty:
        df_binned["state_oi"] = df_binned["feat_oi_change_5"].apply(_bin_oi)
    else:
        df_binned["state_oi"] = "oi_flat"

    # ── T3 futures COT state ──────────────────────────────────────────
    # state_cot: fixed thresholds on 52-week z of managed-money net % of OI.
    def _bin_cot(val):
        if pd.isna(val):
            return np.nan
        if val < -1.0:
            return "cot_short"
        if val > 1.0:
            return "cot_long"
        return "cot_neutral"
    if "feat_cot_mm_z52" in df_binned.columns and not df_binned["feat_cot_mm_z52"].dropna().empty:
        df_binned["state_cot"] = df_binned["feat_cot_mm_z52"].apply(_bin_cot)
    else:
        df_binned["state_cot"] = "cot_neutral"

    # ── T4 equity macro-context states ────────────────────────────────
    # state_vix: VIX extension vs 20d MA (fixed thresholds; < -5% = calm,
    # > +20% = fear).
    def _bin_vix(val):
        if pd.isna(val):
            return np.nan
        if val < -0.05:
            return "vix_low"
        if val > 0.20:
            return "vix_high"
        return "vix_mid"
    if "feat_vix_ext" in df_binned.columns and not df_binned["feat_vix_ext"].dropna().empty:
        df_binned["state_vix"] = df_binned["feat_vix_ext"].apply(_bin_vix)
    else:
        df_binned["state_vix"] = "vix_mid"
    # state_breadth: % of breadth ETF universe above 20d MA (fixed
    # thresholds -- <40% weak, >70% strong).
    def _bin_breadth(val):
        if pd.isna(val):
            return np.nan
        if val < 0.40:
            return "breadth_weak"
        if val > 0.70:
            return "breadth_strong"
        return "breadth_mixed"
    if "feat_breadth_pct" in df_binned.columns and not df_binned["feat_breadth_pct"].dropna().empty:
        df_binned["state_breadth"] = df_binned["feat_breadth_pct"].apply(_bin_breadth)
    else:
        df_binned["state_breadth"] = "breadth_mixed"
    # state_dxy: dollar strength via 20d MA extension (fixed +/- 1%).
    def _bin_dxy(val):
        if pd.isna(val):
            return np.nan
        if val < -0.01:
            return "dxy_weak"
        if val > 0.01:
            return "dxy_strong"
        return "dxy_flat"
    if "feat_dxy_slope" in df_binned.columns and not df_binned["feat_dxy_slope"].dropna().empty:
        df_binned["state_dxy"] = df_binned["feat_dxy_slope"].apply(_bin_dxy)
    else:
        df_binned["state_dxy"] = "dxy_flat"

    # Ensure every state promised by STATE_LABELS / ML_FEATURE_TO_STATE exists even when the
    # source feat_* column was absent (e.g. synthetic fixtures). This guarantees
    # bin_features() always exposes the full state vocabulary the validation and ML
    # bridge expect, with a neutral fallback where data is missing.
    _fallback_state = {
        "state_ext": np.nan,
        "state_slope": "flat",
        "state_vol": "mid_vol",
        "state_volume": "vol_normal",
        "state_session": np.nan,
        "state_dow": np.nan,
        "state_utc_session": np.nan,
        "state_weekpart": np.nan,
        "state_ret_1": "ret_flat",
        "state_ret_3": "ret_flat",
        "state_ret_5": "ret_flat",
        "state_ret_10": "ret_flat",
        "state_ret_20": "ret_flat",
        "state_atr_ext": "atr_neutral",
        "state_vol_regime": "vol_regime_mid",
        "state_trend_strength": "mod_trend",
        "state_gap": "gap_flat",
        "state_range_pos": "range_mid",
        "state_ret_day": "ret_day_flat",
        "state_vol_day": "vol_day_mid",
        "state_funding": "funding_neutral",
        "state_oi": "oi_flat",
        "state_cot": "cot_neutral",
        "state_vix": "vix_mid",
        "state_breadth": "breadth_mixed",
        "state_dxy": "dxy_flat",
    }
    for _state_col, _fallback in _fallback_state.items():
        if _state_col not in df_binned.columns:
            df_binned[_state_col] = _fallback

    return df_binned


def bin_features_rolling(
    df: pd.DataFrame,
    min_periods: int = DEFAULT_ROLLING_MIN_PERIODS,
) -> pd.DataFrame:
    """Look-ahead-free variant of bin_features.

    Produces the SAME state_* columns and label vocabularies as bin_features,
    but every quantile-based field uses _expanding_qcut (boundary at bar T
    computed from bars strictly before T only) instead of full-history pd.qcut.

    What changes vs bin_features:
      - state_slope, state_vol, state_ret_{1,3,5,10,20}, state_atr_ext,
        state_vol_regime, state_trend_strength, state_gap, state_range_pos:
        now binned with a time-respecting expanding quantile.
      - state_ext: UNCHANGED. It already uses fixed +-0.015 thresholds (a
        fixed prior), so it has no look-ahead to remove. This is deliberate:
        state_ext is the one state field that combinatorial survivors
        repeatedly clear BH/Bonferroni on, and that is BECAUSE it is a fixed
        prior, not an in-sample quantile. Keeping it fixed preserves that
        property.
      - state_session, state_dow: UNCHANGED (categorical maps, not quantile).
      - The first ~min_periods rows get NaN state (dropped by label_eligible
        selection downstream).

    Rationale: the HANDOVER's V5 methodological note names this as the
    highest-value next improvement. The ML path's in-sample 75th-percentile
    cut and bin_features' full-history qcut are the two overfit sources that
    keep ML slices (and quantile-based combinatorial slices) failing the
    search-wide gate while fixed-prior state_ext slices sometimes pass.
    """
    if df.empty:
        return df

    df_binned = df.copy()

    # state_ext: fixed prior (unchanged from bin_features -- no look-ahead).
    def bin_ext(val):
        if pd.isna(val):
            return np.nan
        if val < -0.015:
            return "stretched_down"
        elif val > 0.015:
            return "stretched_up"
        else:
            return "neutral"

    if "feat_ext_vs_ma_20" in df_binned.columns:
        df_binned["state_ext"] = df_binned["feat_ext_vs_ma_20"].apply(bin_ext)
    else:
        df_binned["state_ext"] = np.nan

    if "feat_trend_slope_20" in df_binned.columns and not df_binned["feat_trend_slope_20"].dropna().empty:
        df_binned["state_slope"] = _expanding_qcut(
            df_binned["feat_trend_slope_20"],
            ["downtrend", "flat", "uptrend"], min_periods, "flat",
        )
    else:
        df_binned["state_slope"] = "flat"

    if "feat_realized_vol_20" in df_binned.columns and not df_binned["feat_realized_vol_20"].dropna().empty:
        df_binned["state_vol"] = _expanding_qcut(
            df_binned["feat_realized_vol_20"],
            ["low_vol", "mid_vol", "high_vol"], min_periods, "mid_vol",
        )
    else:
        df_binned["state_vol"] = "mid_vol"

    # state_volume: fixed prior, like state_ext -- identical thresholds and
    # rationale in bin_features (no look-ahead to remove), so rolling mode
    # reuses the same mapper rather than an expanding quantile.
    if "feat_volume_rel" in df_binned.columns and not df_binned["feat_volume_rel"].dropna().empty:
        def bin_volume_rolling(val):
            if pd.isna(val):
                return np.nan
            if val < 0.7:
                return "vol_quiet"
            if val > 1.5:
                return "vol_surge"
            return "vol_normal"
        df_binned["state_volume"] = df_binned["feat_volume_rel"].apply(bin_volume_rolling)
    else:
        df_binned["state_volume"] = "vol_normal"

    session_map = {0: "morning", 1: "lunch", 2: "afternoon"}
    if "feat_session_bucket" in df_binned.columns:
        df_binned["state_session"] = df_binned["feat_session_bucket"].map(session_map)
    else:
        df_binned["state_session"] = np.nan

    dow_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    if "feat_dow" in df_binned.columns:
        df_binned["state_dow"] = df_binned["feat_dow"].map(dow_map)
    else:
        df_binned["state_dow"] = np.nan

    utc_session_map = {0: "utc_asia", 1: "utc_europe", 2: "utc_us"}
    if "feat_utc_session_bucket" in df_binned.columns:
        df_binned["state_utc_session"] = df_binned["feat_utc_session_bucket"].map(utc_session_map)
    else:
        df_binned["state_utc_session"] = np.nan

    weekpart_map = {0: "weekday", 1: "weekend"}
    if "feat_weekpart" in df_binned.columns:
        df_binned["state_weekpart"] = df_binned["feat_weekpart"].map(weekpart_map)
    else:
        df_binned["state_weekpart"] = np.nan

    for _period in [1, 3, 5, 10, 20]:
        _feat = f"feat_ret_{_period}"
        if _feat in df_binned.columns:
            df_binned[f"state_ret_{_period}"] = _expanding_qcut(
                df_binned[_feat],
                ["ret_down", "ret_flat", "ret_up"], min_periods, "ret_flat",
            )
    if "feat_atr_norm_ext" in df_binned.columns:
        df_binned["state_atr_ext"] = _expanding_qcut(
            df_binned["feat_atr_norm_ext"],
            ["atr_down", "atr_neutral", "atr_up"], min_periods, "atr_neutral",
        )
    if "feat_vol_regime" in df_binned.columns:
        df_binned["state_vol_regime"] = _expanding_qcut(
            df_binned["feat_vol_regime"],
            ["vol_regime_low", "vol_regime_mid", "vol_regime_high"], min_periods, "vol_regime_mid",
        )
    if "feat_trend_strength_20" in df_binned.columns:
        df_binned["state_trend_strength"] = _expanding_qcut(
            df_binned["feat_trend_strength_20"],
            ["weak_trend", "mod_trend", "strong_trend"], min_periods, "mod_trend",
        )
    if "feat_gap" in df_binned.columns:
        df_binned["state_gap"] = _expanding_qcut(
            df_binned["feat_gap"],
            ["gap_down", "gap_flat", "gap_up"], min_periods, "gap_flat",
        )
    if "feat_range_position" in df_binned.columns:
        df_binned["state_range_pos"] = _expanding_qcut(
            df_binned["feat_range_position"],
            ["range_low", "range_mid", "range_high"], min_periods, "range_mid",
        )
    if "feat_ret_day_equiv" in df_binned.columns:
        df_binned["state_ret_day"] = _expanding_qcut(
            df_binned["feat_ret_day_equiv"],
            ["ret_day_down", "ret_day_flat", "ret_day_up"], min_periods, "ret_day_flat",
        )
    if "feat_realized_vol_day_equiv" in df_binned.columns:
        df_binned["state_vol_day"] = _expanding_qcut(
            df_binned["feat_realized_vol_day_equiv"],
            ["vol_day_low", "vol_day_mid", "vol_day_high"], min_periods, "vol_day_mid",
        )

    # ── T2/T3/T4 new states -- all FIXED PRIOR thresholds (no look-ahead
    # in the rolling sense).  Reuse the exact same mappers as bin_features.
    def _bin_funding(val):
        if pd.isna(val):
            return np.nan
        if val < -1.0:
            return "funding_short"
        if val > 1.0:
            return "funding_long"
        return "funding_neutral"
    if "feat_funding_z20" in df_binned.columns and not df_binned["feat_funding_z20"].dropna().empty:
        df_binned["state_funding"] = df_binned["feat_funding_z20"].apply(_bin_funding)
    else:
        df_binned["state_funding"] = "funding_neutral"

    def _bin_oi(val):
        if pd.isna(val):
            return np.nan
        if val < -0.03:
            return "oi_collapsing"
        if val > 0.03:
            return "oi_building"
        return "oi_flat"
    if "feat_oi_change_5" in df_binned.columns and not df_binned["feat_oi_change_5"].dropna().empty:
        df_binned["state_oi"] = df_binned["feat_oi_change_5"].apply(_bin_oi)
    else:
        df_binned["state_oi"] = "oi_flat"

    def _bin_cot(val):
        if pd.isna(val):
            return np.nan
        if val < -1.0:
            return "cot_short"
        if val > 1.0:
            return "cot_long"
        return "cot_neutral"
    if "feat_cot_mm_z52" in df_binned.columns and not df_binned["feat_cot_mm_z52"].dropna().empty:
        df_binned["state_cot"] = df_binned["feat_cot_mm_z52"].apply(_bin_cot)
    else:
        df_binned["state_cot"] = "cot_neutral"

    def _bin_vix(val):
        if pd.isna(val):
            return np.nan
        if val < -0.05:
            return "vix_low"
        if val > 0.20:
            return "vix_high"
        return "vix_mid"
    if "feat_vix_ext" in df_binned.columns and not df_binned["feat_vix_ext"].dropna().empty:
        df_binned["state_vix"] = df_binned["feat_vix_ext"].apply(_bin_vix)
    else:
        df_binned["state_vix"] = "vix_mid"

    def _bin_breadth(val):
        if pd.isna(val):
            return np.nan
        if val < 0.40:
            return "breadth_weak"
        if val > 0.70:
            return "breadth_strong"
        return "breadth_mixed"
    if "feat_breadth_pct" in df_binned.columns and not df_binned["feat_breadth_pct"].dropna().empty:
        df_binned["state_breadth"] = df_binned["feat_breadth_pct"].apply(_bin_breadth)
    else:
        df_binned["state_breadth"] = "breadth_mixed"

    def _bin_dxy(val):
        if pd.isna(val):
            return np.nan
        if val < -0.01:
            return "dxy_weak"
        if val > 0.01:
            return "dxy_strong"
        return "dxy_flat"
    if "feat_dxy_slope" in df_binned.columns and not df_binned["feat_dxy_slope"].dropna().empty:
        df_binned["state_dxy"] = df_binned["feat_dxy_slope"].apply(_bin_dxy)
    else:
        df_binned["state_dxy"] = "dxy_flat"

    _fallback_state = {
        "state_ext": np.nan,
        "state_slope": "flat",
        "state_vol": "mid_vol",
        "state_volume": "vol_normal",
        "state_session": np.nan,
        "state_dow": np.nan,
        "state_utc_session": np.nan,
        "state_weekpart": np.nan,
        "state_ret_1": "ret_flat",
        "state_ret_3": "ret_flat",
        "state_ret_5": "ret_flat",
        "state_ret_10": "ret_flat",
        "state_ret_20": "ret_flat",
        "state_atr_ext": "atr_neutral",
        "state_vol_regime": "vol_regime_mid",
        "state_trend_strength": "mod_trend",
        "state_gap": "gap_flat",
        "state_range_pos": "range_mid",
        "state_ret_day": "ret_day_flat",
        "state_vol_day": "vol_day_mid",
        "state_funding": "funding_neutral",
        "state_oi": "oi_flat",
        "state_cot": "cot_neutral",
        "state_vix": "vix_mid",
        "state_breadth": "breadth_mixed",
        "state_dxy": "dxy_flat",
    }
    for _state_col, _fallback in _fallback_state.items():
        if _state_col not in df_binned.columns:
            df_binned[_state_col] = _fallback

    return df_binned


def apply_state_bins(
    df: pd.DataFrame,
    bin_mode: str = "insample",
    rolling_min_periods: int = DEFAULT_ROLLING_MIN_PERIODS,
) -> pd.DataFrame:
    """Dispatcher between full-history (insample) and look-ahead-free (rolling)
    state binning. `bin_mode="insample"` reproduces the original bin_features
    exactly (backward compatible). `bin_mode="rolling"` uses bin_features_rolling.
    """
    if bin_mode == "rolling":
        return bin_features_rolling(df, min_periods=rolling_min_periods)
    return bin_features(df)


def discover_market_slices(
    symbol: str,
    timeframe: str,
    slice_fields: List[str],
    min_samples: int = 15,
    cond_symbols: List[str] = None,
    bin_mode: str = "insample",
    _precomputed_binned: pd.DataFrame = None,
) -> pd.DataFrame:
    """Discover market-state slices for a single symbol/timeframe.

    Parameters
    ----------
    _precomputed_binned : pd.DataFrame, optional
        A pre-computed, binned, cross-asset-attached frame. When provided,
        the expensive load→feature→bin→attach pipeline is skipped entirely.
        This is the critical performance optimisation for discovery loops
        that test multiple field combinations on the same (symbol, timeframe):
        features and bins are computed ONCE, then each combination is tested
        against the cached frame.  Without this, a 13-combination search on
        236 symbols triggers 3,068 feature computations (each loading the
        full warehouse partition and recomputing all rolling features).
        With this, it triggers 236.
    """
    if _precomputed_binned is not None:
        df_binned = _precomputed_binned
    else:
        df_raw = load_from_warehouse(symbol, timeframe)
        if df_raw.empty:
            print(f"No warehouse data found for {symbol} ({timeframe}).")
            return pd.DataFrame()

        df_feat = compute_price_features(df_raw)
        df_binned = apply_state_bins(df_feat, bin_mode=bin_mode)

        # Optional cross-asset conditioning: attach each conditioning symbol's
        # most-recent-completed state (backward as-of, no look-ahead) as
        # cross_<SYM>_state_* columns so slice_fields can reference them.
        if cond_symbols:
            for cond_sym in cond_symbols:
                df_binned = attach_cross_asset_states(
                    df_binned,
                    cond_sym,
                    timeframe,
                    ["state_ext", "state_slope", "state_vol"],
                    bin_mode=bin_mode,
                )

    eval_df = df_binned[df_binned['label_eligible']]
    if eval_df.empty:
        print(f"No eligible forward-looking evaluation rows for {symbol} ({timeframe}).")
        return pd.DataFrame()

    for f in slice_fields:
        if f not in eval_df.columns:
            raise ValueError(f"Slice feature field '{f}' is not available in the state DataFrame.")

    grouped = eval_df.groupby(slice_fields)

    slice_metrics = []
    for keys, group in grouped:
        n = len(group)
        if n < min_samples:
            continue

        slice_key = " + ".join([f"{f}={k}" for f, k in zip(slice_fields, keys if isinstance(keys, tuple) else [keys])])

        mean_ret = group['fwd_ret_5'].mean()
        std_ret = group['fwd_ret_5'].std()
        win_rate = (group['fwd_ret_5'] > 0).sum() / n

        mean_mfe = group['fwd_mfe_5'].mean()
        mean_mae = group['fwd_mae_5'].mean()

        ratio = mean_ret / std_ret if std_ret > 0 else 0.0

        # Direction tag: a slice whose mean forward return is negative is a
        # SHORT (it profits when price falls). The validation gate then
        # direction-adjusts so the same mean_return > 0 test works for both.
        # tradeable_mean_fwd_ret_5 is the sign-adjusted expected P&L, used to
        # rank long and short edges on a single scale (best edge of either
        # direction floats to the top).
        side = "short" if mean_ret < 0 else "long"

        slice_metrics.append({
            'symbol': symbol,
            'timeframe': timeframe,
            'slice_combination': slice_key,
            'sample_count': n,
            'mean_fwd_ret_5': mean_ret,
            'side': side,
            'tradeable_mean_fwd_ret_5': -mean_ret if mean_ret < 0 else mean_ret,
            'win_rate': win_rate,
            'mean_mfe_5': mean_mfe,
            'mean_mae_5': mean_mae,
            'fwd_ret_ratio': ratio
        })

    df_slices = pd.DataFrame(slice_metrics)
    if not df_slices.empty:
        # Rank by tradeable (direction-adjusted) expected P&L so the strongest
        # edge of EITHER direction surfaces first, not just the strongest long.
        df_slices = df_slices.sort_values(
            "tradeable_mean_fwd_ret_5", ascending=False
        ).reset_index(drop=True)

    return df_slices


def align_cross_asset_states(primary_df, cond_state_df, cond_symbol, fields):
    """Backward as-of merge of a conditioning symbol's binned state onto
    primary_df without look-ahead. For each primary bar at time t, the
    conditioning state is its most recent bar with bar_ts_utc <= t. New
    columns are named cross_<COND>_<field>. Row order is preserved.
    """
    if primary_df.empty:
        return primary_df.copy()
    if "bar_ts_utc" not in primary_df.columns:
        raise ValueError("primary_df must contain a bar_ts_utc column.")
    if cond_state_df.empty or "bar_ts_utc" not in cond_state_df.columns:
        raise ValueError("cond_state_df must contain a non-empty bar_ts_utc column.")

    prefix = f"cross_{cond_symbol.upper()}_"
    rename = {f: f"{prefix}{f}" for f in fields}

    cond = cond_state_df[["bar_ts_utc", *fields]].rename(columns=rename)
    cond = cond.sort_values("bar_ts_utc").reset_index(drop=True)
    cond["_cond_ts"] = cond["bar_ts_utc"]

    primary = primary_df.copy()
    primary["_orig_order"] = range(len(primary))
    primary_sorted = primary.sort_values("bar_ts_utc").reset_index(drop=True)

    merged = pd.merge_asof(
        primary_sorted,
        cond,
        on="bar_ts_utc",
        direction="backward",
    )

    MAX_CROSS_STALENESS = pd.Timedelta(days=5)
    stale_mask = (
        primary_sorted["bar_ts_utc"] - merged["_cond_ts"]
    ) > MAX_CROSS_STALENESS
    cross_cols = [c for c in merged.columns if c.startswith(f"cross_{cond_symbol.upper()}_")]
    if stale_mask.any() and cross_cols:
        merged.loc[stale_mask, cross_cols] = np.nan

    merged = merged.sort_values("_orig_order").reset_index(drop=True)
    merged = merged.drop(columns=["_orig_order", "_cond_ts"], errors="ignore")
    return merged


def attach_cross_asset_states(primary_df, cond_symbol, timeframe, fields, bin_mode="insample"):
    """Load the conditioning symbol from the warehouse, rebuild its binned
    state frame the same way as the primary (compute_price_features +
    state binning over full history), and backward as-of merge the requested
    state fields onto primary_df. Returns primary_df unchanged if the
    conditioning symbol has no warehouse data.

    bin_mode is threaded to apply_state_bins so the conditioning symbol is
    binned consistently with the primary (a cross-asset slice must not mix
    in-sample primary state with rolling conditioning state, or vice versa).
    """
    cond_raw = load_from_warehouse(cond_symbol, timeframe)
    if cond_raw.empty:
        return primary_df.copy()

    cond_feat = compute_price_features(cond_raw)
    cond_binned = apply_state_bins(cond_feat, bin_mode=bin_mode)
    return align_cross_asset_states(primary_df, cond_binned, cond_symbol, fields)


# ── Pre-computation helpers for discovery loops ─────────────────────────
#
# When discover_slices.py iterates over many field combinations per symbol,
# it was previously calling discover_market_slices() once per combination,
# each time reloading from warehouse + recomputing features + bins + cross-asset
# states.  For 236 symbols × 13 combinations that's 3,068 redundant
# feature computations.  These helpers let the caller build the binned frame
# ONCE per (symbol, timeframe) and pass it to discover_market_slices via
# the _precomputed_binned parameter, cutting compute by ~12×.

# Module-level cache for conditioning symbols' binned state frames.
# Keyed by (cond_symbol, timeframe, bin_mode).  Populated by
# precompute_binned_frame and reused across all primary symbols that share
# the same conditioning symbols (e.g. USO and TLT are loaded once, not 236×).
_COND_BINS_CACHE: Dict[tuple, pd.DataFrame] = {}


def precompute_binned_frame(
    symbol: str,
    timeframe: str,
    cond_symbols: List[str] = None,
    bin_mode: str = "insample",
    rolling_min_periods: int = DEFAULT_ROLLING_MIN_PERIODS,
) -> pd.DataFrame:
    """Load, feature-compute, bin, and attach cross-asset states for one
    (symbol, timeframe) pair.  Returns the fully-prepared frame that
    discover_market_slices can accept via _precomputed_binned.

    Cross-asset conditioning frames are cached globally so USO/TLT are
    loaded+featured+binned only once per (symbol, timeframe, bin_mode)
    regardless of how many primary symbols reference them.
    """
    df_raw = load_from_warehouse(symbol, timeframe)
    if df_raw.empty:
        return pd.DataFrame()

    df_feat = compute_price_features(df_raw)

    # Attach lane-specific external/macro features (crypto funding/OI,
    # futures COT, equity VIX/breadth/DXY) BEFORE binning so their feat_*
    # columns flow through apply_state_bins into state_* bins. All
    # external-data failures degrade to NaN/neutral-state inside
    # attach_lane_externals and never abort discovery.
    try:
        from price.external_data import attach_lane_externals
        df_feat = attach_lane_externals(df_feat, symbol)
    except Exception:
        pass

    df_binned = apply_state_bins(df_feat, bin_mode=bin_mode,
                                  rolling_min_periods=rolling_min_periods)

    if cond_symbols:
        for cond_sym in cond_symbols:
            cache_key = (cond_sym.upper(), timeframe, bin_mode)
            if cache_key not in _COND_BINS_CACHE:
                cond_raw = load_from_warehouse(cond_sym, timeframe)
                if cond_raw.empty:
                    _COND_BINS_CACHE[cache_key] = pd.DataFrame()
                else:
                    cond_feat = compute_price_features(cond_raw)
                    try:
                        cond_feat = attach_lane_externals(cond_feat, cond_sym)
                    except Exception:
                        pass
                    cond_binned = apply_state_bins(
                        cond_feat, bin_mode=bin_mode,
                        rolling_min_periods=rolling_min_periods,
                    )
                    _COND_BINS_CACHE[cache_key] = cond_binned

            cond_binned = _COND_BINS_CACHE[cache_key]
            if not cond_binned.empty:
                df_binned = align_cross_asset_states(
                    df_binned, cond_binned, cond_sym,
                    ["state_ext", "state_slope", "state_vol"],
                )

    return df_binned


def clear_cond_bins_cache():
    """Clear the cross-asset conditioning cache and external-data caches.
    Call between research shards or when switching timeframes to avoid
    stale cache entries.
    """
    _COND_BINS_CACHE.clear()
    try:
        from price.external_data import reset_breadth_cache
        reset_breadth_cache()
    except Exception:
        pass
