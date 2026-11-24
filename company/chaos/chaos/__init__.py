"""Failure-injection CLI for the NetSwift company stack.

Where this sits: an operator tool run as `make chaos SCENARIO=...`. It talks to
the company's own databases and REST APIs, and imports nothing from outside
company code.

Key ideas:
- Chaos / failure injection: put the stack into states that are
  indistinguishable from real incidents so support tooling and monitoring can
  be rehearsed against them.
- The package owns no business logic; it reuses the company's own write paths
  wherever it can, and leaves an audit trail where it cannot.

Read next: `cli.py`, then `scenarios/__init__.py`.
"""
