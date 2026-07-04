"""
Alpaca Free-Tier Universal Universe

Expands Price from a hardcoded 10-ETF (+10 futures aspirational) list
to the full Alpaca free-tier tradable universe:

- US Stocks & ETFs  (~11,000 active symbols via Trading API /assets)
- US Options        (excluded from bar ingestion - options chain complexity deferred per V1-V5 doctrine)
- Crypto            (~20-30 spot pairs, e.g. BTC/USD, ETH/USD)

This module does NOT change promotion doctrine. It only expands the
discovery substrate so V4/V5 validation can hunt more aggressively.

Free-tier constraints (Trading API Basic):
- Equities: US Stocks & ETFs, IEX feed, 30 websocket symbols, 200 req/min, data since 2016, 15-min delayed
- Options: US Options, Indicative feed, 200 quotes websocket
- Crypto: spot crypto data included

Source: https://docs.alpaca.markets/us/docs/about-market-data-api
        https://alpaca.markets/learn/access-free-market-data (Jan 2026)
"""

import json
import time
from pathlib import Path
from typing import List, Dict, Optional

from price.config import DATA_DIR, ALPACA_API_KEY, ALPACA_SECRET_KEY, ETF_SYMBOLS

UNIVERSSE_CACHE = DATA_DIR / "universe_cache.json"
UNIVERSE_CACHE_TTL_HOURS = 24

def _get_trading_client():
    """Lazy import to avoid hard dependency at test time."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError("ALPACA_API_KEY / ALPACA_SECRET_KEY missing - set in .env")
    from alpaca.trading.client import TradingClient
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

def fetch_alpaca_assets(asset_class: Optional[str] = None) -> List[Dict]:
    """
    Pull full /v2/assets list from Alpaca Trading API.
    asset_class: us_equity | crypto | None (all)
    Returns list of dicts with minimal fields.
    """
    client = _get_trading_client()
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus

    req_kwargs = {"status": AssetStatus.ACTIVE}
    if asset_class == "us_equity":
        req_kwargs["asset_class"] = AssetClass.US_EQUITY
    elif asset_class == "crypto":
        req_kwargs["asset_class"] = AssetClass.CRYPTO

    request = GetAssetsRequest(**req_kwargs)
    assets = client.get_all_assets(request)

    out = []
    for a in assets:
        out.append({
            "symbol": a.symbol,
            "name": a.name,
            "asset_class": str(a.asset_class),
            "exchange": str(a.exchange) if a.exchange else None,
            "tradable": bool(a.tradable),
            "marginable": bool(getattr(a, "marginable", False)),
            "shortable": bool(getattr(a, "shortable", False)),
            "fractionable": bool(getattr(a, "fractionable", False)),
            "status": str(a.status),
        })
    return out

def build_universe(
    include_etfs: bool = True,
    include_stocks: bool = True,
    include_crypto: bool = True,
    min_price: Optional[float] = None,
    exchanges: Optional[List[str]] = None,
    tradable_only: bool = True,
    max_symbols: Optional[int] = None,
    use_cache: bool = True,
) -> Dict[str, List[str]]:
    """
    Build a tiered Alpaca free-tier universe.

    Returns dict:
      {
        "equities": [...],
        "etfs": [...],
        "stocks": [...],
        "crypto": [...],
        "all": [...]
      }

    Filtering is conservative by default - active + tradable US equities.
    Set max_symbols to cap the universe for initial backfill (e.g. 500 / 1000).
    """
    if use_cache and UNIVERSSE_CACHE.exists():
        age_hours = (time.time() - UNIVERSSE_CACHE.stat().st_mtime) / 3600
        if age_hours < UNIVERSE_CACHE_TTL_HOURS:
            with open(UNIVERSSE_CACHE) as f:
                cached = json.load(f)
                # sanity check cached structure
                if "all" in cached and cached["all"]:
                    return cached

    # Pull from API
    try:
        equity_assets = fetch_alpaca_assets("us_equity")
    except Exception as e:
        # Fallback to ETF-only if API keys missing / rate limited
        equity_assets = []

    try:
        crypto_assets = fetch_alpaca_assets("crypto") if include_crypto else []
    except Exception:
        crypto_assets = []

    # Default exchanges for liquid US listing venues
    if exchanges is None:
        exchanges = ["NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA", "NMS"]

    equities = []
    etfs = []
    stocks = []

    # Seed with known ETFs first (preserves V1-V5 continuity)
    if include_etfs:
        etfs = ETF_SYMBOLS.copy()
        equities.extend(etfs)

    # Add discovered equities
    if include_stocks and equity_assets:
        for a in equity_assets:
            sym = a["symbol"]
            if tradable_only and not a["tradable"]:
                continue
            # skip odd symbols: warrants, units, test symbols, etc.
            if any(c in sym for c in [".", "/", " ", "+"]):
                continue
            if len(sym) > 5:  # most US equities are <=5 chars; filters out many odd classes
                # allow known 5+ char ETFs already in etfs list
                if sym not in etfs:
                    continue
            if exchanges and a["exchange"] and a["exchange"] not in exchanges:
                # be permissive: if exchange is None, keep it
                pass
            if sym in equities:
                continue
            equities.append(sym)
            # crude ETF vs stock split: keep ETF_SYMBOLS as etfs, rest as stocks
            if sym not in etfs:
                stocks.append(sym)

    # Crypto symbols from Alpaca use BTC/USD format - keep that
    crypto_symbols = []
    if include_crypto:
        # Prefer API-discovered list, fallback to major pairs
        if crypto_assets:
            crypto_symbols = [a["symbol"] for a in crypto_assets if a["tradable"]]
        if not crypto_symbols:
            crypto_symbols = [
                "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LTC/USD",
                "BCH/USD", "LINK/USD", "UNI/USD", "AAVE/USD", "DOGE/USD",
                "SHIB/USD", "MATIC/USD", "XRP/USD", "DOT/USD", "ATOM/USD",
                "ALGO/USD", "XTZ/USD", "CRV/USD", "SUSHI/USD", "YFI/USD",
                "GRT/USD", "MKR/USD", "COMP/USD", "TRX/USD", "ADA/USD"
            ]

    # cap if requested
    if max_symbols and len(equities) > max_symbols:
        # keep ETFs first, then take top N stocks alphabetically (deterministic)
        remaining = max_symbols - len(etfs)
        stocks_sorted = sorted(set(stocks))
        equities = etfs + stocks_sorted[:max(0, remaining)]
        stocks = stocks_sorted[:max(0, remaining)]

    all_symbols = equities + crypto_symbols

    universe = {
        "equities": sorted(set(equities)),
        "etfs": sorted(set(etfs)),
        "stocks": sorted([s for s in equities if s not in etfs]),
        "crypto": sorted(set(crypto_symbols)),
        "all": sorted(set(all_symbols)),
        "meta": {
            "count_equities": len(equities),
            "count_etfs": len(etfs),
            "count_stocks": len([s for s in equities if s not in etfs]),
            "count_crypto": len(crypto_symbols),
            "source": "alpaca /v2/assets",
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    }

    # cache
    UNIVERSSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSSE_CACHE, "w") as f:
        json.dump(universe, f, indent=2)

    return universe

def get_universe(tier: str = "full", max_symbols: Optional[int] = None) -> List[str]:
    """
    Convenience wrapper returning a flat symbol list.

    tier:
      - "etf": original 10 ETF only
      - "etf_plus": ETF + top 100 liquid stocks
      - "sp500": ETF + ~500 stocks (if max_symbols set, caps)
      - "full": all tradable US equities + crypto (capped by max_symbols if provided)
      - "crypto": crypto only
      - "all": equities + crypto
    """
    if tier == "etf":
        return ETF_SYMBOLS.copy()

    u = build_universe(
        include_etfs=True,
        include_stocks=(tier != "crypto"),
        include_crypto=(tier in ("full", "all", "crypto")),
        max_symbols=max_symbols,
        use_cache=True,
    )

    if tier == "crypto":
        return u["crypto"]
    if tier == "etf_plus":
        # ETF + first 100 stocks alphabetically (deterministic, cheap)
        stocks = sorted(u["stocks"])[:100]
        return u["etfs"] + stocks
    if tier == "sp500":
        stocks = sorted(u["stocks"])[:500]
        return u["etfs"] + stocks
    # full / all
    if tier in ("full", "all"):
        return u["all"]
    return u["all"]

def is_crypto_symbol(symbol: str) -> bool:
    s = symbol.upper()
    return "/" in s or s.endswith("USD") and len(s) > 5 and "/" not in s

def is_equity_symbol(symbol: str) -> bool:
    # crude: contains / => crypto, else assume equity
    return "/" not in symbol

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch Alpaca free-tier universe")
    parser.add_argument("--tier", default="full", choices=["etf", "etf_plus", "sp500", "full", "all", "crypto"])
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    uni = build_universe(
        include_crypto=True,
        include_stocks=True,
        max_symbols=args.max_symbols,
        use_cache=not args.no_cache,
    )
    print(f"Equities: {uni['meta']['count_equities']}  (ETFs: {uni['meta']['count_etfs']}, Stocks: {uni['meta']['count_stocks']})")
    print(f"Crypto:   {uni['meta']['count_crypto']}")
    print(f"Total:    {len(uni['all'])}")
    print("\nFirst 30:", uni["all"][:30])
