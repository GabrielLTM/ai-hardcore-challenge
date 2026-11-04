from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    workspace_id: str
    role: str
    is_demo: bool = False
