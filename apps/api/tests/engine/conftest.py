"""Decision Engine test configuration: makes ``sufficia`` and ``engine_builders`` importable
regardless of pytest's import mode or whether the package is installed."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
API_ROOT = HERE.parents[1]
for p in (str(API_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
