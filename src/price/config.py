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
    "SPY", "QQQ", "IWM", "DIA", "GLD",
    "TLT", "USO", "XLK", "XLF", "XLE"
]

TIMEFRAMES = ["15m", "1h", "1d"]

PRIMARY_SOURCES = {
    "15m": "alpaca",
    "1h": "resampled",
    "1d": "tiingo"
}

ALPACA_RATE_LIMIT = 200
CHUNKS_DAYS = 90
