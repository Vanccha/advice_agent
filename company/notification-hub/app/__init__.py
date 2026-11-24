"""Notification hub: the company's Teams/Slack-style internal channels.

Where this sits: ticketing and Alertmanager post messages in over REST; staff
read them in the web UI. It owns the `notify` schema in its own database.

Key ideas:
- Department channels are the routing target for tickets and alerts.
- Posting a message is the only write; there is no delete or edit.

Read next: `main.py`, then `routers/api.py` and `routers/alertmanager.py`.
"""
