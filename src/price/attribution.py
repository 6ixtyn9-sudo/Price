"""Per-slice realized P&L attribution (lever 5).

This module closes the loop between the backtested edge (validation's
fwd_ret_5) and realized trading P&L. It reconstructs round-trips from the
trade journal, measures realized cost (fill price vs signal-bar close --
the slippage calibration for lever 4's placeholder), and aggregates P&L
per slice so we can see which deployed edges actually earn their capital.

What it does
  1. Reconstruct round-trips: pair each entry with its exit (by symbol) to
     get per-trade realized P&L, bars held, and gross/return.
  2. Measure realized slippage: compare the fill price to the signal bar's
     close_adj (from the paper_trade_log or warehouse). This is the realized
     signal-to-fill gap that lever 4's slippage term stands in for. Over
     enough fills, the mean realized slippage replaces the conservative 3bp
     default -- closing the loop on the one honest placeholder.
  3. Attribute per slice: group round-trips by slice_combination, compute
     win rate, mean realized return, total P&L, and compare to the validation
     expectation (valid_mean_ret_costadj from the leaderboard).
  4. Report: a human-readable summary that works with zero round-trips
     (clearly says so and shows what it WILL measure) and becomes more
     useful as fills accumulate.

What it does NOT do
  - Place orders, modify the journal, or change any execution behaviour.
  - Claim an edge. Realized P&L is measurement, not promotion. A slice that
    is "up X% on paper" does not validate the edge; it is a data point that
    future validation can use.
  - Over-interpret small samples. With < N round-trips per slice the report
    flags the attribution as preliminary.

Doctrine: read-only analysis. Never the source of a promotion claim.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from price.config import DATA_DIR


TRADE_JOURNAL_PATH = DATA_DIR / "trade_journal.csv"
PAPER_TRADE_LOG_PATH = DATA_DIR / "paper_trade_log.csv"
LIVE_FORWARD_RETURNS_PATH = DATA_DIR / "live_forward_returns.csv"
CANDIDATE_LEADERBOARD_PATH = DATA_DIR / "candidate_leaderboard.csv"

# Below this many round-trips, per-slice stats are flagged as preliminary.
MIN_ROUND_TRIPS_FOR_STATS = 5
CONFIRMED_FILL_STATUSES = frozenset({"filled", "partially_filled", "closed"})


def _float_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _identity_key(symbol, timeframe, slice_combination, side="long", bin_mode="insample"):
    """Collision-safe identity for attribution and expected-return lookup."""
    return json.dumps([
        str(symbol).upper(), str(timeframe or ""), str(slice_combination),
        str(side or "long").lower(), str(bin_mode or "insample").lower(),
    ], separators=(",", ":"))


identity_key = _identity_key


def _canonical_status(row):
    broker = str(row.get("broker_status", ""))
    if broker.lower() not in ("", "nan", "none"):
        return broker.lower()
    return str(row.get("status", "")).lower()


def _confirmed_qty(row):
    status = _canonical_status(row)
    qty = _float_or_none(row.get("filled_qty"))
    if qty is None and status in CONFIRMED_FILL_STATUSES:
        qty = _float_or_none(row.get("qty"))
    return qty if qty is not None and qty > 0 else None


def _confirmed_price(row, action):
    status = _canonical_status(row)
    price = _float_or_none(row.get("filled_avg_price"))
    if price is not None and price > 0:
        return price
    # Compatibility for deterministic synthetic status=filled fixtures only.
    if status not in CONFIRMED_FILL_STATUSES:
        return None
    cols = ("avg_entry_price", "price", "limit_price") if action == "entry" else ("current_price",)
    for col in cols:
        price = _float_or_none(row.get(col))
        if price is not None and price > 0:
            return price
    return None


@dataclass
class RoundTrip:
    """One completed entry->exit cycle for a symbol."""

    symbol: str
    slice_combination: str
    side: str                      # "long" | "short"
    qty: float
    entry_price: float
    entry_ts: str
    exit_price: float
    exit_ts: str
    gross_pnl: float               # (exit-entry)*qty for long, negated for short
    gross_return: float            # gross_pnl / (entry_price*qty), signed by side
    bars_held: Optional[int] = None
    timeframe: str = ""
    bin_mode: str = "insample"
    entry_order_id: str = ""
    exit_order_id: str = ""
    signal_bar_ts: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "slice_combination": self.slice_combination,
            "side": self.side,
            "qty": self.qty,
            "entry_price": round(self.entry_price, 6),
            "exit_price": round(self.exit_price, 6),
            "gross_pnl": round(self.gross_pnl, 4),
            "gross_return": round(self.gross_return, 6),
            "bars_held": self.bars_held,
            "timeframe": self.timeframe,
            "bin_mode": self.bin_mode,
            "entry_order_id": self.entry_order_id,
            "exit_order_id": self.exit_order_id,
            "signal_bar_ts": self.signal_bar_ts,
            "identity_key": _identity_key(
                self.symbol, self.timeframe, self.slice_combination,
                self.side, self.bin_mode,
            ),
        }


@dataclass
class SliceAttribution:
    """Aggregated realized P&L for one slice_combination."""

    slice_combination: str
    symbol: str
    side: str
    n_round_trips: int
    win_rate: float                # fraction of round-trips with gross_pnl > 0
    mean_gross_return: float       # mean per-trade gross return
    total_gross_pnl: float
    expected_return: Optional[float]   # from validation valid_mean_ret_costadj
    realized_slippage_bps: Optional[float]  # fill-vs-signal gap, if measurable
    net_of_cost_return: Optional[float]     # mean_gross_return - realized_cost_drag
    preliminary: bool              # True if n < MIN_ROUND_TRIPS_FOR_STATS
    timeframe: str = ""
    bin_mode: str = "insample"
    signal_to_fill_bps: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "slice_combination": self.slice_combination,
            "symbol": self.symbol,
            "side": self.side,
            "timeframe": self.timeframe,
            "bin_mode": self.bin_mode,
            "identity_key": _identity_key(
                self.symbol, self.timeframe, self.slice_combination,
                self.side, self.bin_mode,
            ),
            "n_round_trips": self.n_round_trips,
            "win_rate": round(self.win_rate, 4),
            "mean_gross_return": round(self.mean_gross_return, 6),
            "total_gross_pnl": round(self.total_gross_pnl, 4),
            "expected_return": (round(self.expected_return, 6)
                                 if self.expected_return is not None else None),
            "realized_slippage_bps": (round(self.realized_slippage_bps, 2)
                                      if self.realized_slippage_bps is not None else None),
            "signal_to_fill_bps": (round(self.signal_to_fill_bps, 2)
                                    if self.signal_to_fill_bps is not None else None),
            "net_of_cost_return": (round(self.net_of_cost_return, 6)
                                   if self.net_of_cost_return is not None else None),
            "preliminary": self.preliminary,
        }


def load_trade_journal(path: Optional[Path] = None) -> pd.DataFrame:
    """Load the trade journal; empty DataFrame if absent."""
    p = Path(path) if path else TRADE_JOURNAL_PATH
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def reconstruct_round_trips(journal: Optional[pd.DataFrame] = None) -> List[RoundTrip]:
    """Pair entries with exits by symbol to build completed round-trips.

    Logic: for each symbol, entries and exits are matched FIFO by timestamp.
    An entry with no subsequent exit is an open position (not a round-trip).
    Partial fills are handled by matching min(entry_qty, exit_qty).

    Returns only COMPLETED round-trips (entry+exit paired). Open positions
    are excluded -- they have unrealized, not realized, P&L.
    """
    if journal is None:
        journal = load_trade_journal()
    if journal is None or journal.empty:
        return []
    if "action" not in journal.columns or "symbol" not in journal.columns:
        return []

    # Submission-time accepted/pending rows are not fills. Require a final
    # broker-confirmed status and a positive filled quantity/price. This is
    # what prevents expired/canceled/unfilled orders from becoming trades.
    j = journal.copy()
    j["_canonical_status"] = j.apply(_canonical_status, axis=1)
    j = j[
        j["action"].isin(["entry", "exit", "close"])
        & j["_canonical_status"].isin(CONFIRMED_FILL_STATUSES)
    ].copy()
    if j.empty:
        return []
    j["resolved_qty"] = j.apply(_confirmed_qty, axis=1)
    j["resolved_price"] = j.apply(
        lambda row: _confirmed_price(row, "entry" if row["action"] == "entry" else "exit"),
        axis=1,
    )
    j = j.dropna(subset=["resolved_qty", "resolved_price"])
    j = j[(j["resolved_qty"] > 0) & (j["resolved_price"] > 0)]
    if j.empty:
        return []

    j["_ts"] = pd.to_datetime(j.get("submitted_at", j.get("timestamp_utc")),
                              errors="coerce", utc=True)
    j = j.dropna(subset=["_ts"]).sort_values("_ts")
    if j.empty:
        return []

    def _get(row, col, default=0.0):
        v = getattr(row, col, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return default
        return v if v == v else default

    def _str(row, col, default=""):
        v = getattr(row, col, None)
        return str(v) if v is not None and str(v).lower() != "nan" else default

    round_trips: List[RoundTrip] = []

    for symbol, grp in j.groupby("symbol"):
        entries = grp[grp["action"] == "entry"].copy()
        exits = grp[grp["action"].isin(["exit", "close"])].copy()
        if entries.empty or exits.empty:
            continue

        # FIFO matching: consume entry quantity from the exit quantity.
        entry_q = list(zip(entries["_ts"], entries.itertuples()))
        entry_q.sort(key=lambda x: x[0])
        exit_q = list(zip(exits["_ts"], exits.itertuples()))
        exit_q.sort(key=lambda x: x[0])

        pending_entries = list(entry_q)  # FIFO queue
        pending_residuals: dict = {}
        for exit_ts, exit_row in exit_q:
            exit_qty_remaining = _get(exit_row, "resolved_qty", 0.0)
            if exit_qty_remaining <= 0:
                continue
            exit_price = _get(exit_row, "resolved_price", 0.0)

            while exit_qty_remaining > 1e-9 and pending_entries:
                ent_ts, ent_row = pending_entries.pop(0)
                ent_qty = pending_residuals.pop(id(ent_row), _get(ent_row, "resolved_qty", 0.0))
                if ent_qty <= 0:
                    continue
                ent_price = _get(ent_row, "resolved_price", 0.0)
                if ent_price <= 0:
                    continue

                matched_qty = min(ent_qty, exit_qty_remaining)
                exit_qty_remaining -= matched_qty

                # Side is determined by the ENTRY, not the exit: a long enters
                # "buy" and closes "sell"; a short enters "sell" and closes
                # "buy". Reading the exit's side would mis-label every long
                # close as a short.
                ent_side = _str(ent_row, "side", "buy")
                is_short = ent_side.lower() in ("sell", "short")
                gross_pnl = ((exit_price - ent_price) * matched_qty
                             if not is_short
                             else (ent_price - exit_price) * matched_qty)
                notional = ent_price * matched_qty
                gross_return = gross_pnl / notional if notional > 0 else 0.0

                slice_lbl = _str(ent_row, "slice_label", "")
                if not slice_lbl:
                    slice_lbl = _str(exit_row, "slice_label", "unknown")

                round_trips.append(RoundTrip(
                    symbol=str(symbol).upper(),
                    slice_combination=slice_lbl,
                    side="short" if is_short else "long",
                    qty=matched_qty,
                    entry_price=ent_price,
                    entry_ts=str(ent_ts),
                    exit_price=exit_price,
                    exit_ts=str(exit_ts),
                    gross_pnl=gross_pnl,
                    gross_return=gross_return,
                    timeframe=_str(ent_row, "timeframe", ""),
                    bin_mode=_str(ent_row, "bin_mode", "insample"),
                    entry_order_id=_str(ent_row, "order_id", ""),
                    exit_order_id=_str(exit_row, "order_id", ""),
                    signal_bar_ts=_str(ent_row, "entry_bar_ts", ""),
                ))

                # If entry had leftover qty, push it back (partial fill).
                residual = ent_qty - matched_qty
                if residual > 1e-9:
                    pending_residuals[id(ent_row)] = residual
                    pending_entries.insert(0, (ent_ts, ent_row))

    return round_trips


def load_expected_returns(
    leaderboard_path: Optional[Path] = None,
) -> Dict[str, float]:
    """{slice_combination: valid_mean_ret_costadj} from the leaderboard.

    Used to compare realized P&L to the validation expectation. Returns {}
    when no leaderboard is present (graceful degradation).
    """
    p = Path(leaderboard_path) if leaderboard_path else CANDIDATE_LEADERBOARD_PATH
    if not p.exists():
        return {}
    try:
        lb = pd.read_csv(p)
    except Exception:  # noqa: BLE001
        return {}
    if lb is None or lb.empty or "slice_combination" not in lb.columns:
        return {}
    col = "valid_mean_ret_costadj" if "valid_mean_ret_costadj" in lb.columns else None
    if col is None:
        return {}
    out: Dict[str, float] = {}
    for _, r in lb.iterrows():
        sc = str(r.get("slice_combination", ""))
        symbol = str(r.get("symbol", "")).upper()
        timeframe = str(r.get("timeframe", ""))
        side = str(r.get("side", "long") or "long").lower()
        bin_mode = str(r.get("bin_mode", "insample") or "insample").lower()
        v = r.get(col)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if sc and symbol and timeframe and v == v:
            out[_identity_key(symbol, timeframe, sc, side, bin_mode)] = v
    return out


def _signal_rows_for_round_trip(rt: RoundTrip, matched: pd.DataFrame) -> pd.DataFrame:
    """Join a round-trip to its exact signal, never to a slice-wide average."""
    if rt.entry_order_id and "order_id" in matched.columns:
        by_order = matched[matched["order_id"].astype(str) == rt.entry_order_id]
        if not by_order.empty:
            return by_order

    mask = (
        matched["symbol"].astype(str).str.upper() == rt.symbol.upper()
    ) & (matched["slice_combination"].astype(str) == rt.slice_combination)
    if rt.timeframe and "timeframe" in matched.columns:
        mask &= matched["timeframe"].astype(str) == rt.timeframe
    if "bin_mode" in matched.columns:
        mask &= matched["bin_mode"].fillna("insample").astype(str).str.lower() == rt.bin_mode.lower()
    candidates = matched[mask]
    if rt.signal_bar_ts and "bar_ts_utc" in candidates.columns:
        exact = candidates[candidates["bar_ts_utc"].astype(str) == rt.signal_bar_ts]
        if not exact.empty:
            return exact
    return candidates


def _measure_signal_gaps(round_trips: List[RoundTrip], paper_log_path: Optional[Path] = None):
    p = Path(paper_log_path) if paper_log_path else PAPER_TRADE_LOG_PATH
    if not round_trips or not p.exists():
        return {}, {}
    try:
        log = pd.read_csv(p)
    except Exception:  # noqa: BLE001
        return {}, {}
    if log.empty or "close_adj" not in log.columns or "slice_combination" not in log.columns:
        return {}, {}

    matched = log.copy()
    if "matched" in matched.columns:
        matched = matched[matched["matched"].astype(str).str.lower().isin({"true", "1", "yes"})]
    if "action" in matched.columns:
        matched = matched[matched["action"].isin(["enter", "would_enter"])]
    matched["close_adj"] = pd.to_numeric(matched["close_adj"], errors="coerce")
    matched = matched.dropna(subset=["close_adj"])
    if matched.empty:
        return {}, {}

    adverse: Dict[str, List[float]] = {}
    signed: Dict[str, List[float]] = {}
    for rt in round_trips:
        candidates = _signal_rows_for_round_trip(rt, matched)
        if candidates.empty:
            continue
        closes = candidates["close_adj"].dropna()
        # If the join is not exact, refuse to average unrelated signal bars.
        if closes.empty or len(closes.unique()) != 1:
            continue
        signal_close = float(closes.iloc[0])
        if signal_close <= 0 or rt.entry_price <= 0:
            continue
        gap = (rt.entry_price - signal_close) / signal_close
        if rt.side == "short":
            gap = -gap
        key = _identity_key(rt.symbol, rt.timeframe, rt.slice_combination, rt.side, rt.bin_mode)
        signed.setdefault(key, []).append(gap * 10000.0)
        # Favorable movement is a diagnostic gap, not negative execution cost.
        adverse.setdefault(key, []).append(max(gap, 0.0) * 10000.0)

    return (
        {key: sum(values) / len(values) for key, values in adverse.items()},
        {key: sum(values) / len(values) for key, values in signed.items()},
    )


def measure_realized_slippage(
    round_trips: List[RoundTrip],
    paper_log_path: Optional[Path] = None,
) -> Dict[str, float]:
    """Measure adverse entry slippage after an exact signal join."""
    adverse, _ = _measure_signal_gaps(round_trips, paper_log_path)
    return adverse


def attribute_pnl(
    journal: Optional[pd.DataFrame] = None,
    leaderboard_path: Optional[Path] = None,
    paper_log_path: Optional[Path] = None,
    broker_positions: Optional[pd.DataFrame] = None,
) -> dict:
    """Build attribution from confirmed fills and optional broker exposure."""
    if journal is None:
        journal = load_trade_journal()
    round_trips = reconstruct_round_trips(journal)
    expected = load_expected_returns(leaderboard_path)
    slippage, signed_gap = _measure_signal_gaps(round_trips, paper_log_path)

    by_key: Dict[str, List[RoundTrip]] = {}
    for rt in round_trips:
        key = _identity_key(rt.symbol, rt.timeframe, rt.slice_combination, rt.side, rt.bin_mode)
        by_key.setdefault(key, []).append(rt)

    slice_attrs: List[SliceAttribution] = []
    for key, rts in by_key.items():
        first = rts[0]
        n = len(rts)
        mean_ret = sum(r.gross_return for r in rts) / n
        slip = slippage.get(key)
        net = mean_ret - (slip * 2.0 / 10000.0) if slip is not None else None
        slice_attrs.append(SliceAttribution(
            slice_combination=first.slice_combination,
            symbol=first.symbol,
            side=first.side,
            n_round_trips=n,
            win_rate=sum(1 for r in rts if r.gross_pnl > 0) / n,
            mean_gross_return=mean_ret,
            total_gross_pnl=sum(r.gross_pnl for r in rts),
            expected_return=expected.get(key),
            realized_slippage_bps=slip,
            net_of_cost_return=net,
            preliminary=n < MIN_ROUND_TRIPS_FOR_STATS,
            timeframe=first.timeframe,
            bin_mode=first.bin_mode,
            signal_to_fill_bps=signed_gap.get(key),
        ))
    slice_attrs.sort(key=lambda a: (-a.n_round_trips, -a.total_gross_pnl))

    n_open = None
    if broker_positions is not None:
        if broker_positions.empty or "qty" not in broker_positions.columns:
            n_open = 0
        else:
            qty = pd.to_numeric(broker_positions["qty"], errors="coerce").fillna(0).abs()
            n_open = int((qty > 0).sum())

    notes: List[str] = []
    if broker_positions is None:
        notes.append("Broker positions were not supplied; run attribute_pnl.py --sync-broker for authoritative exposure.")
    if not round_trips:
        notes.append("No broker-confirmed completed round-trips yet. Unfilled, pending, expired, canceled, and rejected orders are excluded.")
    prelim = [a for a in slice_attrs if a.preliminary]
    if prelim:
        notes.append(
            f"{len(prelim)} slice(s) have fewer than {MIN_ROUND_TRIPS_FOR_STATS} round-trips; "
            "their stats are preliminary and should not be interpreted as stable."
        )
    if not expected:
        notes.append("No symbol/timeframe-matched candidate leaderboard rows found; expected-vs-realized comparison is unavailable.")

    return {
        "summary": {
            "n_round_trips": len(round_trips),
            "n_open_positions": n_open,
            "open_positions_source": "alpaca" if broker_positions is not None else "unavailable",
            "total_realized_pnl": round(sum(rt.gross_pnl for rt in round_trips), 4),
        },
        "by_slice": [a.to_dict() for a in slice_attrs],
        "round_trips": [rt.to_dict() for rt in round_trips],
        "realized_slippage": {k: round(v, 2) for k, v in slippage.items()},
        "signal_to_fill_bps": {k: round(v, 2) for k, v in signed_gap.items()},
        "expected_returns": {k: round(v, 6) for k, v in expected.items()},
        "notes": notes,
    }


def format_report(report: dict) -> str:
    """Human-readable attribution report. Works with zero round-trips."""
    lines: List[str] = []
    s = report["summary"]
    lines.append("=" * 72)
    lines.append("P&L ATTRIBUTION REPORT")
    lines.append("=" * 72)
    lines.append(f"  Completed round-trips: {s['n_round_trips']}")
    open_positions = s.get("n_open_positions")
    open_text = str(open_positions) if open_positions is not None else "unavailable (run --sync-broker)"
    lines.append(f"  Open positions:        {open_text}")
    lines.append(f"  Total realized P&L:    ${s['total_realized_pnl']:.2f}")
    lines.append("")

    by_slice = report["by_slice"]
    if by_slice:
        lines.append("-" * 72)
        lines.append("PER-SLICE ATTRIBUTION")
        lines.append("-" * 72)
        hdr = (f"{'symbol':8} {'tf':4} {'slice':46} {'n':>3} {'win%':>5} {'meanRet':>8} "
               f"{'totPnL':>9} {'expRet':>8} {'slipBp':>7}")
        lines.append(hdr)
        lines.append("-" * 72)
        for a in by_slice:
            sc = a["slice_combination"][:50]
            exp = f"{a['expected_return']*100:.2f}%" if a["expected_return"] is not None else "  n/a "
            slip = f"{a['realized_slippage_bps']:.1f}" if a["realized_slippage_bps"] is not None else "  n/a"
            flag = " *" if a["preliminary"] else ""
            lines.append(
                f"{a.get('symbol', '')[:8]:8} {a.get('timeframe', '')[:4]:4} "
                f"{sc[:44]:46} {a['n_round_trips']:>3} "
                f"{a['win_rate']*100:>4.0f}% "
                f"{a['mean_gross_return']*100:>7.2f}% "
                f"${a['total_gross_pnl']:>8.2f} {exp:>8} {slip:>7}{flag}"
            )
        lines.append("  (* = preliminary, < "
                     f"{MIN_ROUND_TRIPS_FOR_STATS} round-trips)")
        lines.append("")

    slip = report["realized_slippage"]
    if slip:
        lines.append("-" * 72)
        lines.append("REALIZED SLIPPAGE (calibrates the cost model's slippage term)")
        lines.append("-" * 72)
        for sc, bps in slip.items():
            lines.append(f"  {sc[:50]:50} {bps:>7.1f} bps (entry leg)")
        lines.append("  Favorable signal-to-fill movement is not treated as negative cost.")
        lines.append("")

    if report.get("signal_to_fill_bps"):
        lines.append("SIGNAL-TO-FILL GAP (signed; diagnostic only)")
        for key, bps in report["signal_to_fill_bps"].items():
            lines.append(f"  {key}: {bps:>7.1f} bps")
        lines.append("")

    if report["notes"]:
        lines.append("-" * 72)
        lines.append("NOTES")
        lines.append("-" * 72)
        for n in report["notes"]:
            lines.append(f"  - {n}")

    lines.append("=" * 72)
    return "\n".join(lines)
