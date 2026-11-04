import ast
from pathlib import Path


def test_domain_does_not_import_framework_or_infrastructure():
    forbidden = {'fastapi', 'pymongo', 'openai', 'app.adapters', 'app.bootstrap'}
    domain = Path(__file__).parents[1] / 'src' / 'app' / 'domain'
    for path in domain.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
            for module in modules:
                assert not any(module == f or module.startswith(f + '.') for f in forbidden), (path, module)
