# Compatibility Policy (Pre-1.0)

This project is currently pre-1.0 and iterating quickly.

## Versioning posture

- Minor releases may include breaking API changes.
- Patch releases should remain behaviorally compatible unless a critical fix requires otherwise.

## Required release hygiene

- **No silent breaking changes.**
- Any breaking change must be called out in `CHANGELOG.md` with clear migration notes.
- If public exports or integration contracts change, update both:
  - `README.md`
  - `docs/architecture.md`

## Experimental namespaces

Contracts under `agent_control_plane.experimental.*` are intentionally non-stable and may change rapidly between minor releases.
