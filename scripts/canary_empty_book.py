#!/usr/bin/env python3
"""
Cross-lane empty-book canary.

FAILS the job if ALL monitored books are empty (probable gate regression).
WARNS if a book that was previously non-empty is now empty.
Logs expected zeros (futures) vs unexpected zeros (equities, crypto).

Usage:
    python scripts/canary_empty_book.py
    python scripts/canary_empty_book.py --history-path localdata/book_history.json

Returns exit code 1 on CRITICAL (all books empty), 0 otherwise.
Warnings are emitted via ::warning:: GitHub Actions annotations.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# ── Config ──────────────────────────────────────────────────────────────
# Asset classes where 0 candidates is EXPECTED (gate is honest, no edges exist)
EXPECTED_EMPTY = {"futures"}

# Asset classes where 0 candidates means something broke
UNEXPECTED_EMPTY = {"equities", "crypto"}

# Maximum consecutive runs a book can be empty before we stop warning
# (prevents noise when futures stays empty for weeks — that's normal)
MAX_CONSECUTIVE_WARN = 5

# ── Book paths ──────────────────────────────────────────────────────────
BOOKS = {
    "equities": ROOT / "localdata" / "monitored_slices.csv",
    "crypto": ROOT / "localdata" / "monitored_slices_crypto.csv",
    "futures": ROOT / "localdata" / "monitored_slices_futures.csv",
}

HISTORY_PATH = ROOT / "localdata" / "book_history.json"


def _count_rows(path: Path) -> int:
    """Count data rows in a CSV (excludes header)."""
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path)
        return len(df)
    except Exception:
        return 0


def _load_history() -> dict:
    if not HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(HISTORY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_history(state: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(state, indent=2))


def _github_annotation(level: str, message: str) -> str:
    """Format a GitHub Actions annotation."""
    return f"::{level}::{message}"


def main() -> int:
    books = {}
    for name, path in BOOKS.items():
        books[name] = _count_rows(path)

    history = _load_history()
    now = datetime.now(timezone.utc).isoformat()

    # ── Update history tracking ──
    for name in BOOKS:
        if name not in history:
            history[name] = {
                "consecutive_empty": 0,
                "last_non_empty_count": None,
                "last_non_empty_at": None,
                "last_check_at": None,
            }

    # ── Check each book ──
    all_empty = True
    has_critical = False
    warnings = []

    for name, count in books.items():
        entry = history[name]

        if count == 0:
            entry["consecutive_empty"] = entry.get("consecutive_empty", 0) + 1
            if name in UNEXPECTED_EMPTY:
                all_empty = all_empty and True  # stays true only if all are empty
                warnings.append(
                    f"[{name.upper()}] Book is EMPTY ({entry['consecutive_empty']} consecutive runs). "
                    f"This is UNEXPECTED — gate may have regressed."
                )
                has_critical = True
            elif name in EXPECTED_EMPTY:
                if entry["consecutive_empty"] <= MAX_CONSECUTIVE_WARN:
                    print(
                        _github_annotation(
                            "notice",
                            f"[{name.upper()}] Book is empty — expected. "
                            f"Gate produced zero tradeable candidates (run {entry['consecutive_empty']}).",
                        )
                    )
                # Don't count expected-empty classes toward the "all empty" check
                all_empty = all_empty and (name in EXPECTED_EMPTY)
        else:
            # Book has candidates
            all_empty = False
            if entry["consecutive_empty"] > 0:
                print(
                    _github_annotation(
                        "warning",
                        f"[{name.upper()}] Book recovered: {count} slices after "
                        f"{entry['consecutive_empty']} consecutive empty runs.",
                    )
                )
            entry["consecutive_empty"] = 0
            entry["last_non_empty_count"] = count
            entry["last_non_empty_at"] = now

        entry["last_check_at"] = now

    _save_history(history)

    # ── Summary ──
    print("=== BOOK HEALTH ===")
    for name, count in books.items():
        expected = name in EXPECTED_EMPTY
        status = "EMPTY (expected)" if count == 0 and expected else \
                 "EMPTY ⚠️" if count == 0 else \
                 f"{count} slices ✓"
        print(f"  {name:12s} {status}")

    # ── CRITICAL: all monitored books are empty ──
    if all_empty and has_critical:
        print()
        print(_github_annotation(
            "error",
            "CRITICAL: All monitored books are empty (equities=0, crypto=0). "
            "This likely indicates a gate regression. Check _tradeable_candidate() "
            "in research_lifecycle.py and recent leaderboard changes.",
        ))
        print("::error::CANARY_FAILED: all books empty")
        return 1

    # ── WARN: unexpected empty ──
    for w in warnings:
        print(_github_annotation("warning", w))

    # ── Weekly summary if futures is healthy ──
    if books["futures"] == 0 and history.get("futures", {}).get("consecutive_empty", 0) > MAX_CONSECUTIVE_WARN:
        # Futures has been empty for a while — suppress, it's normal
        pass

    print("\n✅ Canary: no critical failures detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
