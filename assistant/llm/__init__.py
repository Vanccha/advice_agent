"""The LLM provider seam: one interface, several vendors, one PII guard.

Public entry points: ``llm.factory.get_provider()`` (the only way application code should get a
provider), ``llm.base`` (``LLMProvider``, ``ChatMessage``, ``AgentMessage``, ``ToolStep`` and
the error types) and ``llm.scripted.ScriptedProvider`` (deterministic, for tests and evals).
Vendor code lives in ``openai_agents.py`` / ``anthropic_provider.py`` with their ``*_mapping.py``
helpers.
"""
