"""Conftest for orchestration-kit tests — ensures tools/ is on sys.path."""

import sys
from pathlib import Path

# Ensure tools/ is on the path so test modules can `from cloud.batch import ...`
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
