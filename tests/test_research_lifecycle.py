"""Tests for research lifecycle scripts."""
import json
import sys
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from research_lifecycle import _valid_regimes_json

def test_valid_regimes_json():
    row_none = {}
    assert _valid_regimes_json(row_none) == "[]"
    
    row_bull = {"valid_in_bull": "True"}
    assert _valid_regimes_json(row_bull) == '["bull"]'
    
    row_all = {"valid_in_bull": 1, "valid_in_neutral": "yes", "valid_in_bear": True}
    parsed = json.loads(_valid_regimes_json(row_all))
    assert set(parsed) == {"bull", "neutral", "bear"}
