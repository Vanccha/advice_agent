"""Server-rendered chat widget + embedding host page (contracts §4.2).

Templates (Jinja2) and static CSS/vanilla JS live here; the real HTTP endpoints are served by
`assistant/api/` (built separately). `preview.py` is a standalone, non-production harness for
rendering these templates in isolation — see its module docstring.

This `__init__.py` exists so `assistant/web/tests/` is importable as the package
``web.tests`` (consistent with every other `assistant/*` subpackage) rather than colliding
with the top-level `tests/` package at the repo root when both are collected in one pytest
run.
"""
