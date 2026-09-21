# AI-assisted development evidence

The application was developed with Codex from the team's requested invoice capstone. This file records the actual discussion, not a claim that team members have independently verified the results.

## User prompts that shaped the implementation

- “we are 2 members in a team … Vendor Invoice intelligent System”
- “there should be a tab to enter more data … and it gets stored”
- “bill due or paid should be visible or partial paid due date should be visible”
- “Ui must be very friendly and easy to understand”
- “can we also test using pytest?”
- “divide project into different modules”
- “consider all the things … for evaluation especially oops”
- “also add an option to update the exchange rate”
- “every record can not be modified but can be deleted and add new same corrected record”
- “use python only for ui also”

## AI contributions

Proposed the class/module separation, implemented Tkinter forms and tables, SQLite persistence, exact money arithmetic, dated exchange rates, validated imports, payment history, audit snapshots, reports, pytest cases, and the Word project report.

## Improvements and verification approach

Separated paid-late history from currently overdue balances; avoided mixed-currency totals; made missing exchange rates explicit; preserved historical rate entries; aggregated payments before joining invoices to prevent duplicate totals; accepted fractional amounts including JPY using exact Decimal storage; used temporary databases in tests; escaped formula-like strings in CSV exports. Added selectable reporting currency with INR as the default and tests for cross-currency conversion and exports.

The team should review the functions, run tests on its own Python installation, verify an invoice and payment manually, and explain the rules during the demo. Automated results and any remaining environment limitations are described in the delivered task summary.
