"""Reports aggregate each invoice once and never mix original currencies."""
from collections import defaultdict
from decimal import Decimal
from validation import parse_date, CURRENCIES

class ReportGenerator:
    def __init__(self, repository, converter):
        self.repo, self.converter = repository, converter

    def rows(self, as_of, target='INR'):
        parse_date(as_of)
        if target not in CURRENCIES:
            raise ValueError('Select a supported reporting currency.')
        result = []
        for i in self.repo.list_invoices(as_of):
            latest = self.repo.connection.execute('SELECT MAX(payment_date) FROM payments WHERE invoice_id=? AND payment_date<=?', (i.invoice_id,as_of)).fetchone()[0]
            late = max(0,(parse_date(latest)-parse_date(i.invoice_due_date)).days) if i.status=='Paid' and latest else 0
            amount, paid, balance = (i.amount(v) for v in (i.amount_minor,i.paid_minor,i.balance_minor))
            converted = self.converter.convert(balance,i.currency,as_of,target)
            result.append(dict(invoice_id=i.invoice_id,supplier_id=i.supplier_id,supplier=i.supplier_name,
                invoice_number=i.invoice_number,currency=i.currency,amount=str(amount),paid=str(paid),
                outstanding=str(balance),due_date=i.invoice_due_date,status=i.status,
                days_overdue=i.days_overdue(as_of),paid_late_days=late,
                outstanding_converted=str(converted) if converted is not None else 'Rate missing',
                reporting_currency=target,report_date=as_of))
        return result

    def summaries(self, rows):
        groups = defaultdict(lambda: {'invoiced':Decimal(0),'paid':Decimal(0),'outstanding':Decimal(0),'overdue':Decimal(0)})
        for r in rows:
            if r['status']=='Cancelled':
                continue
            group = groups[(r['supplier_id'],r['supplier'],r['currency'])]
            for dest,src in [('invoiced','amount'),('paid','paid'),('outstanding','outstanding')]:
                group[dest] += Decimal(r[src])
            if r['days_overdue']:
                group['overdue'] += Decimal(r['outstanding'])
        return [dict(supplier_id=k[0],supplier=k[1],currency=k[2],**{a:str(b) for a,b in v.items()}) for k,v in sorted(groups.items())]
