"""Web assets for the demo site and chat widget (templates, CSS, vanilla JS) plus a preview tool.

Where this sits: presentation layer. `assistant/api/` serves these files (`api/templating.py`,
`api/routes/pages.py`); the browser then calls the HTTP API back from `static/widget.js`.
Nothing here imports the API.

Key ideas:
- Server-rendered Jinja2 plus vanilla JS: no Node toolchain, no build step to learn first.
- Only relative URLs, so the same widget works for any host or tenant.
- This `__init__.py` makes `web/tests/` importable as `web.tests`, so it does not collide
  with the repo-root `tests/` package when pytest collects both.

Read next: `web/templates/site.html`, then `web/static/widget.js`.
"""
