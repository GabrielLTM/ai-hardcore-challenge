from datetime import datetime, timezone
from app.domain.agents import changed_fields, contains_secret, diff_versions, new_draft, plan_publication, slugify
from app.domain.graph import Node, check_graph

AT = datetime(2026, 11, 3, tzinfo=timezone.utc)
D = ('delegate_to_agent',)


def codes(nodes, root=None):
    return [i.code for i in check_graph(nodes, root)]


def test_valid_graph_has_no_issues():
    nodes = {'router': Node(('a', 'b'), D), 'a': Node(('b',), D), 'b': Node()}
    assert check_graph(nodes, root='router') == []


def test_self_reference_is_reported_once_and_not_as_cycle():
    assert codes({'a': Node(('a',), D)}) == ['self_reference']


def test_cycle_reports_the_path():
    issues = check_graph({'a': Node(('b',), D), 'b': Node(('c',), D), 'c': Node(('a',), D)})
    assert [(i.code, i.path) for i in issues] == [('cycle', ('a', 'b', 'c', 'a'))]


def test_unknown_and_disabled_destination_unknown_tool_missing_delegate_tool_and_root():
    nodes = {'a': Node(('ghost', 'off'), D + ('magic',)), 'off': Node(enabled=False), 'b': Node(('a',), ())}
    assert codes(nodes, root='router') == ['missing_root', 'unknown_tool', 'unknown_destination', 'disabled_destination', 'missing_delegate_tool']
    assert codes({'router': Node(enabled=False)}, root='router') == ['missing_root']


def test_draft_content_diff_is_readable_per_field():
    before = {**new_draft('x', {'name': 'X', 'description': '', 'kind': 'custom', 'model': 'm', 'tools': ['web_search'], 'delegates': [],
                                'limits': {}, 'enabled': True, 'instructions': 'linha 1\nlinha 2'}, 'a', AT)}
    after = {**before, 'tools': ['web_search', 'search_knowledge'], 'instructions': 'linha 1\nlinha 2 alterada', 'name': 'Y'}
    assert changed_fields(before, after) == ['name', 'tools', 'instructions']
    changes = {c['field']: c for c in diff_versions(before, after)}
    assert changes['tools']['added'] == ['search_knowledge'] and changes['tools']['removed'] == []
    assert changes['instructions']['lines'] == [{'op': 'equal', 'text': 'linha 1'}, {'op': 'remove', 'text': 'linha 2'},
                                                {'op': 'add', 'text': 'linha 2 alterada'}]
    assert (changes['name']['before'], changes['name']['after']) == ('X', 'Y')


def test_plan_publication_versions_prompt_only_when_instructions_change():
    draft = new_draft('x', {'name': 'X', 'description': '', 'kind': 'custom', 'model': 'm', 'tools': [], 'delegates': [],
                            'limits': {}, 'enabled': True, 'instructions': 'v1'}, 'ana', AT)
    first = plan_publication(draft, draft, actor='ana', at=AT)
    assert (first.version['version'], first.version['prompt_version'], first.prompt['version']) == (1, 1, 1)
    assert first.version['summary'] == 'Versão inicial'
    published = {k: v for k, v in first.version.items() if k not in ('agent_id', 'prompt_id')}
    draft = {**draft, 'published': published, 'description': 'nova'}
    second = plan_publication(draft, draft, actor='ana', at=AT)
    assert (second.version['version'], second.version['prompt_version'], second.prompt) == (2, 1, None)
    assert second.version['summary'] == 'Alterou: descrição'
    third = plan_publication({**draft, 'instructions': 'v2'}, {**draft, 'instructions': 'v2'}, actor='ana', at=AT)
    assert (third.version['prompt_version'], third.prompt['instructions']) == (2, 'v2')
    restored = plan_publication(draft, first.version, actor='ana', at=AT, restored_from=1, replace_draft=True)
    assert restored.version['restored_from'] == 1 and restored.version['summary'] == 'Restaurada a partir da versão 1'
    assert restored.draft_update['description'] == ''


def test_secrets_and_slug():
    assert contains_secret('use a chave sk-abcdefghijklmnopqrstuvwxyz123456 aqui')
    assert contains_secret('Authorization: Bearer abcdefghijklmnopqrstuvwxyz')
    assert not contains_secret('Responda sempre em português do Brasil.')
    assert slugify('Atendimento ao Cliente — Pix!') == 'atendimento-ao-cliente-pix'
    assert slugify('???') == 'agente'
