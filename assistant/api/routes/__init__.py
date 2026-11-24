"""One module per resource, each exposing an `APIRouter` named `router`.

Where this sits: HTTP layer, imported only by `api.main.create_app()`.

Key ideas:
- Routers per resource keep each file small and let you find an endpoint by its URL.
- Route functions stay thin: authenticate, check ownership, delegate to the orchestrator,
  shape the response. Business logic lives in `modes/`, never here.

Read next: `api/main.py` (how routers are mounted), then `api/state.py`.
"""
