"""Local JSONL fallback sink for trace spans (contracts §4.9).

Used whenever Langfuse is disabled or unreachable, so a demo run without the observability
overlay still shows the reasoning trail. Every record appended here has already passed
through ``observability.redaction`` — this module does no masking of its own, it only
persists what it is given.

Must never raise: a read-only or missing directory degrades to a single logged warning and
a dropped record, never a crashed turn.
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

#: Overridable via env for container deployments; tests pass ``trace_dir=`` explicitly instead
#: of touching this module global.
DEFAULT_TRACE_DIR = Path(os.environ.get("ASSISTANT_TRACE_DIR", "/app/var/traces"))

_warned_dirs: set[str] = set()
_warn_lock = threading.Lock()


def _warn_once(key: str, message: str) -> None:
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
