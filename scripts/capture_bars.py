import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
import pandas as pd
from datetime import datetime, timedelta, timezone
from price.config import SYMBOLS, is_futures, is_crypto, is_equity, ETF_SYMBOLS, UNIVERSE_TIER, UNIVERSE_MAX_SYMBOLS
from price.data_sources import (
    fetch_universal_bars,
    fetch_alpaca_bars,
    fetch_tiingo_daily_bars,
    fetch_alpaca_futures_bars,
    fetch_crypto_bars,
    resolve_universal_source,
)
from price.warehouse import save_to_warehouse, load_from_warehouse, resample_15m_to_1h, propagate_adjustment_factors


def _needs_resample_and_propagate(symbol: str, tf: str, source: str) -> bool:
    """Return True if this (symbol, tf, source) combo still needs the old
    15m→1h resample + adjustment-propagation pipeline.

    yfinance 1h provides adjusted bars directly (no resample, no propagation).
    Alpaca 1h / 15m are raw-only and still need the resample + propagate step.
    """
    if tf != "15m":
        return False
    # If the source is alpaca (the old path), 15m needs resample+propagate.
    # yfinance doesn't serve 15m beyond 60 days, so 15m is always Alpaca.
    return True


def capture_bars(target_symbols=None, target_timeframes=None, days_lookback=365, use_universal_router=True):
    symbols = target_symbols or SYMBOLS
    timeframes = target_timeframes or ["1d", "15m", "1h"]

    end_dt = datetime.now(timezone.utc)

    print(f"🌐 Universe tier: {UNIVERSE_TIER} | symbols in batch: {len(symbols)} | max_cap: {UNIVERSE_MAX_SYMBOLS}")
    print(f"   Sample: {symbols[:10]}")

    # ── Phase 1: Fetch all timeframes ────────────────────────────────────
    # With yfinance as primary for equity 1d + 1h, the 1h bars come directly
    # (no 15m→1h resample). The 1h timeframe is no longer skipped.
    failed = []  # Track failures for proper exit code

    for symbol in symbols:
        symbol = symbol.upper() if "/" not in symbol else symbol  # keep BTC/USD case
        for tf in timeframes:
            if tf not in ["1d", "15m", "1h"]:
                continue

            # Determine source for logging using the same routing rules as
            # fetch_universal_bars.
            if use_universal_router:
                source = resolve_universal_source(symbol, tf)
            elif is_crypto(symbol):
                source = "alpaca_crypto"
            elif is_futures(symbol):
                source = "alpaca_futures"
            elif tf == "1d" and symbol in ETF_SYMBOLS:
                source = "tiingo"
            else:
                source = "alpaca"

            print(f"\n🚀 Ingesting {symbol} ({tf}) from {source.upper()}...")

            existing_df = load_from_warehouse(symbol, tf)
            if not existing_df.empty:
                latest_ts = pd.to_datetime(existing_df['bar_ts_utc'].max())
                start_dt = latest_ts - timedelta(days=1)
                print(f"Incremental update: Existing data found. Querying starting at {start_dt}.")
            else:
                start_dt = end_dt - timedelta(days=days_lookback)
                print(f"No existing data. Querying history of {days_lookback} days (starting at {start_dt}).")

            if start_dt >= end_dt:
                print("Warehouse is already up to date.")
                continue

            try:
                if use_universal_router:
                    df = fetch_universal_bars(symbol, tf, start_dt, end_dt)
                else:
                    # legacy path
                    if is_futures(symbol):
                        df = fetch_alpaca_futures_bars(symbol, tf, start_dt, end_dt)
                    elif is_crypto(symbol):
                        df = fetch_crypto_bars(symbol, tf, start_dt, end_dt)
                    elif tf == "1d" and source == "tiingo":
                        df = fetch_tiingo_daily_bars(symbol, start_dt, end_dt)
                    else:
                        df = fetch_alpaca_bars(symbol, tf, start_dt, end_dt)

                if df is not None and not df.empty:
                    print(f"Successfully fetched {len(df)} bars.")
                    save_to_warehouse(df)
                else:
                    print("No new bars returned.")
            except Exception as e:
                print(f"❌ Error fetching {symbol} ({tf}) from {source}: {e}")
                failed.append((symbol, tf, source, str(e)))

    # ── Phase 2: Post-process 15m-derived data ───────────────────────────
    # Only symbols that still use Alpaca 15m need the resample+propagate step.
    # yfinance 1h bars arrive with adj columns already filled — no propagation.
    # This eliminates the double-save cascade where capture_bars and
    # build_warehouse both did resample+propagate.
    for symbol in symbols:
        symbol = symbol.upper() if "/" not in symbol else symbol

        # Check if 15m data exists (Alpaca path) and needs 1h resample
        df_15m = load_from_warehouse(symbol, "15m")
        if df_15m.empty:
            continue

        # Resample 15m→1h only if there's no yfinance 1h data already
        df_1h = load_from_warehouse(symbol, "1h")
        needs_resample = df_1h.empty or (
            "source" in df_1h.columns
            and df_1h["source"].astype(str).str.contains("alpaca").any()
        )
        if needs_resample:
            print(f"\n🔧 Resampling 15m→1h for {symbol} (Alpaca 1h path)...")
            try:
                resample_15m_to_1h(symbol)
            except Exception as re:
                print(f"  1h resample warning: {re}")
                failed.append((symbol, "1h_resample", "resample_15m_to_1h", str(re)))

        # Propagate adjustment factors for equities from daily bars.
        # yfinance 1h already has adj columns; only 15m and Alpaca-derived
        # 1h need propagation.
        if is_equity(symbol) and not is_futures(symbol) and "/" not in symbol:
            print(f"🔧 Propagating adjustment factors for {symbol}...")
            try:
                propagate_adjustment_factors(symbol)
            except Exception as pe:
                print(f"  adj propagate warning: {pe}")
                failed.append((symbol, "adj_propagate", "propagate_adjustment_factors", str(pe)))

    # Summary and exit code
    print("\n✅ Capture complete.")
    if failed:
        print(f"\n⚠️  {len(failed)} operation(s) failed:")
        for sym, tf, op, err in failed:
            print(f"  - {sym} ({tf}) [{op}]: {err}")
        print("Exiting with code 1 due to failures.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest historical OHLCV bar data - universal Alpaca free-tier")
    parser.add_argument("--symbols", nargs="+", help="Symbols to ingest (overrides universe)")
    parser.add_argument("--timeframes", nargs="+", choices=["15m", "1d", "1h"], help="Timeframes to ingest")
    parser.add_argument("--days", type=int, default=365, help="Days of lookback")
    parser.add_argument("--tier", choices=["etf", "etf_plus", "sp500", "allowlist", "full", "crypto", "all"], help="Override UNIVERSE_TIER")
    parser.add_argument("--max-symbols", type=int, help="Cap universe size")
    parser.add_argument("--universe", action="store_true", help="Print resolved universe and exit")
    parser.add_argument("--no-router", action="store_true", help="Disable universal router (legacy)")

    args = parser.parse_args()

    # tier override
    if args.tier:
        import os
        os.environ["UNIVERSE_TIER"] = args.tier
        # re-import symbols dynamically
        from price.universe import get_universe
        target_symbols = get_universe(args.tier, max_symbols=args.max_symbols)
    elif args.symbols:
        target_symbols = args.symbols
    else:
        target_symbols = None

    if args.universe:
        from price.config import SYMBOLS as CFG_SYMBOLS
        syms = target_symbols or CFG_SYMBOLS
        print(f"Resolved universe ({len(syms)} symbols):")
        for s in syms[:50]:
            print(f"  {s}")
        if len(syms) > 50:
            print(f"  ... +{len(syms)-50} more")
        import sys
        sys.exit(0)

    capture_bars(
        target_symbols=target_symbols,
        target_timeframes=args.timeframes,
        days_lookback=args.days,
        use_universal_router=not args.no_router
    )