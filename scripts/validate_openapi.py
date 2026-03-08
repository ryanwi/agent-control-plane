"""Validate versioned OpenAPI contracts."""

from __future__ import annotations

from pathlib import Path

import yaml
from openapi_spec_validator import validate_spec


def _validate(path: Path) -> None:
    with path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    validate_spec(doc)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    specs = sorted((root / "docs" / "openapi").glob("*.yml"))
    if not specs:
        raise SystemExit("No OpenAPI specs found in docs/openapi")
    for spec in specs:
        _validate(spec)
        print(f"openapi-check: OK {spec.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
