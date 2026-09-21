"""Pure helpers for numeric table sorting and today's overdue alerts."""
from decimal import Decimal, InvalidOperation

NUMERIC_COLUMNS = {'amount','paid','outstanding','invoiced','overdue','outstanding_inr','outstanding_converted',
                   'days_overdue','paid_late_days','payment_id','inr_per_unit'}

def sorted_table_rows(rows, column, descending=False):
    """Rows are (item id, displayed value); missing values stay last in either order."""
    valid, missing = [], []
    for item, value in rows:
        if column in NUMERIC_COLUMNS:
            try:
                key = Decimal(str(value).replace(',',''))
                if not key.is_finite(): raise InvalidOperation
            except InvalidOperation:
                missing.append((item,value)); continue
        else:
            key = str(value).casefold()
        valid.append((item,value,key))
    return [(item,value) for item,value,key in sorted(valid,key=lambda row:row[2],reverse=descending)] + missing

def overdue_alert(rows):
    overdue = [row for row in rows if row['days_overdue'] > 0]
    totals = {}
    for row in overdue:
        totals[row['currency']] = totals.get(row['currency'],Decimal(0)) + Decimal(row['outstanding'])
    signature = tuple(sorted((r['invoice_id'],r['outstanding'],r['days_overdue']) for r in overdue))
    amounts = ' | '.join(f'{currency} {amount:,.2f}' for currency,amount in sorted(totals.items()))
    return signature, (f'{len(overdue)} overdue invoices today: {amounts}' if overdue else 'No overdue balances today.')
