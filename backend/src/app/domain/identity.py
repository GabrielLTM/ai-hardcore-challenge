from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    workspace_id: str
    role: str
    is_demo: bool = False


@dataclass(frozen=True, slots=True)
class Operator:
    """A human attendant of the handoff area; acts only on tickets of `workspace_id`."""
    id: str
    name: str
    workspace_id: str
