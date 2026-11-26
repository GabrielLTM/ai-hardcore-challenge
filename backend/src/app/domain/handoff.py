"""Human-handoff ticket states. They move only forward: aberto -> em_atendimento -> encerrado (aberto -> encerrado if the customer gives up)."""
OPEN, IN_PROGRESS, CLOSED = 'aberto', 'em_atendimento', 'encerrado'


class HandoffError(Exception):
    """A refused attendance action; `code` is stable for clients, `details` may name who holds the ticket or its state."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details
