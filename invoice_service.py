"""Validated, atomic domain operations shared by CSV and manual entry."""
import json
import logging
import sqlite3
from datetime import date
from decimal import Decimal
from validation import money, parse_date, required

class InvoiceService:
    def __init__(self, repository):
        self.repo = repository

    def add_invoice(self, data):
        d = dict(data)
        for field in ('invoice_id', 'supplier_id', 'supplier_name', 'invoice_number', 'currency'):
            d[field] = required(d.get(field), field.replace('_', ' ').title())
        d['currency'] = d['currency'].upper()
        issued = parse_date(d.get('invoice_date', ''))
        due = parse_date(d.get('invoice_due_date', ''))
        submitted = parse_date(d.get('invoice_submission_date') or d['invoice_date'])
        if due < issued or submitted < issued:
            raise ValueError('Due date and submission date cannot precede invoice date.')
        if issued > date.today() or submitted > date.today():
            raise ValueError('Invoice and submission dates cannot be in the future.')
        cancelled = str(d.get('payment_status', '')).lower() == 'cancelled'
        amount = money(d.get('invoice_amount', ''), d['currency'])
        paid = money(d.get('payment_amount') or '0', d['currency'])
        source_status = str(d.get('payment_status') or '').lower()
        if source_status not in ('', 'active', 'paid', 'pending', 'overdue', 'cancelled', 'partial'):
            raise ValueError('Unrecognized source payment status.')
        if source_status == 'paid' and paid != amount:
            raise ValueError('Source says paid but the recorded payment does not equal the invoice amount.')
        if source_status == 'partial' and not 0 < paid < amount:
            raise ValueError('Source says partial but the recorded amount is not a partial payment.')
        if source_status == 'pending' and paid:
            raise ValueError('Source says pending but a payment is recorded. Review the status.')
        if amount == 0 and not cancelled:
            raise ValueError('A non-cancelled invoice must have a positive amount.')
        if paid > amount or (cancelled and paid):
            raise ValueError('Overpayments and paid cancelled invoices require review.')
        if paid:
            payment_date = parse_date(d.get('payment_date', ''))
            if payment_date < issued or payment_date > date.today():
                raise ValueError('Payment date must be between invoice date and today.')
        elif d.get('payment_date'):
            raise ValueError('A payment date requires a positive payment amount.')
        try:
            with self.repo.connection as c:
                existing = c.execute('SELECT * FROM suppliers WHERE supplier_id=?', (d['supplier_id'],)).fetchone()
                if existing and (existing['supplier_name'] != d['supplier_name'] or
                                 existing['supplier_tax_id'] != (d.get('supplier_tax_id') or '')):
                    raise ValueError('Supplier ID already has different name/tax details. Review the supplier identity.')
                c.execute('INSERT OR IGNORE INTO suppliers VALUES (?,?,?)',
                          (d['supplier_id'], d['supplier_name'], d.get('supplier_tax_id') or ''))
                c.execute('''INSERT INTO invoices(invoice_id,supplier_id,invoice_number,invoice_date,
                    invoice_due_date,invoice_submission_date,currency,amount_minor,cancelled,
                    source_status,source_days,source_overdue) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (d['invoice_id'],d['supplier_id'],d['invoice_number'],str(issued),str(due),str(submitted),
                     d['currency'],str(amount),int(cancelled),d.get('payment_status',''),d.get('days_to_payment',''),d.get('is_overdue','')))
                if paid:
                    c.execute('INSERT INTO payments(invoice_id,amount_minor,payment_date,payment_method,payment_reference) VALUES (?,?,?,?,?)',
                              (d['invoice_id'],str(paid),d['payment_date'],d.get('payment_method',''),d.get('payment_reference','')))
                self.repo.audit('ADD_INVOICE', json.dumps(d))
        except sqlite3.IntegrityError:
            raise ValueError('Invoice ID or supplier/invoice-number combination already exists.') from None
        logging.info('Added invoice %s', d['invoice_id'])

    def add_payment(self, invoice_id, amount, payment_date, method='', reference=''):
        with self.repo.connection as c:
            row = c.execute('SELECT * FROM invoices WHERE invoice_id=?', (invoice_id,)).fetchone()
            if not row:
                raise ValueError('Select an existing invoice.')
            if row['cancelled']:
                raise ValueError('Cannot pay a cancelled invoice.')
            value = money(amount, row['currency'])
            when = parse_date(payment_date)
            if when < parse_date(row['invoice_date']) or when > date.today():
                raise ValueError('Payment date must be between invoice date and today.')
            paid = sum((Decimal(p[0]) for p in c.execute('SELECT amount_minor FROM payments WHERE invoice_id=?',(invoice_id,))),Decimal(0))
            if value <= 0 or value + paid > Decimal(row['amount_minor']):
                raise ValueError('Payment must be positive and cannot exceed the outstanding balance.')
            c.execute('INSERT INTO payments(invoice_id,amount_minor,payment_date,payment_method,payment_reference) VALUES (?,?,?,?,?)',
                      (invoice_id,str(value),str(when),method,reference))
            self.repo.audit('ADD_PAYMENT', json.dumps([invoice_id,str(value),str(when),method,reference]))
        logging.info('Added payment for %s', invoice_id)

    def delete_invoice(self, invoice_id):
        with self.repo.connection as c:
            row = c.execute('SELECT * FROM invoices WHERE invoice_id=?', (invoice_id,)).fetchone()
            if not row:
                raise ValueError('Invoice not found.')
            payments = [dict(r) for r in c.execute('SELECT * FROM payments WHERE invoice_id=?', (invoice_id,))]
            self.repo.audit('DELETE_INVOICE', json.dumps({'invoice':dict(row),'payments':payments}))
            c.execute('DELETE FROM invoices WHERE invoice_id=?', (invoice_id,))
            c.execute('DELETE FROM suppliers WHERE supplier_id=? AND NOT EXISTS (SELECT 1 FROM invoices WHERE supplier_id=?)',
                      (row['supplier_id'], row['supplier_id']))
        logging.info('Deleted invoice %s and linked payments', invoice_id)

    def delete_payment(self, payment_id):
        with self.repo.connection as c:
            row = c.execute('SELECT * FROM payments WHERE payment_id=?', (payment_id,)).fetchone()
            if not row:
                raise ValueError('Payment not found.')
            self.repo.audit('DELETE_PAYMENT', json.dumps(dict(row)))
            c.execute('DELETE FROM payments WHERE payment_id=?', (payment_id,))
        logging.info('Deleted payment %s', payment_id)

    def clear_database(self, password):
        if password != 'Delete':
            raise ValueError('Incorrect password. Enter Delete exactly, including the capital D.')
        with self.repo.connection as c:
            for table in ('payments','invoices','suppliers','exchange_rates','audit'):
                c.execute(f'DELETE FROM {table}')
        logging.warning('User cleared all database records. CSV files and external logs were retained.')
