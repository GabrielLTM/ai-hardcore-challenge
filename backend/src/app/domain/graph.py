"""Validation of delegation relationships: pure rules over a candidate graph."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

DELEGATE_TOOL = 'delegate_to_agent'
KNOWN_TOOLS = frozenset({DELEGATE_TOOL, 'search_knowledge', 'web_search', 'get_receivables', 'get_terminal_status',
                         'request_human_handoff'})
ROOT_AGENT = 'router'


@dataclass(frozen=True, slots=True)
class Node:
    delegates: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class GraphIssue:
    code: str
    message: str
    agent_id: str | None = None
    path: tuple[str, ...] = ()


class GraphError(ValueError):
    def __init__(self, issues: Sequence[GraphIssue]):
        super().__init__('; '.join(i.message for i in issues))
        self.issues = list(issues)


def check_graph(nodes: Mapping[str, Node], root: str | None = None) -> list[GraphIssue]:
    """Return every problem found; empty means the graph can be published."""
    issues: list[GraphIssue] = []
    if root is not None and (root not in nodes or not nodes[root].enabled):
        issues.append(GraphIssue('missing_root', f"O agente raiz '{root}' precisa existir e estar ativo.", root))
    edges: dict[str, list[str]] = {}
    for source, node in nodes.items():
        for tool in node.tools:
            if tool not in KNOWN_TOOLS:
                issues.append(GraphIssue('unknown_tool', f"Ferramenta desconhecida em '{source}': {tool}.", source))
        if node.delegates and DELEGATE_TOOL not in node.tools:
            issues.append(GraphIssue('missing_delegate_tool', f"'{source}' tem destinos, mas não usa a ferramenta {DELEGATE_TOOL}.", source))
        edges[source] = []
        for target in dict.fromkeys(node.delegates):
            if target == source:
                issues.append(GraphIssue('self_reference', f"'{source}' não pode delegar para si mesmo.", source, (source, source)))
            elif target not in nodes:
                issues.append(GraphIssue('unknown_destination', f"Destino inexistente ou não publicado: '{target}' (origem '{source}').", source, (source, target)))
            elif not nodes[target].enabled:
                issues.append(GraphIssue('disabled_destination', f"Destino desativado: '{target}' (origem '{source}').", source, (source, target)))
            else:
                edges[source].append(target)
    return issues + _cycles(edges)


def _cycles(edges: Mapping[str, Sequence[str]]) -> list[GraphIssue]:
    state: dict[str, int] = {}  # 1 = on the current path, 2 = fully explored
    path: list[str] = []
    found: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        state[node] = 1
        path.append(node)
        for target in edges[node]:
            if state.get(target) == 1:
                found.append((*path[path.index(target):], target))
            elif target not in state:
                visit(target)
        path.pop()
        state[node] = 2

    for node in edges:
        if node not in state:
            visit(node)
    return [GraphIssue('cycle', 'Ciclo: ' + ' → '.join(cycle), cycle[0], cycle) for cycle in found]


def validate_graph(graph: Mapping[str, Sequence[str]]) -> None:
    """Edge-only shorthand: raises GraphError listing every problem."""
    issues = check_graph({k: Node(tuple(v), (DELEGATE_TOOL,) if v else ()) for k, v in graph.items()})
    if issues:
        raise GraphError(issues)
