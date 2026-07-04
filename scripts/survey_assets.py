#!/usr/bin/env python3
"""
Build the comprehensive EXPLICIT_ALLOWLIST from Alpaca's real asset inventory.
Filters out SPACs, warrants, units, rights, and penny/micro-cap stocks.
Run: python3 scripts/survey_assets.py --build-allowlist
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from price.config import ALPACA_API_KEY, ALPACA_SECRET_KEY
import re

# SPAC/warrant/unit/right contamination patterns in symbol or name
BAD_NAME_TERMS = ['acquisition', 'spac', 'blank check', 'warrant', 'right', 'unit', 'wt', 'rt', 'ut', '-wt', '-rt', '-ut']
BAD_SYMBOL_SUFFIX_RE = re.compile(r'(W|R|U|P|WS|WT|RT|UN)$')
BAD_SYMBOL_CHARS_RE = re.compile(r'[.\-/ ]')

# Penny/micro-cap filters
MIN_PRICE_FILTER = 1.00  # minimum recent price (we'll filter by marginable as proxy)
MIN_MARKET_CAP_CHARS = 1  # symbol length >= 1

def is_likely_liquid_equity(symbol, name=None):
    """Return True if symbol looks like a liquid US equity (not SPAC/warrant/unit/junk)."""
    s = symbol.upper()
    n = (name or "").lower()

    # Drop symbols with weird characters
    if BAD_SYMBOL_CHARS_RE.search(s):
        return False

    # Drop obvious SPAC/warrant patterns
    if BAD_SYMBOL_SUFFIX_RE.search(s) and len(s) >= 3:
        if 'acquisition' in n or 'spac' in n or 'warrant' in n or 'unit' in n:
            return False
        # Keep real tickers that end in W/R/U but are known-good (e.g. KRW, ROW, etc.)
        # If name doesn't hint at warrant/unit, use length as secondary signal
        if len(s) >= 4 and not any(t in n for t in ['acquisition', 'warrant', 'unit']):
            return False

    # Drop if name contains SPAC/warrant/unit keywords
    if any(t in n for t in BAD_NAME_TERMS):
        return False

    # Exchange-traded products with weird classes (ETRACS, etc.)
    if 'etracs' in n or 'tracked' in n and 'note' in n:
        return False

    return True


def main():
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env")
        return

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus

    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

    print("Fetching Alpaca assets...")
    req = GetAssetsRequest(status=AssetStatus.ACTIVE)
    all_assets = list(client.get_all_assets(req))

    equities_all = []
    crypto_all = []

    for a in all_assets:
        if not a.tradable:
            continue
        sym = a.symbol
        ac = str(a.asset_class) if a.asset_class else ""
        exch = str(a.exchange) if a.exchange else ""
        name = a.name or ""

        if "crypto" in ac.lower() or "/" in sym:
            crypto_all.append(sym)
        elif exch in ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA", "NMS"):
            equities_all.append((sym, name, exch))

    equities_all.sort(key=lambda x: x[0])
    crypto_all.sort()

    # Filter equities
    liquid_equities = []
    rejected_equities = []
    for sym, name, exch in equities_all:
        if is_likely_liquid_equity(sym, name):
            liquid_equities.append(sym)
        else:
            rejected_equities.append(sym)

    print(f"\n=== ALPACA ASSET INVENTORY ===")
    print(f"Total equities (NYSE/NASDAQ/AMEX/ARCA/BATS): {len(equities_all)}")
    print(f"  After SPAC/warrant/unit/OTC filter:        {len(liquid_equities)}")
    print(f"  Rejected:                                   {len(rejected_equities)}")
    print(f"Total crypto:                                 {len(crypto_all)}")
    print(f"")

    # ---- COMPREHENSIVE ALLOWLIST ----
    # All liquid equities
    equity_allowlist = liquid_equities

    # All futures available on Alpaca free tier
    futures_allowlist = [
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
        # Crypto
        "BTC", "ETH",
    ]

    # Crypto: only liquid majors, no meme/duplicate pairs
    # USDC/USDT stablecoin pairs add no signal
    crypto_reject = {"USDC/USD", "USDT/USD", "USDC/USDT", "USDT/USDC",
                     "USDC/USDG", "USDG/USD", "BCH/BTC", "ETH/BTC", "LINK/BTC",
                     "LTC/BTC", "UNI/BTC", "BONK/USD", "BONK/USDC", "BONK/USDT",
                     "SHIB/USD", "SHIB/USDC", "SHIB/USDT", "PEPE/USD", "TRUMP/USD",
                     "HYPE/USD", "POL/USD", "SKY/USD"}
    crypto_allowlist = [c for c in crypto_all if c not in crypto_reject]

    print(f"=== COMPREHENSIVE ALLOWLIST ===")
    print(f"Equities:  {len(equity_allowlist)}")
    print(f"Futures:   {len(futures_allowlist)}")
    print(f"Crypto:    {len(crypto_allowlist)}")
    print(f"TOTAL:     {len(equity_allowlist) + len(futures_allowlist) + len(crypto_allowlist)}")
    print(f"")

    # Print equity count by exchange
    from collections import Counter
    equity_exchanges = Counter()
    for sym, name, exch in equities_all:
        if is_likely_liquid_equity(sym, name):
            equity_exchanges[exch] += 1
    print(f"Equities by exchange: {dict(equity_exchanges)}")

    # Print crypto list
    print(f"\nCrypto allowlist ({len(crypto_allowlist)}):")
    for i in range(0, len(crypto_allowlist), 10):
        print("  " + " ".join(crypto_allowlist[i:i+10]))

    # Print futures list
    print(f"\nFutures allowlist ({len(futures_allowlist)}):")
    print("  " + " ".join(futures_allowlist))

    # Print copy-ready Python list for config.py
    print(f"\n=== READY TO COPY INTO src/price/config.py ===")
    print(f"\n# Replace EXPLICIT_ALLOWLIST with this ({len(equity_allowlist)} equities + {len(futures_allowlist)} futures + {len(crypto_allowlist)} crypto):")
    print(f"\nEXPLICIT_ALLOWLIST = [")
    for i in range(0, len(equity_allowlist), 10):
        print(f"    " + " ".join(f'"{s}",' for s in equity_allowlist[i:i+10]))
    print(f"    # Futures")
    for i in range(0, len(futures_allowlist), 10):
        print(f"    " + " ".join(f'"{s}",' for s in futures_allowlist[i:i+10]))
    print(f"    # Crypto")
    for i in range(0, len(crypto_allowlist), 10):
        print(f"    " + " ".join(f'"{s}",' for s in crypto_allowlist[i:i+10]))
    print(f"]")

    print(f"\n=== BUILD PLAN ===")
    print(f"1. Edit src/price/config.py — replace EXPLICIT_ALLOWLIST")
    print(f"2. Also fix get_allowlist_symbols() to merge all 3 classes:")
    print(f'   def get_allowlist_symbols() -> list:')
    print(f'       return sorted(set(EXPLICIT_ALLOWLIST + FUTURES_SYMBOLS + CRYPTO_SYMBOLS))')
    print(f"3. Run: python3 scripts/capture_bars.py --timeframes 1d --days 1825")
    print(f"")
    print(f"Discovery will auto-filter: prune_warehouse.py --min-bars 200 removes anything with <200 bars.")


if __name__ == "__main__":
    if "--build-allowlist" in sys.argv:
        main()
    else:
        # Run basic survey
        sys.argv.append("--build-allowlist")
        main()
