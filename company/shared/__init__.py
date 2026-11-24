"""Shared building blocks for every NetSwift internal service.

Where this sits: imported by core-api, ticketing, notification-hub, the
payment gateway simulator and the provisioning worker (each of which also owns
a package called `app`).

Key ideas:
- One house standard for services: app factory (health, metrics, error
  envelope), API-key auth, DB session helpers and a service-to-service client.
- Shared code carries no business rules; each service owns its own data.

Read next: `app_factory.py`, then `auth.py` and `errors.py`.
"""
