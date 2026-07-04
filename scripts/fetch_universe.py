#!/usr/bin/env python3
"""
Fetch the full Alpaca free-tier tradable universe and cache it.

Usage:
  python3 scripts/fetch_universe.py --tier full --max-symbols 1000
  python3 scripts/fetch_universe.py --tier etf_plus
  python3 scripts/fetch_universe.py --tier crypto

Outputs:
  localdata/universe_cache.json
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import argparse
from pathlib import Path

from price.universe import build_universe, get_universe

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Alpaca free-tier universe builder")
    p.add_argument("--tier", default="full", choices=["etf","etf_plus","sp500","full","all","crypto"])
    p.add_argument("--max-symbols", type=int, default=None, help="Cap total equity symbols (ETFs always kept)")
    p.add_argument("--no-crypto", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--out", default=None, help="Write flat symbol list to file")
    args = p.parse_args()

    uni = build_universe(
        include_etfs=True,
        include_stocks=(args.tier != "crypto"),
        include_crypto=not args.no_crypto,
        max_symbols=args.max_symbols,
        use_cache=not args.no_cache,
    )

    print("✅ Universe built")
    print(f"  Equities: {uni['meta']['count_equities']}  (ETFs {uni['meta']['count_etfs']}, stocks {uni['meta']['count_stocks']})")
    print(f"  Crypto:   {uni['meta']['count_crypto']}")
    print(f"  Total:    {len(uni['all'])}")
    print()
    print("First 50 symbols:")
    for s in uni["all"][:50]:
        print(f"  {s}")
    if len(uni["all"]) > 50:
        print(f"  ... +{len(uni['all'])-50} more")

    if args.out:
        out_path = Path(args.out)
        out_path.write_text("\n".join(uni["all"]))
        print(f"\nWrote {len(uni['all'])} symbols to {out_path}")

    # also show tier-filtered view
    tier_syms = get_universe(args.tier, max_symbols=args.max_symbols)
    print(f"\nTier '{args.tier}' resolves to {len(tier_syms)} symbols")
