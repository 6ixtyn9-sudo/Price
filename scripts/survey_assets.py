#!/usr/bin/env python3
"""
Build the comprehensive allowlist from Alpaca's live asset inventory.

The old workflow printed an 8k+ Python list that then had to be pasted into
src/price/config.py.  The robust workflow writes the generated universe to
localdata/explicit_allowlist.json; config.py automatically loads that file when
UNIVERSE_TIER=allowlist.

Run:
    python3 scripts/survey_assets.py

Optional:
    python3 scripts/survey_assets.py --output localdata/explicit_allowlist.json
    python3 scripts/survey_assets.py --print-python-list
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from price.config import ALLOWLIST_CACHE_PATH, ALPACA_API_KEY, ALPACA_SECRET_KEY  # noqa: E402

# SPAC/warrant/unit/right contamination patterns in symbol or name.
BAD_NAME_TERMS = [
    "acquisition",
    "spac",
    "blank check",
    "warrant",
    "right",
    "unit",
    " wt",
    " rt",
    " ut",
    "-wt",
    "-rt",
    "-ut",
]
BAD_SYMBOL_SUFFIX_RE = re.compile(r"(W|R|U|P|WS|WT|RT|UN)$")
BAD_SYMBOL_CHARS_RE = re.compile(r"[.\-/ ]")


def is_likely_liquid_equity(symbol: str, name: str | None = None) -> bool:
    """Return True if symbol looks like a usable US equity/ETF.

    This is intentionally hygiene-focused, not a final liquidity validator.
    The later warehouse prune step removes symbols with too few bars.
    """
    s = symbol.upper()
    n = (name or "").lower()

    # Drop symbols with weird characters.
    if BAD_SYMBOL_CHARS_RE.search(s):
        return False

    # Drop obvious SPAC/warrant/unit patterns.
    if BAD_SYMBOL_SUFFIX_RE.search(s) and len(s) >= 3:
        if "acquisition" in n or "spac" in n or "warrant" in n or "unit" in n:
            return False
        # Keep real short tickers that happen to end W/R/U/P; reject longer
        # suffix-looking classes unless the name is clearly harmless.
        if len(s) >= 4 and not any(t in n for t in ["acquisition", "warrant", "unit"]):
            return False

    # Drop if name contains SPAC/warrant/unit keywords.
    if any(t in n for t in BAD_NAME_TERMS):
        return False

    # Exchange-traded products with ETN-style tracking notes often pollute state discovery.
    if "etracs" in n or ("tracked" in n and "note" in n):
        return False

    return True


def get_futures_allowlist() -> list[str]:
    return [
        # Equity Index
        "ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "DMY",
        # Interest Rates
        "ZB", "MZB", "ZN", "MZN", "ZT", "MZT", "ZF",
        # Energy
        "CL", "MCL", "NG", "MNG",
        # Metals
        "GC", "MGC", "SI", "SIL", "HG",
        # Agriculture
        "LE", "HE", "CC", "KC", "CT", "ZS", "ZM", "SB", "RS",
        # Crypto futures / symbols exposed by Alpaca where available
        "BTC", "ETH",
    ]


def filter_crypto_allowlist(crypto_symbols: list[str]) -> list[str]:
    # Stablecoin-only pairs and extreme meme/noise pairs add little signal here.
    crypto_reject = {
        "USDC/USD", "USDT/USD", "USDC/USDT", "USDT/USDC",
        "USDC/USDG", "USDG/USD", "BCH/BTC", "ETH/BTC", "LINK/BTC",
        "LTC/BTC", "UNI/BTC", "BONK/USD", "BONK/USDC", "BONK/USDT",
        "SHIB/USD", "SHIB/USDC", "SHIB/USDT", "PEPE/USD", "TRUMP/USD",
        "HYPE/USD", "POL/USD", "SKY/USD",
    }
    return sorted(c for c in crypto_symbols if c not in crypto_reject)


def print_wrapped_symbols(title: str, symbols: list[str], per_line: int = 10) -> None:
    print(f"\n{title} ({len(symbols)}):")
    for i in range(0, len(symbols), per_line):
        print("  " + " ".join(symbols[i : i + per_line]))


def print_python_list(equities: list[str], futures: list[str], crypto: list[str]) -> None:
    print("\n=== COPY-READY PYTHON LIST ===")
    print(
        f"# Generated allowlist ({len(equities)} equities + "
        f"{len(futures)} futures + {len(crypto)} crypto):"
    )
    print("EXPLICIT_ALLOWLIST = [")
    for i in range(0, len(equities), 10):
        print("    " + " ".join(f'\"{s}\",' for s in equities[i : i + 10]))
    print("    # Futures")
    for i in range(0, len(futures), 10):
        print("    " + " ".join(f'\"{s}\",' for s in futures[i : i + 10]))
    print("    # Crypto")
    for i in range(0, len(crypto), 10):
        print("    " + " ".join(f'\"{s}\",' for s in crypto[i : i + 10]))
    print("]")


def write_allowlist_file(
    output_path: Path,
    equities: list[str],
    futures: list[str],
    crypto: list[str],
    equity_exchanges: Counter,
    rejected_count: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_symbols = sorted(set(equities + futures + crypto))
    payload = {
        "equities": equities,
        "futures": futures,
        "crypto": crypto,
        "all": all_symbols,
        "meta": {
            "count_equities": len(equities),
            "count_futures": len(futures),
            "count_crypto": len(crypto),
            "count_total": len(all_symbols),
            "count_rejected_equities": rejected_count,
            "equities_by_exchange": dict(equity_exchanges),
            "source": "alpaca /v2/assets",
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Survey Alpaca assets and build allowlist cache")
    parser.add_argument(
        "--output",
        type=Path,
        default=ALLOWLIST_CACHE_PATH,
        help=f"JSON output path loaded by config.py (default: {ALLOWLIST_CACHE_PATH})",
    )
    parser.add_argument("--no-write", action="store_true", help="Survey only; do not write JSON")
    parser.add_argument(
        "--print-python-list",
        action="store_true",
        help="Also print the old copy/paste Python list (very large)",
    )
    args = parser.parse_args(argv)

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env", file=sys.stderr)
        return 1

    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

    print("Fetching Alpaca assets...")
    req = GetAssetsRequest(status=AssetStatus.ACTIVE)
    all_assets = list(client.get_all_assets(req))

    equities_all: list[tuple[str, str, str]] = []
    crypto_all: list[str] = []

    for a in all_assets:
        if not a.tradable:
            continue
        sym = str(a.symbol).upper()
        ac = str(a.asset_class) if a.asset_class else ""
        exch = str(a.exchange) if a.exchange else ""
        name = a.name or ""

        if "crypto" in ac.lower() or "/" in sym:
            crypto_all.append(sym)
        elif any(e in exch for e in ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS")) and "OTC" not in exch:
            equities_all.append((sym, name, exch))

    equities_all.sort(key=lambda x: x[0])
    crypto_all = sorted(set(crypto_all))

    liquid_equities: list[str] = []
    rejected_equities: list[str] = []
    equity_exchanges: Counter = Counter()
    for sym, name, exch in equities_all:
        if is_likely_liquid_equity(sym, name):
            liquid_equities.append(sym)
            equity_exchanges[exch] += 1
        else:
            rejected_equities.append(sym)

    futures_allowlist = get_futures_allowlist()
    crypto_allowlist = filter_crypto_allowlist(crypto_all)
    total_count = len(set(liquid_equities + futures_allowlist + crypto_allowlist))

    print("\n=== ALPACA ASSET INVENTORY ===")
    print(f"Total equities (NYSE/NASDAQ/AMEX/ARCA/BATS): {len(equities_all)}")
    print(f"  After SPAC/warrant/unit/OTC filter:        {len(liquid_equities)}")
    print(f"  Rejected:                                   {len(rejected_equities)}")
    print(f"Total crypto:                                 {len(crypto_all)}")

    print("\n=== COMPREHENSIVE ALLOWLIST ===")
    print(f"Equities:  {len(liquid_equities)}")
    print(f"Futures:   {len(futures_allowlist)}")
    print(f"Crypto:    {len(crypto_allowlist)}")
    print(f"TOTAL:     {total_count}")
    print(f"Equities by exchange: {dict(equity_exchanges)}")

    print_wrapped_symbols("Crypto allowlist", crypto_allowlist)
    print_wrapped_symbols("Futures allowlist", futures_allowlist)

    if not args.no_write:
        write_allowlist_file(
            args.output,
            liquid_equities,
            futures_allowlist,
            crypto_allowlist,
            equity_exchanges,
            len(rejected_equities),
        )
        print(f"\n✅ Wrote generated allowlist JSON: {args.output}")

    if args.print_python_list:
        print_python_list(liquid_equities, futures_allowlist, crypto_allowlist)

    print("\n=== BUILD PLAN ===")
    if args.no_write:
        print("1. Re-run without --no-write to create localdata/explicit_allowlist.json")
    else:
        print("1. No manual paste needed — src/price/config.py loads the JSON automatically")
    print("2. Verify: python3 scripts/capture_bars.py --tier allowlist --universe")
    print("3. Backfill: python3 scripts/capture_bars.py --timeframes 1d --days 1825")
    print("4. Prune: python3 scripts/prune_warehouse.py --min-bars 200")
    print("\nDiscovery will auto-filter thin histories after the prune step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
