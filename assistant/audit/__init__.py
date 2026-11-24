"""Tamper-evident record of what the assistant decided and did.

Public entry point: ``audit.log.AuditLog`` (``append``, ``timeline``, ``verify_chain``).
The pure hash helpers (``canonical_json``, ``compute_entry_hash``, ``digest_tool_output``) live
in the same module and need no database. Imported as the top-level package ``audit``.
"""
