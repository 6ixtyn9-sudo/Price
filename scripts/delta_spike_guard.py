import subprocess
import sys
from pathlib import Path

GUARDED = [
    "localdata/live_forward_returns.csv",
    "localdata/trade_journal.csv",
    "localdata/paper_trade_log.csv",
    "localdata/candidate_leaderboard.csv",
    "localdata/monitored_slices.csv",
]
SPIKE_FACTOR = 10.0
SPIKE_MIN_DELTA = 50


def _row_count(path):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            n = sum(1 for _ in f) - 1
        return max(n, 0)
    except OSError as e:
        print(f"GUARD: cannot read {path}: {e}")
        return -1


def _committed_row_count(rel):
    res = subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        return -1
    n = res.stdout.count("\n") - 1
    return max(n, 0)


def main():
    fail = 0
    for rel in GUARDED:
        p = Path(rel)
        if not p.exists():
            continue
        new_count = _row_count(p)
        if new_count < 0:
            continue
        old_count = _committed_row_count(rel)
        if old_count <= 0:
            continue
        delta = new_count - old_count
        if delta <= 0:
            continue
        factor = new_count / old_count
        if factor >= SPIKE_FACTOR and delta >= SPIKE_MIN_DELTA:
            print(
                f"GUARD: row-count spike on {rel}: "
                f"{old_count} -> {new_count} (x{factor:.1f}, +{delta}). "
                f"Refusing auto-commit. Inspect manually."
            )
            fail += 1
    if fail:
        print(f"GUARD: {fail} spike(s) detected. Auto-commit blocked.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
