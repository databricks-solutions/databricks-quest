"""SQL safety + templating for the SQL assertion validator.

These helpers are deliberately pure (no DB, no SDK) so they can be unit-tested
exhaustively and reasoned about for security review. The validator runs them
*before* any statement reaches a warehouse.

Controls (per ``docs/06`` "Validator safety"):

- read-only by default: only a single ``SELECT`` / ``WITH ... SELECT`` is allowed;
  destructive/DDL/DML verbs are blocked.
- single statement: trailing-semicolon is tolerated, but two real statements are
  rejected (defends against stacked-query injection).
- server-side templating: ``${name}`` slots resolve only from a server-provided
  variable map; an unknown name is a hard error (a player cannot inject a slot
  the host did not define), and values are scrubbed of SQL metacharacters.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# Verbs that must never run from a validator. Read-only is the default posture;
# there is intentionally no allowlist escape hatch in the MVP.
_BLOCKED_STATEMENT_RE = re.compile(
    r"(?is)\b("
    r"insert|update|delete|merge|upsert|truncate|drop|alter|create|replace|"
    r"grant|revoke|copy|call|exec|execute|vacuum|analyze|comment|"
    r"refresh|optimize|restore|set|use|attach|detach"
    r")\b"
)

# Inline comments and block comments are stripped before analysis so a blocked
# verb cannot hide behind a comment, and so comments cannot smuggle a ``;``.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Template slot: ${name} with a conservative identifier charset.
_TEMPLATE_SLOT_RE = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")

# A resolved template value must look like a SQL identifier / qualified name or
# a plain literal — never contain statement-breaking metacharacters.
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.\- ]*$")


class UnsafeSQLError(Exception):
    """Raised when a statement violates a safety rule. Caller maps to ``error``."""


def strip_comments(sql: str) -> str:
    """Remove line and block comments (used for analysis, not execution)."""
    no_block = _BLOCK_COMMENT_RE.sub(" ", sql)
    return _LINE_COMMENT_RE.sub(" ", no_block)


def split_statements(sql: str) -> List[str]:
    """Split into non-empty statements, ignoring a single trailing semicolon.

    Operates on comment-stripped SQL so a ``;`` inside a comment does not count.
    String-literal-aware enough for the validator's read-only surface: it does
    not split on semicolons inside single-quoted strings.
    """
    cleaned = strip_comments(sql)
    statements: List[str] = []
    buf: List[str] = []
    in_str = False
    i = 0
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "'":
            # Handle escaped '' inside a string literal.
            if in_str and i + 1 < len(cleaned) and cleaned[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_str = not in_str
            buf.append(ch)
        elif ch == ";" and not in_str:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def ensure_safe_select(sql: str) -> str:
    """Validate a single read-only statement and return it trimmed.

    Raises :class:`UnsafeSQLError` if the input is empty, contains more than one
    statement, is not a ``SELECT``/``WITH`` query, or mentions a blocked verb.
    """
    if not sql or not sql.strip():
        raise UnsafeSQLError("empty SQL statement")

    statements = split_statements(sql)
    if len(statements) == 0:
        raise UnsafeSQLError("empty SQL statement")
    if len(statements) > 1:
        raise UnsafeSQLError("multiple statements are not allowed")

    stmt = statements[0]
    head = stmt.lstrip().split(None, 1)[0].lower() if stmt.strip() else ""
    if head not in ("select", "with"):
        raise UnsafeSQLError("only read-only SELECT/WITH queries are allowed")

    blocked = _BLOCKED_STATEMENT_RE.search(stmt)
    if blocked:
        raise UnsafeSQLError(
            f"statement contains a blocked keyword: {blocked.group(1).upper()}"
        )
    return stmt.strip()


def referenced_variables(sql: str) -> List[str]:
    """Return the distinct ``${name}`` slot names referenced in ``sql``."""
    seen: List[str] = []
    for m in _TEMPLATE_SLOT_RE.finditer(sql):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def _scrub_value(name: str, value: Any) -> str:
    """Coerce a template value to a safe string or raise."""
    if value is None:
        raise UnsafeSQLError(f"template variable '{name}' resolved to null")
    text = str(value)
    if not _SAFE_VALUE_RE.match(text):
        raise UnsafeSQLError(
            f"template variable '{name}' has an unsafe value"
        )
    return text


def resolve_template(sql: str, variables: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    """Resolve ``${name}`` slots from ``variables`` (server-provided only).

    Returns the resolved SQL and the map of substitutions actually applied.
    Raises :class:`UnsafeSQLError` if a referenced variable is missing or its
    value is unsafe. Players cannot introduce new slots — the SQL comes from the
    quest pack and the variables come from the resolved team/event context.
    """
    applied: Dict[str, str] = {}
    missing = [n for n in referenced_variables(sql) if n not in variables]
    if missing:
        raise UnsafeSQLError(
            "unresolved template variable(s): " + ", ".join(sorted(missing))
        )

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        safe = _scrub_value(name, variables[name])
        applied[name] = safe
        return safe

    resolved = _TEMPLATE_SLOT_RE.sub(_sub, sql)
    return resolved, applied


def prepare_statement(
    sql: str, variables: Dict[str, Any], *, max_length: int = 20000
) -> str:
    """Resolve templates then enforce the read-only single-statement rules.

    The single entry point used by the validator: template-resolve first (so the
    final text is what runs), then safety-check the resolved statement.
    """
    if sql is None:
        raise UnsafeSQLError("missing SQL statement")
    if len(sql) > max_length:
        raise UnsafeSQLError("SQL statement exceeds maximum length")
    resolved, _ = resolve_template(sql, variables)
    return ensure_safe_select(resolved)
