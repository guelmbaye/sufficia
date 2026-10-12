"""Exports the canonical JSON Schemas of the authority domain and the OpenAPI document to packages/schemas.

    python scripts/export_schemas.py            # write
    python scripts/export_schemas.py --check    # exit 1 if the committed files are stale (CI gate)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from sufficia.config import Settings  # noqa: E402
from sufficia.domain import models  # noqa: E402

MODELS = {
    "intent": models.Intent,
    "evidence-contract": models.EvidenceContract,
    "evidence-item": models.EvidenceItem,
    "evidence-set": models.EvidenceSet,
    "decision": models.Decision,
    "canonical-action": models.CanonicalAction,
    "authorization": models.Authorization,
    "execution": models.Execution,
    "evidence-receipt": models.EvidenceReceipt,
}


# pydantic < 2.14 adds this regex to every decimal serialized as a string; 2.14+ does not. Dropping it keeps the
# committed schemas identical whichever pydantic version generates them (the CI gate compares them byte for byte).
PYDANTIC_DECIMAL_PATTERN = r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"


def _normalize(node):
    if isinstance(node, dict):
        return {k: _normalize(v) for k, v in node.items() if not (k == "pattern" and v == PYDANTIC_DECIMAL_PATTERN)}
    if isinstance(node, list):
        return [_normalize(v) for v in node]
    return node


def render() -> dict[Path, str]:
    out: dict[Path, str] = {}
    base = ROOT / "packages" / "schemas"
    for name, model in MODELS.items():
        schema = _normalize(model.model_json_schema(mode="serialization"))
        schema["$id"] = f"https://sufficia.dev/schemas/{name}.v1.json"
        out[base / "json" / f"{name}.v1.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    from sufficia.api.app import create_app

    app = create_app(Settings(database_url="sqlite://", chain_mode="DISABLED", _env_file=None, log_level="WARNING"))
    out[base / "openapi.json"] = json.dumps(_normalize(app.openapi()), indent=2, sort_keys=True) + "\n"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    files = render()
    stale = [p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if args.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if stale else 0
    for p, text in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(files)} files ({len(stale)} changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
