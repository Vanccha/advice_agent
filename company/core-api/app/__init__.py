"""core-api: the company's system of record for customers, subscriptions, payments and incidents.

Where this sits: the hub of the company stack. Called by the CRM front office
and by partner integrations over REST; calls the payment gateway; owns the
`core` database schema and the read-only `diag` views.

Key ideas:
- A system of record owns its data: other systems read through views or ask
  through the API, they never write its tables.
- Every route is protected by an explicit API-key scope.

Read next: `main.py`, then `bootstrap.py` and `routers/subscriptions.py`.
"""
