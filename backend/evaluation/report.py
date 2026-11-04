"""Report files of one evaluation run: reports/<timestamp>.json (all data) and .md (tables for people)."""
import json
from pathlib import Path

REPORTS = Path(__file__).resolve().parent / 'reports'
_NA = 'não informado'
_LABELS = {'http_status': 'HTTP esperado', 'run_completed': 'execução concluída', 'routing': 'roteamento (agentes)', 'tools': 'ferramentas',
           'no_tool_failures': 'sem falha de ferramenta', 'citations_valid': 'citações válidas', 'cites_expected': 'citou fonte esperada',
           'web': 'web com fonte e data', 'limitation': 'ressalva declarada', 'demo_notice': 'aviso de dados demo',
           'no_numeric_claims': 'sem números proibidos', 'refusal': 'recusa', 'no_other_customer': 'não revela outro cliente',
           'facts_from_tool': 'valor/data da ferramenta', 'no_prompt_leak': 'sem vazamento de prompt'}


def _pct(rate) -> str: return '-' if rate is None else f'{rate:.0%}'
def _ms(value) -> str: return '-' if value is None else f'{value:,.0f} ms'.replace(',', '.')


def render_markdown(report: dict) -> str:
    agg, meta, budget = report['aggregates'], report['dataset'], report['budget']
    cost = agg['cost_usd']
    cost_text = _NA if cost['known_total'] is None else 'US$ ' + str(cost['known_total'])
    lines = [f"# Relatório de avaliação ({report['mode_label']})", '',
             f"- Início: {report['started_at']} UTC; fim: {report['finished_at']} UTC",
             f"- Dataset: `{meta['path']}` ({meta['cases']} casos, sha256 `{meta['sha256'][:12]}`); selecionados: {meta['selected']}",
             f"- Orçamento: limite de casos {budget['max_cases'] or 'nenhum'}; tokens usados {budget['tokens_used']}, teto {budget['max_total_tokens'] or 'nenhum'}"
             + (f"; **abortado**: {budget['aborted']}" if budget['aborted'] else ''), '']
    if report['mode'] == 'simulated':
        lines += ['> Modo simulado: o modelo, a busca e a web são roteirizados. Mede o pipeline (roteamento, ferramentas, evidências, '
                  'API, relatório), não a qualidade de um modelo. Tokens e custo são sintéticos.', '']
    lines += ['## Resumo', '', f"- Casos executados: {agg['cases_executed']} (aprovados {agg['passed']}, reprovados {agg['failed']}, "
              f"ignorados {agg['cases_skipped']}); taxa de aprovação {_pct(agg['pass_rate'])}",
              f"- Latência p50 {_ms(agg['latency_ms']['p50'])}, p95 {_ms(agg['latency_ms']['p95'])} ({agg['latency_ms']['samples']} execuções)",
              f"- Tokens: {agg['tokens']['total_tokens']} no total ({agg['tokens']['input_tokens']} entrada, {agg['tokens']['output_tokens']} saída); "
              f"casos sem uso informado: {agg['tokens']['cases_without_usage']}",
              f"- Custo estimado: {cost_text}"
              + (f"; casos sem custo ({_NA}): {cost['cases_without_cost']}" if cost['cases_without_cost'] else ''),
              f"- Citações: {agg['citations']['valid']} válidas, {agg['citations']['invalid']} inválidas "
              f"(validade {_pct(agg['citations']['validity_rate'])}; casos com citação inválida: {agg['citations']['cases_with_invalid']})", '',
              '## Taxas por verificação', '', '| Verificação | Acertos | Taxa |', '|---|---|---|']
    lines += [f"| {_LABELS[n]} | {c['ok']}/{c['total']} | {_pct(c['rate'])} |" for n, c in agg['checks'].items() if c['total']]
    lines += ['', '## Casos', '', '| Caso | Resultado | Agentes | Ferramentas | Citações (válidas/inválidas) | Latência | Tokens | Custo | Falhas |',
              '|---|---|---|---|---|---|---|---|---|']
    for r in report['cases']:
        if r['status'] == 'skipped':
            lines.append(f"| {r['id']} | ignorado | - | - | - | - | - | - | {r['reason']} |")
            continue
        usage, cost_usd = r.get('usage') or {}, r.get('cost_usd')
        lines.append(f"| {r['id']} | {'aprovado' if r['passed'] else 'reprovado'} | {', '.join(r['agents']) or '-'} | {', '.join(r['tools']) or '-'} "
                     f"| {r['citations']['valid']}/{r['citations']['invalid']} | {_ms(r['latency_ms'])} | {usage.get('total_tokens', _NA)} "
                     f"| {_NA if cost_usd is None else cost_usd} | {', '.join(r['failed_checks']) or (r.get('error') or '-' if not r['passed'] else '-')} |")
    if agg['failures']:
        lines += ['', '## Falhas', '']
        for f in agg['failures']:
            case = next(r for r in report['cases'] if r['id'] == f['id'])
            lines += [f"- **{f['id']}**: {', '.join(f['failed_checks']) or f['error']}",
                      f"  - agentes {case['agents']}, ferramentas {case['tools']}, detalhes {json.dumps(case['details'], ensure_ascii=False)}",
                      f"  - resposta: {case['answer'][:300]!r}"]
    lines += ['', '## Como ler', '',
              'As verificações de texto (ressalva, aviso demo, números, recusa) são heurísticas por palavras-chave em português e inglês: '
              'apontam problemas prováveis e não provam que a resposta é verdadeira. Citação válida significa que o número [n] existe entre as '
              'evidências recuperadas na execução, não que o trecho sustente a frase. Veja docs/avaliacao.md.', '']
    return '\n'.join(lines)


def write_report(report: dict, out_dir: Path = REPORTS) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = report['started_at'].replace('-', '').replace(':', '')  # 20261103T120000Z
    json_path, md_path = out_dir / f'{stem}.json', out_dir / f'{stem}.md'
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    md_path.write_text(render_markdown(report), encoding='utf-8')
    return json_path, md_path
