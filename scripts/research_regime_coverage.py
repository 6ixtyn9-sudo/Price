"""Build a full-universe historical bull/bear/neutral coverage report.

Read-only against the warehouse. No discovery, deployment, or orders.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from price.config import SYMBOLS
from price.warehouse import load_from_warehouse

SHORT_MA = 50
LONG_MA = 200
DEFAULT_OUTPUT = Path("localdata/research/universe_regime_coverage.csv")


def _regime_series(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty or "close_adj" not in df.columns:
        return pd.Series(dtype="object")
    close = pd.to_numeric(df["close_adj"], errors="coerce")
    short = close.rolling(SHORT_MA, min_periods=SHORT_MA).mean()
    long = close.rolling(LONG_MA, min_periods=LONG_MA).mean()
    regime = pd.Series("warmup", index=df.index, dtype="object")
    bull = short.notna() & long.notna() & (short > long) & (close > short)
    bear = short.notna() & long.notna() & (short < long) & (close < short)
    neutral = short.notna() & long.notna() & ~(bull | bear)
    regime.loc[bull] = "bull"
    regime.loc[bear] = "bear"
    regime.loc[neutral] = "neutral"
    return regime


def build_coverage(symbols=None, timeframes=("1d", "1h")) -> pd.DataFrame:
    symbols = list(symbols or SYMBOLS)
    rows = []
    for symbol in symbols:
        for timeframe in timeframes:
            try:
                df = load_from_warehouse(symbol, timeframe)
            except Exception as exc:  # noqa: BLE001 - preserve per-symbol status
                rows.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "coverage_status": "warehouse_error",
                    "error": str(exc),
                })
                continue
            if df is None or df.empty:
                rows.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "coverage_status": "missing_warehouse",
                })
                continue
            df = df.sort_values("bar_ts_utc").reset_index(drop=True)
            regimes = _regime_series(df)
            counts = regimes.value_counts()
            latest = regimes.iloc[-1] if len(regimes) else "unknown"
            rows.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "coverage_status": "ok",
                "first_bar": str(df["bar_ts_utc"].iloc[0]),
                "last_bar": str(df["bar_ts_utc"].iloc[-1]),
                "total_bars": len(df),
                "bull_bars": int(counts.get("bull", 0)),
                "bear_bars": int(counts.get("bear", 0)),
                "neutral_bars": int(counts.get("neutral", 0)),
                "warmup_bars": int(counts.get("warmup", 0)),
                "current_regime": latest,
            })
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build full-universe regime coverage telemetry.")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--timeframes", nargs="+", default=["1d", "1h"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_coverage(args.symbols, tuple(args.timeframes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    print(f"Saved {len(result)} universe regime coverage rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
