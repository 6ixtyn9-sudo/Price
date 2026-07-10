"""Live state monitor: polls Alpaca for recent bars, computes features,
bins state, checks slice matches, and gates every signal through the
risk-limits guard before emitting it.

This is the bridge between the research pipeline (warehouse, features,
discovery, validation) and the paper-trading execution layer (trading.py).

It does NOT place orders. It only produces signals (and a list of
exit intents for any open positions whose originating slice's stable
state no longer matches). The paper_trade.py script consumes these
and decides whether to act on them.

Coupling note: it does READ account state (open positions, trade
journal, today's realized P&L) to enforce the risk gate. This is a
deliberate deviation from a pure 'signal-only' monitor and exists
specifically so that no signal that breaches the risk limits can
ever reach trading.submit_entry(). See HANDOVER.md,
"Paper-Trading Exploration Layer (2026-07-02)".
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
import re

import numpy as np
import pandas as pd

from price.config import DATA_DIR
# Backward-compatible hook for tests/older monitor workflows that monkeypatch
# a live-refresh fetcher. get_current_state intentionally uses warehouse only.
from price.data_sources import fetch_alpaca_bars  # noqa: F401
from price.discovery import apply_state_bins, attach_cross_asset_states
from price.features import compute_price_features
from price.leverage import total_open_notional
from price.position_manager import ExitPolicy, check_exits, get_today_realized_pnl
from price.regime import check_regime
from price.risk_limits import RiskLimits, check_entry, risk_group_key
from price.sizing import compute_atr_14, compute_position_size
from price.stop_manager import reconcile_stops
from price.stops import DEFAULT_STOP_ATR_MULT, load_stop_states
from price.trading import get_open_positions, get_open_orders
from price.validation import parse_slice_combination
from price.warehouse import load_from_warehouse


DEFAULT_MONITORED_SLICES: List[dict] = [
    {"symbol": "XLF", "timeframe": "1d", "slice_combination": "state_ext=stretched_up + state_slope=flat", "side": "long"},
    {"symbol": "XLK", "timeframe": "1d", "slice_combination": "cross_TLT_state_slope=uptrend + state_ext=neutral", "side": "long"},
    {"symbol": "XLK", "timeframe": "1h", "slice_combination": "cross_USO_state_vol=mid_vol + state_ext=stretched_down", "side": "long"},
    {"symbol": "SPY", "timeframe": "1h", "slice_combination": "state_session=afternoon + state_slope=downtrend", "side": "long"},
]
CANDIDATE_LEADERBOARD_PATH = DATA_DIR / "candidate_leaderboard.csv"
MONITORED_SLICES_PATH = DATA_DIR / "monitored_slices.csv"
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}(/[A-Z0-9][A-Z0-9.\-]{0,14})?$")
VALID_TIMEFRAMES = {"1d", "1h", "15m"}
VALID_BIN_MODES = {"insample", "rolling"}
VALID_SIDES = {"long", "short"}


def _valid_symbol_text(value) -> bool:
    return bool(SYMBOL_PATTERN.fullmatch(str(value).strip().upper()))


def _load_clean_survivor_monitored_slices() -> Optional[List[dict]]:
    """Prefer the current clean-survivor leaderboard set when available.

    This keeps the monitor aligned with live_forward_returns.py, which tracks
    the dynamic clean_survivor* universe rather than the older hardcoded V4
    slice list.
    """
    path = Path(CANDIDATE_LEADERBOARD_PATH)
    if not path.exists():
        return None
    try:
        lb = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return None
    if lb.empty or "triage_bucket" not in lb.columns:
        return None

    clean = lb[lb["triage_bucket"].astype(str).str.startswith("clean_survivor")].copy()
    if clean.empty:
        return None

    out = []
    for _, row in clean.iterrows():
        side = str(row.get("side", "long") or "long").lower()
        if side not in ("long", "short"):
            side = "long"
        bin_mode = str(row.get("bin_mode", "insample") or "insample").lower()
        if bin_mode not in ("insample", "rolling"):
            bin_mode = "insample"
        out.append(
            {
                "symbol": str(row["symbol"]),
                "timeframe": str(row["timeframe"]),
                "slice_combination": str(row["slice_combination"]),
                "side": side,
                "bin_mode": bin_mode,
            }
        )
    return out or None


def _load_explicit_monitored_slices() -> Optional[List[dict]]:
    """Load an operator-curated monitored_slices.csv when present.

    This is the deployment/watch-list source. It prevents the monitor from
    accidentally scanning every clean_survivor* row in whichever research
    candidate_leaderboard.csv happens to be current.
    """
    path = Path(MONITORED_SLICES_PATH)
    if not path.exists():
        return None

    try:
        rows = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return None

    if rows.empty:
        return None

    required = {"symbol", "timeframe", "slice_combination"}
    missing = required - set(rows.columns)
    if missing:
        print(f"monitored_slices.csv missing required columns: {sorted(missing)}")
        return None

    out = []
    for idx, row in rows.iterrows():
        symbol = str(row["symbol"]).strip().upper()
        timeframe = str(row["timeframe"]).strip()
        slice_combination = str(row["slice_combination"]).strip()
        side = str(row.get("side", "long") or "long").lower()
        if not _valid_symbol_text(symbol):
            print(f"monitored_slices.csv row {idx}: invalid symbol {symbol!r}; skipping")
            continue
        if timeframe not in VALID_TIMEFRAMES:
            print(f"monitored_slices.csv row {idx}: invalid timeframe {timeframe!r}; skipping")
            continue
        if side not in VALID_SIDES:
            print(f"monitored_slices.csv row {idx}: invalid side {side!r}; skipping")
            continue
        try:
            parse_slice_combination(slice_combination)
        except ValueError as exc:
            print(f"monitored_slices.csv row {idx}: invalid slice_combination: {exc}; skipping")
            continue

        record = {
            "symbol": symbol,
            "timeframe": timeframe,
            "slice_combination": slice_combination,
            "side": side,
        }
        # Optional deployment metadata. `regime_symbol` is functional: the
        # regime gate uses it as the macro/sector series for this slice. Keep
        # it when present instead of silently discarding the operator's gate.
        for optional_col in ("regime_symbol", "source_note"):
            if optional_col in rows.columns and pd.notna(row.get(optional_col)):
                value = str(row.get(optional_col)).strip()
                if value:
                    record[optional_col] = value.upper() if optional_col == "regime_symbol" else value
        bin_mode = str(row.get("bin_mode", "insample") or "insample").lower()
        if bin_mode not in VALID_BIN_MODES:
            print(f"monitored_slices.csv row {idx}: invalid bin_mode {bin_mode!r}; skipping")
            continue
        record["bin_mode"] = bin_mode
        out.append(record)

    return out or None


def get_default_monitored_slices() -> List[dict]:
    explicit = _load_explicit_monitored_slices()
    if explicit is not None:
        return explicit
    print("monitor: monitored_slices.csv not present; falling back to candidate_leaderboard.csv clean_survivor set.")
    dynamic = _load_clean_survivor_monitored_slices()
    if dynamic is not None:
        return dynamic
    print("monitor: candidate_leaderboard.csv clean_survivor empty; falling back to hardcoded DEFAULT_MONITORED_SLICES.")
    return DEFAULT_MONITORED_SLICES


def _drop_incomplete_intraday_rows(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Keep only completed intraday bars.

    `build_warehouse.py` may already have the current partial 1h bar because it
    resamples the latest 15m data. The monitor should reason about the latest
    completed bar, not a still-forming bar.
    """
    if df.empty or timeframe not in ("15m", "1h"):
        return df

    bar_delta = timedelta(minutes=15) if timeframe == "15m" else timedelta(hours=1)
    cutoff = datetime.now(timezone.utc)
    complete = df[df["bar_ts_utc"] + bar_delta <= cutoff].copy()
    return complete.reset_index(drop=True)


def _state_unavailable_context(symbol: str, timeframe: str) -> dict:
    """Best-effort reason payload for an unavailable monitor state."""
    ctx = {
        "reason": "no_completed_state",
        "bar_ts_utc": None,
        "close_adj": None,
        "current_state": {},
    }

    df = load_from_warehouse(symbol, timeframe)
    if df.empty:
        ctx["reason"] = "no_warehouse_data"
        return ctx

    if "close_adj" not in df.columns:
        df = df.copy()
        df["close_adj"] = df.get("close_raw", df.get("close", np.nan))

    df = df.sort_values("bar_ts_utc").reset_index(drop=True)
    df = _drop_incomplete_intraday_rows(df, timeframe)
    if df.empty:
        return ctx

    latest = df.iloc[-1]
    ctx["bar_ts_utc"] = str(latest.get("bar_ts_utc"))
    close_adj = latest.get("close_adj", np.nan)
    try:
        close_adj_float = float(close_adj)
    except (TypeError, ValueError):
        close_adj_float = float("nan")
    ctx["close_adj"] = close_adj_float if close_adj_float == close_adj_float else None

    if pd.isna(close_adj):
        ctx["reason"] = "nan_state_features"
    return ctx


def get_current_state(
    symbol: str,
    timeframe: str,
    lookback_bars: Optional[int] = None,
    cross_symbols: Optional[Dict[str, List[str]]] = None,
    required_fields: Optional[List[str]] = None,
    bin_mode: str = "insample",
) -> Optional[pd.DataFrame]:
    """Compute the most recent completed binned state row.

    Important invariant: monitor state is rebuilt from the already-refreshed
    local warehouse only. Do NOT overlay fresh Alpaca rows here.

    Deployment/research alignment invariant: by default this computes bins
    over the full local warehouse partition, not only the latest 200 bars.
    Validation bins are computed over the full eligible history (or expanding
    history for bin_mode="rolling"); a short live-only tail can relabel
    quantile-derived states like state_slope/state_vol and make the monitor
    deploy a different state definition than the one that authorized the slice.
    Pass lookback_bars only for tests or deliberately-local diagnostics.

    Why:
    - the workflow refreshes the warehouse immediately before paper_trade.py
    - Alpaca's 1d/15m bars are raw-only, while the warehouse carries adjusted
      fields; mixing them here can make the latest row's close_adj/high_adj/
      low_adj NaN and collapse the daily state into NaNs
    - 1h monitoring must use the resampled 1h warehouse partition, not a raw
      concat of 1h bars with fresh 15m bars
    """
    df_warehouse = load_from_warehouse(symbol, timeframe)
    if df_warehouse.empty:
        return None

    if "close_adj" not in df_warehouse.columns:
        df_warehouse["open_adj"] = df_warehouse.get("open_raw", df_warehouse.get("open", np.nan))
        df_warehouse["high_adj"] = df_warehouse.get("high_raw", df_warehouse.get("high", np.nan))
        df_warehouse["low_adj"] = df_warehouse.get("low_raw", df_warehouse.get("low", np.nan))
        df_warehouse["close_adj"] = df_warehouse.get("close_raw", df_warehouse.get("close", np.nan))
        df_warehouse["adj_factor"] = 1.0
        df_warehouse["split_factor"] = 1.0
        df_warehouse["dividend_cash"] = 0.0

    df_tail = df_warehouse.tail(lookback_bars).copy() if lookback_bars else df_warehouse.copy()
    df_tail = df_tail.sort_values("bar_ts_utc").reset_index(drop=True)
    df_tail = _drop_incomplete_intraday_rows(df_tail, timeframe)

    if len(df_tail) < 60:
        print(f"  Only {len(df_tail)} completed bars for {symbol} ({timeframe}); need ~60 for features.")
        return None

    # Bin-frame provenance: quantile-derived states (state_slope/state_vol/
    # ret bands) depend on the frame they are binned over. Log it so a
    # research/live window mismatch is visible instead of silent.
    print(
        f"  Bin frame: {len(df_tail)} bars "
        f"[{df_tail['bar_ts_utc'].iloc[0]} .. {df_tail['bar_ts_utc'].iloc[-1]}] "
        f"bin_mode={bin_mode}"
    )

    latest_close = df_tail["close_adj"].iloc[-1] if "close_adj" in df_tail.columns else np.nan
    if pd.isna(latest_close):
        print(f"  Latest completed bar for {symbol} ({timeframe}) has NaN close_adj.")
        return None

    if bin_mode not in ("insample", "rolling"):
        bin_mode = "insample"
    df_feat = compute_price_features(df_tail)
    df_binned = apply_state_bins(df_feat, bin_mode=bin_mode)

    if cross_symbols:
        for cond_sym, fields in cross_symbols.items():
            df_binned = attach_cross_asset_states(
                df_binned, cond_sym, timeframe, fields, bin_mode=bin_mode,
            )

    required_fields = required_fields or []
    if required_fields:
        missing = [field for field in required_fields if field not in df_binned.columns]
        if missing:
            print(f"  Missing required state fields for {symbol} ({timeframe}): {missing}")
            return None

        latest = df_binned.iloc[-1:]
        invalid_fields = []
        if latest["close_adj"].isna().iloc[0]:
            invalid_fields.append("close_adj")
        for field in required_fields:
            if latest[field].isna().iloc[0]:
                invalid_fields.append(field)

        if invalid_fields:
            print(
                f"  Latest state row for {symbol} ({timeframe}) has NaN "
                f"required fields: {invalid_fields}"
            )
            return None
        return latest

    return df_binned.iloc[-1:]


def check_slice_match(current_state: pd.DataFrame, slice_combination: str) -> bool:
    """Return True iff every field=value in the slice matches the current
    state row. False on parse error or any mismatch.
    """
    try:
        slice_filter = parse_slice_combination(slice_combination)
    except ValueError:
        return False
    for field, value in slice_filter.items():
        if field not in current_state.columns:
            print(f"  Field '{field}' not in current state columns; skipping match.")
            return False
        actual = str(current_state[field].iloc[0])
        if actual != value:
            return False
    return True


def extract_cross_symbols(slice_combination: str) -> Dict[str, List[str]]:
    """{cond_symbol: [state_field]} extracted from a slice_combination
    string's cross_* fields. Empty dict if none.
    """
    try:
        filt = parse_slice_combination(slice_combination)
    except ValueError:
        return {}
    cross_syms: Dict[str, List[str]] = {}
    for field in filt:
        if not field.startswith("cross_"):
            continue
        rest = field[len("cross_"):]
        marker = "_state_"
        idx = rest.find(marker)
        if idx == -1:
            continue
        sym = rest[:idx]
        state_field = rest[idx + 1:]
        cross_syms.setdefault(sym, [])
        if state_field not in cross_syms[sym]:
            cross_syms[sym].append(state_field)
    return cross_syms


def _load_open_position_slice_labels() -> Dict[str, str]:
    """{symbol: slice_combination} for current open positions by scanning
    the trade journal for the most recent 'entry' per symbol.

    v1 heuristic: one slice per symbol, which matches the current
    4-monitored-slice set. If a symbol has multiple open positions from
    different slices, this collapses them.
    """
    from price.trading import load_trade_journal
    journal = load_trade_journal()
    if journal is None or journal.empty:
        return {}
    if "action" not in journal.columns or "symbol" not in journal.columns:
        return {}

    entries = journal[journal["action"] == "entry"].copy()
    if entries.empty:
        return {}

    # Only confirmed broker fills can supply slice context for a live
    # position. Submission-time accepted/pending/expired/canceled rows are
    # not positions and must not label current exposure.
    status_series = entries["broker_status"] if "broker_status" in entries.columns else entries.get(
        "status", pd.Series("", index=entries.index)
    )
    status = status_series.astype(str).str.lower()
    entries = entries[status.isin({"filled", "partially_filled", "closed"})].copy()
    if entries.empty:
        return {}
    if "filled_qty" in entries.columns:
        qty = pd.to_numeric(entries["filled_qty"], errors="coerce").fillna(0)
    else:
        qty = pd.to_numeric(entries.get("qty", 0), errors="coerce").fillna(0)
    entries = entries[qty > 0].copy()
    if entries.empty:
        return {}

    entries["ts"] = pd.to_datetime(entries["timestamp_utc"], errors="coerce", utc=True)
    entries = entries.sort_values("ts").dropna(subset=["ts"])
    if entries.empty:
        return {}
    last_per_symbol = entries.groupby("symbol").tail(1)

    return {str(r["symbol"]).upper(): str(r.get("slice_label", ""))
            for _, r in last_per_symbol.iterrows()}


def scan_all_slices(
    slices: Optional[List[dict]] = None,
    limits: Optional[RiskLimits] = None,
    dry_run: bool = False,
    exit_policy: Optional[ExitPolicy] = None,
    cost_model=None,
    regime_filter_enabled: bool = False,
    entry_sync_blocked: bool = False,
    reconciliation_health: Optional[dict] = None,
) -> List[dict]:
    """Scan all monitored slices; emit tradable signals + exit intents.

    regime_filter_enabled : when True, each matched slice is additionally
        checked against its macro regime (SMA trend of the slice's own
        symbol or a configured regime symbol). Entries are blocked when the
        regime is 'bear'. Defaults False (zero-risk to the live book).
    """
    if slices is None:
        slices = get_default_monitored_slices()
    limits = limits or RiskLimits()
    signals: List[dict] = []

    open_positions_df = get_open_positions()
    open_positions_list = (
        open_positions_df.to_dict("records")
        if open_positions_df is not None and not open_positions_df.empty
        else []
    )

    open_orders_df = get_open_orders()
    open_orders_list = (
        open_orders_df.to_dict("records")
        if open_orders_df is not None and not open_orders_df.empty
        else []
    )

    # Treat pending/open orders like open exposure for risk gating so repeated
    # weekend/after-hours scans cannot queue duplicate DAY market orders before
    # the first accepted order has had a chance to fill or expire.
    exposure_for_entry_gate = open_positions_list + open_orders_list

    # Gross notional exposure already deployed, for the leverage budget
    # (risk_limits.check_entry's gross-notional cap). Computed unconditionally
    # (cheap, pure) but only actually GATES anything when the caller also
    # supplies equity_for_risk_cap below -- see price.leverage's fail-open
    # contract.
    open_positions_notional = total_open_notional(open_positions_list)

    # Real-time buying power for the margin-cushion backstop. Only fetched
    # when leverage is actually configured beyond the default (an extra
    # live account call otherwise adds no value and costs an API round
    # trip on every scan). Never crashes the scan on a fetch failure --
    # the margin-cushion check fails open when buying_power is None.
    buying_power = None
    if (
        getattr(limits, "target_leverage_multiple", 1.0) != 1.0
        and getattr(limits, "margin_cushion_pct", None)
    ):
        try:
            from price.trading import get_account_info
            buying_power = get_account_info().get("buying_power")
        except Exception as e:  # noqa: BLE001 - a fetch failure must not crash the scan
            print(f"  could not fetch buying_power for margin-cushion check: {e}")
            buying_power = None

    today_pnl = get_today_realized_pnl()
    open_position_slice_labels = _load_open_position_slice_labels()
    # Risk group per CURRENT exposure (symbol -> stable-condition key). Built
    # from the trade journal's slice labels because broker positions/orders do
    # not carry their originating slice. Critically, filter the journal labels
    # to symbols that are actually open/pending RIGHT NOW; a stale historical
    # entry row for a symbol that has since exited/canceled must not consume a
    # risk-group slot forever.
    exposure_symbols = {
        str(p.get("symbol", "")).upper()
        for p in exposure_for_entry_gate
        if p.get("symbol")
    }
    open_position_risk_groups = {
        sym: risk_group_key(sym, lbl)
        for sym, lbl in open_position_slice_labels.items()
        if lbl and sym in exposure_symbols
    }

    if open_positions_list:
        print(f"\nChecking exits for {len(open_positions_list)} open position(s)...")
        try:
            exit_intents = check_exits(
                open_positions_df,
                open_position_slice_labels,
                exit_policy=exit_policy,
            )
        except Exception as e:
            print(f"  exit-check failed: {e}")
            exit_intents = []
        for intent in exit_intents:
            print(f"  [{intent.get('action', '?').upper()}] {intent.get('symbol', '')}: {intent.get('reason', '')}")
            signals.append({
                "kind": "exit_intent",
                **intent,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })

    # ---- Protective-stop reconciliation (real, broker-side R management) ----
    # Attaches an initial k*ATR stop the first scan after a fill, ratchets it
    # to breakeven at +1R and then trails it (chandelier) beyond that, and
    # cleans up bookkeeping for any tracked stop whose position is no longer
    # open (stopped out, or closed by state-break/horizon). This is what
    # makes "small losses, large profits" real rather than aspirational --
    # the stop is a resting order at the broker, not just a check that only
    # runs when this scan happens to run. See HANDOVER.md's R-based stop
    # design (2026-07-06).
    try:
        stop_intents = reconcile_stops(open_positions_df, limits, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001 - stop reconciliation must never crash the scan
        print(f"  stop-reconciliation failed: {e}")
        stop_intents = []
    for intent in stop_intents:
        print(f"  [STOP:{intent.get('action', '?').upper()}] {intent.get('symbol', '')}: "
              f"{intent.get('reason', '')}")
        signals.append({
            "kind": "stop_intent",
            **intent,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        })

    # Snapshot of every position's current tracked R-state, used below by
    # the aggregate open-risk budget check (the leverage prerequisite): a
    # new entry cannot push the book's total open risk past
    # limits.max_aggregate_open_risk_pct of equity.
    open_stop_states = load_stop_states()

    groups: Dict[tuple, List[dict]] = {}
    for s in slices:
        bin_mode = str(s.get("bin_mode", "insample") or "insample").lower()
        if bin_mode not in ("insample", "rolling"):
            bin_mode = "insample"
        s = {**s, "bin_mode": bin_mode}
        groups.setdefault((s["symbol"], s["timeframe"], bin_mode), []).append(s)

    for (symbol, timeframe, bin_mode), group_slices in groups.items():
        print(f"\nScanning {symbol} ({timeframe}, bin_mode={bin_mode})...")

        all_cross_symbols: Dict[str, List[str]] = {}
        required_state_fields: List[str] = []
        for s in group_slices:
            try:
                slice_filter = parse_slice_combination(s["slice_combination"])
            except ValueError:
                continue
            for field in slice_filter:
                if field not in required_state_fields:
                    required_state_fields.append(field)
            for sym, fields in extract_cross_symbols(s["slice_combination"]).items():
                all_cross_symbols.setdefault(sym, [])
                for f in fields:
                    if f not in all_cross_symbols[sym]:
                        all_cross_symbols[sym].append(f)

        current_state = get_current_state(
            symbol,
            timeframe,
            cross_symbols=all_cross_symbols if all_cross_symbols else None,
            required_fields=required_state_fields,
            bin_mode=bin_mode,
        )

        if current_state is None:
            print(f"  Could not compute a completed state for {symbol} ({timeframe})")
            for s in group_slices:
                # Emit a no_state_data entry_signal so paper_trade.py
                # can log it, AND a state_unavailable row for the
                # monitor's audit trail. The two serve different
                # purposes: the entry_signal row is per-slice (each
                # slice in the group gets its own row), while the
                # state_unavailable row is per-(symbol, timeframe).
                signals.append({
                    "kind": "entry_signal",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": s["slice_combination"],
                    "bin_mode": bin_mode,
                    "matched": False,
                    "tradable": False,
                    "error": "no_state_data",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                })
            unavailable_ctx = _state_unavailable_context(symbol, timeframe)
            signals.append({
                "kind": "state_unavailable",
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": group_slices[0]["slice_combination"],
                "bin_mode": bin_mode,
                **unavailable_ctx,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            })
            continue

        state_cols = [c for c in current_state.columns if c.startswith("state_") or c.startswith("cross_")]
        state_dict = {c: current_state[c].iloc[0] for c in state_cols}
        print(f"  Current state: {state_dict}")

        try:
            close_adj = float(current_state["close_adj"].iloc[0])
        except (KeyError, ValueError, TypeError):
            close_adj = float("nan")

        for s in group_slices:
            matched = check_slice_match(current_state, s["slice_combination"])
            if not matched:
                signals.append({
                    "kind": "entry_signal",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "slice_combination": s["slice_combination"],
                    "bin_mode": bin_mode,
                    "matched": False,
                    "tradable": False,
                    "current_state": state_dict,
                    "bar_ts_utc": str(current_state["bar_ts_utc"].iloc[0]),
                    "close_adj": close_adj,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                })
                print(f"  -   {s['slice_combination']}")
                continue

            # ---- Regime deployment gate ----
            # Converts today's finding (watchlist edges are regime-conditional)
            # into an actionable gate rather than a demotion. When enabled, a
            # matched slice whose macro regime is 'bear' is blocked from entry
            # (the fold-0 condition turned into an automatic dismount). When
            # disabled (default) it is a no-op pass-through.
            regime_state = check_regime(
                slice_symbol=symbol,
                slice_filter=parse_slice_combination(s["slice_combination"]),
                configured_regime_symbol=s.get("regime_symbol"),
                timeframe=timeframe,
                enabled=regime_filter_enabled,
            )

            # Edge- and volatility-aware sizing. Falls back to equal-notional
            # when no candidate_leaderboard.csv edge data is available, so the
            # live paper book is unaffected on a fresh/leaderboard-less run.
            size = compute_position_size(
                symbol=symbol,
                timeframe=timeframe,
                slice_combination=s["slice_combination"],
                close_adj=close_adj,
                limits=limits,
                cost_model=cost_model,
            )
            qty = size.qty
            # LOUD missing-edge warning: a monitored slice with no edge row
            # means conviction sizing silently degraded to equal-notional
            # (e.g. candidate_leaderboard.csv was overwritten by a research
            # diagnostic). Print every scan so the operator can see it.
            if size.sizing_mode == "fallback_no_data":
                print(
                    f"  WARNING: no edge metrics for {symbol} {timeframe} "
                    f"'{s['slice_combination']}' -- conviction sizing is "
                    "INACTIVE (neutral 1.0 / equal-notional). Provide "
                    "localdata/monitored_edge_metrics.csv or regenerate "
                    "candidate_leaderboard.csv."
                )
            # Regime gate outcome: when the macro regime is hostile AND the
            # filter is enabled, block the entry regardless of the risk gate.
            # Folded into the audit trail as a tradable=False reason so the
            # operator can see regime-blocking separately from risk-blocking.
            regime_blocked = (
                regime_filter_enabled and not regime_state.favourable()
            )
            side = str(s.get("side", "long") or "long").lower()
            if side not in ("long", "short"):
                side = "long"
            suggested_side = "sell" if side == "short" else "buy"
            candidate_group = risk_group_key(symbol, s["slice_combination"])

            if not dry_run:

                # Proposed R for the aggregate open-risk budget: the same
                # k_stop * ATR distance stop_manager.reconcile_stops will
                # actually place at the broker once this entry fills. None
                # when ATR is unavailable -- the budget check fails open in
                # that case, consistent with every other data-dependent
                # lever in this project (see risk_limits.check_entry).
                proposed_r_dollars = None
                try:
                    candidate_atr = compute_atr_14(load_from_warehouse(symbol, timeframe))
                    if candidate_atr is not None and qty > 0:
                        k_stop = getattr(limits, "stop_atr_multiple", DEFAULT_STOP_ATR_MULT)
                        proposed_r_dollars = k_stop * candidate_atr * qty
                except Exception:  # noqa: BLE001 - sizing/risk must never crash the scan
                    proposed_r_dollars = None

                risk_result = check_entry(
                    symbol=symbol,
                    qty=qty,
                    price=close_adj,
                    limits=limits,
                    open_positions=exposure_for_entry_gate,
                    today_realized_pnl=today_pnl,
                    side=side,
                    symbol_risk_group=candidate_group,
                    open_position_risk_groups=open_position_risk_groups,
                    proposed_r_dollars=proposed_r_dollars,
                    open_stop_states=open_stop_states,
                    equity_for_risk_cap=getattr(limits, "account_equity_for_sizing", None),
                    open_positions_notional=open_positions_notional,
                    buying_power=buying_power,
                )
                gate_reasons = list(risk_result.reasons)
                tradable = risk_result.allowed
                if regime_blocked:
                    tradable = False
                    gate_reasons.insert(
                        0,
                        f"regime hostile ({regime_state.regime} on "
                        f"{regime_state.symbol}); entry blocked",
                    )
                if entry_sync_blocked:
                    tradable = False
                    gate_reasons.insert(
                        0,
                        "broker reconciliation incomplete; new entry blocked",
                    )
                status_label = "MATCH  " if tradable else "BLOCKED"
                reasons_str = ", ".join(gate_reasons) if gate_reasons else "risk gate passed"
                risk_payload = {
                    "allowed": tradable,
                    "reasons": gate_reasons,
                    "details": {
                        **risk_result.details,
                        "reconciliation_health": reconciliation_health,
                    },
                }
            else:
                gate_reasons = ["dry_run"]
                tradable = not regime_blocked and not entry_sync_blocked
                if regime_blocked:
                    gate_reasons.append(
                        f"regime hostile ({regime_state.regime} on "
                        f"{regime_state.symbol}); entry blocked",
                    )
                if entry_sync_blocked:
                    gate_reasons.append(
                        "broker reconciliation incomplete; new entry blocked",
                    )
                status_label = "MATCH  " if tradable else "BLOCKED"
                reasons_str = ", ".join(gate_reasons)
                risk_payload = {
                    "allowed": tradable,
                    "reasons": gate_reasons,
                    "details": {"reconciliation_health": reconciliation_health},
                }

            signal = {
                "kind": "entry_signal",
                "symbol": symbol,
                "timeframe": timeframe,
                "slice_combination": s["slice_combination"],
                "bin_mode": bin_mode,
                "matched": True,
                "tradable": tradable,
                "side": side,
                "current_state": state_dict,
                "bar_ts_utc": str(current_state["bar_ts_utc"].iloc[0]),
                "close_adj": close_adj,
                "suggested_qty": qty,
                "suggested_side": suggested_side,
                "suggested_notional": (qty * close_adj) if close_adj == close_adj else None,
                "risk_group": candidate_group,
                **regime_state.to_audit_dict(),
                **size.to_audit_dict(),
                "risk_check": risk_payload,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
            signals.append(signal)
            verb = "tradable" if tradable else "blocked"
            print(f"  {status_label} {s['slice_combination']}  ({verb}: {reasons_str})")

    return signals
