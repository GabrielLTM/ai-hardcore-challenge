"""Agent administration against a real (local) Mongo, in a throwaway database."""
import asyncio
import os
from datetime import date, timedelta
from uuid import uuid4
import pytest
from pymongo import AsyncMongoClient
from app.adapters.outbound.mongo.agents import MongoAgentRepository, load_published_snapshot
from app.adapters.outbound.mongo.seed import seed_demo
from app.application.use_cases.agents import AgentService, node
from app.bootstrap.settings import Settings
from app.domain.agents import AgentError
from app.domain.graph import check_graph
from app.domain.identity import Identity

URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/?directConnection=true')
SETTINGS = Settings(app_profile='local-demo', openai_model='gpt-test', demo_workspace_id='demo', demo_user_id='cliente1988')
WHO = Identity('admin1', 'demo', 'admin', is_demo=True)


@pytest.fixture
async def db():
    client = AsyncMongoClient(URI, serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command('ping')
    except Exception:
        await client.close()
        pytest.skip('Mongo local indisponível')
    database = client[f'test_agents_{uuid4().hex[:10]}']
    yield database
    await client.drop_database(database.name)
    await client.close()


@pytest.fixture
async def svc(db):
    await seed_demo(db, SETTINGS)
    return AgentService(MongoAgentRepository(db), 'gpt-test')


async def new_agent(svc, name='Agente', **fields):
    created = await svc.create(WHO, {'name': name, 'instructions': 'Responda em português.', 'tools': ['delegate_to_agent'], **fields})
    await svc.publish(WHO, created['id'], if_match=None, summary=None)
    return await svc.get(WHO, created['id'])


async def test_seed_publishes_initial_graph_with_pinned_versions(db):
    await seed_demo(db, SETTINGS)
    graph_version, agents = await load_published_snapshot(db, 'demo')
    assert graph_version == '1'
    assert {a.id: (a.name, a.version, a.model) for a in agents.values()} == {
        'router': ('Roteador', 1, 'gpt-test'), 'support': ('Atendimento ao Cliente', 1, 'gpt-test'),
        'knowledge': ('Especialista Getnet', 1, 'gpt-test'), 'escalation': ('Atendimento Humano', 1, 'gpt-test')}
    assert agents['router'].delegates == ('support', 'knowledge', 'escalation') and agents['support'].delegates == ('knowledge', 'escalation')
    assert agents['knowledge'].delegates == () and agents['knowledge'].tools == ('search_knowledge', 'web_search')
    assert 'delegate_to_agent' in agents['support'].tools and agents['router'].kind.value == 'router'
    assert '[n]' in agents['knowledge'].instructions and 'Pix' in agents['knowledge'].instructions
    assert await db.prompt_versions.count_documents({}) == 4 and await db.audit_events.count_documents({'actor': 'system'}) == 4


async def test_seed_support_data_is_demo_and_relative_to_today(db):
    await seed_demo(db, SETTINGS)
    terminal = await db.terminals.find_one({'user_id': 'cliente1988'})
    assert (terminal['error'], terminal['carrier'], terminal['serial_suffix'], terminal['is_demo']) == ('Sem sinal da rede móvel', 'Algar', '4821', True)
    sale = await db.receivables.find_one({'user_id': 'cliente1988'})
    assert (sale['amount'], sale['payment_type'], sale['is_demo']) == ('3482.90', 'credito_a_vista', True)
    assert date.fromisoformat(sale['due_date']) - date.fromisoformat(sale['sale_date']) == timedelta(days=30)


async def test_seed_is_idempotent_and_keeps_admin_edits(db):
    await seed_demo(db, SETTINGS)
    service = AgentService(MongoAgentRepository(db), 'gpt-test')
    router = await service.get(WHO, 'router')
    await service.update(WHO, 'router', router['revision'], {'name': 'Roteador editado'})
    await service.publish(WHO, 'router', if_match=None, summary=None)
    before = {c: await db[c].count_documents({}) for c in ('agents', 'agent_versions', 'prompt_versions', 'graph_versions', 'terminals', 'receivables')}
    await seed_demo(db, SETTINGS)
    assert {c: await db[c].count_documents({}) for c in before} == before
    assert (await service.get(WHO, 'router'))['name'] == 'Roteador editado'
    assert (await load_published_snapshot(db, 'demo'))[0] == '2'


async def test_seed_upgrades_legacy_documents_without_revision(db):
    await db.agents.insert_one({'id': 'router', 'workspace_id': 'demo', 'name': 'Orquestrador', 'status': 'published', 'version': 1})
    await seed_demo(db, SETTINGS)
    assert (await db.agents.find_one({'id': 'router'}))['name'] == 'Roteador'
    assert await db.agents.count_documents({}) == 4 and len((await load_published_snapshot(db, 'demo'))[1]) == 4


async def test_draft_edits_do_not_touch_the_published_configuration(svc, db):
    support = await svc.get(WHO, 'support')
    saved = await svc.update(WHO, 'support', support['revision'], {'instructions': 'Novas instruções.'})
    assert saved['revision'] == 2 and saved['has_unpublished_changes'] and saved['published']['instructions'] != 'Novas instruções.'
    assert (await load_published_snapshot(db, 'demo'))[1]['support'].instructions != 'Novas instruções.'
    assert (await svc.update(WHO, 'support', 2, {'instructions': 'Novas instruções.'}))['revision'] == 2  # no-op keeps the revision


async def test_stale_revision_is_rejected_with_the_current_one(svc):
    support = await svc.get(WHO, 'support')
    await svc.update(WHO, 'support', support['revision'], {'description': 'primeira sessão'})
    with pytest.raises(AgentError) as exc:
        await svc.update(WHO, 'support', support['revision'], {'description': 'segunda sessão'})
    assert exc.value.code == 'revision_conflict' and exc.value.details == {'current_revision': 2}
    assert (await svc.get(WHO, 'support'))['description'] == 'primeira sessão'


async def test_secrets_are_not_stored(svc):
    with pytest.raises(AgentError) as exc:
        await svc.create(WHO, {'name': 'X', 'instructions': 'use sk-abcdefghijklmnopqrstuvwxyz123456'})
    assert exc.value.code == 'secret_detected'


async def test_publish_creates_versions_and_a_new_graph_snapshot_without_rewriting_old_ones(svc, db):
    old_support = await db.agent_versions.find_one({'agent_id': 'support', 'version': 1})
    support = await svc.get(WHO, 'support')
    await svc.update(WHO, 'support', support['revision'], {'instructions': 'Instruções novas.', 'tools': ['get_receivables', 'delegate_to_agent']})
    done = await svc.publish(WHO, 'support', if_match=2, summary=None)
    assert (done['version'], done['prompt_version'], done['graph_version']) == (2, 2, '2')
    assert done['agent']['status'] == 'published' and not done['agent']['has_unpublished_changes']
    assert await db.agent_versions.find_one({'agent_id': 'support', 'version': 1}) == old_support  # immutable
    assert (await db.graph_versions.find_one({'version': 1}))['nodes'] == {'router': 1, 'support': 1, 'knowledge': 1, 'escalation': 1}
    assert (await db.graph_versions.find_one({'version': 2}))['nodes'] == {'router': 1, 'support': 2, 'knowledge': 1, 'escalation': 1}
    graph_version, agents = await load_published_snapshot(db, 'demo')
    assert graph_version == '2' and agents['support'].version == 2 and agents['router'].version == 1
    history = await svc.versions(WHO, 'support', limit=10, cursor=None)
    assert [(v['version'], v['is_current'], v['created_by']) for v in history['items']] == [(2, True, 'admin1'), (1, False, 'system')]
    assert history['items'][0]['summary'] == 'Alterou: ferramentas, instruções'
    audit = await db.audit_events.find_one({'agent_id': 'support', 'version': 2})
    assert (audit['actor'], audit['type'], audit['graph_version']) == ('admin1', 'agent.published', 2)
    with pytest.raises(AgentError) as exc:
        await svc.publish(WHO, 'support', if_match=None, summary=None)
    assert exc.value.code == 'no_changes'


async def test_publish_with_stale_if_match_is_a_revision_conflict(svc):
    with pytest.raises(AgentError) as exc:
        await svc.publish(WHO, 'support', if_match=99, summary=None)
    assert exc.value.code == 'revision_conflict' and exc.value.details == {'current_revision': 1}


async def test_validate_reports_each_error_kind_without_saving(svc):
    other = await new_agent(svc, 'Outro')
    await svc.update(WHO, other['id'], other['revision'], {'enabled': False})
    await svc.publish(WHO, other['id'], if_match=None, summary=None)
    cases = {
        'self_reference': {'delegates': ['support']},
        'cycle': {'delegates': ['router']},
        'unknown_destination': {'delegates': ['fantasma']},
        'disabled_destination': {'delegates': [other['id']]},
        'unknown_tool': {'tools': ['delegate_to_agent', 'magia']},
        'missing_delegate_tool': {'tools': ['get_receivables']},
    }
    for code, proposal in cases.items():
        result = await svc.validate(WHO, 'knowledge' if code == 'cycle' else 'support', proposal)
        assert not result['valid'] and code in [e['code'] for e in result['errors']], (code, result)
    cycle = await svc.validate(WHO, 'knowledge', {'delegates': ['router'], 'tools': ['delegate_to_agent']})
    assert cycle['cycle'][0] == cycle['cycle'][-1] == 'knowledge' and 'router' in cycle['cycle']
    ok = await svc.validate(WHO, 'support', {'delegates': ['knowledge'], 'graph_revision': 3})
    assert ok == {'valid': True, 'graph_revision': 3, 'stale': False, 'errors': [], 'cycle': None}
    assert (await svc.validate(WHO, 'support', {'graph_revision': 1}))['stale'] is True
    assert (await svc.get(WHO, 'support'))['delegates'] == ['knowledge', 'escalation'] and (await svc.get(WHO, 'support'))['revision'] == 1


async def test_cycle_is_refused_with_its_path_and_the_published_version_stays_active(svc, db):
    knowledge = await svc.get(WHO, 'knowledge')
    await svc.update(WHO, 'knowledge', knowledge['revision'], {'delegates': ['router'], 'tools': ['delegate_to_agent']})
    with pytest.raises(AgentError) as exc:
        await svc.publish(WHO, 'knowledge', if_match=None, summary=None)
    assert exc.value.code == 'graph_invalid' and exc.value.details['cycle'][0] == exc.value.details['cycle'][-1]
    assert {'knowledge', 'router'} <= set(exc.value.details['cycle'])
    graph_version, agents = await load_published_snapshot(db, 'demo')
    assert graph_version == '1' and agents['knowledge'].delegates == () and await db.agent_versions.count_documents({'agent_id': 'knowledge'}) == 1
    assert (await svc.get(WHO, 'knowledge'))['has_unpublished_changes']  # the draft is kept for correction


async def test_unpublished_destination_and_disabling_the_router_are_refused(svc):
    draft = await svc.create(WHO, {'name': 'Rascunho', 'instructions': 'x'})
    support = await svc.get(WHO, 'support')
    await svc.update(WHO, 'support', support['revision'], {'delegates': ['knowledge', draft['id']]})
    with pytest.raises(AgentError) as exc:
        await svc.publish(WHO, 'support', if_match=None, summary=None)
    assert [e['code'] for e in exc.value.details['errors']] == ['unknown_destination']
    router = await svc.get(WHO, 'router')
    await svc.update(WHO, 'router', router['revision'], {'enabled': False})
    with pytest.raises(AgentError) as exc:
        await svc.publish(WHO, 'router', if_match=None, summary=None)
    assert 'missing_root' in [e['code'] for e in exc.value.details['errors']]


def gate_first_read(service):
    """Make concurrent publications read the same snapshot before either commits, so the CAS is what decides."""
    barrier, seen, real = asyncio.Barrier(2), set(), service.repo.graph_state

    async def gated(ws, session=None):
        state = await real(ws)
        if (task := asyncio.current_task()) not in seen:
            seen.add(task)
            await barrier.wait()
        return state
    service.repo.graph_state = gated


async def test_concurrent_publications_cannot_create_a_cycle(svc, db):
    a, b = await new_agent(svc, 'A'), await new_agent(svc, 'B')
    await svc.update(WHO, a['id'], a['revision'], {'delegates': [b['id']]})
    await svc.update(WHO, b['id'], b['revision'], {'delegates': [a['id']]})
    before, _ = await load_published_snapshot(db, 'demo')
    gate_first_read(svc)
    results = await asyncio.gather(svc.publish(WHO, a['id'], if_match=None, summary=None), svc.publish(WHO, b['id'], if_match=None, summary=None),
                                   return_exceptions=True)
    refused = [r for r in results if isinstance(r, AgentError)]
    assert len(refused) == 1 and refused[0].code == 'graph_invalid' and refused[0].details['cycle'][0] == refused[0].details['cycle'][-1]
    assert sum(isinstance(r, dict) for r in results) == 1
    graph_version, agents = await load_published_snapshot(db, 'demo')
    assert int(graph_version) == int(before) + 1
    _, versions = await MongoAgentRepository(db).graph_state('demo')  # fresh repo: svc's is gated
    assert check_graph({k: node(v) for k, v in versions.items()}, root='router') == []


async def test_concurrent_non_conflicting_publications_both_land_without_lost_updates(svc, db):
    support, knowledge = await svc.get(WHO, 'support'), await svc.get(WHO, 'knowledge')
    await svc.update(WHO, 'support', support['revision'], {'description': 'Novo suporte'})
    await svc.update(WHO, 'knowledge', knowledge['revision'], {'description': 'Novo conhecimento'})
    gate_first_read(svc)
    results = await asyncio.gather(svc.publish(WHO, 'support', if_match=None, summary=None), svc.publish(WHO, 'knowledge', if_match=None, summary=None))
    assert sorted(r['graph_version'] for r in results) == ['2', '3']
    graph_version, agents = await load_published_snapshot(db, 'demo')
    assert graph_version == '3' and (agents['support'].version, agents['knowledge'].version, agents['router'].version) == (2, 2, 1)
    assert agents['support'].description == 'Novo suporte' and agents['knowledge'].description == 'Novo conhecimento'


async def test_restore_publishes_a_new_version_with_the_old_content_and_keeps_history(svc, db):
    original = await svc.get(WHO, 'support')
    await svc.update(WHO, 'support', 1, {'instructions': 'Outra instrução.', 'description': 'Outra descrição.'})
    await svc.publish(WHO, 'support', if_match=None, summary='Reescrita')
    restored = await svc.restore(WHO, 'support', 1, if_match=None, summary=None)
    assert (restored['version'], restored['prompt_version'], restored['graph_version']) == (3, 3, '3')
    agent = restored['agent']
    assert agent['instructions'] == original['instructions'] and agent['description'] == original['description']
    assert not agent['has_unpublished_changes'] and agent['revision'] == 3
    versions = (await svc.versions(WHO, 'support', limit=10, cursor=None))['items']
    assert [(v['version'], v['restored_from'], v['is_current']) for v in versions] == [(3, 1, True), (2, None, False), (1, None, False)]
    assert versions[0]['summary'] == 'Restaurada a partir da versão 1' and versions[1]['summary'] == 'Reescrita'
    assert (await svc.diff(WHO, 'support', 1, 3))['identical'] is True
    changes = (await svc.diff(WHO, 'support', 1, 2))['changes']
    assert [c['field'] for c in changes] == ['description', 'instructions'] and changes[1]['lines'][-1] == {'op': 'add', 'text': 'Outra instrução.'}
    assert (await db.audit_events.find_one({'agent_id': 'support', 'version': 3}))['type'] == 'agent.restored'
    prompts = (await svc.prompt_versions(WHO, 'prompt-support', limit=10, cursor=None))['items']
    assert [p['version'] for p in prompts] == [3, 2, 1] and prompts[0]['instructions'] == original['instructions']
    with pytest.raises(AgentError) as exc:
        await svc.restore(WHO, 'support', 3, if_match=None, summary=None)
    assert exc.value.code == 'no_changes'
    with pytest.raises(AgentError) as exc:
        await svc.restore(WHO, 'support', 9, if_match=None, summary=None)
    assert exc.value.code == 'version_not_found'


async def test_restore_that_would_create_a_cycle_is_refused(svc, db):
    a, b = await new_agent(svc, 'A'), await new_agent(svc, 'B')
    await svc.update(WHO, a['id'], 1, {'delegates': [b['id']]})
    await svc.publish(WHO, a['id'], if_match=None, summary=None)  # a v2 -> b
    await svc.update(WHO, a['id'], 2, {'delegates': []})
    await svc.publish(WHO, a['id'], if_match=None, summary=None)  # a v3 -> nothing
    await svc.update(WHO, b['id'], 1, {'delegates': [a['id']]})
    await svc.publish(WHO, b['id'], if_match=None, summary=None)  # b v2 -> a
    with pytest.raises(AgentError) as exc:
        await svc.restore(WHO, a['id'], 2, if_match=None, summary=None)
    assert exc.value.code == 'graph_invalid' and exc.value.details['cycle']
    assert (await load_published_snapshot(db, 'demo'))[1][a['id']].version == 3


async def test_versions_paginate_by_cursor(svc):
    for n in range(3):
        support = await svc.get(WHO, 'support')
        await svc.update(WHO, 'support', support['revision'], {'description': f'v{n}'})
        await svc.publish(WHO, 'support', if_match=None, summary=None)
    first = await svc.versions(WHO, 'support', limit=3, cursor=None)
    second = await svc.versions(WHO, 'support', limit=3, cursor=first['next_cursor'])
    assert [v['version'] for v in first['items']] == [4, 3, 2] and [v['version'] for v in second['items']] == [1] and second['next_cursor'] is None
    graphs = await svc.graph_versions(WHO, limit=2, cursor=None)
    assert [g['id'] for g in graphs['items']] == ['4', '3'] and graphs['next_cursor']
    with pytest.raises(AgentError) as exc:
        await svc.versions(WHO, 'support', limit=3, cursor='!!')
    assert exc.value.code == 'invalid_cursor'


async def test_relationships_show_inbound_and_outbound_for_draft_and_published(svc):
    published = await svc.relationships(WHO, 'support', 'published')
    assert [(e['id'], e['name'], e['version']) for e in published['outbound']] == [('knowledge', 'Especialista Getnet', 1), ('escalation', 'Atendimento Humano', 1)]
    assert [e['id'] for e in published['inbound']] == ['router'] and published['graph_version'] == '1'
    knowledge = await svc.relationships(WHO, 'knowledge', 'published')
    assert sorted(e['id'] for e in knowledge['inbound']) == ['router', 'support'] and knowledge['outbound'] == []
    support = await svc.get(WHO, 'support')
    await svc.update(WHO, 'support', support['revision'], {'delegates': ['knowledge', 'fantasma']})
    draft = await svc.relationships(WHO, 'support', 'draft')
    assert [(e['id'], e['status']) for e in draft['outbound']] == [('knowledge', 'published'), ('fantasma', 'missing')]
    assert [e['id'] for e in (await svc.relationships(WHO, 'knowledge', 'draft'))['inbound']] == ['router', 'support']
    assert [e['id'] for e in (await svc.relationships(WHO, 'support', 'published'))['outbound']] == ['knowledge', 'escalation']  # draft change is not live


async def test_list_filters_search_and_paginates_in_a_stable_order(svc):
    ids = [(await svc.create(WHO, {'name': f'Extra {n}', 'instructions': 'x'}))['id'] for n in range(3)]
    page = await svc.list_agents(WHO, q=None, kind=None, status=None, limit=4, cursor=None)
    assert [a['id'] for a in page['items']] == ['router', 'support', 'knowledge', 'escalation'] and page['next_cursor']
    rest = await svc.list_agents(WHO, q=None, kind=None, status=None, limit=4, cursor=page['next_cursor'])
    assert [a['id'] for a in rest['items']] == ids and rest['next_cursor'] is None
    assert [a['id'] for a in (await svc.list_agents(WHO, q='getnet', kind=None, status=None, limit=10, cursor=None))['items']] == ['knowledge']
    assert [a['id'] for a in (await svc.list_agents(WHO, q=None, kind='support', status=None, limit=10, cursor=None))['items']] == ['support']
    drafts = await svc.list_agents(WHO, q=None, kind=None, status='draft', limit=10, cursor=None)
    assert [a['id'] for a in drafts['items']] == ids and all(a['status'] == 'draft' and a['version'] is None for a in drafts['items'])
    assert len((await svc.list_agents(WHO, q=None, kind=None, status='published', limit=10, cursor=None))['items']) == 4
    assert (await svc.list_agents(WHO, q='.*', kind=None, status=None, limit=10, cursor=None))['items'] == []  # regex is escaped
