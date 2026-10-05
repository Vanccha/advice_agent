"""The two worlds must stay separate.

These tests are the mechanical guarantee behind the product story: the assistant is a guest
system that only reaches the company through the integration layer, and the company was
written as if the assistant did not exist.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

ASSISTANT = REPO / "assistant"
COMPANY = REPO / "company"
INTEGRATIONS = REPO / "integrations"

# Top-level module names each world is forbidden to import.
FORBIDDEN_IMPORTS = {
    "assistant": {"company", "shared", "integrations", "common"},
    "company": {"assistant", "integrations", "common"},
    "integrations": {"assistant", "company"},
}

# The company codebase must not betray any awareness of the assistant product.
FORBIDDEN_TOKENS = re.compile(
    r"(?i)\b(assistant|advisor|chatbot|llm|openai|anthropic|langfuse|mcp)\b"
)

SKIP_DIR_PARTS = {"__pycache__", ".pytest_cache", "var", "node_modules", ".git"}

# Narrow, documented allowances. A company may configure its Alertmanager to deliver alerts
# to a third-party integration endpoint; that is a hostname in a deployment config file, not
# the company's code knowing anything about the product. Nothing else may claim an allowance,
# and no `.py` file ever gets one.
TOKEN_ALLOWANCES: set[tuple[str, str]] = {
    ("company/monitoring/alertmanager.yml", "mcp"),
}


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        p
        for p in root.rglob("*.py")
        if not SKIP_DIR_PARTS & set(p.parts)
    ]


def _imported_roots(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - surfaced as a test failure
        raise AssertionError(f"{path} does not parse: {exc}") from exc
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, stays inside its own world
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_import_boundaries() -> None:
    violations: list[str] = []
    for world, root in (("assistant", ASSISTANT), ("company", COMPANY), ("integrations", INTEGRATIONS)):
        forbidden = FORBIDDEN_IMPORTS[world]
        for path in _python_files(root):
            bad = _imported_roots(path) & forbidden
            if bad:
                violations.append(f"{path.relative_to(REPO)} imports {sorted(bad)}")
    assert not violations, "cross-world imports found:\n" + "\n".join(violations)


def test_company_code_never_mentions_the_assistant() -> None:
    """Scan company sources (py/sql/yml/html/j2) for assistant-aware identifiers.

    Alertmanager's webhook target is deliberately an environment variable, so pointing the
    company at a third-party tool stays *configuration*, never code.
    """
    violations: list[str] = []
    patterns = ("*.py", "*.sql", "*.yml", "*.yaml", "*.html", "*.j2", "*.css", "*.js")
    for pattern in patterns:
        for path in COMPANY.rglob(pattern):
            if SKIP_DIR_PARTS & set(path.parts):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = FORBIDDEN_TOKENS.search(line)
                if match:
                    rel = path.relative_to(REPO).as_posix()
                    if (rel, match.group(0).lower()) in TOKEN_ALLOWANCES:
                        continue
                    violations.append(
                        f"{rel}:{lineno} mentions '{match.group(0)}': {line.strip()[:100]}"
                    )
    assert not violations, (
        "the company world must be unaware of the assistant:\n" + "\n".join(violations)
    )


def test_assistant_never_hardcodes_company_endpoints() -> None:
    """Company addresses must come from tenant config / env, not from assistant source.

    Test files are exempt: fixtures legitimately spell out a concrete tenant's endpoints to
    prove the configuration layer resolves them.
    """
    violations: list[str] = []
    hardcoded = re.compile(r"(?i)(core-api|payment-gateway|ticketing|notification-hub|nethiz)")
    for path in _python_files(ASSISTANT):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            if hardcoded.search(stripped) and "http" in stripped:
                violations.append(f"{path.relative_to(REPO)}:{lineno} {stripped[:100]}")
    assert not violations, (
        "assistant code must reach the company only through configured adapters:\n"
        + "\n".join(violations)
    )
