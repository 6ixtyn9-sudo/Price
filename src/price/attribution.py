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

    def to_dict(self) -> dict:
        return {
            "slice_combination": self.slice_combination,
            "symbol": self.symbol,
            "side": self.side,
            "n_round_trips": self.n_round_trips,
            "win_rate": round(self.win_rate, 4),
            "mean_gross_return": round(self.mean_gross_return, 6),
            "total_gross_pnl": round(self.total_gross_pnl, 4),
            "expected_return": (round(self.expected_return, 6)
                                 if self.expected_return is not None else None),
            "realized_slippage_bps": (round(self.realized_slippage_bps, 2)
                                      if self.realized_slippage_bps is not None else None),
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

    # Only count filled orders (status accepted/filled/closed). "accepted"
    # pre-fill is NOT a fill; but the journal does not always carry a fill
    # status distinctly. We treat any non-rejected row as a fill candidate
    # and rely on the entry/exit pairing to define round-trips.
    j = journal.copy()
    if "status" in j.columns:
        j = j[j["status"].astype(str).str.lower() != "rejected"]
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
        for exit_ts, exit_row in exit_q:
            exit_qty_remaining = _get(exit_row, "qty", 0.0)
            if exit_qty_remaining <= 0:
                continue
            exit_price = _get(exit_row, "avg_entry_price",
                              _get(exit_row, "current_price", 0.0))
            # Exit rows may not carry a fill price; try current_price then
            # fall back to the position's avg_entry (means PnL ~0).
            exit_price = _get(exit_row, "current_price", exit_price)

            while exit_qty_remaining > 1e-9 and pending_entries:
                ent_ts, ent_row = pending_entries.pop(0)
                ent_qty = _get(ent_row, "qty", 0.0)
                if ent_qty <= 0:
                    continue
                # Entry rows created at submit-time do not always know the
                # eventual fill price. For close_position exits, the exit row's
                # avg_entry_price is the broker/account average entry basis and
                # is the best available source for reconstructing realized P&L.
                # Never allow a missing entry fill to become 0.0, because that
                # turns notional value into fake profit.
                ent_price = _get(ent_row, "avg_entry_price", 0.0)
                if ent_price <= 0:
                    ent_price = _get(exit_row, "avg_entry_price", 0.0)
                if ent_price <= 0:
                    ent_price = _get(ent_row, "price", 0.0)
                if ent_price <= 0:
                    ent_price = _get(ent_row, "limit_price", 0.0)
                if ent_price <= 0:
                    # Cannot price this round-trip honestly; skip it rather
                    # than fabricating P&L from a zero entry.
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
                ))

                # If entry had leftover qty, push it back (partial fill).
                if ent_qty - matched_qty > 1e-9:
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
        v = r.get(col)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if sc and v == v:
            out[sc] = v
    return out


def measure_realized_slippage(
    round_trips: List[RoundTrip],
    paper_log_path: Optional[Path] = None,
) -> Dict[str, float]:
    """Measure the realized signal-to-fill slippage per slice.

    Compares each round-trip's entry price to the signal bar's close_adj
    (recorded in the paper_trade_log when the entry signal fired). The mean
    gap, in basis points, is the REALIZED slippage that calibrates lever 4's
    conservative 3bp default.

    Returns {slice_combination: mean_realized_slippage_bps}. Empty when the
    paper log lacks close_adj or the round-trips have no matching signal.
    """
    if not round_trips:
        return {}
    p = Path(paper_log_path) if paper_log_path else PAPER_TRADE_LOG_PATH
    if not p.exists():
        return {}
    try:
        log = pd.read_csv(p)
    except Exception:  # noqa: BLE001
        return {}
    if log is None or log.empty:
        return {}
    if "close_adj" not in log.columns or "slice_combination" not in log.columns:
        return {}

    # Keep only matched entry signals with a usable close_adj.
    matched = log.copy()
    if "matched" in matched.columns:
        matched = matched[matched["matched"].astype(str).str.lower() == "true"]
    if "action" in matched.columns:
        matched = matched[matched["action"].isin(["enter", "would_enter"])]
    matched["close_adj"] = pd.to_numeric(matched["close_adj"], errors="coerce")
    matched = matched.dropna(subset=["close_adj"])
    if matched.empty:
        return {}

    # Mean signal-bar close per slice (the "expected" entry price).
    signal_close = (
        matched.groupby("slice_combination")["close_adj"].mean().to_dict()
    )

    out: Dict[str, float] = {}
    by_slice: Dict[str, list] = {}
    for rt in round_trips:
        sc = rt.slice_combination
        sig = signal_close.get(sc)
        if sig is None or sig <= 0 or rt.entry_price <= 0:
            continue
        # Slippage = how much WORSE than the signal-bar close we filled.
        # For a long, adverse = entry_price > signal_close.
        gap = (rt.entry_price - sig) / sig  # positive = adverse for long
        if rt.side == "short":
            gap = -gap  # for a short, filling lower is favorable
        by_slice.setdefault(sc, []).append(gap * 10000.0)  # to bps

    for sc, gaps in by_slice.items():
        out[sc] = sum(gaps) / len(gaps)
    return out


def attribute_pnl(
    journal: Optional[pd.DataFrame] = None,
    leaderboard_path: Optional[Path] = None,
    paper_log_path: Optional[Path] = None,
) -> dict:
    """Build the full P&L attribution report.

    Returns a dict with:
      - summary: totals (n_round_trips, n_open_positions, total_realized_pnl)
      - by_slice: list of SliceAttribution dicts
      - round_trips: list of RoundTrip dicts
      - realized_slippage: {slice: bps}
      - expected_returns: {slice: valid_mean_ret_costadj}
      - notes: human-readable caveats
    """
    if journal is None:
        journal = load_trade_journal()
    round_trips = reconstruct_round_trips(journal)
    expected = load_expected_returns(leaderboard_path)
    slippage = measure_realized_slippage(round_trips, paper_log_path)

    # Count open positions (entries with no matching exit).
    n_open = 0
    if journal is not None and not journal.empty:
        for sym, grp in journal[journal["action"] == "entry"].groupby("symbol") \
                if "action" in journal.columns else []:
            exits = journal[(journal["symbol"] == sym) &
                            (journal["action"].isin(["exit", "close"]))]
            if len(exits) == 0:
                n_open += 1

    # Per-slice aggregation.
    by_slice_rt: Dict[str, List[RoundTrip]] = {}
    for rt in round_trips:
        by_slice_rt.setdefault(rt.slice_combination, []).append(rt)

    slice_attrs: List[SliceAttribution] = []
    for sc, rts in by_slice_rt.items():
        n = len(rts)
        wins = sum(1 for r in rts if r.gross_pnl > 0)
        mean_ret = sum(r.gross_return for r in rts) / n if n else 0.0
        total_pnl = sum(r.gross_pnl for r in rts)
        exp = expected.get(sc)
        slip = slippage.get(sc)
        # Net of cost: subtract the realized slippage drag (if measured) from
        # the mean gross return. A round-trip pays slippage on entry+exit;
        # the measured entry slippage is a lower bound on the total.
        net = None
        if slip is not None:
            net = mean_ret - (slip * 2.0 / 10000.0)  # x2 for entry+exit legs
        slice_attrs.append(SliceAttribution(
            slice_combination=sc,
            symbol=rts[0].symbol,
            side=rts[0].side,
            n_round_trips=n,
            win_rate=wins / n if n else 0.0,
            mean_gross_return=mean_ret,
            total_gross_pnl=total_pnl,
            expected_return=exp,
            realized_slippage_bps=slip,
            net_of_cost_return=net,
            preliminary=n < MIN_ROUND_TRIPS_FOR_STATS,
        ))

    # Sort: most round-trips first, then highest total P&L.
    slice_attrs.sort(key=lambda a: (-a.n_round_trips, -a.total_gross_pnl))

    total_pnl = sum(rt.gross_pnl for rt in round_trips)

    notes: List[str] = []
    if not round_trips:
        notes.append(
            "No completed round-trips yet (entries without matching exits, "
            "or orders accepted but not filled). Realized P&L is zero. "
            "This report will populate as fills + exits accumulate."
        )
    prelim = [a for a in slice_attrs if a.preliminary]
    if prelim and round_trips:
        notes.append(
            f"{len(prelim)} slice(s) have fewer than "
            f"{MIN_ROUND_TRIPS_FOR_STATS} round-trips; their stats are "
            "preliminary and should not be interpreted as stable."
        )
    if not expected:
        notes.append(
            "No candidate_leaderboard.csv found; cannot compare realized to "
            "expected validation returns. Regenerate the leaderboard to enable "
            "expected-vs-realized comparison."
        )

    return {
        "summary": {
            "n_round_trips": len(round_trips),
            "n_open_positions": n_open,
            "total_realized_pnl": round(total_pnl, 4),
        },
        "by_slice": [a.to_dict() for a in slice_attrs],
        "round_trips": [rt.to_dict() for rt in round_trips],
        "realized_slippage": {k: round(v, 2) for k, v in slippage.items()},
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
    lines.append(f"  Open positions:        {s['n_open_positions']}")
    lines.append(f"  Total realized P&L:    ${s['total_realized_pnl']:.2f}")
    lines.append("")

    by_slice = report["by_slice"]
    if by_slice:
        lines.append("-" * 72)
        lines.append("PER-SLICE ATTRIBUTION")
        lines.append("-" * 72)
        hdr = (f"{'slice':52} {'n':>3} {'win%':>5} {'meanRet':>8} "
               f"{'totPnL':>9} {'expRet':>8} {'slipBp':>7}")
        lines.append(hdr)
        lines.append("-" * 72)
        for a in by_slice:
            sc = a["slice_combination"][:50]
            exp = f"{a['expected_return']*100:.2f}%" if a["expected_return"] is not None else "  n/a "
            slip = f"{a['realized_slippage_bps']:.1f}" if a["realized_slippage_bps"] is not None else "  n/a"
            flag = " *" if a["preliminary"] else ""
            lines.append(
                f"{sc:52} {a['n_round_trips']:>3} "
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
        lines.append("  Compare to cost_model.DEFAULT_SLIPPAGE_BPS = 3.0")
        lines.append("")

    if report["notes"]:
        lines.append("-" * 72)
        lines.append("NOTES")
        lines.append("-" * 72)
        for n in report["notes"]:
            lines.append(f"  - {n}")

    lines.append("=" * 72)
    return "\n".join(lines)
