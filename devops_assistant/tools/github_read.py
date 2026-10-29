import json
from typing import Any

from fastmcp import FastMCP

from ..audit import audit_tool
from ..errors import InvalidInputError
from ..security import limit
from ..services.github_client import GitHubClient

_MAX_RESULTS = 100


def _validate_limit(count: int) -> int:
    if not 1 <= count <= _MAX_RESULTS:
        raise InvalidInputError(f"limit must be between 1 and {_MAX_RESULTS}")
    return count


def _validate_state(state: str) -> str:
    if state not in {"open", "closed", "all"}:
        raise InvalidInputError("state must be 'open', 'closed', or 'all'")
    return state


def _json_result(data: Any, max_chars: int) -> str:
    return limit(json.dumps(data, ensure_ascii=True, separators=(",", ":")), max_chars)


def register(mcp: FastMCP, client: GitHubClient) -> None:
    @mcp.tool
    @audit_tool
    def github_list_repositories(limit: int = 30) -> str:
        """List repositories accessible to the configured GitHub token, subject to the repository allowlist."""
        count = _validate_limit(limit)
        return _json_result(
            client.list_repositories(count), client.settings.max_output_chars
        )

    @mcp.tool
    @audit_tool
    def github_get_repository(repository: str) -> str:
        """Return safe metadata for an accessible owner/repository."""
        return _json_result(
            client.get_repository(repository), client.settings.max_output_chars
        )

    @mcp.tool
    @audit_tool
    def github_list_files(
        repository: str, path: str = "", ref: str | None = None, limit: int = 100
    ) -> str:
        """List files and directories at a repository path and ref, with a bounded result count."""
        count = _validate_limit(limit)
        return _json_result(
            client.list_files(repository, path, ref, count),
            client.settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    def github_read_file(repository: str, path: str, ref: str | None = None) -> str:
        """Read a bounded, redacted UTF-8 text file from an accessible repository."""
        return client.read_file(repository, path, ref)

    @mcp.tool
    @audit_tool
    def github_list_commits(
        repository: str, branch: str | None = None, limit: int = 30
    ) -> str:
        """List recent commits for a repository and optional branch."""
        count = _validate_limit(limit)
        return _json_result(
            client.list_commits(repository, branch, count),
            client.settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    def github_list_issues(
        repository: str, state: str = "open", limit: int = 30
    ) -> str:
        """List issues (excluding pull requests) for a repository."""
        count = _validate_limit(limit)
        return _json_result(
            client.list_issues(repository, _validate_state(state), count),
            client.settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    def github_list_pull_requests(
        repository: str, state: str = "open", limit: int = 30
    ) -> str:
        """List pull requests for a repository."""
        count = _validate_limit(limit)
        return _json_result(
            client.list_pull_requests(repository, _validate_state(state), count),
            client.settings.max_output_chars,
        )
