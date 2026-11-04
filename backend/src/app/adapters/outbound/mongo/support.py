from datetime import date, datetime
from decimal import Decimal
from app.domain.identity import Identity
from app.domain.support import Receivable, Terminal


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else date.fromisoformat(value) if isinstance(value, str) else value


class MongoSupportRepository:
    """Reads only the authenticated customer's fixtures: every query is scoped by workspace and user."""

    def __init__(self, db):
        self.db = db

    async def get_receivables(self, identity: Identity) -> list[Receivable]:
        query = {'workspace_id': identity.workspace_id, 'user_id': identity.user_id}
        docs = await self.db.receivables.find(query, {'_id': 0}).sort('sale_date', -1).to_list(length=100)
        return [Receivable(_day(d['sale_date']), _day(d['due_date']), Decimal(str(d['amount'])), d['status'], d.get('payment_type'))
                for d in docs]

    async def get_terminals(self, identity: Identity) -> list[Terminal]:
        query = {'workspace_id': identity.workspace_id, 'user_id': identity.user_id}
        docs = await self.db.terminals.find(query, {'_id': 0}).to_list(length=100)
        return [Terminal(d['model'], d['connectivity'], d.get('error'), d.get('serial_suffix'), d.get('carrier'), d.get('carrier_signal'))
                for d in docs]
