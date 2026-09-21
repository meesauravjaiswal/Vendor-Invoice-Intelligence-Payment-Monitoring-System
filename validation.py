"""Input validation and exact money conversion."""
from datetime import date
from decimal import Decimal, InvalidOperation

CURRENCIES = ('INR', 'USD', 'EUR', 'GBP', 'CNY', 'SEK', 'BRL', 'CAD', 'JPY')

def parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError('Enter dates as YYYY-MM-DD.') from None

def decimal_value(value):
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError('Enter a valid number.') from None
    if not result.is_finite():
        raise ValueError('Amounts must be finite numbers.')
    return result

def money(value, currency):
    if currency not in CURRENCIES:
        raise ValueError('Select a supported currency.')
    amount = decimal_value(value)
    scale = 1 if currency == 'JPY' else 100
    if amount < 0:
        raise ValueError('Amount must be non-negative.')
    if amount.as_tuple().exponent < -12:
        raise ValueError('Amounts support up to 12 decimal places.')
    if amount * scale > 10**15:
        raise ValueError('Amount exceeds the supported limit.')
    return amount * scale

def required(value, label):
    value = str(value or '').strip()
    if not value:
        raise ValueError(f'{label} is required.')
    return value
