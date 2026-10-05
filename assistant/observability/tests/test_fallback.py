from __future__ import annotations

import json
import logging

from observability import fallback


def test_append_writes_one_jsonl_line_per_record(tmp_path):
    ok1 = fallback.append_fallback_record({"kind": "turn", "session_id": "conv-1"}, trace_dir=tmp_path)
    ok2 = fallback.append_fallback_record({"kind": "event", "name": "mode_decision"}, trace_dir=tmp_path)

    assert ok1 is True
    assert ok2 is True

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "turn"
    assert first["session_id"] == "conv-1"
    assert "ts" in first


def test_missing_or_blocked_directory_degrades_without_raising(tmp_path, caplog):
    # A regular file where a directory is expected: mkdir(parents=True) must fail with an
    # OSError subclass, which append_fallback_record must swallow.
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("i am a file, not a directory")
    bad_dir = blocker / "traces"

    with caplog.at_level(logging.WARNING, logger="assistant.observability.fallback"):
        ok = fallback.append_fallback_record({"kind": "turn"}, trace_dir=bad_dir)
        ok_again = fallback.append_fallback_record({"kind": "turn"}, trace_dir=bad_dir)

    assert ok is False
    assert ok_again is False
    warnings = [r for r in caplog.records if r.name == "assistant.observability.fallback"]
    # warned once, not once per call
    assert len(warnings) == 1


def test_non_json_serializable_values_do_not_raise(tmp_path):
    class Weird:
        def __repr__(self):
            return "<Weird>"

    ok = fallback.append_fallback_record({"kind": "event", "payload": Weird()}, trace_dir=tmp_path)
    assert ok is True
    files = list(tmp_path.glob("*.jsonl"))
    line = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert line["payload"] == "<Weird>"
