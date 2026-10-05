"""Failure-injection CLI for the NetHiz company stack.

This package owns no business logic of its own: it reaches into the company's
own databases and REST APIs (as a privileged operator tool would) to put the
system into states that are indistinguishable from real incidents. It never
imports any code from outside the company's own services.
"""
