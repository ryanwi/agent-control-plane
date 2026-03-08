from __future__ import annotations

from importlib.metadata import version

import agent_control_plane as acp


def test_runtime_version_helpers_match_package_metadata() -> None:
    expected = version("agent-control-plane")
    assert acp.__version__ == expected
    assert acp.get_version() == expected
