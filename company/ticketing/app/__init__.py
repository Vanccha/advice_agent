"""Ticketing service: the company's internal Jira/Zendesk-style department ticket system.

Where this sits: called over REST by CRM tools and partner integrations (API
key with `tickets:*` scopes) and used by department agents through the `/agent`
web panel. Pushes new-ticket messages to the notification hub and signed
webhooks to subscribers.

Key ideas:
- A ticket moves through an explicit state table with an SLA deadline.
- Creating a ticket is idempotent on `external_ref`.
- Side effects (webhooks, chat messages) never fail the request that caused them.

Read next: `main.py`, then `services.py`.
"""
