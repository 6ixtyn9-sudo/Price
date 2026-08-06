import json

import pandas as pd
from datetime import datetime, timezone

from price.config import WAREHOUSE_DIR, SYMBOL_PATTERN, is_crypto

def _sanitize_symbol(symbol: str) -> str:
    """
    Filesystem-safe symbol encoding.
    - 'BTC/USD' -> 'BTC-USD'
    - Keeps uppercase
    - Rejects anything outside the market-symbol grammar before path use.
    """
    s = str(symbol).strip().upper()
    if not SYMBOL_PATTERN.fullmatch(s):
        raise ValueError(f"Invalid symbol for warehouse path: {symbol!r}")
    return s.replace("/", "-")

def _desanitize_symbol(safe: str) -> str:
    # best-effort reverse - mainly for display
    # Note: ambiguous if original contained '-', but we store true symbol in data
    return safe.replace("-", "/")

VALID_TIMEFRAMES = {"1d", "1h", "15m"}


def _assert_within_warehouse(path):
    """Belt-and-suspenders containment check for warehouse paths.

    Symbol/timeframe validation should make traversal impossible. This check
    still verifies the resolved path is under the configured WAREHOUSE_DIR so a
    future sanitizer regression or symlink/path trick fails closed before any
    read, write, or unlink.
    """
    root = WAREHOUSE_DIR.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Warehouse path escapes root: {path}") from exc
    return path


def _validate_timeframe(timeframe: str) -> str:
    tf = str(timeframe).strip()
    if tf not in VALID_TIMEFRAMES:
        raise ValueError(f"Invalid warehouse timeframe: {timeframe!r}")
    return tf


# ── Adjustment-book integrity gate (daily frames) ────────────────────────────
# A booked corporate action (split_factor != 1 and/or dividend_cash > 0) MUST
# move adj_factor through the ex-date; conversely a material adj_factor step
# MUST carry a booked event. Staging paths that book an event next to a flat
# factor — e.g. an ex-date special dividend whose price gap sits below the
# 35% crash detector — would otherwise persist silently and hand adjusted-
# price consumers a phantom drawdown. The gate refuses to PERSIST such daily
# frames (fail-closed on integrity) while leaving the rest of the ingest run
# untouched (fail-safe on availability), and writes a JSONL quarantine ledger.
_ADJ_EVENT_MIN_MATERIALITY = 0.01   # 1% price impact: smaller mis-adjusted
                                    # events cannot move sizing/ATR materially
_ADJ_FACTOR_TRACK_TOL = 0.002       # 0.2%: band around "factor stayed flat"
_ADJ_FACTOR_TRACK_REL = 0.25        # factor must track >=75% of expected step
_ADJ_UNBOOKED_STEP_TOL = 0.01       # >=1% factor step with no booked event
_ADJUSTMENT_QUARANTINE_LEDGER = "_adjustment_quarantine.jsonl"


def adjustment_integrity_violations(df: pd.DataFrame) -> list:
    """Audit a canonical DAILY frame's adjustment bookkeeping.

    Booked corporate actions must move ``adj_factor``; material ``adj_factor``
    steps must carry a booked action. Returns a list of reason strings
    (empty = clean). Frames that lack bookkeeping columns, have < 2 bars, or
    carry non-positive/NaN factors on a boundary are unauditable -> clean
    (fail-open on missing data; the gate only fires on *contradictory* data).
    """
    reasons: list = []
    if df is None or len(df) < 2 or "adj_factor" not in df.columns:
        return reasons

    work = df.copy()
    if "bar_ts_utc" in work.columns:
        work = work.sort_values("bar_ts_utc")
    work = work.reset_index(drop=True)

    factor = pd.to_numeric(work["adj_factor"], errors="coerce")
    split = pd.to_numeric(
        work["split_factor"] if "split_factor" in work.columns else 1.0,
        errors="coerce",
    ).fillna(1.0)
    div = pd.to_numeric(
        work["dividend_cash"] if "dividend_cash" in work.columns else 0.0,
        errors="coerce",
    ).fillna(0.0)
    if "close_raw" in work.columns:
        prev_close = pd.to_numeric(work["close_raw"], errors="coerce").shift(1)
    else:
        prev_close = pd.Series([float("nan")] * len(work))
    prev_factor = factor.shift(1)
    has_ts = "bar_ts_utc" in work.columns

    for i in range(1, len(work)):
        f_prev, f_now = prev_factor.iat[i], factor.iat[i]
        if not (pd.notna(f_prev) and pd.notna(f_now)) or f_prev <= 0 or f_now <= 0:
            continue  # junk factors on this boundary: unauditable, not a breach
        ratio = f_now / f_prev
        sf = split.iat[i] if pd.notna(split.iat[i]) else 1.0
        dv = div.iat[i] if pd.notna(div.iat[i]) else 0.0
        pc = prev_close.iat[i]
        div_effect = (dv / pc) if (pd.notna(pc) and pc > 0 and dv > 0) else 0.0
        material_event = abs(sf - 1.0) > 0.005 or div_effect >= _ADJ_EVENT_MIN_MATERIALITY

        stamp = ""
        if has_ts:
            stamp = f" @ {work['bar_ts_utc'].iat[i]}"

        if material_event:
            expected = sf * ((1.0 - dv / pc) if (div_effect and pd.notna(pc)) else 1.0)
            track_tol = max(_ADJ_FACTOR_TRACK_TOL, _ADJ_FACTOR_TRACK_REL * abs(expected - 1.0))
            if abs(ratio - expected) > track_tol:
                reasons.append(
                    f"booked_event_factor_mismatch{stamp}: split_factor={sf}, "
                    f"dividend_cash={dv} (prev_close={pc}); adj_factor step "
                    f"{ratio:.4f} vs expected {expected:.4f}"
                )
        elif abs(ratio - 1.0) >= _ADJ_UNBOOKED_STEP_TOL:
            reasons.append(
                f"unbooked_adj_factor_step{stamp}: adj_factor step {ratio:.4f} "
                "with no booked corporate action"
            )
    return reasons


def _write_adjustment_quarantine(symbol: str, violations: list) -> None:
    """Append a quarantine record; ledger failure must never break ingest."""
    try:
        ledger = WAREHOUSE_DIR / _ADJUSTMENT_QUARANTINE_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "symbol": symbol,
            "timeframe": "1d",
            "quarantined_at_utc": datetime.now(timezone.utc).isoformat(),
            "violations": violations,
        }
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"⚠️  Could not write adjustment quarantine ledger: {exc}")


def load_from_warehouse(symbol: str, timeframe: str) -> pd.DataFrame:
    safe_sym = _sanitize_symbol(symbol)
    timeframe = _validate_timeframe(timeframe)
    partition_dir = _assert_within_warehouse(
        WAREHOUSE_DIR / f"symbol={safe_sym}" / f"timeframe={timeframe}"
    )
    if not partition_dir.exists():
        return pd.DataFrame()
    
    files = list(partition_dir.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
        
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs).sort_values("bar_ts_utc").reset_index(drop=True)
    df['bar_ts_utc'] = pd.to_datetime(df['bar_ts_utc']).dt.tz_convert('UTC')
    return df

def save_to_warehouse(df: pd.DataFrame):
    if df.empty:
        return
        
    groups = df.groupby(["symbol", "timeframe"])
    for (symbol, timeframe), group in groups:
        symbol = str(symbol).strip().upper()
        safe_sym = _sanitize_symbol(symbol)
        timeframe = _validate_timeframe(timeframe)
        if timeframe == "1d":
            violations = adjustment_integrity_violations(group)
            if violations:
                print(
                    f"⛔ Adjustment-book violation: {symbol} | 1d frame NOT persisted "
                    f"({len(violations)} issue(s)). First: {violations[0]}"
                )
                _write_adjustment_quarantine(symbol, violations)
                continue
        partition_dir = _assert_within_warehouse(
            WAREHOUSE_DIR / f"symbol={safe_sym}" / f"timeframe={timeframe}"
        )
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        existing_df = load_from_warehouse(symbol, timeframe)
        
        if not existing_df.empty:
            combined = pd.concat([existing_df, group]).reset_index(drop=True)
            # ingested_at_utc may be missing from old yfinance partitions;
            # fill with a sentinel so sort doesn't KeyError.
            if "ingested_at_utc" not in combined.columns:
                combined["ingested_at_utc"] = pd.NaT
            else:
                combined["ingested_at_utc"] = combined["ingested_at_utc"].fillna(pd.NaT)
            combined = combined.sort_values("ingested_at_utc")
            combined = combined.drop_duplicates(subset=["bar_ts_utc"], keep="last")
            final_df = combined.sort_values("bar_ts_utc").reset_index(drop=True)
        else:
            final_df = group.sort_values("bar_ts_utc").reset_index(drop=True)
            
        for old_file in partition_dir.glob("*.parquet"):
            old_file.unlink()
            
        output_file = partition_dir / "data.parquet"
        
        save_df = final_df.copy()
        if "symbol" in save_df.columns:
            save_df = save_df.drop(columns=["symbol"])
        if "timeframe" in save_df.columns:
            save_df = save_df.drop(columns=["timeframe"])
            
        save_df.to_parquet(output_file, index=False)
        print(f"Warehouse saved: {symbol} | {timeframe} | {len(final_df)} total rows.")

def _filter_regular_hours_for_equity_intraday(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Filter equity intraday warehouse rows to regular market hours.

    Crypto remains 24/7. Futures are excluded from the current liquid236
    universe and are not filtered here.
    """
    if df.empty or is_crypto(symbol):
        return df
    if "bar_ts_utc" not in df.columns:
        return df
    ny = pd.to_datetime(df["bar_ts_utc"], utc=True).dt.tz_convert("America/New_York")
    minutes = ny.dt.hour * 60 + ny.dt.minute
    rth = (minutes >= 9 * 60 + 30) & (minutes < 16 * 60)
    return df.loc[rth].reset_index(drop=True)


def resample_15m_to_1h(symbol: str):
    # symbol may be 'BTC/USD' – load_from_warehouse handles sanitizing
    df_15m = load_from_warehouse(symbol, "15m")
    df_15m = _filter_regular_hours_for_equity_intraday(df_15m, symbol)
    if df_15m.empty:
        print(f"No 15m bars found to resample for {symbol}.")
        return
        
    df_15m = df_15m.sort_values("bar_ts_utc")
    
    # Build agg dict dynamically – crypto may lack 'source' column in some paths
    agg_rules = {
        'open_raw': 'first',
        'high_raw': 'max',
        'low_raw': 'min',
        'close_raw': 'last',
        'volume_raw': 'sum',
    }
    # optional cols
    for opt in ['open_adj','high_adj','low_adj','close_adj','adj_factor','split_factor','dividend_cash','vwap','trade_count','source']:
        if opt in df_15m.columns:
            if opt in ['high_adj','high_raw','volume_raw','vwap']:
                agg_rules[opt] = 'max' if 'high' in opt else 'sum' if 'volume' in opt else 'last'
            elif opt in ['low_adj','low_raw']:
                agg_rules[opt] = 'min'
            elif opt in ['open_adj','open_raw']:
                agg_rules[opt] = 'first'
            else:
                agg_rules[opt] = 'last'
    
    # ensure source aggregation exists
    if 'source' in df_15m.columns and 'source' not in agg_rules:
        agg_rules['source'] = 'first'
    
    resampled = df_15m.resample('1h', on='bar_ts_utc').agg(agg_rules).dropna(subset=['open_raw','close_raw']).reset_index()
    
    resampled['symbol'] = symbol.upper()
    resampled['timeframe'] = "1h"
    resampled['ingested_at_utc'] = datetime.now(timezone.utc)
    
    # fill adj = raw if missing
    for col in ['open_adj','high_adj','low_adj','close_adj']:
        raw = col.replace('_adj','_raw')
        if col not in resampled.columns and raw in resampled.columns:
            resampled[col] = resampled[raw]
    for fcol, default in [('adj_factor',1.0),('split_factor',1.0),('dividend_cash',0.0)]:
        if fcol not in resampled.columns:
            resampled[fcol] = default
    
    save_to_warehouse(resampled)

def propagate_adjustment_factors(symbol: str):
    """Propagate daily adjustment factors into intraday partitions.

    Uses a vectorized merge instead of row-wise apply, making it 100-1000×
    faster for large intraday partitions (thousands of bars per symbol).
    """
    # Skip crypto – no corporate actions, adj = raw already
    if is_crypto(symbol):
        return
    df_1d = load_from_warehouse(symbol, "1d")
    if df_1d.empty:
        print(f"No daily bars found to extract adjustments for {symbol}.")
        return
    if 'adj_factor' not in df_1d.columns:
        # nothing to propagate – assume 1.0
        return
        
    # Daily bars are stored at midnight UTC, but semantically represent
    # the market session date. Converting midnight UTC to America/New_York would
    # shift the date to the prior evening and apply each daily adjustment factor
    # to the wrong intraday session. Keep daily bars keyed by their UTC date,
    # while intraday bars below are keyed by their New York market date.
    df_1d['market_date'] = df_1d['bar_ts_utc'].dt.tz_convert('UTC').dt.date

    # Mixed historical daily sources can leave duplicate rows for the same
    # market date (e.g. Tiingo daily at 00:00 UTC and Alpaca daily at 04:00 UTC).
    # Keep the most recently ingested row per market date before building the
    # adjustment map; otherwise to_dict(orient='index') raises on duplicate index.
    if 'ingested_at_utc' in df_1d.columns:
        df_1d['_ingested_sort'] = pd.to_datetime(df_1d['ingested_at_utc'], errors='coerce', utc=True)
        df_1d = df_1d.sort_values(['market_date', '_ingested_sort', 'bar_ts_utc'])
        df_1d = df_1d.drop(columns=['_ingested_sort'])
    else:
        df_1d = df_1d.sort_values(['market_date', 'bar_ts_utc'])
    df_1d = df_1d.drop_duplicates(subset=['market_date'], keep='last')

    # Build a DataFrame of adjustment factors keyed by market_date for vectorized merge
    adj_df = df_1d.set_index('market_date')[['adj_factor', 'split_factor', 'dividend_cash']].copy()
    # Ensure defaults for dates not in the daily data
    adj_df['adj_factor'] = adj_df['adj_factor'].fillna(1.0)
    adj_df['split_factor'] = adj_df['split_factor'].fillna(1.0)
    adj_df['dividend_cash'] = adj_df['dividend_cash'].fillna(0.0)
    
    for tf in ["15m", "1h"]:
        df_tf = load_from_warehouse(symbol, tf)
        if df_tf.empty:
            continue
        
        # crypto runs 24/7 – use UTC date; equities use NY date
        if is_crypto(symbol):
            df_tf['market_date'] = df_tf['bar_ts_utc'].dt.tz_convert('UTC').dt.date
        else:
            df_tf['market_date'] = df_tf['bar_ts_utc'].dt.tz_convert('America/New_York').dt.date

        # Drop stale adj columns from intraday before merge to avoid
        # pandas suffix collision (_x / _y).  The merge replaces them.
        for drop_col in ('adj_factor', 'split_factor', 'dividend_cash'):
            if drop_col in df_tf.columns:
                df_tf = df_tf.drop(columns=[drop_col])

        # Vectorized merge: join adjustment factors by market_date instead of
        # row-wise Python apply. This is the critical performance fix — the
        # old apply() path called a Python function per row (thousands of
        # calls per symbol), while merge+vectorized multiply is a single
        # C-level operation.
        df_tf = df_tf.merge(adj_df, on='market_date', how='left')
        df_tf['adj_factor'] = df_tf['adj_factor'].fillna(1.0)
        df_tf['split_factor'] = df_tf['split_factor'].fillna(1.0)
        df_tf['dividend_cash'] = df_tf['dividend_cash'].fillna(0.0)

        for col in ['open', 'high', 'low', 'close']:
            raw_col = f'{col}_raw'
            adj_col = f'{col}_adj'
            if raw_col in df_tf.columns:
                df_tf[adj_col] = df_tf[raw_col] * df_tf['adj_factor']
            
        df_tf = df_tf.drop(columns=['market_date'])
        
        df_tf['symbol'] = symbol.upper()
        df_tf['timeframe'] = tf
        df_tf['ingested_at_utc'] = datetime.now(timezone.utc)  # Force update to current time to ensure overwrite
        
        save_to_warehouse(df_tf)
