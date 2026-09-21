"""Run python -m pytest -q. Every test uses isolated temporary storage."""
import csv
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from decimal import Decimal
import pytest
from database import InvoiceRepository
from invoice_service import InvoiceService
from currency import CurrencyConverter
from reports import ReportGenerator
from csv_handler import import_csv, export_csv
from validation import money

@pytest.fixture
def system(tmp_path):
    repo=InvoiceRepository(tmp_path/'test.db')
    yield repo,InvoiceService(repo),CurrencyConverter(repo)
    repo.close()

def sample(**overrides):
    data=dict(invoice_id='I1',supplier_id='S1',supplier_name='Supplier',invoice_number='N1',
              invoice_date='2024-01-01',invoice_due_date='2024-01-31',invoice_amount='100',currency='USD')
    data.update(overrides)
    return data

def test_partial_due_today_and_overdue(system):
    repo,service,conv=system
    service.add_invoice(sample())
    service.add_payment('I1','40','2024-01-20')
    inv=repo.list_invoices('2024-01-31')[0]
    assert inv.status=='Partially paid' and inv.balance_minor==6000
    assert inv.days_overdue('2024-01-31')==0
    assert inv.days_overdue('2024-02-03')==3

def test_payments_as_of_and_no_double_count(system):
    repo,service,conv=system
    service.add_invoice(sample())
    service.add_payment('I1','40','2024-01-20')
    service.add_payment('I1','60','2024-02-03')
    report=ReportGenerator(repo,conv)
    assert report.rows('2024-01-31')[0]['outstanding']=='60'
    rows=report.rows('2024-02-04')
    assert rows[0]['status']=='Paid' and rows[0]['paid_late_days']==3
    assert rows[0]['days_overdue']==0
    assert report.summaries(rows)[0]['invoiced']=='100'

def test_duplicate_and_atomic_rollback(system):
    repo,service,_=system
    service.add_invoice(sample())
    with pytest.raises(ValueError): service.add_invoice(sample(invoice_id='I2'))
    with pytest.raises(ValueError): service.add_invoice(sample(supplier_id='S2'))
    assert repo.connection.execute('SELECT COUNT(*) FROM suppliers').fetchone()[0]==1

@pytest.mark.parametrize('changes',[{'invoice_amount':'-1'},{'invoice_due_date':'bad'},
    {'invoice_due_date':'2023-01-01'},{'invoice_amount':'NaN'},{'supplier_name':''},
    {'currency':'INVALID'},{'invoice_amount':'1.0000000000001'},{'payment_amount':'101','payment_date':'2024-01-10'}])
def test_invalid_input(system,changes):
    with pytest.raises(ValueError): system[1].add_invoice(sample(**changes))

def test_jpy_precision():
    assert money('100','JPY')==100
    assert money('100.50','JPY')==Decimal('100.50')

@pytest.mark.parametrize('status,paid',[('paid','20'),('partial','0'),('pending','10'),('unknown','0')])
def test_inconsistent_source_status(system,status,paid):
    with pytest.raises(ValueError):
        system[1].add_invoice(sample(payment_status=status,payment_amount=paid,payment_date='2024-01-10' if paid!='0' else ''))

def test_overpayment_rejected(system):
    repo,service,_=system
    service.add_invoice(sample())
    with pytest.raises(ValueError): service.add_payment('I1','101','2024-01-10')
    assert repo.connection.execute('SELECT COUNT(*) FROM payments').fetchone()[0]==0

def test_delete_replace_cascade_and_audit(system):
    repo,service,_=system
    service.add_invoice(sample())
    service.add_payment('I1','10','2024-01-10')
    service.delete_invoice('I1')
    assert repo.connection.execute('SELECT COUNT(*) FROM payments').fetchone()[0]==0
    assert repo.connection.execute("SELECT COUNT(*) FROM audit WHERE action='DELETE_INVOICE'").fetchone()[0]==1
    service.add_invoice(sample(invoice_amount='200',supplier_name='Corrected supplier'))
    assert repo.list_invoices('2024-02-01')[0].amount_minor==20000

def test_delete_payment_restores_balance(system):
    repo,service,_=system
    service.add_invoice(sample()); service.add_payment('I1','100','2024-01-10')
    service.delete_payment(repo.connection.execute('SELECT payment_id FROM payments').fetchone()[0])
    assert repo.list_invoices('2024-02-01')[0].status=='Unpaid'

def test_cancelled_excluded(system):
    repo,service,conv=system
    service.add_invoice(sample(payment_status='cancelled'))
    row=repo.list_invoices('2024-02-01')[0]
    assert row.balance_minor==0 and row.days_overdue('2024-02-01')==0
    with pytest.raises(ValueError): service.add_payment('I1','1','2024-01-10')
    assert ReportGenerator(repo,conv).summaries(ReportGenerator(repo,conv).rows('2024-02-01'))==[]

def test_exchange_history_and_rounding(system):
    _,_,conv=system
    assert conv.rate('USD','2024-01-01') is None
    conv.add_rate('USD','85','2024-01-01','Test rate')
    conv.add_rate('USD','86','2024-02-01','Test rate')
    assert conv.convert(Decimal('10'),'USD','2024-01-31')==Decimal('850.00')
    conv.add_rate('USD','87','2024-02-01','Corrected test rate')
    assert conv.rate('USD','2024-02-01')==87
    assert conv.convert(Decimal('0.005'),'INR','2024-01-01')==Decimal('0.01')
    with pytest.raises(ValueError): conv.add_rate('INR','2','2024-01-01','invalid')

def test_import_partial_success(system,tmp_path):
    path=tmp_path/'in.csv'
    rows=[sample(),sample(invoice_id='I2',invoice_number='N2',invoice_amount='-1')]
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    count,issues=import_csv(path,system[1])
    assert count==1 and len(issues)==1 and issues[0]['result']=='Rejected'

def test_export_formula_safety(tmp_path):
    path=tmp_path/'report.csv'
    export_csv(path,[{'supplier':'=HYPERLINK("bad")','amount':'100'}])
    assert list(csv.DictReader(path.open(encoding='utf-8-sig')))[0]['supplier'].startswith("'=")

def test_storage_persists(tmp_path):
    path=tmp_path/'persist.db'; repo=InvoiceRepository(path)
    InvoiceService(repo).add_invoice(sample()); repo.close()
    repo=InvoiceRepository(path)
    assert repo.list_invoices('2024-02-01')[0].invoice_id=='I1'
    repo.close()

def test_excel_import_dates_blanks_and_bad_rows(system,tmp_path):
    from openpyxl import Workbook
    from datetime import datetime
    from csv_handler import import_file
    workbook=Workbook(); sheet=workbook.active
    row=sample(payment_amount='',payment_date='')
    headers=list(row)
    sheet.append(headers)
    values=list(row.values()); values[headers.index('invoice_date')]=datetime(2024,1,1)
    values[headers.index('invoice_amount')]=100
    sheet.append(values)
    sheet.append([None]*len(headers))
    bad=sample(invoice_id='I2',invoice_number='N2',invoice_amount='-10',payment_amount='',payment_date='')
    sheet.append([bad[k] for k in headers])
    sheet.append(['=1+1']+['']*(len(headers)-1))
    path=tmp_path/'invoices.xlsx'; workbook.save(path)
    count,issues=import_file(path,system[1])
    assert count==1 and len(issues)==2
    assert issues[0]['line']==4 and issues[1]['line']==5
    assert system[0].list_invoices('2024-02-01')[0].amount_minor==10000

def test_excel_missing_headers(system,tmp_path):
    from openpyxl import Workbook
    from csv_handler import import_file
    workbook=Workbook(); workbook.active.append(['Wrong heading'])
    path=tmp_path/'bad.xlsx'; workbook.save(path)
    with pytest.raises(ValueError,match='missing required columns'):
        import_file(path,system[1])

def test_default_rates_and_manual_override(system):
    import json
    payload=json.loads((Path(__file__).parent/'files'/'default_rates.json').read_text())
    repo,service,converter=system
    count,when=converter.load_defaults(payload)
    assert count==8
    assert converter.rate('USD',when)==Decimal(1)/Decimal('0.01044')
    converter.add_rate('USD','90',when,'Manual override')
    assert converter.load_defaults(payload)[0]==0
    assert converter.rate('USD',when)==90
    assert converter.rate('USD','2024-01-01') is None

def test_invalid_default_payload_atomic(system):
    import json
    payload=json.loads((Path(__file__).parent/'files'/'default_rates.json').read_text())
    payload['rates']['USD']=0
    with pytest.raises(ValueError): system[2].load_defaults(payload)
    assert system[0].connection.execute('SELECT COUNT(*) FROM exchange_rates').fetchone()[0]==0

@pytest.mark.parametrize('password',['delete','DELETE','','Delete '])
def test_clear_database_wrong_password_preserves_data(system,password):
    repo,service,_=system; service.add_invoice(sample())
    with pytest.raises(ValueError): service.clear_database(password)
    assert len(repo.list_invoices('2024-02-01'))==1

def test_clear_database_correct_password(system):
    repo,service,converter=system
    service.add_invoice(sample()); service.add_payment('I1','20','2024-01-05')
    converter.add_rate('USD','85','2024-01-01','Test')
    service.clear_database('Delete')
    for name in ('payments','invoices','suppliers','exchange_rates','audit'):
        assert repo.connection.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]==0
    service.add_invoice(sample())
    assert len(repo.list_invoices('2024-02-01'))==1

def test_numeric_sort_and_missing_last():
    from ui_helpers import sorted_table_rows
    rows=[('a','100'),('b','20'),('c','Rate missing')]
    assert [r[0] for r in sorted_table_rows(rows,'outstanding_inr')]==['b','a','c']
    assert [r[0] for r in sorted_table_rows(rows,'outstanding_inr',True)]==['a','b','c']
    assert sorted_table_rows([('a','2024-02-01'),('b','2024-01-01')],'due_date')[0][0]=='b'

def test_overdue_alert_only_unpaid_past_due(system):
    from ui_helpers import overdue_alert
    repo,service,converter=system
    service.add_invoice(sample())
    service.add_payment('I1','40','2024-01-20')
    report=ReportGenerator(repo,converter)
    assert overdue_alert(report.rows('2024-01-31'))[0]==()
    signature,text=overdue_alert(report.rows('2024-02-01'))
    assert len(signature)==1 and 'USD 60.00' in text
    service.add_payment('I1','60','2024-02-01')
    assert overdue_alert(report.rows('2024-02-02'))[0]==()

@pytest.mark.parametrize('currency',['JPY','INR','USD'])
def test_fractional_amounts_exact(system,currency):
    repo,service,converter=system
    service.add_invoice(sample(currency=currency,invoice_amount='135.50123'))
    service.add_payment('I1','0.10001','2024-01-10')
    service.add_payment('I1','0.20002','2024-01-11')
    inv=repo.list_invoices('2024-02-01')[0]
    assert inv.amount(inv.paid_minor)==Decimal('0.30003')
    assert inv.amount(inv.balance_minor)==Decimal('135.20120')
    service.add_payment('I1','135.20120','2024-02-01')
    assert repo.list_invoices('2024-02-01')[0].status=='Paid'

def test_all_source_amounts_import(system):
    count,issues=import_csv(Path(__file__).parent/'files'/'supplier-invoice-payment-timelines.csv',system[1])
    assert count==200
    assert len(issues)==1 and issues[0]['result']=='Imported with warning'
    invoice=next(i for i in system[0].list_invoices('2024-06-30') if i.invoice_id=='INV-00375')
    assert invoice.amount(invoice.amount_minor)==Decimal('135.5')
    assert invoice.status=='Cancelled' and invoice.balance_minor==0

def test_legacy_integer_database_migration(tmp_path):
    import sqlite3
    template=InvoiceRepository(':memory:')
    schemas=[r[0] for r in template.connection.execute("SELECT sql FROM sqlite_master WHERE type='table'")]
    template.close()
    path=tmp_path/'legacy.db'
    old=sqlite3.connect(path)
    for sql in schemas:
        old.execute(sql.replace('amount_minor TEXT NOT NULL','amount_minor INTEGER NOT NULL'))
    old.execute("INSERT INTO suppliers VALUES ('S1','Supplier','')")
    old.execute("INSERT INTO invoices(invoice_id,supplier_id,invoice_number,invoice_date,invoice_due_date,invoice_submission_date,currency,amount_minor) VALUES ('I1','S1','N1','2024-01-01','2024-01-31','2024-01-01','USD',10000)")
    old.execute("INSERT INTO payments(invoice_id,amount_minor,payment_date) VALUES ('I1',4000,'2024-01-10')")
    old.commit(); old.close()
    repo=InvoiceRepository(path)
    inv=repo.list_invoices('2024-02-01')[0]
    assert inv.amount(inv.amount_minor)==100 and inv.amount(inv.balance_minor)==60
    InvoiceService(repo).add_payment('I1','0.001','2024-01-11')
    assert repo.list_invoices('2024-02-01')[0].amount(repo.list_invoices('2024-02-01')[0].balance_minor)==Decimal('59.999')
    assert repo.connection.execute('PRAGMA foreign_key_check').fetchall()==[]
    repo.close()
    reopened=InvoiceRepository(path)
    assert reopened.list_invoices('2024-02-01')[0].paid_minor==Decimal('4000.100')
    reopened.close()

def test_reporting_currency_conversion(system):
    repo,service,converter=system
    service.add_invoice(sample())
    converter.add_rate('USD','80','2024-01-01','Test')
    converter.add_rate('EUR','100','2024-01-01','Test')
    report=ReportGenerator(repo,converter)
    row=report.rows('2024-02-01','EUR')[0]
    assert row['outstanding_converted']=='80.00'
    assert row['reporting_currency']=='EUR'
    assert row['currency']=='USD' and row['outstanding']=='100'
    assert report.rows('2024-02-01')[0]['outstanding_converted']=='8000.00'
    assert converter.convert(Decimal('100'),'INR','2024-02-01','USD')==Decimal('1.25')

def test_reporting_currency_same_missing_and_historical(system):
    converter=system[2]
    assert converter.convert(Decimal('135.50'),'JPY','2024-01-01','JPY')==Decimal('135.50')
    converter.add_rate('USD','80','2024-01-01','Test')
    converter.add_rate('EUR','100','2024-02-01','Test')
    assert converter.convert(Decimal('10'),'USD','2024-01-15','EUR') is None
    assert converter.convert(Decimal('10'),'USD','2024-02-01','EUR')==Decimal('8.00')
    with pytest.raises(ValueError): converter.convert(Decimal('10'),'USD','2024-02-01','XXX')

def test_selected_currency_export(system,tmp_path):
    repo,service,converter=system
    service.add_invoice(sample())
    path=tmp_path/'report.csv'
    export_csv(path,ReportGenerator(repo,converter).rows('2024-02-01','USD'))
    with path.open(encoding='utf-8-sig') as source:
        row=next(csv.DictReader(source))
    assert row['reporting_currency']=='USD' and row['outstanding_converted']=='100.00'
