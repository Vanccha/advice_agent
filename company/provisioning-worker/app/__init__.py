"""Provisioning worker: the background process that sets up customers' fibre service.

Where this sits: polls the `core` database for queued provisioning jobs
created by core-api; exposes `/health` and `/metrics` like every service.

Key ideas:
- Background worker with retries: the API only queues work; this process
  claims, executes, heartbeats and finishes it.

Read next: `main.py`, then `worker.py`.
"""
