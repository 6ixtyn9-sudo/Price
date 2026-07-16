"""External / macro data fetchers.

All fetchers here return tz-aware UTC DataFrames with a ``bar_ts_utc`` column.
They are deliberately lazy, cached to localdata/external/, and always return
an empty DataFrame on failure so a missing API key, rate limit, or bad network
can never poison feature computation.  Features/bins in features.py and
discovery.py handle missing columns/NaN by pinning to an "unknown" fallback
state, so a half-populated external-data frame degrades gracefully instead of
silently breaking discovery.

Tranches implemented here:
  T2  Crypto funding + open interest (Binance public REST, no key).
  T3  CFTC COT legacy reports (public CSV, no key).
  T4  VIX / DXY daily via yfinance (falls back to warehouse bars if already
      ingested).
  T5  Macro event blackout calendar (hardcoded FOMC/CPI/NFP/OPEX dates).

Breadth (T4) is computed inside features.py from bars already landed in the
warehouse -- it needs no new fetcher.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from price.config import DATA_DIR, is_crypto, is_futures

EXTERNAL_DIR = DATA_DIR / "external"
EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)

# HTTP timeout for all external fetches (seconds).
HTTP_TIMEOUT = 15


# ── generic cache helpers ─────────────────────────────────────────────
def _cache_path(name: str) -> Path:
    return EXTERNAL_DIR / f"{name}.parquet"


def _read_cached(name: str, max_age_hours: Optional[float]) -> Optional[pd.DataFrame]:
    p = _cache_path(name)
    if not p.exists():
        return None
    if max_age_hours is None:
        try:
            return pd.read_parquet(p)
        except Exception:
            return None
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    age_h = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600.0
    if age_h > max_age_hours:
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def _write_cache(name: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    p = _cache_path(name)
    try:
        df.to_parquet(p, index=False)
    except Exception:
        # cache writes are best-effort; never fail the caller
        pass


# ── T2: crypto funding + open interest (Binance) ─────────────────────
# Binance public REST endpoints (no key):
#   /fapi/v1/fundingRate   -- 8h historical funding rates
#   /fapi/v1/openInterestHist -- open interest history (daily, in coins)
# These are perp-futures endpoints; we map each crypto/{USD,USDC,USDT} pair
# to the Binance perp ticker (e.g. BTC/USD -> BTCUSDT) and backfill history.
# Funding-rate and OI signals apply to crypto perps/spot and are meaningless
# outside crypto; features.py only attaches them when is_crypto(symbol).

_BINANCE_PERP_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "DOGE": "DOGEUSDT",
    "DOT": "DOTUSDT",
    "LINK": "LINKUSDT",
    "LTC": "LTCUSDT",
    "BCH": "BCHUSDT",
    "MATIC": "MATICUSDT",
    "UNI": "UNIUSDT",
    "AAVE": "AAVEUSDT",
    "SUSHI": "SUSHIUSDT",
    "CRV": "CRVUSDT",
    "FIL": "FILUSDT",
    "GRT": "GRTUSDT",
    "XTZ": "XTZUSDT",
    "YFI": "YFIUSDT",
    "LDO": "LDOUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "WIF": "WIFUSDT",
    "RENDER": "RENDERUSDT",
    "ONDO": "ONDOUSDT",
    "PAXG": "PAXGUSDT",
    "BAT": "BATUSDT",
}


def _binance_perp_ticker(symbol: str) -> Optional[str]:
    """Map e.g. 'BTC/USD' -> 'BTCUSDT'. Returns None for unsupported pairs."""
    s = symbol.upper().split("/")[0]
    return _BINANCE_PERP_MAP.get(s)


def _http_get_json(url: str, params: Optional[dict] = None) -> Optional[list | dict]:
    try:
        import requests
    except ImportError:
        return None
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        # 403/451 = geo-blocked; 429 = rate-limited; 5xx = transient. All
        # are treated as "no data right now" and do NOT get written to the
        # on-disk cache, so the next run in a different network context
        # can retry without manual cache deletion.
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _binance_or_bybit_funding(symbol: str, perp: str) -> pd.DataFrame:
    """Try Binance first; on geo-block/error, fall back to Bybit's public API.
    Returns a DataFrame with [bar_ts_utc, funding_rate, funding_ann] or empty.
    """
    # Binance
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - 730 * 24 * 3600 * 1000
    all_rows = []
    cursor = start_ms
    for _ in range(8):
        rows = _http_get_json(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": perp, "startTime": cursor, "endTime": end_ms, "limit": 1000},
        )
        if not isinstance(rows, list):
            rows = None
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        last_ts = rows[-1].get("fundingTime", cursor)
        cursor = int(last_ts) + 1
        if cursor >= end_ms:
            break
    if all_rows:
        df = pd.DataFrame(all_rows)
        if {"fundingTime", "fundingRate"}.issubset(df.columns):
            df["bar_ts_utc"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms", utc=True)
            df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
            df["funding_ann"] = df["funding_rate"] * 3 * 365
            return df[["bar_ts_utc", "funding_rate", "funding_ann"]].sort_values("bar_ts_utc").reset_index(drop=True)

    # Bybit fallback (symbol e.g. BTCUSDT; category=linear for USDT perps).
    # Bybit returns results DESCENDING by timestamp (newest first). To paginate
    # BACKWARD in time: fix startTime at the window start, walk endTime to
    # `oldest - 1` each batch. Walking startTime forward with descending
    # results returns the same batch forever.
    try:
        import time as _time
        bybit_rows = []
        bybit_end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        bybit_start_ms = bybit_end_ms - 730 * 24 * 3600 * 1000
        cursor_end = bybit_end_ms
        for _ in range(40):
            payload = _http_get_json(
                "https://api.bybit.com/v5/market/funding/history",
                params={"category": "linear", "symbol": perp,
                        "startTime": bybit_start_ms, "endTime": cursor_end, "limit": 200},
            )
            if not isinstance(payload, dict):
                break
            items = (payload.get("result") or {}).get("list") or []
            if not items:
                break
            bybit_rows.extend(items)
            if len(items) < 200:
                break
            oldest = min(int(it["fundingRateTimestamp"]) for it in items if "fundingRateTimestamp" in it)
            cursor_end = oldest - 1
            if cursor_end <= bybit_start_ms:
                break
            _time.sleep(0.1)  # light rate-limit courtesy
        if bybit_rows:
            df = pd.DataFrame(bybit_rows)
            if {"fundingRateTimestamp", "fundingRate"}.issubset(df.columns):
                df["bar_ts_utc"] = pd.to_datetime(df["fundingRateTimestamp"].astype(int), unit="ms", utc=True)
                df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
                df["funding_ann"] = df["funding_rate"] * 3 * 365
                return df[["bar_ts_utc", "funding_rate", "funding_ann"]].sort_values("bar_ts_utc").reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame()


def _binance_or_bybit_oi(symbol: str, perp: str) -> pd.DataFrame:
    """Try Binance openInterestHist first; fall back to Bybit open-interest."""
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - 730 * 24 * 3600 * 1000
    cursor = start_ms
    all_rows = []
    for _ in range(60):
        rows = _http_get_json(
            "https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": perp, "period": "1d", "startTime": cursor, "endTime": end_ms, "limit": 30},
        )
        if not isinstance(rows, list):
            rows = None
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 30:
            break
        last_ts = rows[-1].get("timestamp", cursor)
        cursor = int(last_ts) + 1
        if cursor >= end_ms:
            break
    if all_rows:
        df = pd.DataFrame(all_rows)
        if {"timestamp", "sumOpenInterest"}.issubset(df.columns):
            df["bar_ts_utc"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
            df["oi_sum_open_interest"] = pd.to_numeric(df["sumOpenInterest"], errors="coerce")
            df["oi_value_usd"] = pd.to_numeric(df.get("sumOpenInterestValue", pd.Series(np.nan)), errors="coerce")
            return df[["bar_ts_utc", "oi_sum_open_interest", "oi_value_usd"]].sort_values("bar_ts_utc").reset_index(drop=True)

    # Bybit fallback (daily OI via /v5/market/open-interest). Returns newest first;
    # same backward-walking pagination strategy as funding above.
    try:
        import time as _time
        bybit_rows = []
        end_ts = end_ms
        start_ts = int((datetime.now(timezone.utc) - pd.Timedelta(days=365)).timestamp() * 1000)
        cursor_end = end_ts
        for _ in range(30):
            payload = _http_get_json(
                "https://api.bybit.com/v5/market/open-interest",
                params={"category": "linear", "symbol": perp,
                        "intervalTime": "1d", "startTime": start_ts, "endTime": cursor_end, "limit": 200},
            )
            if not isinstance(payload, dict):
                break
            items = (payload.get("result") or {}).get("list") or []
            if not items:
                break
            bybit_rows.extend(items)
            if len(items) < 200:
                break
            oldest = min(int(it["timestamp"]) for it in items if "timestamp" in it)
            cursor_end = oldest - 1
            if cursor_end <= start_ts:
                break
            _time.sleep(0.1)
        if bybit_rows:
            df = pd.DataFrame(bybit_rows)
            if {"timestamp", "openInterest"}.issubset(df.columns):
                df["bar_ts_utc"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
                df["oi_sum_open_interest"] = pd.to_numeric(df["openInterest"], errors="coerce")
                df["oi_value_usd"] = np.nan
                return df[["bar_ts_utc", "oi_sum_open_interest", "oi_value_usd"]].sort_values("bar_ts_utc").reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame()


def fetch_crypto_funding(symbol: str, lookback_days: int = 730) -> pd.DataFrame:
    """Fetch historical 8h funding rates for a crypto symbol via Binance with
    a Bybit fallback (each public, no key).

    Returns a DataFrame with columns [bar_ts_utc, funding_rate, funding_ann]
    where funding_ann is the annualised rate (funding * 3*365).  Empty DF on
    any failure (geo-block, network, missing pair).
    """
    perp = _binance_perp_ticker(symbol)
    if not perp:
        return pd.DataFrame()
    cache_name = f"crypto_funding_{perp}"
    cached = _read_cached(cache_name, max_age_hours=12)
    if cached is not None and not cached.empty:
        return cached
    df = _binance_or_bybit_funding(symbol, perp)
    _write_cache(cache_name, df)
    return df


def fetch_crypto_open_interest(symbol: str, lookback_days: int = 730) -> pd.DataFrame:
    """Fetch daily open-interest history via Binance with Bybit fallback.

    Returns columns [bar_ts_utc, oi_sum_open_interest, oi_value_usd]. We
    only care about relative changes in OI, so oi_value_usd is best-effort
    (NaN when the secondary source doesn't provide it).
    """
    perp = _binance_perp_ticker(symbol)
    if not perp:
        return pd.DataFrame()
    cache_name = f"crypto_oi_{perp}"
    cached = _read_cached(cache_name, max_age_hours=24)
    if cached is not None and not cached.empty:
        return cached
    df = _binance_or_bybit_oi(symbol, perp)
    _write_cache(cache_name, df)
    return df


# Backward-compatible aliases (older callers / notebooks may reference these).
fetch_binance_funding = fetch_crypto_funding
fetch_binance_open_interest = fetch_crypto_open_interest


def attach_crypto_externals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Backward as-of merge funding + OI onto a crypto OHLCV frame.

    Adds feat_funding_rate, feat_funding_ann, feat_funding_z20,
    feat_oi_change_5, feat_oi_change_20 columns. Missing data -> NaN (pinned
    to 'unknown' in bin_features).
    """
    if df.empty or not is_crypto(symbol):
        return df
    out = df.copy().sort_values("bar_ts_utc").reset_index(drop=True)
    funding = fetch_crypto_funding(symbol)
    oi = fetch_crypto_open_interest(symbol)

    if not funding.empty:
        f = funding.sort_values("bar_ts_utc")
        out = pd.merge_asof(
            out, f, on="bar_ts_utc", direction="backward",
            tolerance=pd.Timedelta(hours=9),
        )
        # z-score of annualised funding vs trailing 20 observations (8h bars).
        out["feat_funding_z20"] = (
            (out["funding_ann"] - out["funding_ann"].rolling(20, min_periods=10).mean())
            / out["funding_ann"].rolling(20, min_periods=10).std().replace(0, np.nan)
        )
        out = out.rename(columns={"funding_rate": "feat_funding_rate", "funding_ann": "feat_funding_ann"})

    if not oi.empty:
        o = oi.sort_values("bar_ts_utc")
        out = pd.merge_asof(
            out, o, on="bar_ts_utc", direction="backward",
            tolerance=pd.Timedelta(days=3),
        )
        out["feat_oi_change_5"] = out["oi_sum_open_interest"].pct_change(5)
        out["feat_oi_change_20"] = out["oi_sum_open_interest"].pct_change(20)
        out = out.rename(columns={"oi_sum_open_interest": "feat_oi", "oi_value_usd": "feat_oi_value"})

    return out


# ── T3: CFTC COT legacy report ───────────────────────────────────────
# CFTC publishes weekly legacy reports (TXT/CSV) at:
#   https://www.cftc.gov/dea/newcot/deafut.txt  (disaggregated futures-only)
# We pull a simpler legacy-CSV-format endpoint that covers futures+options
# combined, or the TXT and parse.  For our conditioning purposes (weekly net
# position of managed money / non-commercials), the legacy COT "futures only"
# CSV-style report is sufficient.  We'll use the public disaggregated reports
# CSV endpoint (annual files + current year) for robustness.
#
# Simplest reliable source: the CFTC's bulk CSV zip for the disaggregated
# reports. To avoid zipfile handling we fall back to a trimmed hardcoded map
# of root -> market code and fetch just the current year via:
#   https://www.cftc.gov/.../annual.txt  (TXT format, parseable).
#
# Given the complexity of full COT parsing, we take the minimal useful slice:
# weekly net positioning for the contract roots we care about (ES, NQ, CL,
# GC, ZB, ZN, NG, SI, HG). We implement a lightweight parser against the
# disaggregated TXT file.

# CFTC market codes (from CFTC disaggregated reports -- CFTC_Contract_Market_Code column)
_CFTC_CODE_BY_ROOT = {
    "ES": "13874A",   # E-mini S&P 500
    "NQ": "209742",   # E-mini Nasdaq-100
    "RTY": "239742",  # E-mini Russell 2000
    "YM": "124603",   # Dow Jones ($5)
    "CL": "067651",   # Crude Oil
    "NG": "023651",   # Natural Gas
    "GC": "088691",   # Gold
    "SI": "084691",   # Silver
    "HG": "085692",   # Copper
    "ZB": "043602",   # 30-year US Treasury Bond
    "ZN": "042601",   # 10-year Treasury Note
    "ZF": "044601",   # 5-year Treasury Note
    "ZT": "040601",   # 2-year Treasury Note
}


def fetch_cot_disaggregated(year: Optional[int] = None) -> pd.DataFrame:
    """Fetch the CFTC disaggregated futures-only TXT and return a long
    DataFrame of [bar_ts_utc, root, mm_net_pct, mm_net_z52] where mm_net_pct
    is managed-money net (long - short) / total open interest, and mm_net_z52
    is its 52-week z-score.  Returns empty on any failure.

    We use the FINAL year files available at
    https://www.cftc.gov/sites/default/files/files/dea/history/deafutYYYY.zip,
    but unzipping single-file zips is a bit fragile in environments without
    the zipfile module. To keep zero dependencies beyond requests, we parse
    the TXT directly if available, otherwise return empty (features will pin
    to 'unknown').
    """
    if year is None:
        year = datetime.now(timezone.utc).year

    # First try cache.
    cache_name = f"cot_disagg_{year}"
    cached = _read_cached(cache_name, max_age_hours=24)
    if cached is not None and not cached.empty:
        return cached

    try:
        import requests
        import zipfile
    except ImportError:
        return pd.DataFrame()

    url = f"https://www.cftc.gov/sites/default/files/files/dea/history/deafut{year}.zip"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200 or len(r.content) < 1000:
            return pd.DataFrame()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        if not names:
            return pd.DataFrame()
        raw = zf.read(names[0]).decode("latin-1", errors="replace")
    except Exception:
        return pd.DataFrame()

    # CFTC disaggregated TXT is comma-separated with a header row. Read it.
    try:
        df = pd.read_csv(io.StringIO(raw), low_memory=False)
    except Exception:
        return pd.DataFrame()

    needed_cols = [
        "Report_Date_as_MM_DD_YYYY",
        "CFTC_Contract_Market_Code",
        "M_Money_Positions_Long_All",
        "M_Money_Positions_Short_All",
        "Open_Interest_All",
    ]
    for c in needed_cols:
        if c not in df.columns:
            return pd.DataFrame()

    df["as_of_ts"] = pd.to_datetime(df["Report_Date_as_MM_DD_YYYY"], errors="coerce", utc=True)
    # CFTC disaggregated reports are released FRIDAY at 3:30 PM ET for the TUESDAY
    # as-of date. Shifting the effective bar_ts_utc from the Tuesday date to Friday
    # 20:30 UTC (= 3:30pm ET winter; DST-insensitive by ~1hr which is fine for a
    # weekly conditioning dim) prevents a 3-day look-ahead where Wednesday/Thursday/
    # Friday-morning bars could join data that is not yet public.
    df["bar_ts_utc"] = df["as_of_ts"] + pd.Timedelta(days=3, hours=20, minutes=30)
    for c in ["M_Money_Positions_Long_All", "M_Money_Positions_Short_All", "Open_Interest_All"]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    df = df.dropna(subset=["bar_ts_utc", "Open_Interest_All"])
    df["mm_net"] = df["M_Money_Positions_Long_All"] - df["M_Money_Positions_Short_All"]
    df["mm_net_pct"] = df["mm_net"] / df["Open_Interest_All"].replace(0, np.nan)
    df["market_code"] = df["CFTC_Contract_Market_Code"].astype(str).str.strip()

    # Build inverse lookup (code -> root).
    code_to_root = {v: k for k, v in _CFTC_CODE_BY_ROOT.items()}
    df["root"] = df["market_code"].map(code_to_root)
    df = df.dropna(subset=["root"])

    out_rows = []
    for root, g in df.groupby("root"):
        g = g.sort_values("bar_ts_utc").reset_index(drop=True)
        g["feat_cot_mm_net_pct"] = g["mm_net_pct"]
        # 52-week z of mm_net_pct (weekly data; ~52 obs/yr).
        mu = g["mm_net_pct"].rolling(52, min_periods=20).mean()
        sd = g["mm_net_pct"].rolling(52, min_periods=20).std().replace(0, np.nan)
        g["feat_cot_mm_z52"] = (g["mm_net_pct"] - mu) / sd
        out_rows.append(g[["bar_ts_utc", "root", "feat_cot_mm_net_pct", "feat_cot_mm_z52"]])
    if not out_rows:
        return pd.DataFrame()
    out = pd.concat(out_rows, ignore_index=True)
    _write_cache(cache_name, out)
    return out


def _futures_root(symbol: str) -> Optional[str]:
    """Extract the root from a canonical FUT/<ROOT> symbol, or bare root."""
    s = symbol.upper()
    if s.startswith("FUT/"):
        s = s[4:]
    # strip leading 'M' micro prefix if present (MES->ES, MNQ->NQ, etc)
    if s.startswith("M") and len(s) >= 3 and s[1:] in _CFTC_CODE_BY_ROOT:
        s = s[1:]
    # use the first 2 chars to look up; handle 1-char roots defensively
    if s in _CFTC_CODE_BY_ROOT:
        return s
    return None


def attach_futures_externals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Attach CFTC COT weekly managed-money net positioning to a futures frame.

    Adds feat_cot_mm_net_pct and feat_cot_mm_z52 via backward as-of merge.
    Non-futures symbols / missing data -> returns df unchanged (NaN cols).
    """
    if df.empty:
        return df
    root = _futures_root(symbol) if is_futures(symbol) else None
    out = df.copy().sort_values("bar_ts_utc").reset_index(drop=True)
    if not root:
        return out

    cot = fetch_cot_disaggregated()
    if cot.empty:
        # Try prior-year archive if current year has no data yet.
        cy = datetime.now(timezone.utc).year
        cot = fetch_cot_disaggregated(year=cy - 1)
    if cot.empty:
        out["feat_cot_mm_net_pct"] = np.nan
        out["feat_cot_mm_z52"] = np.nan
        return out

    sub = cot[cot["root"] == root][["bar_ts_utc", "feat_cot_mm_net_pct", "feat_cot_mm_z52"]]
    if sub.empty:
        out["feat_cot_mm_net_pct"] = np.nan
        out["feat_cot_mm_z52"] = np.nan
        return out

    # COT effective timestamps are weekly Friday ~20:30 UTC after the publication-
    # date shift above. Tolerance of 7 days means we pick up the most recent
    # released report without leaking future reports.
    sub = sub.sort_values("bar_ts_utc")
    out = pd.merge_asof(
        out, sub, on="bar_ts_utc", direction="backward",
        tolerance=pd.Timedelta(days=7),
    )
    return out


# ── T4: VIX / DXY daily bars (yfinance) ──────────────────────────────
def _fetch_yf_daily(ticker: str, cache_name: str, lookback_days: int = 365 * 5) -> pd.DataFrame:
    cached = _read_cached(cache_name, max_age_hours=24)
    if cached is not None and not cached.empty:
        return cached
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()
    try:
        end = datetime.now(timezone.utc)
        start = end - pd.Timedelta(days=lookback_days)
        h = yf.download(ticker, start=start.date(), end=end.date(),
                        progress=False, auto_adjust=True, interval="1d")
        if h is None or h.empty:
            return pd.DataFrame()
        # flatten multiindex from newer yfinance
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = [c[0] for c in h.columns]
        h = h.reset_index()
        date_col = "Date" if "Date" in h.columns else "Datetime"
        # yfinance daily bars are stamped at 00:00 UTC for the session. If we
        # leave the stamp at midnight, an intraday bar at 10:00 UTC backward-
        # merges TODAY's daily close which is not known until ~21:00 UTC (US
        # market close 4pm ET ≈ 20:00-21:00 UTC depending on DST). Shift the
        # effective timestamp to 21:00 UTC so intraday bars only match the
        # PRIOR day's close. For 1d primary frames, merge_asof backward still
        # matches correctly (a 00:00 or 21:00 daily bar lands in the same
        # daily window).
        h["bar_ts_utc"] = pd.to_datetime(h[date_col], utc=True).dt.tz_convert("UTC") + pd.Timedelta(hours=21)
        # Normalise to close + volume
        h["close_adj"] = pd.to_numeric(h["Close"], errors="coerce")
        h["high_adj"] = pd.to_numeric(h["High"], errors="coerce")
        h["low_adj"] = pd.to_numeric(h["Low"], errors="coerce")
        out = h[["bar_ts_utc", "close_adj", "high_adj", "low_adj"]].dropna(subset=["close_adj"])
        out = out.sort_values("bar_ts_utc").reset_index(drop=True)
        _write_cache(cache_name, out)
        return out
    except Exception:
        return pd.DataFrame()


def fetch_vix_daily() -> pd.DataFrame:
    """VIX daily close. Used as a volatility-regime conditioning input."""
    return _fetch_yf_daily("^VIX", "vix_daily")


def fetch_dxy_daily() -> pd.DataFrame:
    """US Dollar Index daily close (macroeconomic context for equities/commodities)."""
    return _fetch_yf_daily("DX-Y.NYB", "dxy_daily")


# ── T5: macro event blackout calendar ────────────────────────────────
# Hardcoded 2024-2027 macro event dates (month/day). FOMC dates are projected
# from the Federal Reserve's published schedule when available; NFP is first
# Friday of each month (with rare exception for holidays); CPI is released
# around the 10th-15th of each month (we cover with a 2-day window). OPEX is
# the third Friday of each month (monthly options expiration).
#
# We represent these as SETS of UTC calendar dates that, at 15m/1h granularity,
# we treat as a boolean blackout flag (do not initiate new paper risk). This
# is a pure risk-control gate, not a discovery dimension, so it joins as a
# feat_event_blackout boolean rather than expanding slice space.

# FOMC meeting CONCLUSION dates (2-day meetings end Wed/Thu; decisions 2pm
# ET).  Sourced from federalreserve.gov; projected ~18 months out.
_FOMC_DATES = {
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (projected -- standard FOMC cadence)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
    "2026-09-16", "2026-11-04", "2026-12-09",
    # 2027
    "2027-01-27",
}


def _nfp_dates(start_year: int = 2024, end_year: int = 2027) -> set:
    """First Friday of each month (BLS Employment Situation release), as UTC
    dates (released 8:30am ET -> ~13:30 UTC same day), with precise holiday shifts."""
    out: set = set()
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            # Find first Friday
            d = pd.Timestamp(year=y, month=m, day=1)
            while d.dayofweek != 4:  # 4 = Friday
                d += pd.Timedelta(days=1)
            
            # BLS precise holiday handling for the release date
            if d.month == 1 and d.day == 1:
                # If Jan 1 is Friday, market is closed; BLS delays to Jan 8.
                d += pd.Timedelta(days=7)
            elif d.month == 7 and d.day == 4:
                # If Jul 4 is Friday, market is closed; BLS pulls forward to Thu Jul 3.
                d -= pd.Timedelta(days=1)
            elif d.month == 7 and d.day == 3:
                # If Jul 3 is Friday, Jul 4 is Saturday (observed Friday); BLS pulls to Thu Jul 2.
                d -= pd.Timedelta(days=1)
                
            out.add(d.strftime("%Y-%m-%d"))
    return out


def _opex_dates(start_year: int = 2024, end_year: int = 2027) -> set:
    """Third Friday of each month (monthly equity options expiration)."""
    out: set = set()
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            d = pd.Timestamp(year=y, month=m, day=1)
            fridays_seen = 0
            while True:
                if d.month != m:
                    break
                if d.dayofweek == 4:
                    fridays_seen += 1
                    if fridays_seen == 3:
                        out.add(d.strftime("%Y-%m-%d"))
                        break
                d += pd.Timedelta(days=1)
    return out


def _cpi_windows(start_year: int = 2024, end_year: int = 2027) -> set:
    """CPI release is typically the 12th, 13th, or 14th of each month at 8:30am ET.
    Marking just those three days avoids the 5-day over-blackout (which blacked out
    ~16% of the trading month) while covering the observed release dates in the
    last decade (off by at most 1 day)."""
    out: set = set()
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            for day in (12, 13, 14):
                try:
                    out.add(pd.Timestamp(year=y, month=m, day=day).strftime("%Y-%m-%d"))
                except ValueError:
                    continue
    return out


_BLACKOUT_CACHE: Optional[set] = None


def _blackout_dates() -> set:
    global _BLACKOUT_CACHE
    if _BLACKOUT_CACHE is not None:
        return _BLACKOUT_CACHE
    _BLACKOUT_CACHE = set(_FOMC_DATES) | _nfp_dates() | _opex_dates() | _cpi_windows()
    return _BLACKOUT_CACHE


def is_blackout(ts: pd.Timestamp) -> bool:
    """Return True if the bar timestamp falls on a macro-risk blackout date.

    For 1d bars the entire day is blackout; for intraday bars we mark a
    wider window around the event (US morning) by blacking out the entire
    UTC date for simplicity -- this is a conservative risk control, not a
    precise intraday flag.
    """
    if ts is None or pd.isna(ts):
        return False
    d = pd.Timestamp(ts).tz_convert("UTC").date().isoformat()
    return d in _blackout_dates()


def attach_blackout_flag(df: pd.DataFrame) -> pd.DataFrame:
    """Add feat_event_blackout (0/1 int column) onto any OHLCV frame."""
    if df.empty or "bar_ts_utc" not in df.columns:
        return df
    out = df.copy()
    out["feat_event_blackout"] = out["bar_ts_utc"].apply(is_blackout).astype(int)
    return out


# ── Lane-aware macro context attachment ───────────────────────────────
#
# VIX, DXY, and cross-market breadth (T4 equity-context pack) join onto
# every equity frame via backward as-of merge against their daily close.
# Breadth is computed from warehouse bars that already exist for the broad
# universe, so it's zero-new-data.  We expose these as feat_vix_ext,
# feat_dxy_slope, and feat_breadth_pct columns.

# Universe used for breadth computation: broad enough to be representative
# but not so broad that missing bars poison the statistic. ETFs are always
# ingested and cover every major sector, making them a cheap robust proxy.
_BREADTH_ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP",
    "XLY", "XLU", "XLB", "XLRE", "XLC", "KRE", "SMH", "KWEB", "GDX",
]

# In-memory breadth caches. Populated once per process:
#   _BREADTH_ETF_CACHE  {symbol: daily_df}        — each ETF loaded once
#   _BREADTH_PCT_CACHE  {(date_iso, lookback): pct} — per-date breadth result
# Without these caches a 236-symbol daily run would reload the 19 breadth
# ETFs ~236 times (once per symbol); with them, 19 loads total plus a few
# dict lookups per symbol.
_BREADTH_ETF_CACHE: dict = {}
_BREADTH_PCT_CACHE: dict = {}


def _load_breadth_etf(sym: str) -> pd.DataFrame:
    if sym in _BREADTH_ETF_CACHE:
        return _BREADTH_ETF_CACHE[sym]
    try:
        from price.warehouse import load_from_warehouse
        bars = load_from_warehouse(sym, "1d")
    except Exception:
        bars = pd.DataFrame()
    _BREADTH_ETF_CACHE[sym] = bars
    return bars


def compute_breadth_pct(reference_date: pd.Timestamp, lookback: int = 20,
                       intraday: bool = False) -> float:
    """Fraction of breadth-universe ETFs whose close is above their
    `lookback`-bar SMA as of `reference_date`. Uses 1d bars (always available
    via Tiingo/yfinance). Returns NaN on failure (features pin to unknown).

    Results are cached per (date, lookback, intraday); ETF loads are cached per
    symbol, so the amortised cost per call across a discovery run is a dict
    lookup.

    Look-ahead guard: when `intraday` is True, the current UTC calendar day's
    daily bar (which may still be forming at the time of an intraday primary
    bar) is EXCLUDED -- we only look at daily bars whose UTC date is strictly
    less than the reference date's UTC date. For daily primary frames
    (intraday=False) we include today's daily bar (it is complete by the time
    a 1d bar is emitted).
    """
    if reference_date is None or pd.isna(reference_date):
        return float("nan")
    ref = pd.Timestamp(reference_date)
    key = (ref.date().isoformat(), lookback, intraday)
    if key in _BREADTH_PCT_CACHE:
        return _BREADTH_PCT_CACHE[key]

    # Determine the cutoff: intraday frames can only see daily bars strictly
    # before today (UTC); daily frames can see today's completed bar.
    if intraday:
        today_utc = ref.tz_convert("UTC").normalize()
        cutoff = today_utc - pd.Timedelta(nanoseconds=1)  # < today 00:00 UTC
    else:
        cutoff = ref

    above = 0
    counted = 0
    for sym in _BREADTH_ETFS:
        try:
            bars = _load_breadth_etf(sym)
            if bars is None or bars.empty:
                continue
            c = bars["close_adj"] if "close_adj" in bars.columns else bars.get("close_raw")
            if c is None:
                continue
            hist = bars[bars["bar_ts_utc"] <= cutoff]
            if len(hist) < lookback:
                continue
            tail = c.loc[hist.index].tail(lookback)
            sma = tail.mean()
            last = tail.iloc[-1]
            counted += 1
            if pd.notna(sma) and pd.notna(last) and last > sma:
                above += 1
        except Exception:
            continue
    pct = above / counted if counted > 0 else float("nan")
    _BREADTH_PCT_CACHE[key] = pct
    return pct


def reset_breadth_cache() -> None:
    """Clear in-memory breadth caches. Call between research shards, after
    warehouse refreshes, or in tests so stale data doesn't persist."""
    _BREADTH_ETF_CACHE.clear()
    _BREADTH_PCT_CACHE.clear()


def _is_intraday_frame(df: pd.DataFrame) -> bool:
    """Detect whether a frame's bars are intraday (<=1h). Uses median gap
    between consecutive bars; falls back to False (daily) if undecidable."""
    if df is None or df.empty or "bar_ts_utc" not in df.columns:
        return False
    ts = df["bar_ts_utc"]
    if len(ts) < 2:
        return False
    try:
        med = ts.sort_values().diff().dropna().median()
        return pd.notna(med) and med <= pd.Timedelta(hours=2)
    except Exception:
        return False


def _daily_effective_ts(ts: pd.Timestamp, intraday: bool) -> pd.Timestamp:
    """For VIX/DXY/breadth lookups, shift the reference timestamp so that
    an intraday bar at 14:00 UTC cannot see that same UTC day's daily close.
    We map intraday bars to the END of the PRIOR UTC day (23:59:59) so the
    as-of merge only picks up daily bars effective-timestamped on prior days.
    Daily frames can reference the same day's daily bar."""
    if intraday:
        return ts.tz_convert("UTC").normalize() - pd.Timedelta(nanoseconds=1)
    return ts


def _attach_macro_context(df: pd.DataFrame) -> pd.DataFrame:
    """Attach VIX extension, DXY slope, and breadth for equity frames."""
    if df.empty:
        return df
    out = df.copy().sort_values("bar_ts_utc").reset_index(drop=True)
    intraday = _is_intraday_frame(out)

    # VIX daily -> VIX extension vs its 20d MA (high VIX = risk-off regime).
    vix = fetch_vix_daily()
    if not vix.empty:
        v = vix.sort_values("bar_ts_utc").copy()
        v["vix_sma20"] = v["close_adj"].rolling(20, min_periods=10).mean()
        v["feat_vix_ext"] = (v["close_adj"] / v["vix_sma20"]) - 1.0
        v = v[["bar_ts_utc", "feat_vix_ext", "close_adj"]].rename(columns={"close_adj": "feat_vix_close"})
        # asof-join against a lookup key that prevents intraday frames from
        # matching today's not-yet-known close.
        out["_macro_lookup_ts"] = out["bar_ts_utc"].apply(lambda t: _daily_effective_ts(t, intraday))
        out = pd.merge_asof(
            out.sort_values("_macro_lookup_ts"), v.sort_values("bar_ts_utc"),
            left_on="_macro_lookup_ts", right_on="bar_ts_utc",
            direction="backward", tolerance=pd.Timedelta(days=7),
        )
        # The merge adds bar_ts_utc_y / renames; reconcile.
        if "bar_ts_utc_y" in out.columns:
            out = out.drop(columns=["bar_ts_utc_y"])
        if "bar_ts_utc_x" in out.columns:
            out = out.rename(columns={"bar_ts_utc_x": "bar_ts_utc"})
        out = out.drop(columns=["_macro_lookup_ts"], errors="ignore")
    else:
        out["feat_vix_ext"] = np.nan
        out["feat_vix_close"] = np.nan

    # DXY daily -> 20d slope (rate of dollar change; strong dollar = risk-off
    # for EM/cyclicals). Same intraday guard as VIX.
    dxy = fetch_dxy_daily()
    if not dxy.empty:
        d = dxy.sort_values("bar_ts_utc").copy()
        d["dxy_sma20"] = d["close_adj"].rolling(20, min_periods=10).mean()
        d["feat_dxy_slope"] = (d["close_adj"] / d["dxy_sma20"]) - 1.0
        d = d[["bar_ts_utc", "feat_dxy_slope"]]
        out["_macro_lookup_ts"] = out["bar_ts_utc"].apply(lambda t: _daily_effective_ts(t, intraday))
        out = pd.merge_asof(
            out.sort_values("_macro_lookup_ts"), d.sort_values("bar_ts_utc"),
            left_on="_macro_lookup_ts", right_on="bar_ts_utc",
            direction="backward", tolerance=pd.Timedelta(days=7),
        )
        if "bar_ts_utc_y" in out.columns:
            out = out.drop(columns=["bar_ts_utc_y"])
        if "bar_ts_utc_x" in out.columns:
            out = out.rename(columns={"bar_ts_utc_x": "bar_ts_utc"})
        out = out.drop(columns=["_macro_lookup_ts"], errors="ignore")
    else:
        out["feat_dxy_slope"] = np.nan

    # Breadth pct: % of breadth ETF universe above their 20d MA. Use strict
    # prior-day cutoff for intraday frames to prevent the forming day's close
    # leaking into an early bar.
    try:
        out["_mdate"] = out["bar_ts_utc"].dt.tz_convert("UTC").dt.date
        breadth_map: dict = {}
        for d in out["_mdate"].unique():
            if intraday:
                # Reference = end of prior UTC day (so daily bars strictly before today).
                ref_ts = pd.Timestamp(d, tz="UTC") - pd.Timedelta(nanoseconds=1)
            else:
                ref_ts = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=23)
            breadth_map[d] = compute_breadth_pct(ref_ts, lookback=20, intraday=intraday)
        out["feat_breadth_pct"] = out["_mdate"].map(breadth_map).astype(float)
        out = out.drop(columns=["_mdate"])
    except Exception:
        out["feat_breadth_pct"] = np.nan

    out = out.sort_values("bar_ts_utc").reset_index(drop=True)
    return out


def attach_lane_externals(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Lane-aware dispatcher: attach all external/macro features relevant
    to the symbol's substrate (crypto, futures, equity) onto a featured+
    binned frame. Called from discovery.precompute_binned_frame after
    compute_price_features. Always degrades to NaN columns on any failure.

    Adds columns (when applicable):
      - crypto: feat_funding_rate, feat_funding_ann, feat_funding_z20,
                feat_oi, feat_oi_value, feat_oi_change_5, feat_oi_change_20
      - futures: feat_cot_mm_net_pct, feat_cot_mm_z52
      - equities: feat_vix_ext, feat_vix_close, feat_dxy_slope, feat_breadth_pct
      - all lanes: feat_event_blackout  (also attached in compute_price_features,
                  duplicated here is harmless; ensures presence even if features
                  import fails)
    """
    if df.empty:
        return df
    out = df
    try:
        if is_crypto(symbol):
            out = attach_crypto_externals(out, symbol)
        elif is_futures(symbol):
            out = attach_futures_externals(out, symbol)
        else:
            out = _attach_macro_context(out)
    except Exception:
        # Never let an external-data failure poison discovery.
        pass

    # Ensure blackout flag exists on every lane.
    if "feat_event_blackout" not in out.columns:
        try:
            out = attach_blackout_flag(out)
        except Exception:
            out["feat_event_blackout"] = 0
    return out
