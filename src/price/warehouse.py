import json

import pandas as pd
from datetime import datetime, timezone

from price.config import WAREHOUSE_DIR, SYMBOL_PATTERN, is_crypto, is_futures

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
        booked_event = abs(sf - 1.0) > 0.005 or dv > 0.0

        stamp = ""
        if has_ts:
            stamp = f" @ {work['bar_ts_utc'].iat[i]}"

        if booked_event:
            # Convention (verified 2026-08-07 against live Yahoo data and
            # the reciprocal signature of 37 production refusals): in an
            # adjusted series the PRE-EX prices are adjusted down, so moving
            # forward in time adj_factor steps UP across an ex-date —
            # by the split ratio for splits and by the RECIPROCAL dividend
            # factor 1/(1 - dv/pc) for dividends. The original inverted
            # dividend term (shipped 40e53d3) expected the down-step and
            # refused every genuine dividend book; it stayed latent because
            # the vendor's zero-filled splits froze daily frames before the
            # gate ever audited a real dividend in production.
            #
            # A booked event that explains its factor step is clean at ANY
            # materiality — materiality only selects the violation KIND.
            # The previous shape routed sub-1%-yield booked dividends
            # straight to the "unbooked" branch, where their own ~1.01x
            # step tripped the 1% step flag: a ~10bps crack (yield in
            # [0.9902%, 1%)) that refused 7 production books on 2026-08-07
            # (ABBV/CVX/DUK/GILD/INTC/MRK/SO — observed steps all
            # 1.0100-1.0101), flagged as "no booked corporate action" with
            # the dividend sitting in the frame's own actions columns.
            div_factor = (1.0 - dv / pc) if (pd.notna(pc) and pc > 0 and dv > 0) else 1.0
            if div_factor <= 0:
                continue  # junk event (cash >= close): unauditable boundary
            expected = sf / div_factor
            track_tol = max(_ADJ_FACTOR_TRACK_TOL, _ADJ_FACTOR_TRACK_REL * abs(expected - 1.0))
            if abs(ratio - expected) <= track_tol:
                continue  # the booked event explains this step — clean
            if material_event:
                reasons.append(
                    f"booked_event_factor_mismatch{stamp}: split_factor={sf}, "
                    f"dividend_cash={dv} (prev_close={pc}); adj_factor step "
                    f"{ratio:.4f} vs expected {expected:.4f}"
                )
                continue
        if abs(ratio - 1.0) >= _ADJ_UNBOOKED_STEP_TOL:
            reasons.append(
                f"unbooked_adj_factor_step{stamp}: adj_factor step {ratio:.4f} "
                "with no booked corporate action"
            )
    return reasons


def _write_adjustment_quarantine(symbol: str, violations: list, kind: str = "quarantine") -> None:
    """Append a quarantine record; ledger failure must never break ingest."""
    try:
        ledger = WAREHOUSE_DIR / _ADJUSTMENT_QUARANTINE_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "symbol": symbol,
            "timeframe": "1d",
            "kind": kind,
            "quarantined_at_utc": datetime.now(timezone.utc).isoformat(),
            "violations": violations,
        }
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"⚠️  Could not write adjustment quarantine ledger: {exc}")


# ── Adjustment provenance checks (equity daily frames) ───────────────────────
# The booked-event auditor above checks a frame against ITSELF. These checks
# compare it against the history it is about to merge into — closing the
# middle band: a fallback source that writes the full unadjusted dummy
# signature (adj == raw, factors 1.0, no booked events) silently overwrote
# adjusted history through keep-last dedup, and two legitimately-adjusted
# sources could disagree about the same ex-date without tripping any rule.
# Vendor-refresh safety: global restatements shift the adjusted LEVELS of the
# whole series together — the checks compare ratio *discontinuities* across
# shared dates, never levels, so an honest re-adjustment refresh is not blocked.
_PROV_STEP_TOL = 0.005   # 0.5% ratio discontinuity between adjusted sources


def _is_full_dummy_frame(df: pd.DataFrame) -> bool:
    """Full unadjusted-dummy signature: every *_adj equals *_raw, factors all
    1.0, no booked events anywhere in the frame."""
    if df is None or df.empty or "adj_factor" not in df.columns:
        return False
    factor = pd.to_numeric(df["adj_factor"], errors="coerce")
    if not ((factor - 1.0).abs() <= 1e-9).all():
        return False
    if "split_factor" in df.columns:
        split = pd.to_numeric(df["split_factor"], errors="coerce").fillna(1.0)
        if not ((split - 1.0).abs() <= 1e-9).all():
            return False
    if "dividend_cash" in df.columns:
        div = pd.to_numeric(df["dividend_cash"], errors="coerce").fillna(0.0)
        if not (div.abs() <= 1e-9).all():
            return False
    if "close_adj" in df.columns and "close_raw" in df.columns:
        ca = pd.to_numeric(df["close_adj"], errors="coerce")
        cr = pd.to_numeric(df["close_raw"], errors="coerce")
        both = ca.notna() & cr.notna()
        if both.any() and not ((ca[both] - cr[both]).abs() <= 1e-9).all():
            return False
    return True


def adjustment_provenance_violations(incoming: pd.DataFrame, existing: pd.DataFrame) -> list:
    """Audit an incoming DAILY equity frame against stored history.

    (1) A full-dummy frame landing over dates whose stored rows carry real
        adjustment factors is a provenance regression: merged keep-last dedup
        would silently overwrite adjusted history with raw prices.
    (2) When BOTH sides are genuinely adjusted but disagree *discontinuously*
        across shared dates (ratio of adjusted closes steps at a shared
        boundary), the two sources disagree about an ex-date — quarantine
        rather than pick a lineage silently.
    The healing direction is deliberately allowed: a genuinely-adjusted frame
    landing over dummy history repairs it (split self-heal pattern).
    """
    reasons: list = []
    incoming_dummy = _is_full_dummy_frame(incoming)
    if incoming_dummy:
        if existing is not None and not existing.empty and "adj_factor" in existing.columns:
            # Overlap-scoped refusal (2026-08-07): since the vendor basis
            # flip, factor == 1.0 with adj == raw and no booked events is the
            # steady state for every bar after a stock's latest corporate
            # action, so an honest short incremental capture window is
            # frame-locally a "full dummy" — 65 of 123 refusals on the
            # 2026-08-07 captures were this false positive and froze healthy
            # daily books. Persisting a dummy frame can only rewrite stored
            # rows on dates it actually covers (keep-last dedup), so the
            # refusal narrows to OVERLAPPED stored rows that carry real
            # adjustment bookkeeping; a dummy landing on factor-1.0,
            # event-free dates rewrites nothing.
            scope = existing
            if "bar_ts_utc" in incoming.columns and "bar_ts_utc" in existing.columns:
                inc_dates = set(pd.to_datetime(incoming["bar_ts_utc"], utc=True).dt.date)
                scope = existing[
                    pd.to_datetime(existing["bar_ts_utc"], utc=True).dt.date.isin(inc_dates)
                ]
            f = pd.to_numeric(scope["adj_factor"], errors="coerce")
            booked = pd.Series(False, index=scope.index)
            if "split_factor" in scope.columns:
                booked |= pd.to_numeric(scope["split_factor"], errors="coerce").fillna(1.0).sub(1.0).abs() > 1e-9
            if "dividend_cash" in scope.columns:
                booked |= pd.to_numeric(scope["dividend_cash"], errors="coerce").fillna(0.0).abs() > 1e-9
            if ((f - 1.0).abs() > 1e-9).any() or booked.any():
                reasons.append(
                    "dummy_frame_over_adjusted_history: incoming frame is the full "
                    "unadjusted dummy signature but stored history it would overwrite "
                    "carries real adjustment factors; persisting would silently rewrite "
                    "adjusted prices"
                )
        return reasons

    if (
        existing is None
        or existing.empty
        or _is_full_dummy_frame(existing)
        or "close_adj" not in incoming.columns
        or "close_adj" not in existing.columns
        or "bar_ts_utc" not in incoming.columns
        or "bar_ts_utc" not in existing.columns
    ):
        return reasons

    inc = incoming.copy()
    exi = existing.copy()
    inc["_d"] = pd.to_datetime(inc["bar_ts_utc"], utc=True).dt.date
    exi["_d"] = pd.to_datetime(exi["bar_ts_utc"], utc=True).dt.date
    inc = inc.drop_duplicates(subset=["_d"], keep="last").set_index("_d")["close_adj"]
    exi = exi.drop_duplicates(subset=["_d"], keep="last").set_index("_d")["close_adj"]
    shared = inc.index.intersection(exi.index).sort_values()
    if len(shared) < 2:
        return reasons
    a = pd.to_numeric(inc.loc[shared], errors="coerce")
    b = pd.to_numeric(exi.loc[shared], errors="coerce")
    ok = a.notna() & b.notna() & (b != 0)
    a, b = a[ok], b[ok]
    if len(a) < 2:
        return reasons
    ratio = a / b
    steps = (ratio / ratio.shift(1) - 1.0).abs().dropna()
    if not steps.empty and steps.max() > _PROV_STEP_TOL:
        worst = steps.idxmax()
        reasons.append(
            f"conflicting_adjustment_sources @ {worst}: adjusted series disagree "
            f"discontinuously at a shared date (ratio step {steps.max():.4f}); "
            "refusing to rewrite stored adjustment lineage"
        )
    return reasons


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
            if not violations and not (is_crypto(symbol) or is_futures(symbol)):
                existing_1d = load_from_warehouse(symbol, "1d")
                violations = adjustment_provenance_violations(group, existing_1d)
                if not violations and _is_full_dummy_frame(group):
                    # Fail-open on missing data: a dummy frame with NO contradicting
                    # history cannot be proven wrong — persist it, but log the
                    # provenance so the irreducible band is enumerable, not silent.
                    _write_adjustment_quarantine(
                        symbol,
                        [
                            "unverified_provenance: full unadjusted-dummy signature "
                            "(adj == raw, factors 1.0, no booked events) and no "
                            "contradicting stored history; adjustment status not "
                            "independently confirmable"
                        ],
                        kind="unverified_provenance",
                    )
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
