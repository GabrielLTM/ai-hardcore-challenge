"""AttendanceService with in-memory ports: the attendance rules run without Mongo or HTTP."""
from datetime import datetime, timezone
import pytest
from app.application.use_cases.attendance import CLAIMED_NOTICE, AttendanceService
from app.domain.handoff import CLOSED, IN_PROGRESS, OPEN, HandoffError
from app.domain.identity import Identity, Operator

ANA, BRUNO = Operator('op-ana', 'Ana', 'demo'), Operator('op-bruno', 'Bruno', 'demo')
CUSTOMER = Identity('cliente1988', 'demo', 'operator')


class Handoffs:
    def __init__(self):
        self.ticket = {'protocol': 'ATD-1', 'workspace_id': 'demo', 'user_id': 'cliente1988', 'conversation_id': 'c1',
                       'status': OPEN, 'created_at': datetime.now(timezone.utc)}

    async def get(self, workspace_id, protocol):
        return dict(self.ticket) if protocol == 'ATD-1' and workspace_id == 'demo' else None

    async def active_for(self, who, conversation_id):
        return dict(self.ticket) if self.ticket['status'] != CLOSED and conversation_id == 'c1' else None

    def _guarded(self, required, holder=None, **changes):
        if self.ticket['status'] != required or (holder and (self.ticket.get('operator') or {}).get('id') != holder.id):
            return None
        self.ticket.update(changes)
        return dict(self.ticket)

    async def claim(self, workspace_id, protocol, operator):
        return self._guarded(OPEN, status=IN_PROGRESS, operator={'id': operator.id, 'name': operator.name}, claimed_at=datetime.now(timezone.utc))

    async def operator_message(self, workspace_id, protocol, operator):
        return self._guarded(IN_PROGRESS, operator)

    async def note(self, workspace_id, protocol, operator, content):
        return dict(self.ticket)

    async def close(self, workspace_id, protocol, operator, resolution):
        return self._guarded(IN_PROGRESS, operator, status=CLOSED)

    async def cancel(self, who, protocol):
        return self._guarded(OPEN, status=CLOSED)


class Messages:
    def __init__(self): self.rows = []

    async def add_message(self, identity, conversation_id, role, content, author_name=None):
        self.rows.append((identity.user_id, role, content, author_name))
        return {'role': role, 'content': content}


async def test_claim_answer_and_close_with_the_rules_outside_http():
    handoffs, messages, events = Handoffs(), Messages(), []
    service = AttendanceService(handoffs, messages, lambda event, seconds=None: events.append(event))
    await service.claim(ANA, 'ATD-1')
    assert messages.rows[-1] == ('cliente1988', 'system', CLAIMED_NOTICE.format(name='Ana'), None)  # notice goes to the ticket's customer

    with pytest.raises(HandoffError) as taken:
        await service.claim(BRUNO, 'ATD-1')
    assert taken.value.code == 'handoff_already_claimed' and taken.value.details == {'operator_name': 'Ana'}
    with pytest.raises(HandoffError) as not_owner:
        await service.send(BRUNO, 'ATD-1', 'Oi')
    assert not_owner.value.code == 'not_handoff_owner'
    with pytest.raises(HandoffError) as waiting:
        await service.cancel_wait(CUSTOMER, 'c1')
    assert waiting.value.code == 'handoff_already_claimed'  # the customer cannot leave once Ana is talking

    await service.send(ANA, 'ATD-1', 'Vou verificar.')
    await service.close(ANA, 'ATD-1', 'Resolvido.')
    with pytest.raises(HandoffError) as closed:
        await service.close(ANA, 'ATD-1', 'De novo.')
    assert closed.value.code == 'invalid_transition'
    assert events == ['claimed', 'closed'] and [r[1] for r in messages.rows] == ['system', 'operator', 'system']


async def test_the_customer_leaves_an_unclaimed_queue():
    handoffs, messages = Handoffs(), Messages()
    ticket = await AttendanceService(handoffs, messages).cancel_wait(CUSTOMER, 'c1')
    assert ticket['status'] == CLOSED and messages.rows[-1][2].startswith('Você saiu da fila')
    with pytest.raises(HandoffError) as gone:
        await AttendanceService(handoffs, messages).cancel_wait(CUSTOMER, 'c1')
    assert gone.value.code == 'handoff_not_found'
