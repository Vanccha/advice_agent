"""UK GDPR masking: keep personal data away from models, traces and tickets.

Public entry points (all in ``privacy.masking``): ``mask_text`` (free text), ``mask_payload``
(structured data, driven by field names), ``unmask``, and ``assert_no_pii`` (the guard that
raises ``PIILeakError``). ``privacy.detectors`` holds the regex and checksum primitives.
Imported as the top-level package ``privacy``.
"""
