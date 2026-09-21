"""Dated exchange-rate history with explicit missing-rate handling."""
from decimal import Decimal, ROUND_HALF_UP
from validation import CURRENCIES, decimal_value, parse_date, required
import json
from datetime import date
from urllib.request import urlopen, Request

RATE_URL = 'https://api.frankfurter.dev/v1/latest?base=INR'

def fetch_latest_rates():
    """No invoice or supplier information is sent to the rate provider."""
    with urlopen(Request(RATE_URL,headers={'User-Agent':'VendorInvoiceStudentProject/1.0'}),timeout=12) as response:
        return json.loads(response.read(100000))

class CurrencyConverter:
    def __init__(self, repository):
        self.repo = repository

    def load_defaults(self, payload):
        if payload.get('base') != 'INR' or decimal_value(payload.get('amount',1)) != 1:
            raise ValueError('Unexpected exchange-rate base.')
        effective = parse_date(payload.get('date',''))
        if effective > date.today():
            raise ValueError('The provider returned a future exchange-rate date.')
        rates = {}
        for currency in CURRENCIES:
            if currency == 'INR': continue
            value = decimal_value(payload.get('rates',{}).get(currency,''))
            if value <= 0: raise ValueError('Invalid provider exchange rate.')
            rates[currency] = Decimal(1) / value
        inserted = 0
        with self.repo.connection as c:
            for currency, rate in rates.items():
                # Never overwrite manual settings or newer stored rates.
                if c.execute('SELECT 1 FROM exchange_rates WHERE currency=? AND rate_date>=?',(currency,str(effective))).fetchone():
                    continue
                c.execute('INSERT INTO exchange_rates(currency,rate_date,inr_per_unit,source) VALUES (?,?,?,?)',
                    (currency,str(effective),str(rate),'Frankfurter reference rates; '+RATE_URL))
                inserted += 1
            if inserted: self.repo.audit('LOAD_DEFAULT_RATES',f'{inserted} rates effective {effective}')
        return inserted, str(effective)

    def add_rate(self, currency, rate, effective_date, source):
        if currency not in CURRENCIES:
            raise ValueError('Unsupported currency.')
        value = decimal_value(rate)
        if value <= 0 or value > Decimal('1000000000'):
            raise ValueError('Exchange rate must be positive and at most 1,000,000,000.')
        if currency == 'INR' and value != 1:
            raise ValueError('INR exchange rate must be 1.')
        when = str(parse_date(effective_date))
        source = required(source, 'Rate source')
        with self.repo.connection as c:
            c.execute('INSERT INTO exchange_rates(currency,rate_date,inr_per_unit,source) VALUES (?,?,?,?)',
                      (currency,when,str(value),source))
            self.repo.audit('ADD_RATE', f'{currency} {value} {when} {source}')

    def rate(self, currency, as_of):
        if currency == 'INR':
            return Decimal(1)
        row = self.repo.connection.execute('''SELECT inr_per_unit FROM exchange_rates
            WHERE currency=? AND rate_date<=? ORDER BY rate_date DESC, rate_id DESC LIMIT 1''', (currency,as_of)).fetchone()
        return Decimal(row[0]) if row else None

    def convert(self, amount, currency, as_of, target='INR'):
        if currency not in CURRENCIES or target not in CURRENCIES:
            raise ValueError('Select a supported reporting currency.')
        if currency == target or amount == 0:
            return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        source_rate = self.rate(currency, as_of)
        target_rate = self.rate(target, as_of)
        if source_rate is None or target_rate is None:
            return None
        return (amount * source_rate / target_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
