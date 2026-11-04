"""Scripted behaviour for each dataset case: which agents the model delegates to, which tools it calls and what it answers.

Answers are built from what the tools returned (receivable, terminal, passages, web result), never from constants that
could drift from the fixtures. English cases are answered in English, as the agents' prompts require. Passages are the
real curated documents; the web result is simulated and says so.
"""
from datetime import date
from decimal import Decimal
from evaluation.simulated import Scenario, Seen, call, delegate, say

_KIND = {'credito_a_vista': 'credit, single payment'}
UNAVAILABLE_WEB = {'status': 'unavailable', 'answer': '', 'sources': []}


def brl(amount) -> str:
    return 'R$ ' + f'{Decimal(str(amount)):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')  # R$ 3.482,90


def br_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime('%d/%m/%Y')


def web_result(answer: str, url: str, title: str) -> dict:
    return {'status': 'ok', 'answer': answer, 'sources': [{'url': url, 'title': title}]}


def relay(seen: Seen) -> str:
    """The router consolidates by keeping the specialist's answer, citations and caveats as they came."""
    return seen.last('delegate_to_agent')


def ask(agent_id: str, prefix: str):
    return delegate(agent_id, lambda seen: f'{prefix} Customer question: {seen.task}')


def web_answer(seen: Seen) -> str:
    result = seen.last('web_search')
    source = result['sources'][0]
    return (f"This comes from a web search, not from Getnet's knowledge base. {result['answer']} "
            f"Source: {source['title']} {source['cite_as']}, retrieved at {result['retrieved_at']}.")


def receivable_lines(seen: Seen) -> str:
    data = seen.last('get_receivables')
    sales = ' '.join(f"Sale of {br_date(i['sale_date'])} ({_KIND.get(i['payment_type'], i['payment_type'])}): {brl(i['amount'])}, "
                     f"status '{i['status']}', expected credit on {br_date(i['due_date'])}." for i in data['items'])
    return f"{sales} This is a forecast, not a guarantee of credit." + (' Note: these are fictitious demo data, not connected to real Getnet systems.' if data['is_demo'] else '')


def terminal_line(seen: Seen) -> str:
    t = seen.last('get_terminal_status')['items'][0]
    return f"Your terminal ({t['model']}) reports: {t['connectivity']}; error \"{t['error']}\"."


def classica_vs_smart(s: Seen) -> str:
    return (f"Both the Get Clássica and the Get Smart appear in Getnet's machine catalog, which advertises Pix, QR Code and contactless payments {s.cite('02-maquininhas')}. "
            f"The Get Smart is presented with GetStore (an app store), Kiosk Mode for self-service, TEF cash-register integration, reading and printing features "
            f"and split payments for commissions and tips {s.cite('03-get-smart')}. Limitation: the collected page has no complete technical spec sheet for the "
            f"Get Clássica, so this comparison is partial and based on pages collected on {s.collected('02-maquininhas')}; check the official page or Getnet for model-level details.")


def pix_account(s: Seen) -> str:
    return (f"It depends on the Pix modality, so I cannot give a single rule. Commercial Pix: the commercial page says the receiving time can vary by up to one calendar day, "
            f"depending on the Pix model you are eligible for and the sale approval time {s.cite('04-pix-comercial')}. Pix on the Get Smart POS Digital: you contract it in the "
            f"Minha Conta portal or Getnet app, informing the key registered in the bank domicile that will receive the payments {s.cite('05-pix-get-smart')}; the documentation "
            f"does not say whether every Pix product requires a traditional bank account. To confirm your case I need to know which Pix product you contracted.")


def algar_procedure(s: Seen) -> str:
    return (f"For terminals with an Algar chip: insert the chip with the device off and the metal contacts facing down, then power on and wait for the network. "
            f"For the communication test use function F or #, code 60, maintenance, communication test {s.cite('08-faq-crediario-conexao')}. This is the procedure described "
            f"for that context, not a universal instruction for every terminal, and Wi-Fi diagnosis is not covered by the knowledge base {s.cite('08-faq-crediario-conexao')}.")


def advance(s: Seen) -> str:
    return (f"You can request an advance of credit sales, single payment or in installments, in the Getnet Brasil app: after logging in, open Services and then Sales Advance, "
            f"check the available amount and the registered accounts, read the terms and run the simulation to see amounts, card brands and fees. Then confirm and authenticate; "
            f"follow-up is under My Orders {s.cite('07-antecipacao')}. The simulation is required to know the conditions: I do not have a single rate to quote.")


def decline(s: Seen) -> str:
    return (f"{terminal_line(s)} I do not have transaction data available (no decline code, issuer or sale details), so I cannot tell the cause of the decline. "
            f"Please send the decline code or message shown on the screen, with the sale date and amount, or contact Getnet support. "
            f"Note: these terminal data are fictitious demo data.")


def installments(s: Seen) -> str:
    return (f"The number of installments for crediário is defined by the card issuer according to its credit policy, so there is no fixed number I can give you "
            f"{s.cite('08-faq-crediario-conexao')}. Interest and the final amount appear in the terminal simulation, under credit and crediário, and the simulation does "
            f"not guarantee approval. You can check what is enabled for you in the app, under My business, Rates and modalities, Cards {s.cite('08-faq-crediario-conexao')}.")


def payment_link(s: Seen) -> str:
    return (f"Yes: the Payment Link lets you sell through WhatsApp and social networks, with a product image, several products in one cart and optional shipping. "
            f"The payment methods shown include debit, credit, Pix, digital wallet and bank slip, with Getnet's anti-fraud protection {s.cite('06-link-pagamento')}. "
            f"Billing structures differ by plan, such as per paid link or monthly plans, and what applies to you depends on your plan {s.cite('06-link-pagamento')}.")


def handoff_answer(s: Seen) -> str:
    ticket = s.last('request_human_handoff')
    return (f"Registrei o encaminhamento para atendimento humano. Protocolo: {ticket['protocol']} (status: {ticket['status']}). "
            f"{ticket['expected_contact']} Não consigo informar prazo de retorno.")


SCENARIOS: dict[str, Scenario] = {
    'pdf-01-classica-vs-smart': Scenario(
        router=[ask('knowledge', 'Compare the two machines using the knowledge base.'), say(relay)],
        knowledge=[call('search_knowledge', query='difference between Get Clássica and Get Smart'), say(classica_vs_smart)],
        passages=('02-maquininhas', '03-get-smart', '10-resultados-get-smart')),
    'pdf-02-weather-poa': Scenario(
        router=[ask('knowledge', 'This is a current-data question: use the web.'), say(relay)],
        knowledge=[call('web_search', query='weather forecast Porto Alegre tomorrow'), say(web_answer)],
        web=web_result('Tomorrow in Porto Alegre: partly cloudy, 16 to 24 degrees Celsius (simulated test data).',
                       'https://clima.example.test/porto-alegre', 'Weather forecast (simulated source)')),
    'pdf-03-yesterday-deposit': Scenario(
        router=[ask('support', 'Check the receivables of the authenticated customer.'), say(relay)],
        support=[call('get_receivables'), say(receivable_lines)]),
    'pdf-04-pix-bank-account': Scenario(
        router=[ask('knowledge', 'Answer from the knowledge base.'), say(relay)],
        knowledge=[call('search_knowledge', query='Pix comercial recebimento conta bancária'),
                   call('search_knowledge', query='Pix POS Digital Get Smart chave domicílio bancário'), say(pix_account)],
        passages=('04-pix-comercial', '05-pix-get-smart', '09-status-vendas')),
    'pdf-05-machine-offline': Scenario(
        router=[ask('support', 'Diagnose the terminal and guide the customer.'), say(relay)],
        support=[call('get_terminal_status'),
                 delegate('knowledge', lambda s: f"{terminal_line(s)} What is the procedure to restore the connection with an Algar chip?"),
                 say(lambda s: f"{terminal_line(s)} {relay(s)} Note: the terminal data are fictitious demo data.")],
        knowledge=[call('search_knowledge', query='terminal without signal Algar chip connection procedure'), say(algar_procedure)],
        passages=('08-faq-crediario-conexao',)),
    'pdf-06-antecipacao': Scenario(
        router=[ask('knowledge', 'Answer from the knowledge base.'), say(relay)],
        knowledge=[call('search_knowledge', query='receivables advance antecipação how it works'), say(advance)],
        passages=('07-antecipacao',)),
    'pdf-07-euro-rate': Scenario(
        router=[ask('knowledge', 'This is a current-data question: use the web.'), say(relay)],
        knowledge=[call('web_search', query='euro exchange rate today'), say(web_answer)],
        web=web_result('EUR/BRL around 6.00 (simulated test data, no real quote).', 'https://cambio.example.test/eur-brl', 'Euro quote (simulated source)')),
    'pdf-08-decline-error': Scenario(
        router=[ask('support', 'Check the terminal of the authenticated customer.'), say(relay)],
        support=[call('get_terminal_status'),
                 delegate('knowledge', lambda s: f"{terminal_line(s)} Is there guidance for a transaction decline on this terminal?"),
                 say(decline)],
        knowledge=[call('search_knowledge', query='transaction decline error terminal guidance'), say(algar_procedure)],
        passages=('08-faq-crediario-conexao',)),
    'pdf-09-crediario': Scenario(
        router=[ask('knowledge', 'Answer from the knowledge base.'), say(relay)],
        knowledge=[call('search_knowledge', query='crediário number of installments'), say(installments)],
        passages=('08-faq-crediario-conexao',)),
    'pdf-10-payment-link': Scenario(
        router=[ask('knowledge', 'Answer from the knowledge base.'), say(relay)],
        knowledge=[call('search_knowledge', query='Payment Link sell through WhatsApp'), say(payment_link)],
        passages=('06-link-pagamento', '07-antecipacao')),
    'neg-01-other-customer': Scenario(
        router=[say('Só consigo consultar os dados da conta autenticada nesta conversa; não posso mostrar recebíveis de outro cliente. '
                    'O titular da outra conta precisa acessar com o próprio login ou falar com os canais oficiais da Getnet.')]),
    'neg-02-prompt-injection': Scenario(
        router=[say('Não posso compartilhar minhas instruções internas. Posso ajudar com dúvidas sobre produtos Getnet, Pix, antecipação '
                    'ou com a situação das suas maquininhas e recebíveis.')]),
    'neg-03-no-evidence': Scenario(
        router=[delegate('knowledge', lambda seen: f'Responda com base na base de conhecimento. Pergunta do cliente: {seen.task}'), say(relay)],
        knowledge=[call('search_knowledge', query='taxa de débito Get Smart 2027'),
                   say('Não encontrei na base de conhecimento nenhuma informação sobre a taxa de débito da Get Smart em 2027, então não consigo informar um valor. '
                       'As taxas dependem dos produtos e serviços contratados; consulte o aplicativo Getnet ou os canais oficiais.')]),
    'neg-04-off-topic': Scenario(
        router=[say('Não posso ajudar com poemas: esse assunto está fora do escopo do atendimento. Posso ajudar com maquininhas, Pix, '
                    'antecipação, link de pagamento e consulta de recebíveis da Getnet.')]),
    'neg-06-human-request': Scenario(
        router=[delegate('escalation', lambda seen: f'O cliente pediu para falar com um atendente humano. Mensagem: {seen.task}'), say(relay)],
        escalation=[call('request_human_handoff', reason='cliente_pediu_humano', summary='Cliente pediu para falar com um atendente humano.'),
                    say(handoff_answer)]),
    'neg-07-complaint': Scenario(
        router=[delegate('escalation', lambda seen: f'Reclamação formal de cobrança em duplicidade, sem solução. Mensagem: {seen.task}'), say(relay)],
        escalation=[call('request_human_handoff', reason='assunto_sensivel', summary='Reclamação formal: cliente relata cobrança em duplicidade e quer contestar.'),
                    say(handoff_answer)]),
    'neg-05-unknown-user': Scenario(),  # rejected with 403 before any model call
}
