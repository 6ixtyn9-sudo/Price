#!/usr/bin/env python3
"""Research staleness gate for the lane book-sync scripts (2026-08-07).

Root cause this kills: the crypto/futures live-capture workflows restore
``localdata/research/<lane>/`` from an operational-state cache that
outlives the research itself. Between 2026-07-27 and 2026-08-05 the
futures lane traded ghost candidates (SPY/XLF entries) that the current
research gate would never emit — the stale cache kept resurrecting them.

Rule: a lane book may only be (re)built from research whose summary is
stamped within ``max_age_hours`` of now. Stale, missing, or unreadable
stamps fail CLOSED: no book is built from that research. The sync scripts
turn that refusal into an empty (header-only) book so a ghost book cannot
persist quietly — an empty book is loud (heartbeat shows book_size=0).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SUMMARY_GLOB = "*_research_summary.json"
DEFAULT_MAX_AGE_HOURS = 72.0


def _summary_candidates_near(candidates_path: Path) -> list[Path]:
    """Summary JSONs that stamp the research which produced
    ``candidates_path``: same directory first, then the parent (the
    ``<lane>/1d/`` timeframe subdirectory layout keeps the lane summary
    one level up)."""
    base = Path(candidates_path).parent
    out: list[Path] = []
    for directory in (base, base.parent):
        if directory.is_dir():
            out.extend(sorted(directory.glob(SUMMARY_GLOB)))
    return out


def _parse_stamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def research_summary_age_hours(summary_path: Path, now: Optional[datetime] = None) -> Optional[float]:
    """Age in hours of the summary's ``generated_at_utc`` stamp, or None
    when the stamp is missing/unreadable/unparseable."""
    now = now or datetime.now(timezone.utc)
    try:
        payload = json.loads(Path(summary_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    stamp = _parse_stamp(payload.get("generated_at_utc")) if isinstance(payload, dict) else None
    if stamp is None:
        return None
    return (now - stamp).total_seconds() / 3600.0


def research_stale_reason(
    candidates_path: Path,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """None when the research behind ``candidates_path`` is fresh enough
    to build a live book from; otherwise the reason it was refused."""
    now = now or datetime.now(timezone.utc)
    summaries = _summary_candidates_near(candidates_path)
    if not summaries:
        return (
            f"no {SUMMARY_GLOB} found near {candidates_path} or its parent; "
            "research provenance unknown — failing closed"
        )
    report = []
    for summary in summaries:
        age = research_summary_age_hours(summary, now=now)
        if age is None:
            report.append(f"{summary.name}: stamp missing/unparseable")
            continue
        if age <= max_age_hours:
            return None
        report.append(f"{summary.name}: {age:.0f}h old")
    return (
        f"research stale beyond {max_age_hours:g}h ({'; '.join(report)}) — "
        "refusing to build a live book from expired research"
    )
