"""Local JSONL fallback sink for trace spans.

Where this sits: ``observability`` package. ``tracing`` writes here whenever Langfuse is
disabled or unreachable, so a demo without the observability stack still shows the reasoning
trail. Records arrive already redacted (``observability.redaction``); this module does no
masking, it only persists.

Key ideas:
- Fail-open: a read-only or missing directory logs one warning and drops the record; it never
  crashes a customer turn.
- One file per UTC day, one JSON object per line (easy to grep, append-only).

Read next: ``observability/tracing.py``, ``observability/redaction.py``.
See: docs/contracts.md §4.9.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("assistant.observability.fallback")

# Overridable via env for container deployments; tests pass ``trace_dir=`` explicitly instead.
DEFAULT_TRACE_DIR = Path(os.environ.get("ASSISTANT_TRACE_DIR", "/app/var/traces"))

_warned_dirs: set[str] = set()
_warn_lock = threading.Lock()


def _warn_once(key: str, message: str) -> None:
    """Log a warning once per directory, so a broken sink does not flood the logs."""
    with _warn_lock:
        if key in _warned_dirs:
            return
        _warned_dirs.add(key)
    logger.warning(message)


def reset_warnings_for_tests() -> None:
    """Test helper: forget which directories have already produced a warning."""
    with _warn_lock:
        _warned_dirs.clear()


def append_fallback_record(record: dict[str, Any], *, trace_dir: Path | str | None = None) -> bool:
    """Append one JSON line (UTC date-stamped file) describing one span/event.

    Returns ``True`` on success, ``False`` if the sink could not be written to (logged once
    per directory, then silently dropped on subsequent calls). Never raises.
    """
    base_dir = Path(trace_dir) if trace_dir is not None else DEFAULT_TRACE_DIR
    dir_key = str(base_dir)
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        file_path = base_dir / f"{now:%Y-%m-%d}.jsonl"
        line = json.dumps({"ts": now.isoformat(), **record}, ensure_ascii=False, default=str)
        with open(file_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
    except OSError as exc:
        _warn_once(dir_key, f"Trace fallback sink unavailable at {base_dir} ({exc}); dropping trace records")
        return False
    except Exception:  # pragma: no cover - defensive, must never raise
        logger.warning(
            "Trace fallback sink failed unexpectedly at %s; dropping trace record", base_dir, exc_info=True
        )
        return False
