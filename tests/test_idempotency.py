from __future__ import annotations

from uuid import uuid4

from agent_control_plane import proposal_command_id
from agent_control_plane.types.enums import ActionName


def test_proposal_command_id_is_stable() -> None:
    session_id = uuid4()
    first = proposal_command_id(
        session_id=session_id,
        resource_id="res-1",
        resource_type="task",
        decision=ActionName.STATUS,
    )
    second = proposal_command_id(
        session_id=session_id,
        resource_id="res-1",
        resource_type="task",
        decision=ActionName.STATUS,
    )
    assert first == second


def test_proposal_command_id_changes_with_inputs() -> None:
    session_id = uuid4()
    base = proposal_command_id(
        session_id=session_id,
        resource_id="res-1",
        resource_type="task",
        decision=ActionName.STATUS,
    )
    changed = proposal_command_id(
        session_id=session_id,
        resource_id="res-2",
        resource_type="task",
        decision=ActionName.STATUS,
    )
    assert base != changed
