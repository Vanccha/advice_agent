"""Shared vocabulary and infrastructure used by every other assistant package.

Where this sits: the bottom layer. Everything under ``assistant/`` may import ``core_common``;
it imports nothing from them. It does no LLM or network calls.

Key ideas:
- One shared vocabulary: enums and Pydantic models (``types``) that all layers agree on.
- Config as data: tenant YAML is validated once into frozen models (``config``, ``config_models``).
- Import as the top-level name ``core_common``, never ``common`` (enforced by
  ``tests/architecture/test_boundaries.py``).

Public entry points: ``core_common.types``, ``core_common.config.load_tenant_config``,
``core_common.settings.get_settings``, ``core_common.db.session_scope``.

Read next: ``core_common/types.py``, ``core_common/config.py``.
"""
