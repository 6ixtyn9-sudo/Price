"""Regime-detection deployment gate.

This module turns today's key finding (the T1 watchlist edges are
regime-conditional, not structural) into an actionable deployment filter
rather than a demotion. The operator's reframe was correct: a regime-
conditional edge is only a problem if you (a) can't detect the regime, or
(b) mislabel it as structural. This gate solves (a) and labels it
honestly for (b).

What it does
  Before a matched slice is allowed to reach the risk gate / order router,
  the monitor checks whether the slice's REGIME is currently favourable.
  "Regime" = the macro trend of the slice's own market/sector, read from
  live price state (a moving-average trend), NOT a hand-kept table.

Why SMA-50 / SMA-200
  The fingerprint that exposed KLAC as regime-confounded was "fold 0 fails
  across the whole stretched_down+downtrend family, and fold 0 is 2022-2023
  -- the semiconductor/materials/energy downturn." In every one of those
  periods, the broader market was BELOW its long-term moving average. So the
  regime detector is a price-state version of the same signal: "is the
  macro trend up, so that dip-buying is the right trade?" When the macro
  trend is down (a 2022-style bear), dip-buying is exactly the wrong trade,
  and the gate stands aside. This converts the fold-0 failure from a
  demotion into an automatic dismount.

Graceful degradation (the safety property)
  * regime_filter_enabled=False (default) -> NO gate; current behaviour
    exactly. Zero-risk to the live book.
  * No regime_symbol on the slice -> uses the slice's own symbol.
  * No warehouse data for the regime symbol -> FAILS OPEN (allows entry),
    because blocking on missing macro data is worse than deploying without
    the gate. The conviction sizing + risk limits still protect the book.
  * Regime data present + macro trend hostile -> BLOCKS entry; the signal
    is logged with reason='regime_hostile' so the audit trail shows it.

Doctrine: this is a deployment gate, not a validation filter, not a
promotion claim. It makes the existing watch-list deployment honest about
being regime-conditional. The slice is still a watch candidate.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from price.warehouse import load_from_warehouse


# Regime thresholds. The classic 50/200 bull/bear definition, made robust
# to daily/intraday and to short history (falls back to 50-only when < 200
# bars are available). These are NOT magic numbers -- they are the standard
# "golden cross / death cross" prior, deliberately chosen because it is
# well-known, not hand-fit to this dataset (which would be the overfit
# risk the project rejects).
SHORT_MA = 50
LONG_MA = 200


@dataclass
class RegimeState:
    """The current macro regime for one regime symbol."""

    symbol: str
    regime: str               # "bull" | "bear" | "neutral" | "unknown"
    close: Optional[float] = None
    short_ma: Optional[float] = None
    long_ma: Optional[float] = None
    reason: str = ""

    def favourable(self) -> bool:
        """True iff the regime permits dip-buying slices to deploy.

        bull: yes (the regime these edges were measured in).
        neutral: yes (we do not require a confirmed bull, only the absence
          of a confirmed bear -- this is deliberately permissive so the
          gate does not over-block in sideways markets).
        bear: no (this is the fold-0 condition -- the gate stands aside).
        unknown: yes (fail-open on missing data; sizing+risk guard remain).
        """
        return self.regime != "bear"

    def to_audit_dict(self) -> dict:
        return {
            "regime_symbol": self.symbol,
            "regime": self.regime,
            "regime_favourable": self.favourable(),
            "regime_close": (round(self.close, 4) if self.close is not None else None),
            "regime_short_ma": (round(self.short_ma, 4) if self.short_ma is not None else None),
            "regime_long_ma": (round(self.long_ma, 4) if self.long_ma is not None else None),
        }


def assess_regime(
    regime_symbol: str,
    timeframe: str = "1d",
    short_ma: int = SHORT_MA,
    long_ma: int = LONG_MA,
) -> RegimeState:
    """Read the current macro regime for `regime_symbol` from the warehouse.

    Uses the SMA crossover prior (close / short-MA / long-MA) computed on
    adjusted closes. Returns regime='unknown' (fail-open) when there is
    insufficient data, so the gate never blocks on missing macro data.
    """
    try:
        df = load_from_warehouse(regime_symbol, timeframe)
    except Exception:  # noqa: BLE001 - gate must never crash the scan
        return RegimeState(symbol=regime_symbol, regime="unknown",
                           reason="warehouse read failed")
    if df is None or df.empty:
        return RegimeState(symbol=regime_symbol, regime="unknown",
                           reason="no warehouse data for regime symbol")
    if "close_adj" not in df.columns:
        return RegimeState(symbol=regime_symbol, regime="unknown",
                           reason="close_adj missing for regime symbol")

    df = df.sort_values("bar_ts_utc").reset_index(drop=True)
    close = df["close_adj"].astype(float)
    if close.dropna().shape[0] < short_ma:
        return RegimeState(symbol=regime_symbol, regime="unknown",
                           reason=f"insufficient history (< {short_ma} bars)")

    latest_close = float(close.iloc[-1])
    sma_short = float(close.rolling(short_ma).mean().iloc[-1])
    sma_long = None
    if close.dropna().shape[0] >= long_ma:
        sma_long = float(close.rolling(long_ma).mean().iloc[-1])

    if sma_long is not None:
        # Full prior: short-MA above long-MA AND price above short-MA.
        if sma_short > sma_long and latest_close > sma_short:
            regime = "bull"
        elif sma_short < sma_long and latest_close < sma_short:
            regime = "bear"
        else:
            regime = "neutral"
    else:
        # Short-history fallback: use short-MA only (price above = bull-ish,
        # below = bear-ish). Conservative: only commit to 'bear' when clearly
        # below, otherwise 'neutral' (fail-open toward allowing entries).
        if latest_close > sma_short:
            regime = "bull"
        elif latest_close < sma_short * 0.98:
            regime = "bear"
        else:
            regime = "neutral"

    return RegimeState(
        symbol=regime_symbol, regime=regime, close=latest_close,
        short_ma=sma_short, long_ma=sma_long,
        reason=f"SMA{short_ma}{'/'+str(long_ma) if sma_long else ''} regime",
    )


def resolve_regime_symbol(
    slice_symbol: str,
    slice_filter: Optional[dict] = None,
    configured_regime_symbol: Optional[str] = None,
) -> str:
    """Resolve which symbol defines the regime for a given slice.

    Priority:
      1. An explicit regime_symbol configured per-slice (operator-owned).
      2. A cross-asset conditioning symbol if the slice references one
         (the regime is the conditioning market, e.g. USO for an energy slice).
      3. The slice's own symbol (self-referential macro trend).

    For now the sensible default is the slice's OWN symbol, because today's
    finding showed the regime confound is per-symbol-per-sector and the
    self-trend is the most direct read of "is THIS name in its working
    regime." A broader regime (SPY) can be configured per-slice.
    """
    if configured_regime_symbol:
        return configured_regime_symbol.upper()
    # If the slice is cross-asset conditioned, the conditioning symbol's
    # regime is the macro read (e.g. USO slope for an energy slice).
    if slice_filter:
        for field in slice_filter:
            if field.startswith("cross_"):
                rest = field[len("cross_"):]
                idx = rest.find("_state_")
                if idx > 0:
                    return rest[:idx].upper()
    return slice_symbol.upper()


def check_regime(
    slice_symbol: str,
    slice_filter: Optional[dict] = None,
    configured_regime_symbol: Optional[str] = None,
    timeframe: str = "1d",
    enabled: bool = False,
) -> RegimeState:
    """Full regime gate check for a slice.

    When enabled=False, returns a neutral pass-through RegimeState so the
    caller's audit trail records that the gate exists but was off.
    When enabled=True, reads the resolved regime symbol and returns its
    RegimeState (fail-open on any data problem).
    """
    if not enabled:
        return RegimeState(symbol=slice_symbol.upper(), regime="gate_disabled",
                           reason="regime filter disabled (default)")

    regime_sym = resolve_regime_symbol(
        slice_symbol, slice_filter, configured_regime_symbol
    )
    return assess_regime(regime_sym, timeframe=timeframe)


def attach_regime_labels(
    primary_df,
    regime_symbol: str,
    timeframe: str = "1d",
    short_ma: int = SHORT_MA,
    long_ma: int = LONG_MA,
):
    """Attach a per-bar macro `regime` label to primary_df.

    This is the VALIDATION-side companion to assess_regime (which is the
    DEPLOYMENT-side gate). It computes the SMA-50/200 regime AS-OF each bar
    of the regime symbol (look-ahead-free: SMAs use only past data), then
    backward as-of merges the regime label onto primary_df by bar_ts_utc.

    The regime label is the macro trend of the regime symbol, NOT the slice's
    own local state -- this is the right separation for testing whether a
    dip-buying slice's edge is structural (positive across regimes) or
    regime-conditional (positive only in the macro bull).

    Returns primary_df unchanged (with no regime column) when the regime
    symbol has no data -- never raises; the caller treats missing regime as
    a 'regime_unavailable' bucket.
    """
    if primary_df is None or primary_df.empty:
        return primary_df
    if "bar_ts_utc" not in primary_df.columns:
        return primary_df
    try:
        regime_raw = load_from_warehouse(regime_symbol, timeframe)
    except Exception:  # noqa: BLE001 - validation must never crash
        return primary_df
    if regime_raw is None or regime_raw.empty or "close_adj" not in regime_raw.columns:
        return primary_df

    rg = regime_raw.sort_values("bar_ts_utc").reset_index(drop=True)
    close = rg["close_adj"].astype(float)
    if close.dropna().shape[0] < short_ma:
        return primary_df  # insufficient history for even the short MA

    sma_short = close.rolling(short_ma).mean()
    sma_long = close.rolling(long_ma).mean() if close.dropna().shape[0] >= long_ma else None

    def classify(i):
        c = close.iloc[i]
        s = sma_short.iloc[i]
        if pd.isna(s):
            return "regime_warmup"  # before short_ma has a value
        if sma_long is not None:
            lng = sma_long.iloc[i]
            if pd.isna(lng):
                return "regime_warmup"  # short MA valid, long MA still warming
            if s > lng and c > s:
                return "bull"
            if s < lng and c < s:
                return "bear"
            return "neutral"
        # Short-history fallback (mirror assess_regime's logic).
        if c > s:
            return "bull"
        if c < s * 0.98:
            return "bear"
        return "neutral"

    rg = rg.assign(regime=[classify(i) for i in range(len(rg))])
    regime_lookup = rg[["bar_ts_utc", "regime"]].sort_values("bar_ts_utc").reset_index(drop=True)
    regime_lookup["bar_ts_utc"] = pd.to_datetime(regime_lookup["bar_ts_utc"], utc=True)

    primary = primary_df.copy()
    primary["bar_ts_utc"] = pd.to_datetime(primary["bar_ts_utc"], utc=True)
    primary_sorted = primary.sort_values("bar_ts_utc").reset_index(drop=True)
    primary_sorted["_orig_order"] = range(len(primary_sorted))

    merged = pd.merge_asof(
        primary_sorted, regime_lookup, on="bar_ts_utc", direction="backward",
    )
    merged = merged.sort_values("_orig_order").reset_index(drop=True)
    merged = merged.drop(columns=["_orig_order"])
    if merged["regime"].isna().all():
        # No overlap in time between slice bars and regime bars.
        merged["regime"] = "regime_unavailable"
    else:
        merged["regime"] = merged["regime"].fillna("regime_unavailable")
    return merged
