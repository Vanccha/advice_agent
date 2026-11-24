"""Payment gateway simulator (PSP): the company's stand-in for a card processor.

Where this sits: called by core-api to charge and refund; calls core-api's
webhook endpoint when a charge changes status. Operators and chaos scenarios
toggle failures through the control endpoint.

Key ideas:
- A simulator that can fail on demand (failure rate, forced code, latency,
  full outage) lets the rest of the stack be tested against realistic faults.
- Charges are idempotent on `idempotency_key`.

Read next: `main.py`, then `engine.py`.
"""
