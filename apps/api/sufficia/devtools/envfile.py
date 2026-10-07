"""Environment for the command-line tools: the process environment over ``apps/api/.env``.

The API reads ``.env`` through pydantic-settings; the deploy and preflight scripts use the same file so
a key written once in ``.env`` works everywhere (PowerShell users rarely export every variable).
Values already set in the process environment always win.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; ``#`` comments (full-line, or after whitespace in unquoted values); quotes stripped."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ('"', "'") and value.count(value[0]) >= 2:
            value = value[1 : value.index(value[0], 1)]
        else:
            for marker in (" #", "\t#"):
                if marker in value:
                    value = value.split(marker, 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def tool_env(api_root: Path, environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    merged = parse_env_file(api_root / ".env")
    merged.update({k: v for k, v in (os.environ if environ is None else environ).items() if v != ""})
    return merged
