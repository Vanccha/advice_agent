"""Authority: decides what the assistant is allowed to do, independent of the model.

Public entry points: ``policy.engine.PolicyEngine.check(action, context)`` returns a
``PolicyDecision``; ``policy.executor.ActionExecutor.execute(...)`` is the only path that runs
an action (check, audit, run). Imported as the top-level package ``policy``.
"""
