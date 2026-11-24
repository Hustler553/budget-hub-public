# Budget Hub

A self-hosted personal budgeting app: accounts, transactions, budgets,
recurring bills, goals, reports and net worth, for a single household.

* Python 3.12+ / Flask, SQLite, server-rendered templates
* Bank data via Plaid (Transactions) or CSV import
* Per-person logins; secrets are read from environment variables and sensitive
  tokens are encrypted at rest

## Running it

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python wsgi.py
```

Then open http://127.0.0.1:5000.

This is a personal project, run privately for one household. It is not a
hosted service and has no other users.
