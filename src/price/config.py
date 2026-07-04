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

SYMBOLS = [
    # ETF universe (original 10)
    "SPY", "QQQ", "IWM", "DIA", "GLD",
    "TLT", "USO", "XLK", "XLF", "XLE",
    # Futures universe (added 2026-07-04)
    "ES", "NQ", "RTY", "YM", "CL",
    "GC", "SI", "ZB", "ZN", "NG"
]

# Explicit universe splits for future use (ingestion, cross-asset, etc.)
ETF_SYMBOLS = [
    "SPY", "QQQ", "IWM", "DIA", "GLD",
    "TLT", "USO", "XLK", "XLF", "XLE"
]

FUTURES_SYMBOLS = [
    "ES", "MES", "MNQ", "MYM", "CL",
    "MCL", "SI", "NG", "MGC", "M2K"
]


def is_futures(symbol: str) -> bool:
    """Return True if symbol belongs to the futures universe."""
    return symbol.upper() in FUTURES_SYMBOLS

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
