import sys
import types
from pathlib import Path

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "scripts", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import research_futures


def test_futures_defaults_are_research_only():
    assert research_futures.DEFAULT_BIN_MODE == "rolling"
    assert research_futures.DEFAULT_TIMEFRAMES == ("1d",)
    assert research_futures.DEFAULT_OUTPUT_DIR.as_posix().endswith("localdata/research/futures")
