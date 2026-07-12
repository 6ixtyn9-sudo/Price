"""Sync monitored_slices.csv from latest candidate registry. Idempotent.

Builds the monitored set FROM SCRATCH on every run using only the
candidate_registry.csv paper_proposal rows. This ensures stale/legacy
slices that predate sharded discovery do not silently survive.
"""
import sys
import pandas as pd
from pathlib import Path

REGISTRY = Path("localdata/research/merged/candidate_registry.csv")
MONITORED = Path("localdata/monitored_slices.csv")

if not REGISTRY.exists():
    print("sync_monitored: no candidate registry found; nothing to sync")
    sys.exit(0)

sys.path.insert(0, "scripts")
from research_lifecycle import apply_registry_to_monitored
import tempfile

reg = pd.read_csv(REGISTRY)
before = len(pd.read_csv(MONITORED)) if MONITORED.exists() else 0

# Start from an empty slate so only registry-qualified slices survive.
# apply_registry_to_monitored preserves rows absent from the registry
# unless they are decaying_suspended -- but legacy manual slices that
# predate sharded discovery are neither in the registry nor suspended,
# so they get a free pass. Starting from empty means every monitored
# slice must earn its place through the registry.
empty = Path(tempfile.mktemp(suffix=".csv"))
pd.DataFrame(columns=["symbol","timeframe","slice_combination","side","source_note"]).to_csv(empty, index=False)

result = apply_registry_to_monitored(reg, monitored_path=empty, promote_proposals=True)
after = len(result)
added = after - before
print(f"sync_monitored: {before} -> {after} ({added:+d})")
result.to_csv(MONITORED, index=False)
