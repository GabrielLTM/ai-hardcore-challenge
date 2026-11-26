"""Curation of Reviewer verdicts: recurring problems and fix suggestions, computed from stored runs (no model call)."""
from collections import Counter
from typing import Any
from app.domain.review import CRITERIA

LABELS = {'fundamentacao': 'Fundamentação', 'cobertura': 'Cobertura', 'limitacoes': 'Limitações', 'seguranca': 'Segurança'}
SUGGESTION = {
    'fundamentacao': ('search', 'Respostas sem apoio na evidência: confira se a busca trouxe o trecho certo nesses casos (qualidade da '
                                'busca) e reforce nas instruções do agente a regra de citar a fonte de cada número, prazo e regra.'),
    'cobertura': ('prompt', 'Respostas que deixam parte da pergunta sem resposta: peça nas instruções do agente que trate cada parte '
                            'ou diga explicitamente o que não pôde responder.'),
    'limitacoes': ('prompt', 'Respostas que não avisam quando a evidência é parcial: reforce essa regra nas '
                             'instruções do agente.'),
    'seguranca': ('guardrail', 'Respostas com risco de vazamento ou de seguir instruções embutidas: revise os guardrails e as '
                               'instruções do agente.'),
}
SUGGEST_FROM_RATE = 0.2  # a criterion failing in at least 20% of the judged answers gets a suggestion
MAX_EXAMPLES, MAX_QUERIES = 20, 10


def _rate(n: int, d: int) -> float | None:
    return n / d if d else None


def _unique_sources(sources: list[dict]) -> list[dict]:
    seen, out = set(), []
    for s in sources:
        key = s.get('url') or s.get('source_id') or s.get('title')
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def summarize_reviews(records: list[dict[str, Any]], days: int) -> dict[str, Any]:
    """`records`: runs with `review` (newest first), each with `question`, `run_id`, `conversation_id`, `created_at`.

    Criteria and agents are measured on round 1, the answer as the router first wrote it."""
    judged = [r for r in records if r['review'].get('rounds') and r['review']['rounds'][0].get('verdict') in ('aprovar', 'revisar')]
    revised = [r for r in judged if r['review']['rounds'][0]['verdict'] == 'revisar']
    criteria = []
    for c in CRITERIA:
        below = sum(r['review']['rounds'][0]['scores'][c] < 2 for r in judged)
        criteria.append({'criterion': c, 'label': LABELS[c], 'below_max': below, 'rate': _rate(below, len(judged))})
    criteria.sort(key=lambda x: x['below_max'], reverse=True)

    per_agent: dict[str, list[int]] = {}
    for r in judged:
        for agent in r['review']['rounds'][0].get('agents') or []:
            counts = per_agent.setdefault(agent, [0, 0])
            counts[0] += 1
            counts[1] += r in revised
    agents = sorted(({'agent_id': a, 'judged': j, 'revised': v, 'rate': _rate(v, j)} for a, (j, v) in per_agent.items()),
                    key=lambda x: (x['rate'] or 0, x['revised']), reverse=True)

    queries: Counter[str] = Counter()
    last_at: dict[str, Any] = {}
    for r in records:
        for q in r['review'].get('empty_searches') or []:
            queries[q] += 1
            last_at.setdefault(q, r['created_at'])
    empty = [{'query': q, 'count': n, 'last_at': last_at[q]} for q, n in queries.most_common(MAX_QUERIES)]

    examples = [{'run_id': r['run_id'], 'conversation_id': r['conversation_id'], 'question': r.get('question') or '',
                 'created_at': r['created_at'], 'revision': r['review'].get('revision'),
                 'verdicts': [x.get('verdict') for x in r['review']['rounds']], 'scores': r['review']['rounds'][0]['scores'],
                 'failed': [c for c in CRITERIA if r['review']['rounds'][0]['scores'][c] < 2],
                 'issues': r['review']['rounds'][0].get('issues') or [], 'agents': r['review']['rounds'][0].get('agents') or [],
                 'sources': _unique_sources(r['review']['rounds'][0].get('sources') or []),
                 'answers': r.get('answers') or []} for r in revised[:MAX_EXAMPLES]]
    redone = [r for r in revised if len(r['review']['rounds']) > 1]

    # most revised answers, not highest rate: one bad answer out of one must not outrank six out of nine
    flagged = [a for a in agents if a['revised']]
    by_volume = sorted(flagged, key=lambda a: (a['agent_id'] != 'router', a['revised'], a['rate'] or 0), reverse=True)
    worst = by_volume[0]['agent_id'] if by_volume else 'router'
    suggestions = [{'kind': SUGGESTION[c['criterion']][0], 'criterion': c['criterion'], 'target': worst,
                    'text': SUGGESTION[c['criterion']][1].format(agent=worst)}
                   for c in criteria if c['below_max'] and c['rate'] >= SUGGEST_FROM_RATE]
    suggestions += [{'kind': 'knowledge', 'criterion': None, 'target': e['query'],
                     'text': f"A base não trouxe nada para “{e['query']}” ({e['count']}x): avalie incluir uma fonte sobre o assunto."}
                    for e in empty[:5]]
    return {
        'days': days, 'runs': len(records), 'judged': len(judged), 'revised': len(revised),
        'skipped': sum(not r['review'].get('rounds') for r in records),
        'failed': sum(any(x.get('verdict') == 'failed' for x in r['review'].get('rounds', [])) for r in records),
        'first_round_approval': _rate(len(judged) - len(revised), len(judged)),
        'redone': len(redone), 'fixed': sum(r['review']['rounds'][1].get('verdict') == 'aprovar' for r in redone),
        'mean_scores': {c: _rate(sum(r['review']['rounds'][0]['scores'][c] for r in judged), len(judged)) for c in CRITERIA},
        'criteria': criteria, 'agents': agents, 'empty_searches': empty, 'examples': examples, 'suggestions': suggestions,
    }
