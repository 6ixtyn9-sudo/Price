#!/usr/bin/env python3
"""
Survey Alpaca's full /v2/assets list and print categorised symbol counts.
Run: python3 scripts/survey_assets.py
"""
import sys
import os

# Ensure price package is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from price.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

def main():
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("ERROR: ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env")
        return

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus

    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

    categories = {
        "us_equity": {"active": [], "name": "US Equities (NYSE/NASDAQ/AMEX)"},
        "crypto": {"active": [], "name": "Crypto"},
    }

    # Pull all active assets
    for asset_class_key, cat in categories.items():
        req = GetAssetsRequest(
            status=AssetStatus.ACTIVE,
            asset_class=getattr(AssetClass, asset_class_key) if hasattr(AssetClass, asset_class_key) else None
        )
        try:
            assets = client.get_all_assets(req)
            for a in assets:
                if a.tradable:
                    cat["active"].append(a.symbol)
        except Exception as e:
            print(f"  Error fetching {asset_class_key}: {e}")

    # Also get crypto with explicit asset_class
    req = GetAssetsRequest(status=AssetStatus.ACTIVE)
    all_assets = client.get_all_assets(req)

    equities = []
    crypto = []
    exchanges = {}

    for a in all_assets:
        if not a.tradable:
            continue
        if a.status != AssetStatus.ACTIVE:
            continue
        sym = a.symbol
        ac = str(a.asset_class) if a.asset_class else "unknown"
        exch = str(a.exchange) if a.exchange else "unknown"

        if "crypto" in ac.lower() or "/" in sym:
            crypto.append(sym)
        elif "us_equity" in ac.lower() or exch in ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA", "NMS"):
            equities.append(sym)
        exchanges[ac] = exchanges.get(ac, 0) + 1

    equities.sort()
    crypto.sort()

    print(f"=== ALPACA ASSET SURVEY ===")
    print(f"")
    print(f"US Equities (tradable, NYSE/NASDAQ/AMEX/ARCA): {len(equities)}")
    print(f"Crypto (tradable):                              {len(crypto)}")
    print(f"")
    print(f"Asset class breakdown: {exchanges}")
    print(f"")
    print(f"--- US Equities ({len(equities)} symbols) ---")
    # Print first 50 and last 10
    for chunk in [equities[:50], equities[50:100], equities[100:150], equities[150:200], equities[200:]]:
        print("  " + " ".join(chunk))
    print(f"")
    print(f"--- Crypto ({len(crypto)} symbols) ---")
    for chunk in [crypto[i:i+20] for i in range(0, len(crypto), 20)]:
        print("  " + " ".join(chunk))

    # Show exchange breakdown for equities
    from collections import Counter
    equity_exchanges = Counter()
    equity_name_keywords = Counter()
    for a in all_assets:
        if not a.tradable:
            continue
        sym = a.symbol
        ac = str(a.asset_class) if a.asset_class else ""
        exch = str(a.exchange) if a.exchange else ""
        if "us_equity" in ac.lower() or exch in ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA", "NMS"):
            equity_exchanges[exch] += 1
            name_lower = (a.name or "").lower()
            for kw in ["acquisition", "spac", "warrant", "right", "unit"]:
                if kw in name_lower:
                    equity_name_keywords[kw] += 1

    print(f"")
    print(f"Equity exchange breakdown: {dict(equity_exchanges)}")
    print(f"SPAC/Warrant keywords in names: {dict(equity_name_keywords)}")
    print(f"")

    # Futures (Alpaca free tier)
    print(f"--- Futures (Alpaca free tier IEX feed) ---")
    futures_list = [
        # Equity Index
        "ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "DMY",
        # Interest Rates
        "ZB", "MZB", "ZN", "MZN", "ZT", "MZT", "ZF", "ZT",
        # Commodities
        "CL", "MCL", "GC", "MGC", "SI", "SIL", "HG", "NG", "MNG",
        "LE", "HE", "CC", "KC", "CT", "ZS", "ZM", "SB", "RS",
        # Crypto
        "BTC", "ETH",
    ]
    print(f"  Available on free tier (approximate): {len(futures_list)}")
    print(f"  {futures_list}")

    print(f"")
    print(f"=== COPY-READY LISTS ===")
    print(f"")
    print(f"# US EQUITIES (first 200, liquid):")
    print(" ".join(equities[:200]))
    print(f"")
    print(f"# CRYPTO ({len(crypto)} symbols):")
    print(" ".join(crypto))
    print(f"")
    print(f"# FUTURES (major contracts):")
    print(" ".join(futures_list))

if __name__ == "__main__":
    main()
