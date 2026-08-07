"""Lane confinement for broker submissions (adversarial hardening, 2026-08-07).

Why this module exists — evidence from the live ops ledgers:

- The futures-dedicated workflow submitted 6 real equity ETF orders
  (SPY 1h, XLF 1d, ~$975 notional each, broker status "accepted") between
  2026-07-27 and 2026-08-05 from stale cache-resurrected research
  candidates, journaled only to ``trade_journal_futures.csv`` — a ledger
  the equity attribution report never reads. On 2026-08-05 its retries
  were rejected for duplicate ``client_order_id``.
- The crypto-dedicated workflow submitted 72 crypto SHORT orders on
  2026-07-26/27 that the spot broker all rejected ("insufficient balance
  for DOT/LTC" — spot crypto cannot be shorted), and equity symbols
  (KLAC, ASML, AEP, ...) accumulated in its journal and stop-state files
  via shared-account stop reconciliation.

Lane separation used to be a naming convention on ops files. This module
makes it a hard rule at the single broker choke point (``submit_entry``):
a lane may only ever submit its own asset shapes, and the futures lane
submits nothing at all (it has no execution path anywhere — research-only
by design until a futures-capable broker exists).

Rules (fail-closed — an unrecognized lane may submit NOTHING):

- equity lane  -> plain symbols only ("XLF", "SPY"); anything containing
  "/" (crypto pairs, "FUT/*" futures) is refused.
- crypto lane  -> Alpaca USD spot pairs only (suffix "/USD").
- futures lane -> everything refused; there is no futures execution path.
"""

from typing import Optional

LANE_ALIASES = {
    "eq": "equity",
    "equity": "equity",
    "equities": "equity",
    "crypto": "crypto",
    "fut": "futures",
    "futs": "futures",
    "futures": "futures",
}

# Alpaca paper spot crypto uses BASE/USD pair notation. Kept deliberately
# narrow: extending execution to other quote currencies is a venue
# decision, not a string edit away from here.
CRYPTO_SYMBOL_SUFFIXES = ("/USD",)


def normalize_lane(lane: Optional[str]) -> str:
    """Map the various lane spellings (path-derived or CLI) onto the
    canonical lane names: equity / crypto / futures. Unknown stays as-is
    (lower-cased) so callers can fail closed on it."""
    key = str(lane or "").strip().lower()
    return LANE_ALIASES.get(key, key)


def lane_submission_block_reason(lane: Optional[str], symbol: str) -> Optional[str]:
    """Return None when the lane may submit this symbol, else a human-
    readable reason the submission was refused. Fail-closed on unknown
    lanes: a lane the system doesn't recognize gets no broker access."""
    sym = str(symbol or "").strip().upper()
    canon = normalize_lane(lane)

    if canon == "equity":
        if "/" in sym:
            return (
                f"lane_guard: equity lane may not submit non-equity symbol {sym!r} "
                "(pair/futures notation belongs to the crypto/futures substrates)"
            )
        return None

    if canon == "crypto":
        if sym.endswith(CRYPTO_SYMBOL_SUFFIXES):
            return None
        return (
            f"lane_guard: crypto lane may only submit Alpaca USD spot pairs "
            f"(suffix {CRYPTO_SYMBOL_SUFFIXES}); {sym!r} refused"
        )

    if canon == "futures":
        return (
            f"lane_guard: futures lane submits nothing — no broker execution path "
            f"exists for futures (research-only substrate); {sym!r} refused"
        )

    return (
        f"lane_guard: unrecognized lane {str(lane)!r}; failing closed, "
        f"{sym!r} refused"
    )
