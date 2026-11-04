"""Provider-independent domain primitives."""
from dataclasses import dataclass, field
from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class AgentKind(StrEnum):
    ROUTER = 'router'
    KNOWLEDGE = 'knowledge'
    SUPPORT = 'support'
    CUSTOM = 'custom'


@dataclass(frozen=True, slots=True)
class Agent:
    id: str
    name: str
    description: str
    instructions: str
    kind: AgentKind
    model: str
    tools: tuple[str, ...] = ()
    delegates: tuple[str, ...] = ()
    version: int = 1
    limits: Mapping[str, int] = field(default_factory=dict)  # optional per-agent caps: max_output_tokens, max_tool_calls


@dataclass(frozen=True, slots=True)
class ModelEvent:
    type: str
    text: str = ''
    data: dict[str, Any] = field(default_factory=dict)
