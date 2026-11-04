from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Receivable:
    sale_date: date
    due_date: date
    amount: Decimal
    status: str
    payment_type: str | None = None  # e.g. credito_a_vista


@dataclass(frozen=True, slots=True)
class Terminal:
    model: str
    connectivity: str
    error: str | None = None
    serial_suffix: str | None = None
    carrier: str | None = None  # SIM carrier
    carrier_signal: bool | None = None  # None = unknown
