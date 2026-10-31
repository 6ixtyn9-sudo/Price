"""ROI guardrails: shadow-expectancy gating, tail watchdog, calibration.

This module holds the *pure* decision logic for the 2026-10 ROI pass.
It decides nothing about entries by itself: monitor.py / position_manager.py
consume these verdicts, scripts/shadow_gate.py persists them, and every
consumer fails open when the underlying data is absent.

Components
  1. Shadow stats: aggregate live_forward_returns (direction-adjusted
     tradeable forward returns) per slice identity and per
     (timeframe, side) pool. Live shadow signals outnumber realized
     round-trips ~10:1, so this is the cheapest expectancy evidence
     the system owns.
  2. Two-strike demotion: a slice becomes a demotion *candidate* when
     its shadow expectancy is materially negative (below the measured
     round-trip cost drag) on enough signals, on both horizons. It is
     only demoted after `required_strikes` *distinct data cycles*
     (a cycle is keyed by the newest signal timestamp, so re-running
     the script on unchanged data cannot manufacture a second strike).
  3. Tail watchdog: a slice-independent exit rule. A position that is
     not a confirmed winner (>= +1R) may not outlive a multiple of its
     validated horizon, and no position may sit beyond a hard -R loss,
     no matter what the slice logic, pure-horizon mode, or missing
     metadata would otherwise say. Winners are exempt from the time
     cap (the trailing stop is their watchdog) but never from -R.
  4. Calibration: expected-vs-realized statistics used to decide
     whether conviction sizing has earned the right to move capital.

Doctrine: measurement before promotion, fail open on missing data,
never tune off a single small sample. Every threshold here is a
parameter with a conservative default, not a fitted constant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# Measured round-trip cost drag from realized attribution (2026-10):
# mean gross -0.416% vs net-of-cost -0.546% across traded slices.
DEFAULT_COST_DRAG_BPS = 13.0
DEFAULT_MIN_SHADOW_N = 30
DEFAULT_MIN_SHADOW_N_LONG_HORIZON = 15
DEFAULT_REQUIRED_STRIKES = 2


def shadow_key(symbol, timeframe, slice_combination, side="long", bin_mode="insample") -> str:
    """Canonical identity string shared by demotion files and gates."""
    return "|".join([
        str(symbol).upper().strip(),
        str(timeframe or "").strip(),
        str(bin_mode or "insample").strip().lower(),
        str(side or "long").strip().lower(),
        str(slice_combination or "").strip(),
    ])


def normalize_exempt_slice_key(raw: str) -> str:
    """Normalize a pool-gate exemption string so CLI case/whitespace cannot
    silently miss at runtime.

    Accepts either:
      - 3-part: 'SYMBOL|TIMEFRAME|slice_combination'
      - 4-part: 'SYMBOL|TIMEFRAME|bin_mode|slice_combination'
    """
    parts = [p.strip() for p in str(raw or "").split("|")]
    if len(parts) == 3:
        return f"{parts[0].upper()}|{parts[1]}|{parts[2]}"
    if len(parts) >= 4:
        combo = "|".join(parts[3:]).strip()
        return f"{parts[0].upper()}|{parts[1]}|{(parts[2] or 'insample').lower()}|{combo}"
    return str(raw or "").strip()


def is_slice_exempt(
    exempt_slices: Iterable[str],
    symbol: str,
    timeframe: str,
    slice_combination: Optional[str],
    bin_mode: Optional[str] = None,
) -> bool:
    """True when (symbol, timeframe, slice_combination[, bin_mode]) matches
    any entry in `exempt_slices` after case/whitespace normalization."""
    if not exempt_slices:
        return False
    norm_set = {normalize_exempt_slice_key(x) for x in exempt_slices if x}
    sym = str(symbol or "").strip().upper()
    tf = str(timeframe or "").strip()
    combo = str(slice_combination or "").strip()
    if f"{sym}|{tf}|{combo}" in norm_set:
        return True
    if bin_mode:
        bm = str(bin_mode).strip().lower()
        if f"{sym}|{tf}|{bm}|{combo}" in norm_set:
            return True
    return False


def _mean(xs: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and float(x) == float(x)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def compute_shadow_stats(
    live_forward: Optional[pd.DataFrame],
    *,
    short_horizon_col: str = "tradeable_fwd_ret_5b",
    long_horizon_col: str = "tradeable_fwd_ret_20b",
) -> List[dict]:
    """Per-identity shadow expectancy from a live_forward_returns frame.

    Returns one dict per (symbol, timeframe, bin_mode, side, slice) with
    signal counts and mean direction-adjusted forward returns in bps.
    Rows with partial/missing short-horizon returns are excluded from
    that horizon's stats rather than treated as zeros.
    """
    if live_forward is None or live_forward.empty:
        return []
    df = live_forward
    needed = {"symbol", "timeframe", "slice_combination"}
    if not needed.issubset(df.columns):
        return []
    out: List[dict] = []
    group_cols = [c for c in ("symbol", "timeframe", "bin_mode", "side", "slice_combination")
                  if c in df.columns]
    for keys, grp in df.groupby(group_cols, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        rec = dict(zip(group_cols, keys))
        row = {
            "symbol": str(rec.get("symbol", "")).upper(),
            "timeframe": str(rec.get("timeframe", "")),
            "bin_mode": str(rec.get("bin_mode", "insample") or "insample").lower(),
            "side": str(rec.get("side", "long") or "long").lower(),
            "slice_combination": str(rec.get("slice_combination", "")),
        }
        for label, col in (("5b", short_horizon_col), ("20b", long_horizon_col)):
            if col in grp.columns:
                vals = pd.to_numeric(grp[col], errors="coerce").dropna()
                row[f"n_{label}"] = int(len(vals))
                row[f"mean_{label}_bps"] = (float(vals.mean()) * 10000.0) if len(vals) else None
                row[f"win_{label}"] = (float((vals > 0).mean())) if len(vals) else None
            else:
                row[f"n_{label}"] = 0
                row[f"mean_{label}_bps"] = None
                row[f"win_{label}"] = None
        row["key"] = shadow_key(row["symbol"], row["timeframe"],
                                row["slice_combination"], row["side"], row["bin_mode"])
        latest_ts = ""
        if "signal_ts_utc" in grp.columns:
            parsed_ts = pd.to_datetime(
                grp["signal_ts_utc"], format="mixed", errors="coerce", utc=True
            ).dropna()
            if len(parsed_ts):
                latest_ts = parsed_ts.max().isoformat()
        row["latest_signal_ts_utc"] = latest_ts
        row["cycle_key"] = f"{row['n_5b']}|{latest_ts}" if row["n_5b"] > 0 else ""
        out.append(row)
    out.sort(key=lambda r: (-r["n_5b"], r["key"]))
    return out


def compute_pool_stats(live_forward: Optional[pd.DataFrame]) -> List[dict]:
    """Shadow expectancy per (timeframe, side) pool — the coarse view
    that justifies pool-level gates (e.g. 1h shorts) without pretending
    any single slice has enough trades to judge."""
    if live_forward is None or live_forward.empty:
        return []
    if not {"timeframe"}.issubset(live_forward.columns):
        return []
    out: List[dict] = []
    cols = [c for c in ("timeframe", "side") if c in live_forward.columns]
    for keys, grp in live_forward.groupby(cols, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        rec = dict(zip(cols, keys))
        row = {"timeframe": str(rec.get("timeframe", "")),
               "side": str(rec.get("side", "long") or "long").lower()}
        for label, col in (("5b", "tradeable_fwd_ret_5b"), ("20b", "tradeable_fwd_ret_20b")):
            if col in grp.columns:
                vals = pd.to_numeric(grp[col], errors="coerce").dropna()
                row[f"n_{label}"] = int(len(vals))
                row[f"mean_{label}_bps"] = (float(vals.mean()) * 10000.0) if len(vals) else None
            else:
                row[f"n_{label}"] = 0
                row[f"mean_{label}_bps"] = None
        out.append(row)
    return out


def shadow_verdict(
    row: dict,
    *,
    min_n: int = DEFAULT_MIN_SHADOW_N,
    min_n_long: int = DEFAULT_MIN_SHADOW_N_LONG_HORIZON,
    cost_drag_bps: float = DEFAULT_COST_DRAG_BPS,
) -> str:
    """Classify one shadow-stats row.

    "demote_candidate": enough signals AND mean expectancy below
        -cost_drag on the short horizon, confirmed (when measurable)
        on the long horizon. Both horizons must agree so a single
        horizon's noise cannot demote a slice.
    "watch": enough signals and negative, but not beyond cost drag,
        or horizons disagree.
    "insufficient": not enough signals to judge.
    "ok": non-negative expectancy.
    """
    n5 = int(row.get("n_5b") or 0)
    mean5 = row.get("mean_5b_bps")
    if n5 < min_n or mean5 is None:
        return "insufficient"
    if mean5 >= 0:
        return "ok"
    if mean5 >= -abs(cost_drag_bps):
        return "watch"
    n20 = int(row.get("n_20b") or 0)
    mean20 = row.get("mean_20b_bps")
    if n20 >= min_n_long and (mean20 is None or mean20 >= -abs(cost_drag_bps)):
        return "watch"  # long horizon does not confirm the bleed
    return "demote_candidate"


def update_demotion_state(
    state: Optional[dict],
    verdicts: Dict[str, str],
    cycle_key: str,
    *,
    slice_cycle_keys: Optional[Dict[str, str]] = None,
    required_strikes: int = DEFAULT_REQUIRED_STRIKES,
) -> Tuple[dict, List[str]]:
    """Advance the two-strike demotion ledger by one data cycle.

    `verdicts` maps shadow_key -> shadow_verdict for the current cycle.
    A slice is demoted once its strike count reaches `required_strikes`.
    When `slice_cycle_keys` is provided (mapping shadow_key -> per-slice
    `n_5b|latest_signal_ts_utc`), each slice only advances or forgives its
    strike when THAT slice's own resolved signal set has changed — so an
    unrelated 15m signal elsewhere in the book cannot manufacture a second
    strike on an unchanged 1d slice. Falls back to `cycle_key` when
    `slice_cycle_keys` is omitted.
    """
    state = dict(state or {})
    strikes: Dict[str, int] = {str(k): int(v) for k, v in (state.get("strikes") or {}).items()}
    if slice_cycle_keys is not None:
        prev_cycles: Dict[str, str] = {
            str(k): str(v) for k, v in (state.get("slice_cycles") or {}).items()
        }
        new_strikes: Dict[str, int] = {}
        new_cycles: Dict[str, str] = {}
        for key, verdict in verdicts.items():
            sc = str(slice_cycle_keys.get(key) or "")
            if sc:
                new_cycles[key] = sc
            if sc and sc != prev_cycles.get(key):
                if verdict == "demote_candidate":
                    new_strikes[key] = strikes.get(key, 0) + 1
                elif key in strikes and verdict in ("watch",):
                    new_strikes[key] = strikes[key]
                # "ok"/"insufficient" on new slice evidence forgive strikes.
            elif key in strikes:
                # Unchanged slice data: neither manufacture nor erase strikes.
                new_strikes[key] = strikes[key]
        strikes = new_strikes
        state["slice_cycles"] = new_cycles
        if cycle_key:
            state["cycle_key"] = cycle_key
    elif cycle_key and cycle_key != state.get("cycle_key"):
        new_strikes = {}
        for key, verdict in verdicts.items():
            if verdict == "demote_candidate":
                new_strikes[key] = strikes.get(key, 0) + 1
            elif key in strikes and verdict in ("watch",):
                new_strikes[key] = strikes[key]  # hold, neither advance nor forgive
            # "ok"/"insufficient" forgive the strike history.
        strikes = new_strikes
        state["cycle_key"] = cycle_key
    state["strikes"] = strikes
    demoted = sorted(k for k, v in strikes.items() if v >= required_strikes)
    return state, demoted


# ---------------------------------------------------------------------------
# Tail watchdog
# ---------------------------------------------------------------------------

@dataclass
class WatchdogConfig:
    """Slice-independent position tail limits. All disabled by default;
    paper_trade.py opts in explicitly, so library callers keep legacy
    behaviour unless they ask for the watchdog."""

    max_horizon_multiple: Optional[float] = None   # e.g. 3.0 -> cap at 3x horizon
    hard_loss_r: Optional[float] = None            # positive magnitude: exit at -2.0R
    max_no_context_days: Optional[int] = None      # calendar cap when slice context is lost
    loser_stop_fraction: Optional[float] = None    # e.g. 0.5 -> exit losers at 0.5x horizon
    loser_stop_min_neg_r: float = 0.05             # spread-noise deadband: require <= -0.05R
    breakeven_r: float = 1.0                       # winners at/above this are time-cap exempt

    @property
    def enabled(self) -> bool:
        return (self.max_horizon_multiple is not None
                or self.hard_loss_r is not None
                or self.max_no_context_days is not None
                or self.loser_stop_fraction is not None)


def watchdog_exit_reason(
    *,
    bars_held: Optional[int],
    horizon_bars: Optional[int],
    r_multiple: Optional[float],
    config: WatchdogConfig,
) -> Optional[str]:
    """Return a watchdog exit reason, or None to let normal policy rule.

    The hard -R rule fires regardless of R-gate/winner status: a trade
    at -2R is not a winner being managed, it is a failed stop. The time
    cap fires only for trades that have NOT confirmed (>= breakeven_r):
    confirmed winners belong to the trailing stop, which is itself a
    watchdog. When R is unknown (no stop state — the historically
    dangerous case) the time cap still applies.
    """
    if not config.enabled:
        return None
    if config.hard_loss_r is not None and r_multiple is not None:
        if r_multiple <= -abs(float(config.hard_loss_r)):
            return (f"watchdog_hard_loss: unrealized {r_multiple:.2f}R <= "
                    f"-{abs(float(config.hard_loss_r)):.2f}R hard limit; "
                    "slice-independent tail exit")
    if config.loser_stop_fraction is not None:
        # Non-winner time-stop: a position materially underwater (beyond the
        # spread-noise deadband) at this fraction of its horizon has not
        # confirmed; cut it here rather than letting it drift to the stop or
        # full horizon. Skip when trigger >= horizon_bars so normal horizon
        # exits are never mislabeled as watchdog_loser_stop.
        min_neg_r = abs(float(config.loser_stop_min_neg_r or 0.0))
        if (bars_held is not None and horizon_bars and int(horizon_bars) > 0
                and r_multiple is not None and r_multiple <= -max(min_neg_r, 1e-12) and r_multiple < 0):
            trigger = math.ceil(
                abs(float(config.loser_stop_fraction)) * int(horizon_bars))
            if trigger < int(horizon_bars) and int(bars_held) >= trigger:
                return (f"watchdog_loser_stop: underwater {r_multiple:.2f}R at "
                        f"{int(bars_held)} bars >= "
                        f"{config.loser_stop_fraction:g}x horizon "
                        f"({trigger} bars); non-winner time-stop, "
                        "slice-independent tail exit")
    if config.max_horizon_multiple is not None:
        if bars_held is not None and horizon_bars and int(horizon_bars) > 0:
            cap = math.ceil(abs(float(config.max_horizon_multiple)) * int(horizon_bars))
            is_winner = r_multiple is not None and r_multiple >= config.breakeven_r
            if int(bars_held) >= cap and not is_winner:
                return (f"watchdog_max_hold: held {int(bars_held)} bars >= "
                        f"{config.max_horizon_multiple:g}x horizon cap ({cap} bars) "
                        "without reaching +1R; slice-independent tail exit")
    return None


def lot_age_days(entry_ts, as_of) -> Optional[float]:
    """Age of a lot in days between two timestamps. None if unparseable."""
    try:
        start = pd.to_datetime(entry_ts, errors="coerce", utc=True)
        end = pd.to_datetime(as_of, errors="coerce", utc=True)
    except (TypeError, ValueError):
        return None
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return None
    return float((end - start).total_seconds()) / 86400.0


def stale_lots(lots: Iterable[dict], *, max_age_days: float, as_of=None) -> List[dict]:
    """Unmatched lots at least `max_age_days` old (age precomputed on the
    lot when present, else derived from entry_ts/as_of)."""
    out = []
    for lot in lots:
        age = lot.get("age_days")
        if age is None:
            age = lot_age_days(lot.get("entry_ts"), as_of)
        try:
            if age is not None and float(age) >= float(max_age_days):
                out.append(lot)
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Forward-return helpers (expired-order opportunity study)
# ---------------------------------------------------------------------------

def forward_return_from_bars(
    bars: Optional[pd.DataFrame],
    entry_ts,
    horizon_bars: int,
    side: str = "long",
) -> Optional[float]:
    """Direction-adjusted close-to-close return `horizon_bars` after the
    first bar at/after `entry_ts`. None when the warehouse cannot answer
    (missing frame, signal beyond coverage, insufficient bars) — callers
    count Nones as unmeasurable rather than as zero-edge evidence."""
    if bars is None or bars.empty:
        return None
    if "bar_ts_utc" not in bars.columns or "close_adj" not in bars.columns:
        return None
    df = bars.sort_values("bar_ts_utc").reset_index(drop=True)
    ts = pd.to_datetime(entry_ts, errors="coerce", utc=True)
    if ts is None or pd.isna(ts):
        return None
    bar_ts = pd.to_datetime(df["bar_ts_utc"], errors="coerce", utc=True)
    future_idx = [i for i, t in enumerate(bar_ts) if pd.notna(t) and t >= ts]
    if not future_idx:
        return None
    start = future_idx[0]
    end = start + int(horizon_bars)
    if end >= len(df):
        return None
    try:
        start_close = float(df["close_adj"].iloc[start])
        end_close = float(df["close_adj"].iloc[end])
    except (TypeError, ValueError):
        return None
    if not (start_close > 0) or start_close != start_close or end_close != end_close:
        return None
    raw = end_close / start_close - 1.0
    return -raw if str(side).lower() in ("short", "sell") else raw


# ---------------------------------------------------------------------------
# Conviction calibration
# ---------------------------------------------------------------------------

def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def calibration_stats(pairs: Iterable[Tuple[float, float, float]]) -> dict:
    """Expected-vs-realized calibration over (expected, realized, weight)
    triples (one per slice). Reports Pearson correlation and the OLS
    slope of realized on expected: a sizing signal earns capital only
    when slope > 0 with a meaningful correlation on enough slices.
    With fewer than 10 slices the honest verdict is 'insufficient' and
    the recommendation is to freeze conviction at equal-notional."""
    rows = []
    for exp, real, w in pairs:
        try:
            e, r, wt = float(exp), float(real), float(w)
        except (TypeError, ValueError):
            continue
        if e == e and r == r and wt > 0:
            rows.append((e, r, wt))
    n = len(rows)
    corr = _pearson([r[0] for r in rows], [r[1] for r in rows]) if n >= 3 else None
    slope = None
    if n >= 3:
        mx = sum(r[0] for r in rows) / n
        my = sum(r[1] for r in rows) / n
        sxx = sum((r[0] - mx) ** 2 for r in rows)
        if sxx > 0:
            slope = sum((r[0] - mx) * (r[1] - my) for r in rows) / sxx
    if n < 10:
        verdict = "insufficient"
    elif corr is not None and corr >= 0.2 and slope is not None and slope > 0:
        verdict = "calibrated"
    else:
        verdict = "uncalibrated_freeze_conviction"
    return {
        "n_slices": n,
        "pearson_expected_realized": (round(corr, 4) if corr is not None else None),
        "slope_realized_on_expected": (round(slope, 4) if slope is not None else None),
        "verdict": verdict,
    }
