import pytest
from app.domain.graph import GraphError, validate_graph


def test_graph_accepts_dag_and_rejects_cycles_and_unknown():
    validate_graph({'router': ['support', 'knowledge'], 'support': ['knowledge'], 'knowledge': []})
    with pytest.raises(GraphError, match='Ciclo'):
        validate_graph({'a': ['b'], 'b': ['a']})
    with pytest.raises(GraphError, match='inexistente'):
        validate_graph({'a': ['missing']})
