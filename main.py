"""Run with python main.py. The entire interface is Python/Tkinter."""
from pathlib import Path
from datetime import date
from decimal import Decimal
import logging
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from database import InvoiceRepository
from invoice_service import InvoiceService
from currency import CurrencyConverter, fetch_latest_rates
from ui_helpers import sorted_table_rows, overdue_alert
import json
import threading
import queue
from reports import ReportGenerator
from csv_handler import import_file, export_csv
from validation import CURRENCIES, parse_date

BASE = Path(__file__).resolve().parent
FILES = BASE / 'files'

class InvoiceApp(tk.Tk):
    def __init__(self, database_path=None, online_rates=True):
        
        import sys
        if sys.platform == 'win32':
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        super().__init__()
        FILES.mkdir(exist_ok=True)
        logging.basicConfig(filename=FILES/'application.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
        self.repo = InvoiceRepository(database_path or FILES/'vendor_invoices.db')
        self.service = InvoiceService(self.repo)
        self.converter = CurrencyConverter(self.repo)
        self.reporter = ReportGenerator(self.repo,self.converter)
        self.sort_states = {}
        self.alert_signature = None
        self.rate_results = queue.Queue()
        self.rate_loading = False
        self.closed = False
        self.title('Vendor Invoice Intelligence System')
        self.geometry('1240x820')
        self.minsize(1020,740)
        self.configure(bg='#f3f6fa')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10))
        style.configure('TFrame',background='#f3f6fa')
        style.configure('TLabel',background='#f3f6fa',foreground='#213547')
        style.configure('Title.TLabel',font=('Segoe UI',23,'bold'))
        style.configure('TButton',padding=(12,7))
        style.configure('Treeview',rowheight=29,font=('Segoe UI',10))
        style.configure('Treeview.Heading',font=('Segoe UI',10,'bold'),background='#dce6f1')
        style.configure('TNotebook.Tab',padding=(15,10))
        header=ttk.Frame(self,padding=20); header.pack(fill='x')
        ttk.Label(header,text='Vendor Invoice Intelligence',style='Title.TLabel').pack(anchor='w')
        ttk.Label(header,text='Record invoices. Track payments. Understand what is due.').pack(anchor='w',pady=(4,0))
        controls=ttk.Frame(self,padding=(20,0,20,12)); controls.pack(fill='x')
        ttk.Label(controls,text='Reporting date (YYYY-MM-DD)').pack(side='left')
        self.as_of=tk.StringVar(value=date.today().isoformat())
        ttk.Entry(controls,textvariable=self.as_of,width=13).pack(side='left',padx=10)
        ttk.Button(controls,text='Refresh reports',command=lambda:self.safe(self.refresh)).pack(side='left')
        ttk.Label(controls,text='Report currency').pack(side='left',padx=(20,8))
        self.reporting_currency=tk.StringVar(value='INR')
        currency_selector=ttk.Combobox(controls,textvariable=self.reporting_currency,values=CURRENCIES,state='readonly',width=7)
        currency_selector.pack(side='left')
        currency_selector.bind('<<ComboboxSelected>>',lambda event:self.safe(self.refresh))
        self.tabs=ttk.Notebook(self); self.tabs.pack(fill='both',expand=True,padx=20,pady=5)
        self.dashboard=self.page('Overview')
        self.entry_page=self.page('Add invoice')
        self.view_page=self.page('Invoices & payments')
        self.rate_page=self.page('Exchange rates')
        self.io_page=self.page('Import & export')
        self.notice=tk.StringVar(value='Ready. Import the sample CSV or add your first invoice.')
        ttk.Label(self,textvariable=self.notice,padding=(20,12),wraplength=1100).pack(side='bottom',fill='x',before=self.tabs)
        self.build_dashboard(); self.build_entry(); self.build_view(); self.build_rates(); self.build_io()
        self.load_cached_defaults()
        self.refresh()
        self.after(500,self.check_overdue)
        if online_rates: self.after(150,self.update_online_rates)
        self.protocol('WM_DELETE_WINDOW',self.close)

    def load_cached_defaults(self):
        try:
            payload=json.loads((FILES/'default_rates.json').read_text(encoding='utf-8'))
            _,when=self.converter.load_defaults(payload)
            self.rate_notice.set(f'Bundled reference rates dated {when}. Checking online for newer rates; manual overrides are retained.')
        except (OSError,ValueError,KeyError) as error:
            self.rate_notice.set(f'Default rates unavailable: {error}. You can enter rates manually.')

    def update_online_rates(self):
        if self.rate_loading: return
        self.rate_loading=True
        self.rate_notice.set('Checking latest published exchange rates…')
        def work():
            try: self.rate_results.put((fetch_latest_rates(),None))
            except Exception as error: self.rate_results.put((None,str(error)))
        threading.Thread(target=work,daemon=True).start()
        self.after(100,self.poll_rates)

    def poll_rates(self):
        if self.closed: return
        try: payload,error=self.rate_results.get_nowait()
        except queue.Empty:
            self.after(100,self.poll_rates); return
        self.rate_loading=False
        if error:
            self.rate_notice.set('Online refresh unavailable. Using saved, dated rates; manual updates remain available.')
            logging.warning('Rate provider unavailable: %s',error)
            return
        try:
            count,when=self.converter.load_defaults(payload)
            self.rate_notice.set(f'Latest published reference rates: {when}. Added {count}; existing same-date/newer settings retained.')
            self.refresh()
        except (ValueError,sqlite3.Error) as error:
            self.rate_notice.set(f'Rates were not changed: {error}')

    def check_overdue(self):
        if self.closed: return
        try:
            self.update_alerts()
        finally:
            self.after(60000,self.check_overdue)

    def update_alerts(self):
        signature,text=overdue_alert(self.reporter.rows(date.today().isoformat()))
        self.alert_text.set(text)
        if signature and signature != self.alert_signature:
            self.alert_signature=signature
            self.bell()
            messagebox.showwarning('Overdue payment alert',text+'\n\nOpen overdue invoices to review the balances.',parent=self)
        else:
            self.alert_signature=signature

    def open_overdue(self):
        self.as_of.set(date.today().isoformat()); self.status_filter.set('Overdue'); self.search.set('')
        self.tabs.select(self.view_page); self.refresh()

    def sort_table(self,tree,column,toggle=True):
        previous=self.sort_states.get(tree)
        descending=not previous[1] if toggle and previous and previous[0]==column else (previous[1] if not toggle and previous else False)
        self.sort_states[tree]=(column,descending)
        rows=[(item,tree.set(item,column)) for item in tree.get_children()]
        for index,(item,_) in enumerate(sorted_table_rows(rows,column,descending)):
            tree.move(item,'',index)
        for col in tree['columns']:
            label=self.column_label(col)
            tree.heading(col,text=label+(' ▼' if descending else ' ▲') if col==column else label)

    def column_label(self,column):
        if column=='outstanding_converted':
            return 'Outstanding '+self.reporting_currency.get()
        return column.replace('_',' ').title()

    def page(self,name):
        p=ttk.Frame(self.tabs,padding=16); self.tabs.add(p,text=name); return p

    def safe(self,action):
        try:
            action()
        except (ValueError,OSError,sqlite3.Error) as error:
            logging.warning('%s',error)
            messagebox.showerror('Please check these details',str(error),parent=self)

    def table(self,parent,columns,height=10):
        box=ttk.Frame(parent); box.pack(fill='both',expand=True,pady=8)
        tree=ttk.Treeview(box,columns=columns,show='headings',height=height,selectmode='browse')
        for col in columns:
            tree.heading(col,text=self.column_label(col),command=lambda c=col,t=tree:self.sort_table(t,c))
            widths={'supplier':220,'invoice_number':180,'days_overdue':145,'paid_late_days':150,'outstanding_converted':185,'payment_reference':190,'payment_method':160,'inr_per_unit':160,'source':450,'created_at':200}
            tree.column(col,width=widths.get(col,130),minwidth=100,stretch=False)
        ys=ttk.Scrollbar(box,orient='vertical',command=tree.yview)
        xs=ttk.Scrollbar(box,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        tree.grid(row=0,column=0,sticky='nsew'); ys.grid(row=0,column=1,sticky='ns'); xs.grid(row=1,column=0,sticky='ew')
        box.rowconfigure(0,weight=1); box.columnconfigure(0,weight=1)
        tree.tag_configure('overdue',background='#fff0e9')
        tree.tag_configure('paid',background='#e9f6ef')
        return tree

    def fill(self,tree,rows):
        tree.delete(*tree.get_children())
        for r in rows:
            tag='overdue' if r.get('days_overdue',0) else ('paid' if r.get('status')=='Paid' else '')
            tree.insert('', 'end',values=[r.get(c,'') for c in tree['columns']],tags=(tag,))
        if tree in self.sort_states:
            self.sort_table(tree,self.sort_states[tree][0],toggle=False)

    def build_dashboard(self):
        self.alert_text=tk.StringVar(value='Checking overdue balances…')
        ttk.Label(self.dashboard,textvariable=self.alert_text,foreground='#a03018',wraplength=1050).pack(anchor='w')
        ttk.Button(self.dashboard,text='Open overdue invoices',command=self.open_overdue).pack(anchor='w',pady=4)
        self.metrics=tk.StringVar()
        ttk.Label(self.dashboard,textvariable=self.metrics,font=('Segoe UI',13,'bold'),wraplength=1080).pack(anchor='w',pady=8)
        ttk.Label(self.dashboard,text='Vendor totals by original currency • Cancelled invoices excluded').pack(anchor='w')
        self.summary_tree=self.table(self.dashboard,('supplier_id','supplier','currency','invoiced','paid','outstanding','overdue'))
        ttk.Label(self.dashboard,text='Historical views use recorded invoices and payments up to the reporting date. Cancellation history before import is unavailable.',wraplength=1050).pack(anchor='w')

    def fields(self,parent,specs):
        result={}
        for index,(key,label,default,options) in enumerate(specs):
            row=index//2; col=(index%2)*2
            ttk.Label(parent,text=label).grid(row=row,column=col,sticky='w',padx=(0,10),pady=5)
            var=tk.StringVar(value=default); result[key]=var
            widget=ttk.Combobox(parent,textvariable=var,values=options,state='readonly',width=25) if options else ttk.Entry(parent,textvariable=var,width=28)
            widget.grid(row=row,column=col+1,sticky='ew',padx=(0,24),pady=5)
        parent.columnconfigure(1,weight=1); parent.columnconfigure(3,weight=1)
        return result

    def build_entry(self):
        ttk.Label(self.entry_page,text='Enter details from your invoice',font=('Segoe UI',15,'bold')).pack(anchor='w')
        ttk.Label(self.entry_page,text='* Required. Dates: YYYY-MM-DD. Balances and status are calculated automatically.').pack(anchor='w',pady=5)
        form=ttk.Frame(self.entry_page); form.pack(fill='x')
        today=date.today().isoformat()
        self.invoice_fields=self.fields(form,[
            ('invoice_id','Invoice ID *','',''),('invoice_number','Invoice number *','',''),
            ('supplier_id','Supplier ID *','',''),('supplier_name','Supplier name *','',''),
            ('supplier_tax_id','Supplier tax ID','',''),('currency','Currency *','INR',CURRENCIES),
            ('invoice_date','Invoice date *',today,''),('invoice_due_date','Due date *',today,''),
            ('invoice_submission_date','Submission date *',today,''),('invoice_amount','Invoice amount *','',''),
            ('payment_amount','Initial amount paid','0',''),('payment_date','Initial payment date','',''),
            ('payment_method','Payment method','',('', 'bank_transfer','cash','check','credit_card','other')),
            ('payment_reference','Payment reference','',''),
            ('payment_status','Invoice state','active',('active','cancelled'))])
        entry_actions=ttk.Frame(self.entry_page); entry_actions.pack(fill='x',pady=12)
        ttk.Button(entry_actions,text='Save new invoice',command=lambda:self.safe(self.save_invoice)).pack(side='left')
        ttk.Button(entry_actions,text='Import CSV or Excel',command=lambda:self.safe(self.choose_import)).pack(side='left',padx=12)
        ttk.Label(self.entry_page,text='Saved records are read-only. Correct an error by deleting the record and adding its replacement.',wraplength=1000).pack(anchor='w')

    def save_invoice(self):
        self.service.add_invoice({k:v.get() for k,v in self.invoice_fields.items()})
        self.refresh(); self.notice.set('Invoice saved successfully.')
        for k in ('invoice_id','invoice_number','invoice_amount','payment_date','payment_reference'):
            self.invoice_fields[k].set('')
        self.invoice_fields['payment_amount'].set('0')
        messagebox.showinfo('Invoice saved','Your invoice has been stored.',parent=self)

    def build_view(self):
        filters=ttk.Frame(self.view_page); filters.pack(fill='x')
        ttk.Label(filters,text='Search supplier or invoice').pack(side='left')
        self.search=tk.StringVar(); ttk.Entry(filters,textvariable=self.search,width=27).pack(side='left',padx=8)
        self.status_filter=tk.StringVar(value='All')
        ttk.Combobox(filters,textvariable=self.status_filter,values=('All','Paid','Unpaid','Partially paid','Overdue','Cancelled'),state='readonly',width=16).pack(side='left')
        ttk.Button(filters,text='Apply',command=lambda:self.safe(self.refresh)).pack(side='left',padx=8)
        cols=('invoice_id','status','due_date','outstanding','currency','supplier','amount','paid','invoice_number','days_overdue','paid_late_days','outstanding_converted')
        self.invoice_tree=self.table(self.view_page,cols,7)
        self.invoice_tree.bind('<<TreeviewSelect>>',lambda event:self.show_payments())
        actions=ttk.Frame(self.view_page); actions.pack(fill='x')
        ttk.Button(actions,text='View details',command=lambda:self.safe(self.details_dialog)).pack(side='left',padx=(0,10))
        ttk.Button(actions,text='Add payment to selected invoice',command=lambda:self.safe(self.payment_dialog)).pack(side='left')
        ttk.Button(actions,text='Delete selected invoice',command=lambda:self.safe(self.delete_invoice)).pack(side='left',padx=10)
        ttk.Button(actions,text='Delete selected payment',command=lambda:self.safe(self.delete_payment)).pack(side='left')
        ttk.Label(self.view_page,text='Payments for the selected invoice (through reporting date)').pack(anchor='w',pady=(12,0))
        self.payment_tree=self.table(self.view_page,('payment_id','payment_date','amount','currency','payment_method','payment_reference'),3)

    def selected_invoice(self):
        selection=self.invoice_tree.selection()
        if not selection:
            raise ValueError('Select an invoice from the table first.')
        return self.invoice_tree.item(selection[0],'values')[0]

    def show_payments(self):
        self.payment_tree.delete(*self.payment_tree.get_children())
        if not self.invoice_tree.selection(): return
        inv=self.selected_invoice()
        rows=self.repo.connection.execute('''SELECT p.*,i.currency FROM payments p JOIN invoices i USING(invoice_id)
            WHERE invoice_id=? AND payment_date<=? ORDER BY payment_date,payment_id''',(inv,self.as_of.get())).fetchall()
        prepared=[]
        for row in rows:
            r=dict(row); r['amount']=str(Decimal(r['amount_minor'])/(1 if r['currency']=='JPY' else 100)); prepared.append(r)
        self.fill(self.payment_tree,prepared)

    def payment_dialog(self):
        inv=self.selected_invoice()
        window=tk.Toplevel(self); window.title(f'Add payment • {inv}'); window.transient(self); window.grab_set()
        frame=ttk.Frame(window,padding=20); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Enter this payment only, not the cumulative paid amount.').pack(anchor='w')
        currency=self.repo.connection.execute('SELECT currency FROM invoices WHERE invoice_id=?',(inv,)).fetchone()[0]
        ttk.Label(frame,text=f'Invoice currency: {currency}').pack(anchor='w',pady=5)
        fields=ttk.Frame(frame); fields.pack()
        vals=self.fields(fields,[('amount','Payment amount','',''),('date','Payment date',date.today().isoformat(),''),('method','Method','bank_transfer',('bank_transfer','cash','check','credit_card','other')),('reference','Reference','','')])
        def save():
            self.service.add_payment(inv,vals['amount'].get(),vals['date'].get(),vals['method'].get(),vals['reference'].get())
            window.destroy(); self.refresh(); self.notice.set('Payment saved.')
        ttk.Button(frame,text='Save payment',command=lambda:self.safe(save)).pack(anchor='w',pady=10)

    def details_dialog(self):
        inv=self.selected_invoice()
        row=self.repo.connection.execute('SELECT i.*,s.supplier_name,s.supplier_tax_id FROM invoices i JOIN suppliers s USING(supplier_id) WHERE invoice_id=?',(inv,)).fetchone()
        window=tk.Toplevel(self); window.title(f'Invoice details • {inv}'); window.geometry('650x650'); window.transient(self)
        frame=ttk.Frame(window,padding=20); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Saved details • Read only',font=('Segoe UI',15,'bold')).pack(anchor='w')
        text=tk.Text(frame,wrap='word',font=('Segoe UI',11),padx=14,pady=14)
        text.pack(fill='both',expand=True,pady=10)
        labels={'amount_minor':'Amount in minor currency units','source_status':'Original source status','source_days':'Original source payment duration','source_overdue':'Original source overdue flag'}
        for key in row.keys():
            text.insert('end',f'{labels.get(key,key.replace("_"," ").title())}: {row[key]}\n\n')
        text.configure(state='disabled')
        ttk.Button(frame,text='Close',command=window.destroy).pack(anchor='e')

    def delete_invoice(self):
        inv=self.selected_invoice()
        if messagebox.askyesno('Delete invoice?',f'Delete {inv} and ALL its linked payments?\nAn audit record will remain. You can then add a corrected replacement.',parent=self):
            self.service.delete_invoice(inv); self.refresh(); self.notice.set('Invoice and linked payments deleted.')

    def delete_payment(self):
        selected=self.payment_tree.selection()
        if not selected: raise ValueError('Select a payment first.')
        pid=self.payment_tree.item(selected[0],'values')[0]
        if messagebox.askyesno('Delete payment?','Delete this payment? The invoice balance will increase. An audit record will remain.',parent=self):
            self.service.delete_payment(pid); self.refresh(); self.notice.set('Payment deleted. Add a corrected payment if needed.')

    def build_rates(self):
        ttk.Label(self.rate_page,text='Update exchange rates',font=('Segoe UI',15,'bold')).pack(anchor='w')
        ttk.Label(self.rate_page,text='Latest published reference rates load by default. Enter INR for ONE currency unit to override a rate.',wraplength=1000).pack(anchor='w',pady=8)
        self.rate_notice=tk.StringVar()
        ttk.Label(self.rate_page,textvariable=self.rate_notice,wraplength=1050).pack(anchor='w')
        ttk.Button(self.rate_page,text='Refresh online rates',command=self.update_online_rates).pack(anchor='w',pady=5)
        form=ttk.Frame(self.rate_page); form.pack(fill='x')
        self.rate_fields=self.fields(form,[('currency','Currency','USD',CURRENCIES),('rate','INR per unit','',''),('date','Effective date',date.today().isoformat(),''),('source','Source / reference','','')])
        ttk.Button(self.rate_page,text='Save new rate',command=lambda:self.safe(self.save_rate)).pack(anchor='w',pady=10)
        ttk.Label(self.rate_page,text='Reports use the latest effective rate on or before the reporting date. For the same date, the newest entry wins. INR = 1.').pack(anchor='w')
        self.rate_tree=self.table(self.rate_page,('currency','rate_date','inr_per_unit','source','created_at'))

    def save_rate(self):
        f=self.rate_fields
        self.converter.add_rate(f['currency'].get(),f['rate'].get(),f['date'].get(),f['source'].get())
        self.refresh(); self.notice.set('Exchange rate saved; reports recalculated.')

    def build_io(self):
        ttk.Label(self.io_page,text='Bring in data and share reports',font=('Segoe UI',15,'bold')).pack(anchor='w')
        ttk.Label(self.io_page,text='Imports do not overwrite existing invoices. Invalid rows are rejected and recorded in a CSV report.',wraplength=1000).pack(anchor='w',pady=10)
        ttk.Button(self.io_page,text='Import supplied sample dataset',command=lambda:self.safe(lambda:self.run_import(FILES/'supplier-invoice-payment-timelines.csv'))).pack(anchor='w',pady=6)
        ttk.Button(self.io_page,text='Import CSV or Excel',command=lambda:self.safe(self.choose_import)).pack(anchor='w',pady=6)
        ttk.Button(self.io_page,text='Export invoice report',command=lambda:self.safe(lambda:self.export_report(False))).pack(anchor='w',pady=6)
        ttk.Button(self.io_page,text='Export vendor summary',command=lambda:self.safe(lambda:self.export_report(True))).pack(anchor='w',pady=6)
        ttk.Button(self.io_page,text='Clear all records / clean database',command=lambda:self.safe(self.clear_all)).pack(anchor='w',pady=10)
        self.import_result=tk.StringVar(value='No import has run in this session.')
        ttk.Label(self.io_page,textvariable=self.import_result,wraplength=950).pack(anchor='w',pady=20)
        ttk.Label(self.io_page,text='Exchange rates are entered in their own tab. Exported reports use the reporting date above.\nInvoice export includes calculated results; use the supplied input schema for imports.',wraplength=1000).pack(anchor='w')

    def choose_import(self):
        path=filedialog.askopenfilename(parent=self,title='Import invoices from CSV or Excel',filetypes=[('Invoice files','*.csv *.xlsx'),('CSV files','*.csv'),('Excel workbooks','*.xlsx')])
        if path: self.run_import(path)

    def clear_all(self):
        password=simpledialog.askstring('Clear database',
            'This deletes ALL invoices, payments, suppliers, exchange-rate history and database audit records.\nCSV files and log files remain. Default rates will then reload.\n\nEnter password Delete to continue:',show='*',parent=self)
        if password is None: return
        self.service.clear_database(password)
        self.load_cached_defaults()
        self.search.set(''); self.status_filter.set('All'); self.alert_signature=None
        self.refresh(); self.import_result.set('Database cleared. Import a file or add a new invoice.')
        self.notice.set('Database cleared; default reference rates restored. CSV files and logs were retained.')

    def run_import(self,path):
        count,issues=import_file(path,self.service)
        issue_path=FILES/'import_issues.csv'
        if issues: export_csv(issue_path,issues)
        rejected=sum(r['result']=='Rejected' for r in issues)
        self.import_result.set(f'Imported {count} invoices. Rejected {rejected}. Warnings {len(issues)-rejected}.' + (f' Details: {issue_path}' if issues else ''))
        self.refresh(); self.notice.set('Import complete. See Import & export for results.')
        messagebox.showinfo('Import complete',self.import_result.get(),parent=self)

    def export_report(self,summary):
        rows=self.reporter.rows(self.as_of.get(),self.reporting_currency.get())
        if summary: rows=self.reporter.summaries(rows)
        if not rows: raise ValueError('No invoices to export for this reporting date.')
        path=filedialog.asksaveasfilename(parent=self,initialdir=FILES,initialfile='vendor_summary.csv' if summary else 'invoice_report.csv',defaultextension='.csv',filetypes=[('CSV files','*.csv')])
        if path:
            export_csv(path,rows); self.notice.set(f'Report saved: {path}')

    def refresh(self):
        as_of=str(parse_date(self.as_of.get()))
        if parse_date(as_of)>date.today(): raise ValueError('Reporting date cannot be in the future.')
        target=self.reporting_currency.get()
        rows=self.reporter.rows(as_of,target)
        self.invoice_tree.heading('outstanding_converted',text=self.column_label('outstanding_converted'))
        self.fill(self.summary_tree,self.reporter.summaries(rows))
        active=[r for r in rows if r['status']!='Cancelled']
        missing=sorted({r['currency'] for r in active if Decimal(r['outstanding'])>0 and r['outstanding_converted']=='Rate missing'})
        converted=sum((Decimal(r['outstanding_converted']) for r in active if r['outstanding_converted']!='Rate missing'),Decimal(0))
        text=f'{len(rows)} invoices  |  {sum(r["status"]=="Paid" for r in rows)} paid  |  {sum(r["status"]=="Partially paid" for r in rows)} partial  |  {sum(r["days_overdue"]>0 for r in rows)} overdue\n'
        text+=f'Outstanding {target} {"subtotal" if missing else "total"}: {converted:,.2f}'
        text+=f'  •  Rates unavailable for conversion: {", ".join(missing)} to {target}' if missing else '  •  All outstanding balances covered'
        self.metrics.set(text)
        query=self.search.get().strip().casefold(); status=self.status_filter.get()
        visible=[r for r in rows if (not query or query in ' '.join(str(v) for v in r.values()).casefold()) and (status=='All' or r['status']==status or (status=='Overdue' and r['days_overdue']>0))]
        self.fill(self.invoice_tree,visible); self.payment_tree.delete(*self.payment_tree.get_children())
        rates=[dict(r) for r in self.repo.connection.execute('SELECT * FROM exchange_rates ORDER BY rate_date DESC,rate_id DESC')]
        for row in rates:
            row['inr_per_unit']=f"{Decimal(row['inr_per_unit']):.6f}"
        self.fill(self.rate_tree,rates)
        signature,text=overdue_alert(self.reporter.rows(date.today().isoformat()))
        self.alert_text.set(text)
        if self.alert_signature is not None and signature!=self.alert_signature:
            self.after_idle(self.update_alerts)

    def close(self):
        self.closed=True
        self.repo.close(); self.destroy()

if __name__=='__main__':
    InvoiceApp().mainloop()
