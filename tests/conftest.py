"""Ensure `src/` and `scripts/` are importable under bare `pytest` invocations."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for rel in ("src", "scripts"):
    target = str(ROOT / rel)
    if target not in sys.path:
        sys.path.insert(0, target)
