"""Pure hash-chain logic — no database involved."""
from audit.log import GENESIS_HASH, canonical_json, compute_entry_hash, digest_tool_output


def test_canonical_json_sorts_keys_and_is_tight():
    payload = {"b": 1, "a": 2, "nested": {"z": 1, "y": 2}}
    assert canonical_json(payload) == b'{"a":2,"b":1,"nested":{"y":2,"z":1}}'


def test_compute_entry_hash_is_deterministic():
    payload = {"summary": "hello", "seq": 1}
    h1 = compute_entry_hash(GENESIS_HASH, payload)
    h2 = compute_entry_hash(GENESIS_HASH, payload)
    assert h1 == h2
    assert len(h1) == 64


def test_compute_entry_hash_changes_with_prev_hash():
    payload = {"summary": "hello", "seq": 1}
    h1 = compute_entry_hash(GENESIS_HASH, payload)
    h2 = compute_entry_hash("a" * 64, payload)
    assert h1 != h2


def test_compute_entry_hash_changes_with_payload():
    h1 = compute_entry_hash(GENESIS_HASH, {"summary": "hello"})
    h2 = compute_entry_hash(GENESIS_HASH, {"summary": "hello!"})
    assert h1 != h2


def test_digest_tool_output_never_stores_raw_value():
    digest = digest_tool_output({"status": "ok", "job_id": 42})
    assert isinstance(digest, str)
    assert len(digest) == 64
    assert "job_id" not in digest and "42" not in digest
