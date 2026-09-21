"""SQLite persistence. SQL values always use placeholders."""
import sqlite3
import re
from dataclasses import fields
from models import Invoice
from decimal import Decimal

class InvoiceRepository:
    def __init__(self, path):
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys = ON')
        self.connection.executescript('''
        CREATE TABLE IF NOT EXISTS suppliers (
            supplier_id TEXT PRIMARY KEY, supplier_name TEXT NOT NULL, supplier_tax_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY, supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
            invoice_number TEXT NOT NULL, invoice_date TEXT NOT NULL, invoice_due_date TEXT NOT NULL,
            invoice_submission_date TEXT NOT NULL, currency TEXT NOT NULL,
            amount_minor TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
            source_status TEXT DEFAULT '', source_days TEXT DEFAULT '', source_overdue TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(supplier_id, invoice_number));
        CREATE TABLE IF NOT EXISTS payments (
            payment_id INTEGER PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id) ON DELETE CASCADE,
            amount_minor TEXT NOT NULL, payment_date TEXT NOT NULL,
            payment_method TEXT NOT NULL DEFAULT '', payment_reference TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS exchange_rates (
            rate_id INTEGER PRIMARY KEY, currency TEXT NOT NULL, rate_date TEXT NOT NULL,
            inr_per_unit TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS audit (
            audit_id INTEGER PRIMARY KEY, timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            action TEXT NOT NULL, details TEXT NOT NULL);
        ''')
        self._migrate_decimal_storage()

    def _migrate_decimal_storage(self):
        """Preserve legacy scaled integers as exact decimal text, including all keys."""
        tables = [table for table in ('invoices','payments')
                  if any(row['name']=='amount_minor' and row['type']=='INTEGER'
                         for row in self.connection.execute(f'PRAGMA table_info({table})'))]
        if not tables: return
        self.connection.execute('PRAGMA foreign_keys = OFF')
        try:
            self.connection.execute('BEGIN')
            for table in tables:
                sql=self.connection.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
                replacement=sql.replace(f'CREATE TABLE {table}',f'CREATE TABLE {table}_decimal',1)
                replacement=re.sub(r'amount_minor INTEGER NOT NULL(?: CHECK\(amount_minor >=? 0\))?',
                                   'amount_minor TEXT NOT NULL',replacement)
                self.connection.execute(replacement)
                self.connection.execute(f'INSERT INTO {table}_decimal SELECT * FROM {table}')
                self.connection.execute(f'DROP TABLE {table}')
                self.connection.execute(f'ALTER TABLE {table}_decimal RENAME TO {table}')
            if self.connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Database migration failed its relationship checks.')
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute('PRAGMA foreign_keys = ON')

    def audit(self, action, details):
        self.connection.execute('INSERT INTO audit(action, details) VALUES (?,?)', (action, details))

    def list_invoices(self, as_of):
        rows = self.connection.execute('''
            SELECT i.*, s.supplier_name
            FROM invoices i JOIN suppliers s USING(supplier_id)
            WHERE i.invoice_date <= ? ORDER BY i.invoice_due_date, i.invoice_id
        ''', (as_of,)).fetchall()
        paid={}
        for payment in self.connection.execute('SELECT invoice_id,amount_minor FROM payments WHERE payment_date<=?',(as_of,)):
            key=payment['invoice_id']
            paid[key]=paid.get(key,Decimal(0))+Decimal(payment['amount_minor'])
        names = [f.name for f in fields(Invoice)]
        result=[]
        for row in rows:
            data=dict(row)
            data['amount_minor']=Decimal(data['amount_minor'])
            data['paid_minor']=paid.get(data['invoice_id'],Decimal(0))
            result.append(Invoice(**{k:data[k] for k in names}))
        return result

    def close(self):
        self.connection.close()
