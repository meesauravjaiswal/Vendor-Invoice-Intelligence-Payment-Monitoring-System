"""Domain objects encapsulate invoice balance and status rules."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

@dataclass(frozen=True)
class Payment:
    amount_minor: Decimal
    payment_date: str

@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    supplier_id: str
    supplier_name: str
    invoice_number: str
    invoice_date: str
    invoice_due_date: str
    currency: str
    amount_minor: Decimal
    paid_minor: Decimal = Decimal(0)
    cancelled: bool = False

    @property
    def balance_minor(self):
        return 0 if self.cancelled else self.amount_minor - self.paid_minor

    @property
    def status(self):
        if self.cancelled:
            return 'Cancelled'
        if self.paid_minor > self.amount_minor:
            return 'Overpayment'
        if self.paid_minor == self.amount_minor:
            return 'Paid'
        return 'Partially paid' if self.paid_minor else 'Unpaid'

    def days_overdue(self, as_of):
        if self.balance_minor <= 0:
            return 0
        return max(0, (date.fromisoformat(as_of) - date.fromisoformat(self.invoice_due_date)).days)

    def amount(self, minor):
        return Decimal(minor) / (1 if self.currency == 'JPY' else 100)
