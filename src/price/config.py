import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "localdata"
WAREHOUSE_DIR = DATA_DIR / "warehouse"

DATA_DIR.mkdir(parents=True, exist_ok=True)
WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")

# Universe tier control
#  etf       = original 10 ETF only (V1-V4 baseline)
#  etf_plus  = ETF + 100 stocks
#  sp500     = ETF + 500 stocks
#  full      = all tradable US equities + crypto (Alpaca free-tier)
# Can be overridden via env UNIVERSE_TIER and UNIVERSE_MAX_SYMBOLS
UNIVERSE_TIER = os.getenv("UNIVERSE_TIER", "full")
UNIVERSE_MAX_SYMBOLS = os.getenv("UNIVERSE_MAX_SYMBOLS")
try:
    UNIVERSE_MAX_SYMBOLS = int(UNIVERSE_MAX_SYMBOLS) if UNIVERSE_MAX_SYMBOLS else None
except (ValueError, TypeError):
    UNIVERSE_MAX_SYMBOLS = None

# Explicit universe splits for future use (ingestion, cross-asset, etc.)
ETF_SYMBOLS = [
    "SPY", "QQQ", "IWM", "DIA", "GLD",
    "TLT", "USO", "XLK", "XLF", "XLE"
]

FUTURES_SYMBOLS = [
    "ES", "MES", "MNQ", "MYM", "CL",
    "MCL", "SI", "NG", "MGC", "M2K"
]

# Alpaca free-tier crypto pairs (fallback if /assets call fails)
CRYPTO_SYMBOLS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LTC/USD",
    "BCH/USD", "LINK/USD", "UNI/USD", "AAVE/USD", "DOGE/USD",
    "SHIB/USD", "MATIC/USD", "XRP/USD", "DOT/USD", "ATOM/USD",
    "ALGO/USD", "XTZ/USD", "CRV/USD", "SUSHI/USD", "YFI/USD",
    "GRT/USD", "MKR/USD", "COMP/USD", "ADA/USD", "TRX/USD"
]

def is_futures(symbol: str) -> bool:
    """Return True if symbol belongs to the futures universe."""
    return symbol.upper() in FUTURES_SYMBOLS

def is_crypto(symbol: str) -> bool:
    """Crypto symbols contain '/' e.g. BTC/USD"""
    s = symbol.upper()
    return "/" in s or s in CRYPTO_SYMBOLS

def is_equity(symbol: str) -> bool:
    return not is_crypto(symbol) and not is_futures(symbol)

# --- Dynamic universe loading ---
# Try to load cached universe from localdata/universe_cache.json
# Fallback to static ETF+FUTURES+CRYPTO list if cache missing / import fails
def _load_universe_symbols():
    # static fallback first
    static_fallback = ETF_SYMBOLS + FUTURES_SYMBOLS + CRYPTO_SYMBOLS
    try:
        # avoid circular import: import late
        from pathlib import Path
        import json
        cache_path = DATA_DIR / "universe_cache.json"
        if cache_path.exists():
            with open(cache_path) as f:
                u = json.load(f)
                # tier-aware selection
                if UNIVERSE_TIER == "etf":
                    return ETF_SYMBOLS
                elif UNIVERSE_TIER == "crypto":
                    return u.get("crypto", CRYPTO_SYMBOLS)
                elif UNIVERSE_TIER in ("full", "all"):
                    syms = u.get("all", static_fallback)
                    if UNIVERSE_MAX_SYMBOLS:
                        return syms[:UNIVERSE_MAX_SYMBOLS]
                    return syms
                else:
                    # etf_plus / sp500 etc: try to use cached 'all' then slice
                    syms = u.get("all", static_fallback)
                    if UNIVERSE_MAX_SYMBOLS:
                        return syms[:UNIVERSE_MAX_SYMBOLS]
                    return syms
    except Exception:
        pass
    # if tier is explicitly etf, return etf only
    if UNIVERSE_TIER == "etf":
        return ETF_SYMBOLS
    if UNIVERSE_TIER == "crypto":
        return CRYPTO_SYMBOLS
    # default: full free-tier static aggregate
    return static_fallback

SYMBOLS = _load_universe_symbols()

TIMEFRAMES = ["15m", "1h", "1d"]

# Futures use Alpaca as the sole primary source (Tiingo does not cover futures).
# 1d bars for futures are fetched directly from Alpaca (no Tiingo fallback).
PRIMARY_SOURCES = {
    "15m": "alpaca",
    "1h": "resampled",
    "1d": "tiingo"
}

ALPACA_RATE_LIMIT = 200
CHUNKS_DAYS = 90