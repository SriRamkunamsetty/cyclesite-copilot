"""SQL guardrails enforced in code, not in the prompt.

The official lab only *asks* the model to dry-run queries. Here every ``execute_sql*`` call made
through the BigQuery MCP server passes a ``before_tool_callback`` that:
  1. statically rejects anything that is not one read-only SELECT/WITH statement,
  2. requires a spatial filter on the huge OpenStreetMap planet tables,
  3. dry-runs the query itself and blocks it above a byte cap (fail closed on errors),
  4. keeps the agent on our own project,
  5. appends an audit entry (what was asked, how big, allowed or not) to session state.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

GUARDED_TOOLS = {"execute_sql_readonly", "execute_sql"}
_SQL_KEYS = ("query", "sql", "statement", "sqlQuery")
_PROJECT_KEYS = ("projectId", "project_id", "project")
_DRY_RUN_KEYS = ("dryRun", "dry_run")
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXPORT|CALL|LOAD|"
    r"EXECUTE|ASSERT|BEGIN|DECLARE|SET)\b",
    re.IGNORECASE,
)
_SPATIAL_FILTER = re.compile(
    r"\bST_(DWITHIN|INTERSECTS|CONTAINS|WITHIN|COVEREDBY|COVERS|INTERSECTSBOX)\b", re.IGNORECASE
)
_AUDIT_LIMIT = 200


class SqlSyntaxError(ValueError):
    """Raised when the lexer cannot find the end of a string, comment or quoted name."""


def _has_raw_prefix(sql: str, quote_index: int) -> bool:
    """True when the quote at ``quote_index`` opens an r'..' / rb'..' / br'..' literal."""
    for prefix in ("br", "rb", "r"):
        start = quote_index - len(prefix)
        if start < 0 or sql[start:quote_index].lower() != prefix:
            continue
        before = sql[start - 1] if start > 0 else " "
        if not (before.isalnum() or before == "_"):
            return True
    return False


def strip_noise(sql: str, keep_identifiers: bool = False) -> str:
    """Blank out comments and string literals in ONE left-to-right pass.

    A single pass matters: stripping comments first lets ``SELECT '#'; DROP TABLE t`` hide the
    DROP behind a fake ``#`` comment that is really inside a string. Backtick-quoted names are
    blanked too, unless ``keep_identifiers`` is set (needed to spot table names).
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i : i + 2]
        if two == "--" or ch == "#":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif two == "/*":
            j = sql.find("*/", i + 2)
            if j == -1:
                raise SqlSyntaxError("Unterminated /* comment.")
            i = j + 2
            out.append(" ")
        elif ch in "'\"":
            raw = _has_raw_prefix(sql, i)
            quote = ch * 3 if sql[i : i + 3] == ch * 3 else ch
            i += len(quote)
            start = i
            while True:
                if i >= n:
                    raise SqlSyntaxError("Unterminated string literal.")
                if sql[i] == "\\":
                    i += 2
                    continue
                if sql.startswith(quote, i):
                    break
                i += 1
            if raw and "\\" in sql[start:i]:
                # Parsers disagree about where a raw string with a backslash ends, and that gap
                # is exactly where a hidden second statement would live. Fail closed.
                raise SqlSyntaxError("Raw strings containing a backslash are not allowed.")
            i += len(quote)
            out.append("''")
        elif ch == "`":
            j = sql.find("`", i + 1)
            if j == -1:
                raise SqlSyntaxError("Unterminated backtick name.")
            out.append(sql[i : j + 1] if keep_identifiers else "`x`")
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_sql(sql: str) -> list[str]:
    """Return human-readable violations (empty list means statically acceptable)."""
    problems: list[str] = []
    try:
        clean = strip_noise(sql).strip()
        named = strip_noise(sql, keep_identifiers=True)
    except SqlSyntaxError as exc:
        return [f"Cannot parse the query safely: {exc}"]
    if not clean:
        return ["Empty query."]
    body = clean.rstrip(";").strip()
    if ";" in body:
        problems.append("Only a single statement is allowed.")
    if not re.match(r"^(\(\s*)*(SELECT|WITH)\b", body, re.IGNORECASE):
        problems.append("Query must start with SELECT or WITH.")
    hit = _FORBIDDEN.search(body)
    if hit:
        problems.append(f"Forbidden keyword '{hit.group(1).upper()}': queries must be read-only.")
    if re.search(r"planet_(features|layers|ways|nodes|relations)", named, re.IGNORECASE) and not (
        _SPATIAL_FILTER.search(body)
    ):
        problems.append(
            "Queries on the OpenStreetMap planet tables must filter by a bounding area "
            "(ST_DWITHIN / ST_INTERSECTS) to avoid scanning the whole planet."
        )
    return problems


def extract_sql(args: dict[str, Any]) -> str | None:
    for key in _SQL_KEYS:
        if isinstance(args.get(key), str):
            return args[key]
    for value in args.values():
        if isinstance(value, str) and re.match(r"^\s*(WITH|SELECT|\()", value, re.IGNORECASE):
            return value
    return None


class BytesEstimator(Protocol):
    def estimate_bytes(self, sql: str) -> int: ...


class BigQueryEstimator:
    """Dry-runs SQL with the BigQuery client to learn how many bytes it would scan."""

    def __init__(self, client: Any, location: str = "US"):
        self._client = client
        self._location = location

    def estimate_bytes(self, sql: str) -> int:
        from google.cloud import bigquery

        cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = self._client.query(sql, job_config=cfg, location=self._location)
        return int(job.total_bytes_processed or 0)


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    estimated_bytes: int | None = None


class SqlGuard:
    def __init__(self, estimator: BytesEstimator, max_bytes: int, project_id: str | None = None):
        self._estimator = estimator
        self._max_bytes = max_bytes
        self._project_id = project_id

    def check(self, sql: str) -> GuardDecision:
        problems = validate_sql(sql)
        if problems:
            return GuardDecision(False, problems)
        try:
            est = self._estimator.estimate_bytes(sql)
        except Exception as exc:  # fail closed: an unestimable query is not allowed to run
            return GuardDecision(False, [f"Dry run failed, fix the SQL first: {exc}"])
        if est > self._max_bytes:
            return GuardDecision(
                False,
                [
                    f"Query would scan {est / 1e9:.1f} GB, above the "
                    f"{self._max_bytes / 1e9:.1f} GB "
                    "cap. Narrow the date range, bounding box or columns."
                ],
                est,
            )
        return GuardDecision(True, [], est)

    async def before_tool(self, tool: Any, args: dict[str, Any], tool_context: Any):
        """ADK ``before_tool_callback``: return a dict to block, ``None`` to allow."""
        name = getattr(tool, "name", "")
        if name not in GUARDED_TOOLS:
            return None
        sql = extract_sql(args)
        if sql is None:
            decision = GuardDecision(False, ["Could not find the SQL text in the tool call."])
        else:
            decision = await asyncio.to_thread(self.check, sql)
            if decision.allowed and self._project_id:
                for key in _PROJECT_KEYS:
                    if key in args and args[key] != self._project_id:
                        decision = GuardDecision(
                            False, [f"Queries must run in project '{self._project_id}'."]
                        )
        record_audit(
            tool_context,
            name,
            sql or "",
            decision,
            dry_run=any(bool(args.get(k)) for k in _DRY_RUN_KEYS),
        )
        if decision.allowed:
            return None
        return {"status": "blocked_by_guardrail", "reasons": decision.reasons}


def record_audit(
    tool_context: Any, tool: str, sql: str, decision: GuardDecision, dry_run: bool = False
) -> None:
    if tool_context is None or not hasattr(tool_context, "state"):
        return
    entry = {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool": tool,
        "sql_sha256": hashlib.sha256(sql.encode()).hexdigest()[:16],
        "sql": sql[:4000],
        "estimated_bytes": decision.estimated_bytes,
        "allowed": decision.allowed,
        "dry_run": dry_run,
        "reasons": decision.reasons,
    }
    log = list(tool_context.state.get("audit", []))
    log.append(entry)
    tool_context.state["audit"] = log[-_AUDIT_LIMIT:]
