from datetime import date
from decimal import Decimal
import pytest
from app.application.use_cases.support import SupportService
from app.domain.identity import Identity
from app.domain.support import Receivable, Terminal


class Repository:
    async def get_receivables(self, identity):
        if (identity.workspace_id, identity.user_id) != ('demo', 'cliente1988'):
            return []
        return [Receivable(date(2026, 11, 2), date(2026, 11, 5), Decimal('150.00'), 'previsto')]

    async def get_terminals(self, identity):
        if (identity.workspace_id, identity.user_id) != ('demo', 'cliente1988'):
            return []
        return [Terminal('Get Smart', 'sem conexão', 'rede indisponível')]


@pytest.mark.asyncio
async def test_support_uses_authenticated_identity_and_labels_demo():
    service = SupportService(Repository())
    identity = Identity('cliente1988', 'demo', 'admin', is_demo=True)
    assert (await service.get_receivables(identity))['items'][0]['amount'] == Decimal('150.00')
    assert (await service.get_terminal_status(identity))['items'][0]['connectivity'] == 'sem conexão'
    assert (await service.get_receivables(identity))['is_demo'] is True
    assert (await service.get_receivables(Identity('someone-else', 'demo', 'operator')))['items'] == []
    assert (await service.get_terminal_status(Identity('cliente1988', 'other', 'operator')))['items'] == []
