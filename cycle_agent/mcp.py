"""Connection to the official BigQuery remote MCP server, read-only and cost-guarded."""

from __future__ import annotations

from typing import Any

from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

BIGQUERY_MCP_URL = "https://bigquery.googleapis.com/mcp"

# Read-only subset. The official lab also exposes execute_sql, which can modify data.
ALLOWED_TOOLS = [
    "list_dataset_ids",
    "get_dataset_info",
    "list_table_ids",
    "get_table_info",
    "execute_sql_readonly",
]
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def adc_headers(_context: Any = None) -> dict[str, str]:
    """Fresh bearer token from Application Default Credentials for every MCP request."""
    import google.auth
    from google.auth.transport.requests import Request

    creds, project = google.auth.default(scopes=_SCOPES)
    if not creds.valid:
        creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    if project:
        headers["x-goog-user-project"] = project
    return headers


def bigquery_toolset(url: str = BIGQUERY_MCP_URL) -> McpToolset:
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url, timeout=30.0),
        tool_filter=ALLOWED_TOOLS,
        header_provider=adc_headers,
    )
