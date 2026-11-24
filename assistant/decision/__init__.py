"""Replaceable judgement: classify intent, department, urgency and issue type.

Public entry points: ``decision.factory.get_decision_service(provider)`` returns a
``DecisionService`` (``decision.base``); ``decision.fallback.apply_confidence_floor`` is what
callers use to distrust low-confidence answers. ``llm_structured`` is the real implementation
(an LLM with structured output); ``typesafe_jev`` is a documented stub for a future adapter.
"""
