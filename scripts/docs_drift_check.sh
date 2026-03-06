#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

files=("AGENTS.md" "CLAUDE.md" "GEMINI.md")

for f in "${files[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "docs-drift: missing required file: $f" >&2
    exit 1
  fi
done

required_docs=(
  "docs/architecture.md"
  "docs/security_model.md"
  "docs/integration_identity.md"
  "docs/operations_runbook.md"
)

for f in "${files[@]}"; do
  for d in "${required_docs[@]}"; do
    if ! grep -Fq "$d" "$f"; then
      echo "docs-drift: $f missing reference to $d" >&2
      exit 1
    fi
  done
done

if ! grep -Eqi "embedded.*self-hosted|self-hosted.*embedded" AGENTS.md CLAUDE.md GEMINI.md; then
  echo "docs-drift: expected embedded/self-hosted positioning in agent instruction docs" >&2
  exit 1
fi

if grep -Eq "takes an AsyncSession|All DB operations use AsyncSession|SQLAlchemy 2.0\+ \(Async\)$" CLAUDE.md GEMINI.md; then
  echo "docs-drift: async-only language detected in CLAUDE.md or GEMINI.md" >&2
  exit 1
fi

echo "docs-drift: OK"
