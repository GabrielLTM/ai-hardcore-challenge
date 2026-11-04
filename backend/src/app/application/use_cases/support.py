from dataclasses import asdict
from app.application.ports.support import SupportRepository
from app.domain.identity import Identity


def _result(identity: Identity, items, empty_message: str) -> dict:
    result = {'is_demo': identity.is_demo, 'items': [asdict(item) for item in items]}
    if identity.is_demo:
        result['notice'] = 'Dados fictícios de demonstração, sem integração com sistemas reais da Getnet.'
    if not items:
        result['message'] = empty_message
    return result


class SupportService:
    def __init__(self, repository: SupportRepository):
        self.repository = repository

    async def get_receivables(self, identity: Identity) -> dict:
        result = _result(identity, await self.repository.get_receivables(identity),
                         'Nenhum recebível encontrado para este cliente. Informe a ausência; não estime datas nem valores.')
        return {**result, 'currency': 'BRL'}

    async def get_terminal_status(self, identity: Identity) -> dict:
        return _result(identity, await self.repository.get_terminals(identity),
                       'Nenhum terminal encontrado para este cliente. Informe a ausência; não suponha modelo nem diagnóstico.')
