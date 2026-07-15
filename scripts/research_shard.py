"""Run one isolated research discovery/validation shard.

A shard owns one symbol batch and one timeframe. It writes only inside its
provided output directory and emits a manifest so the merge step can reject
missing/failed/partial work.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}(/[A-Z0-9][A-Z0-9.\-]{0,14})?$")


def validate_symbol(symbol: str) -> str:
    """Return a normalized symbol or raise on unsafe/non-market-shaped input.

    Research shard symbols come from tracked JSON and eventually land in a
    GitHub Actions matrix. Treat them as data, never shell syntax. The pattern
    accepts normal equities/ETFs (AAPL, BRK.B, BTC-USD) and crypto pairs
    (BTC/USD), while rejecting whitespace, quotes, `$()`, semicolons, path
    traversal punctuation, and other control-plane characters.
    """
    cleaned = str(symbol).strip().upper()
    if not cleaned or not SYMBOL_PATTERN.fullmatch(cleaned):
        raise ValueError(f"invalid research symbol: {symbol!r}")
    return cleaned


def split_symbols(symbols: list[str], batch_size: int = 20) -> list[list[str]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    cleaned = [validate_symbol(symbol) for symbol in symbols if str(symbol).strip()]
    return [cleaned[i:i + batch_size] for i in range(0, len(cleaned), batch_size)]


def _write_manifest(path: Path, payload: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n")


def run_shard(
    symbols: list[str],
    timeframe: str,
    shard_id: str,
    output_dir: Path,
    condition_symbols: tuple[str, ...] = ("USO", "TLT"),
    bin_mode: str = "rolling",
    profile: str | None = None,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    manifest = {
        "shard_id": shard_id,
        "symbols": symbols,
        "timeframe": timeframe,
        "bin_mode": bin_mode,
        "profile": profile or "default",
        "status": "running",
        "started_at_utc": started,
        "git_sha": os.environ.get("GITHUB_SHA", "local"),
    }
    _write_manifest(output_dir, manifest)

    try:
        import discover_slices
        import validate_slices

        discovered_path = output_dir / "discovered_slices.csv"
        validated_path = output_dir / "validated_slices.csv"
        leaderboard_path = output_dir / "candidate_leaderboard.csv"
        discover_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
        validate_slices.DISCOVERED_SLICES_PATH = str(discovered_path)
        validate_slices.VALIDATED_SLICES_PATH = str(validated_path)
        validate_slices.CANDIDATE_LEADERBOARD_PATH = str(leaderboard_path)

        discover_slices.run_discovery(
            target_symbols=symbols,
            timeframe=timeframe,
            min_samples=15,
            append=False,
            cond_symbols=list(condition_symbols),
            bin_mode=bin_mode,
            profile=profile,
        )
        if discovered_path.exists() and not pd.read_csv(discovered_path).empty:
            validate_slices.run_candidate_leaderboard(
                slices_path=str(discovered_path),
                output_path=str(leaderboard_path),
                bin_mode=bin_mode,
            )
        else:
            pd.DataFrame().to_csv(discovered_path, index=False)
            pd.DataFrame().to_csv(validated_path, index=False)
            pd.DataFrame().to_csv(leaderboard_path, index=False)

        def _row_count(path: Path) -> int:
            if not path.exists():
                return 0
            try:
                return len(pd.read_csv(path))
            except pd.errors.EmptyDataError:
                return 0

        manifest.update({
            "status": "success",
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "discovered_rows": _row_count(discovered_path),
            "leaderboard_rows": _row_count(leaderboard_path),
        })
    except Exception as exc:  # noqa: BLE001 - manifest records shard failure
        manifest.update({
            "status": "failed",
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": repr(exc),
        })
        _write_manifest(output_dir, manifest)
        raise

    _write_manifest(output_dir, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one isolated research discovery shard.")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--symbols-json", default=None,
                        help="JSON array of symbols. Preferred for CI so symbols are data, not shell words.")
    parser.add_argument("--timeframe", choices=["1d", "1h", "15m"], required=True)
    parser.add_argument("--shard-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--condition-on", nargs="+", default=["USO", "TLT"])
    parser.add_argument("--bin-mode", choices=["rolling", "insample"], default="rolling")
    parser.add_argument("--profile", choices=["default", "crypto", "futures"], default="default")
    args = parser.parse_args()
    if args.symbols_json:
        try:
            parsed_symbols = json.loads(args.symbols_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--symbols-json is not valid JSON: {exc}") from exc
        if not isinstance(parsed_symbols, list):
            raise SystemExit("--symbols-json must be a JSON array")
        symbols = [validate_symbol(symbol) for symbol in parsed_symbols]
    elif args.symbols:
        symbols = [validate_symbol(symbol) for symbol in args.symbols]
    else:
        raise SystemExit("one of --symbols or --symbols-json is required")

    run_shard(
        symbols=symbols,
        timeframe=args.timeframe,
        shard_id=args.shard_id,
        output_dir=args.output_dir,
        condition_symbols=tuple(args.condition_on),
        bin_mode=args.bin_mode,
        profile=args.profile,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
