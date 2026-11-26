"""Attendance by a human: claim, answer, note and close a ticket, and let the customer give up waiting.

Each action is one guarded update on the ticket (only the first of two concurrent claims wins) followed by the notice the
customer sees in the chat. When the update matches nothing, the current ticket tells why.
"""
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from app.application.ports.attendance import HandoffRepository, MessageLog
from app.domain.handoff import IN_PROGRESS, OPEN, HandoffError
from app.domain.identity import Identity, Operator

CLAIMED_NOTICE = 'Atendente {name} entrou na conversa.'
CLOSED_NOTICE = 'Atendimento encerrado. Você voltou a falar com o assistente virtual.'
CANCELLED_NOTICE = 'Você saiu da fila de atendimento e voltou a falar com o assistente virtual.'
NOTICE_FOR_ATTENDANT = {CLOSED_NOTICE: 'Atendimento encerrado. O cliente voltou a falar com o assistente virtual.',
                        CANCELLED_NOTICE: 'O cliente saiu da fila e voltou a falar com o assistente virtual.'}


def customer_of(ticket: dict[str, Any]) -> Identity:
    """The ticket's customer: identity comes from the ticket, never from the attendant's request."""
    return Identity(ticket['user_id'], ticket['workspace_id'], 'operator')


def seconds_since(at: datetime) -> float:
    return (datetime.now(timezone.utc) - (at if at.tzinfo else at.replace(tzinfo=timezone.utc))).total_seconds()


class AttendanceService:
    def __init__(self, handoffs: HandoffRepository, messages: MessageLog,
                 record: Callable[[str, float | None], None] = lambda event, seconds=None: None):
        self.handoffs, self.messages, self.record = handoffs, messages, record  # record(event, seconds): metrics

    async def ticket(self, me: Operator, protocol: str) -> dict[str, Any]:
        ticket = await self.handoffs.get(me.workspace_id, protocol)
        if not ticket:
            raise HandoffError('handoff_not_found', 'Protocolo não encontrado.')
        return ticket

    async def _refusal(self, me: Operator, protocol: str) -> HandoffError:
        """After a guarded update matched nothing: someone else holds the ticket, or its state forbids the action."""
        ticket = await self.ticket(me, protocol)
        holder = (ticket.get('operator') or {}).get('name')
        if ticket['status'] == IN_PROGRESS and (ticket.get('operator') or {}).get('id') != me.id:
            return HandoffError('not_handoff_owner', f'Protocolo em atendimento por {holder}.', {'operator_name': holder})
        return HandoffError('invalid_transition', f"Ação não permitida com o protocolo {ticket['status']}.", {'status': ticket['status']})

    async def _notify(self, ticket: dict[str, Any], role: str, content: str, author_name: str | None = None) -> dict[str, Any] | None:
        if not ticket.get('conversation_id'):
            return None
        return await self.messages.add_message(customer_of(ticket), ticket['conversation_id'], role, content, author_name=author_name)

    async def claim(self, me: Operator, protocol: str) -> dict[str, Any]:
        ticket = await self.handoffs.claim(me.workspace_id, protocol, me)
        if not ticket:
            current = await self.ticket(me, protocol)
            if current['status'] == IN_PROGRESS:
                name = (current.get('operator') or {}).get('name')
                raise HandoffError('handoff_already_claimed', f'Protocolo já assumido por {name}.', {'operator_name': name})
            raise await self._refusal(me, protocol)
        self.record('claimed', seconds_since(ticket['created_at']))
        await self._notify(ticket, 'system', CLAIMED_NOTICE.format(name=me.name))
        return ticket

    async def send(self, me: Operator, protocol: str, content: str) -> dict[str, Any]:
        ticket = await self.handoffs.operator_message(me.workspace_id, protocol, me)
        if not ticket:
            raise await self._refusal(me, protocol)
        message = await self._notify(ticket, 'operator', content, author_name=me.name)
        if message is None:
            raise HandoffError('invalid_transition', 'Protocolo sem conversa vinculada.')
        return message

    async def note(self, me: Operator, protocol: str, content: str) -> dict[str, Any]:
        ticket = await self.handoffs.note(me.workspace_id, protocol, me, content)
        if not ticket:
            raise HandoffError('handoff_not_found', 'Protocolo não encontrado.')
        return ticket

    async def close(self, me: Operator, protocol: str, resolution: str) -> dict[str, Any]:
        ticket = await self.handoffs.close(me.workspace_id, protocol, me, resolution)
        if not ticket:
            raise await self._refusal(me, protocol)
        self.record('closed', seconds_since(ticket['claimed_at']))
        await self._notify(ticket, 'system', CLOSED_NOTICE)
        return ticket

    async def cancel_wait(self, who: Identity, conversation_id: str) -> dict[str, Any]:
        """The customer leaves the queue before anyone claims the ticket; once an attendant is talking, only they close it."""
        ticket = await self.handoffs.active_for(who, conversation_id)
        if not ticket:
            raise HandoffError('handoff_not_found', 'Esta conversa não está na fila de atendimento.')
        cancelled = await self.handoffs.cancel(who, ticket['protocol']) if ticket['status'] == OPEN else None
        if not cancelled:
            name = (ticket.get('operator') or {}).get('name')
            raise HandoffError('handoff_already_claimed', f'{name or "Um atendente"} já está nesta conversa.', {'operator_name': name})
        self.record('cancelled', seconds_since(ticket['created_at']))
        await self._notify(cancelled, 'system', CANCELLED_NOTICE)
        return cancelled
