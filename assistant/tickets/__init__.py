"""Structured tickets for human departments.

Public entry points: ``tickets.builder.build_structured_ticket`` (builds a masked, idempotent
``StructuredTicket``) and ``tickets.client.TicketService`` (creates and looks up tickets through
the ticketing MCP adapter). ``tickets.masking_rules`` is the ticket-specific part of PII masking.
"""
