import json
import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "localdata"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
ALLOWLIST_CACHE_PATH = DATA_DIR / "explicit_allowlist.json"
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}(/[A-Z0-9][A-Z0-9.\-]{0,14})?$")

DATA_DIR.mkdir(parents=True, exist_ok=True)
WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")

# Universe tier control
#   etf       = original 10 ETF only
#   etf_plus  = ETF + 100 stocks
#   sp500     = ETF + 500 stocks
#   allowlist = curated EXPLICIT_ALLOWLIST (no SPACs, no junk)
#   full      = all tradable US equities + crypto
# Override via: export UNIVERSE_TIER=allowlist
UNIVERSE_TIER = os.getenv("UNIVERSE_TIER", "allowlist")

# Curated allow-list - liquid names, no SPACs/warrants/units
EXPLICIT_ALLOWLIST = [
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "GLD", "TLT", "USO",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO",
    "JPM", "UNH", "V", "MA", "HD", "PG", "COST", "XOM", "JNJ", "LLY", "ABBV",
    "BAC", "WFC", "GS", "MS", "BLK", "AXP", "SCHW", "C",
    "MRK", "PFE", "TMO", "ABT", "DHR", "AMGN", "GILD", "VRTX", "REGN", "ZTS",
    "CAT", "GE", "HON", "UNP", "RTX", "LMT", "MM", "BA", "GD", "NOC", "UPS",
    "FDX", "CSX", "NSC", "AMD", "INTC", "QCOM", "TXN", "ADI", "AMAT", "LRCX",
    "MU", "KLAC", "MPWR", "ORCL", "CRM", "ADBE", "INTU", "NOW", "SNPS", "CDNS",
    "ADSK", "PANW", "FTNT", "CRWD", "ZS", "MCD", "NKE", "LOW", "TJX", "SBUX",
    "DG", "DLTR", "KO", "PEP", "WMT", "PM", "MDLZ", "STZ", "KMB", "GIS", "KHC",
    "CVX", "EOG", "COP", "SLB", "MPC", "PSX", "VLO", "OXY", "HAL",
    "LIN", "APD", "SHW", "FCX", "NEM", "DOW", "DD", "PPG", "ALB", "CTVA", "FMC",
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "ED", "AWK", "WEC", "EIX",
    "AMT", "PLD", "CCI", "EQIX", "PSA", "SPG", "AVB", "EQR", "WELL", "VTR",
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "PYPL", "ICE", "CME",
    "PGR", "CB", "MMC", "AON", "AFL", "MET", "TRV", "ALL",
    "DE", "MTD", "ITW", "EMR", "ROK", "PH", "ISRG", "SYK", "MDT", "BSX", "HUM",
    "TGT", "ROST", "BBY", "UAL", "DAL", "AAL", "HII", "LHX",
    "ADP", "PAYX", "CPRT", "FICO", "CTAS", "CBOE", "BKNG", "MAR", "HLT", "MGM",
    "WYNN", "LVS", "WMB", "KMI", "ET", "EPD", "TRGP", "OKE",
    "GOLD", "AEM", "FNV", "WPM", "SPGI",
]

UNIVERSE_MAX_SYMBOLS = os.getenv("UNIVERSE_MAX_SYMBOLS")
try:
    UNIVERSE_MAX_SYMBOLS = int(UNIVERSE_MAX_SYMBOLS) if UNIVERSE_MAX_SYMBOLS else None
except (ValueError, TypeError):
    UNIVERSE_MAX_SYMBOLS = None

ETF_SYMBOLS = [
    "SPY", "QQQ", "IWM", "DIA", "GLD",
    "TLT", "USO", "XLK", "XLF", "XLE"
]

FUTURES_SYMBOLS = [
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

CRYPTO_SYMBOLS = [
    "AAVE/USD", "AAVE/USDC", "AAVE/USDT",
    "ADA/USD",
    "ARB/USD",
    "AVAX/USD", "AVAX/USDC", "AVAX/USDT",
    "BAT/USD", "BAT/USDC",
    "BCH/USD", "BCH/USDC", "BCH/USDT",
    "BTC/USD", "BTC/USDC", "BTC/USDT",
    "CRV/USD", "CRV/USDC",
    "DOGE/USD", "DOGE/USDC", "DOGE/USDT",
    "DOT/USD", "DOT/USDC",
    "ETH/USD", "ETH/USDC", "ETH/USDT",
    "FIL/USD",
    "GRT/USD", "GRT/USDC",
    "LDO/USD",
    "LINK/USD", "LINK/USDC", "LINK/USDT",
    "LTC/USD", "LTC/USDC", "LTC/USDT",
    "ONDO/USD",
    "PAXG/USD",
    "RENDER/USD",
    "SOL/USD", "SOL/USDC", "SOL/USDT",
    "SUSHI/USD", "SUSHI/USDC", "SUSHI/USDT",
    "UNI/USD", "UNI/USDC", "UNI/USDT",
    "WIF/USD",
    "XRP/USD",
    "XTZ/USD", "XTZ/USDC",
    "YFI/USD", "YFI/USDC", "YFI/USDT",
]

def is_futures(symbol: str) -> bool:
    s = symbol.upper()

    # If a generated allowlist exists, only symbols explicitly listed under
    # its "futures" key should be treated as futures. This avoids ambiguous
    # roots like CL/ES/BTC being misrouted when the user intentionally set
    # futures=[] in localdata/explicit_allowlist.json.
    try:
        generated = _load_generated_allowlist()
    except NameError:
        generated = {}

    if generated:
        return s in set(generated.get("futures", []))

    return s in FUTURES_SYMBOLS

def is_crypto(symbol: str) -> bool:
    s = symbol.upper()
    return "/" in s or s in CRYPTO_SYMBOLS

def is_equity(symbol: str) -> bool:
    return not is_crypto(symbol) and not is_futures(symbol)

def _coerce_symbol_list(value) -> list:
    if not isinstance(value, list):
        return []
    out = []
    for s in value:
        symbol = str(s).strip().upper()
        if not symbol:
            continue
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f"Invalid symbol in generated allowlist: {symbol!r}")
        out.append(symbol)
    return out


def _load_generated_allowlist() -> dict:
    """Load the optional generated allowlist produced by scripts/survey_assets.py.

    Keeping the 8k+ live Alpaca equity universe in localdata avoids committing
    a giant static Python list while still making UNIVERSE_TIER=allowlist fully
    reproducible on a machine that has already run the survey.
    """
    if not ALLOWLIST_CACHE_PATH.exists():
        return {}
    try:
        with open(ALLOWLIST_CACHE_PATH) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(payload, list):
        return {"all": _coerce_symbol_list(payload)}
    if not isinstance(payload, dict):
        return {}

    return {
        "equities": _coerce_symbol_list(payload.get("equities", [])),
        "futures": _coerce_symbol_list(payload.get("futures", [])),
        "crypto": _coerce_symbol_list(payload.get("crypto", [])),
        "all": _coerce_symbol_list(payload.get("all", [])),
    }


def get_allowlist_symbols() -> list:
    generated = _load_generated_allowlist()
    if generated:
        # Important: an explicit empty list in localdata/explicit_allowlist.json
        # means "exclude this asset class". Do not use `or FUTURES_SYMBOLS`
        # here, because [] is falsey and would silently re-add defaults.
        equities = generated.get("equities", [])
        futures = generated.get("futures", [])
        crypto = generated.get("crypto", [])
        explicit_all = generated.get("all", [])

        combined = explicit_all + equities + futures + crypto
        if combined:
            return sorted(set(combined))

    return sorted(set(EXPLICIT_ALLOWLIST + FUTURES_SYMBOLS + CRYPTO_SYMBOLS))

def _load_universe_symbols():
    static_fallback = ETF_SYMBOLS + FUTURES_SYMBOLS + CRYPTO_SYMBOLS
    try:
        import json
        cache_path = DATA_DIR / "universe_cache.json"
        if cache_path.exists():
            with open(cache_path) as f:
                u = json.load(f)
                if UNIVERSE_TIER == "etf":
                    return ETF_SYMBOLS
                elif UNIVERSE_TIER == "crypto":
                    return u.get("crypto", CRYPTO_SYMBOLS)
                elif UNIVERSE_TIER == "allowlist":
                    return get_allowlist_symbols()
                elif UNIVERSE_TIER in ("full", "all"):
                    syms = u.get("all", static_fallback)
                    if UNIVERSE_MAX_SYMBOLS:
                        return syms[:UNIVERSE_MAX_SYMBOLS]
                    return syms
                else:
                    syms = u.get("all", static_fallback)
                    if UNIVERSE_MAX_SYMBOLS:
                        return syms[:UNIVERSE_MAX_SYMBOLS]
                    return syms
    except Exception:
        pass
    if UNIVERSE_TIER == "etf":
        return ETF_SYMBOLS
    if UNIVERSE_TIER == "crypto":
        return CRYPTO_SYMBOLS
    if UNIVERSE_TIER == "allowlist":
        return get_allowlist_symbols()
    return static_fallback

SYMBOLS = _load_universe_symbols()

TIMEFRAMES = ["15m", "1h", "1d"]

PRIMARY_SOURCES = {
    "15m": "alpaca",
    "1h": "yfinance",     # yfinance primary for equity 1h; Alpaca resample fallback
    "1d": "yfinance",     # yfinance primary for equity daily; Tiingo fallback
}

ALPACA_RATE_LIMIT = 200
CHUNKS_DAYS = 90
