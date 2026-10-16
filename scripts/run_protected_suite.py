"""Run the Python test suite with a hard guard around production port 8765."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUARD_DIR = ROOT / "scripts" / "production_guard"
sys.path.insert(0, str(GUARD_DIR))
import sitecustomize  # noqa: E402,F401  (install the audit hook before discovery)

os.environ["PYTHONPATH"] = str(GUARD_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
sys.path.insert(0, str(ROOT))

suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
