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
    "ES", "MES", "MNQ", "MYM", "CL",
    "MCL", "SI", "NG", "MGC", "M2K"
]

CRYPTO_SYMBOLS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "LTC/USD",
    "BCH/USD", "LINK/USD", "UNI/USD", "AAVE/USD", "DOGE/USD",
    "SHIB/USD", "MATIC/USD", "XRP/USD", "DOT/USD", "ATOM/USD",
    "ALGO/USD", "XTZ/USD", "CRV/USD", "SUSHI/USD", "YFI/USD",
    "GRT/USD", "MKR/USD", "COMP/USD", "ADA/USD", "TRX/USD"
]

def is_futures(symbol: str) -> bool:
    return symbol.upper() in FUTURES_SYMBOLS

def is_crypto(symbol: str) -> bool:
    s = symbol.upper()
    return "/" in s or s in CRYPTO_SYMBOLS

def is_equity(symbol: str) -> bool:
    return not is_crypto(symbol) and not is_futures(symbol)

def get_allowlist_symbols() -> list:
    return sorted(set(EXPLICIT_ALLOWLIST + FUTURES_SYMBOLS + CRYPTO_SYMBOLS))

def _load_universe_symbols():
    static_fallback = ETF_SYMBOLS + FUTURES_SYMBOLS + CRYPTO_SYMBOLS
    try:
        from pathlib import Path
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
    "1h": "resampled",
    "1d": "tiingo"
}

ALPACA_RATE_LIMIT = 200
CHUNKS_DAYS = 90
