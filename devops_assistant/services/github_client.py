import base64
import binascii
import re
from typing import Any
from urllib.parse import quote

import httpx
from fastmcp.exceptions import ToolError

from ..config import Settings
from ..errors import InvalidInputError, PolicyDeniedError
from ..security import limit, redact

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MAX_PAGES = 5
_PER_PAGE = 100


class GitHubClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.github_api_base_url.rstrip("/")

    def validate_repository(self, repository: str) -> str:
        if not _REPOSITORY_PATTERN.fullmatch(repository):
            raise InvalidInputError("Repository must use the owner/repository format")

        owner, name = repository.split("/", 1)
        if owner in {".", ".."} or name in {".", ".."}:
            raise InvalidInputError("Repository must use the owner/repository format")

        if self.settings.allowed_github_repos and repository.casefold() not in {
            item.casefold() for item in self.settings.allowed_github_repos
        }:
            raise PolicyDeniedError("Repository is not on the configured allowlist")
        return repository

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.settings.github_token:
            raise ToolError("GITHUB_TOKEN is required for GitHub tools")

        try:
            response = httpx.get(
                f"{self.base_url}/{path.lstrip('/')}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.settings.github_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params=params,
                timeout=10.0,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise ToolError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise ToolError("GitHub API is unavailable") from exc

        if response.status_code == 401:
            raise ToolError("GitHub rejected the configured token")
        if response.status_code == 403:
            if response.headers.get("X-RateLimit-Remaining") == "0" or response.headers.get("Retry-After"):
                raise ToolError("GitHub API rate limit exceeded; try again later")
            raise ToolError("GitHub denied the request; check token permissions")
        if response.status_code == 404:
            raise ToolError("GitHub resource was not found or is not accessible")
        if response.status_code == 429:
            raise ToolError("GitHub API rate limit exceeded; try again later")
        if response.is_error:
            raise ToolError(f"GitHub API returned HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise ToolError("GitHub API returned an invalid response") from exc

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        query = dict(params or {})
        query["per_page"] = _PER_PAGE
        results: list[dict[str, Any]] = []

        for page in range(1, _MAX_PAGES + 1):
            query["page"] = page
            data = self._request(path, query)
            if not isinstance(data, list):
                raise ToolError("GitHub API returned an unexpected response")
            results.extend(item for item in data if isinstance(item, dict))
            if len(data) < _PER_PAGE:
                break

        return results

    def list_repositories(self, limit_count: int) -> list[dict[str, Any]]:
        repositories = self._paginate("user/repos", {"sort": "updated", "direction": "desc"})
        if self.settings.allowed_github_repos:
            allowed = {item.casefold() for item in self.settings.allowed_github_repos}
            repositories = [
                repository
                for repository in repositories
                if isinstance(repository.get("full_name"), str)
                and repository["full_name"].casefold() in allowed
            ]

        return [
            self._shape(
                {
                    "full_name": repository.get("full_name"),
                    "description": repository.get("description"),
                    "private": repository.get("private"),
                    "default_branch": repository.get("default_branch"),
                    "html_url": repository.get("html_url"),
                }
            )
            for repository in repositories[:limit_count]
        ]

    def get_repository(self, repository: str) -> dict[str, Any]:
        repository = self.validate_repository(repository)
        data = self._request(f"repos/{repository}")
        if not isinstance(data, dict):
            raise ToolError("GitHub API returned an unexpected repository response")
        return self._shape(
            {
                "full_name": data.get("full_name"),
                "description": data.get("description"),
                "private": data.get("private"),
                "default_branch": data.get("default_branch"),
                "html_url": data.get("html_url"),
                "language": data.get("language"),
                "stargazers_count": data.get("stargazers_count"),
                "open_issues_count": data.get("open_issues_count"),
            }
        )

    def list_files(self, repository: str, path: str, ref: str | None, limit_count: int) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        path = self._validate_path(path, allow_empty=True)
        encoded_path = quote(path, safe="/")
        params = {"ref": ref} if ref else None
        data = self._request(f"repos/{repository}/contents/{encoded_path}", params)
        if not isinstance(data, list):
            raise ToolError("The requested GitHub path is not a directory")

        return [
            self._shape(
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "type": item.get("type"),
                    "size": item.get("size"),
                }
            )
            for item in data[:limit_count]
            if isinstance(item, dict)
        ]

    def read_file(self, repository: str, path: str, ref: str | None) -> str:
        repository = self.validate_repository(repository)
        path = self._validate_path(path, allow_empty=False)
        encoded_path = quote(path, safe="/")
        params = {"ref": ref} if ref else None
        data = self._request(f"repos/{repository}/contents/{encoded_path}", params)
        if not isinstance(data, dict):
            raise ToolError("The requested GitHub path is not a file")
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise ToolError("The requested file is binary or too large to read")

        try:
            content = base64.b64decode(data["content"], validate=False).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ToolError("The requested file is not valid UTF-8 text") from exc
        return limit(redact(content), self.settings.max_output_chars)

    def list_commits(self, repository: str, branch: str | None, limit_count: int) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        params: dict[str, Any] = {}
        if branch:
            params["sha"] = branch
        data = self._paginate(f"repos/{repository}/commits", params)
        return [
            self._shape(
                {
                    "sha": item.get("sha"),
                    "message": (item.get("commit") or {}).get("message"),
                    "author": ((item.get("commit") or {}).get("author") or {}).get("name"),
                    "date": ((item.get("commit") or {}).get("author") or {}).get("date"),
                    "html_url": item.get("html_url"),
                }
            )
            for item in data[:limit_count]
        ]

    def list_issues(self, repository: str, state: str, limit_count: int) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        data = self._paginate(f"repos/{repository}/issues", {"state": state, "sort": "updated"})
        issues = [item for item in data if "pull_request" not in item]
        return [
            self._shape(
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "user": (item.get("user") or {}).get("login"),
                    "created_at": item.get("created_at"),
                    "html_url": item.get("html_url"),
                }
            )
            for item in issues[:limit_count]
        ]

    def list_pull_requests(self, repository: str, state: str, limit_count: int) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        data = self._paginate(f"repos/{repository}/pulls", {"state": state, "sort": "updated"})
        return [
            self._shape(
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "user": (item.get("user") or {}).get("login"),
                    "head_branch": (item.get("head") or {}).get("ref"),
                    "base_branch": (item.get("base") or {}).get("ref"),
                    "created_at": item.get("created_at"),
                    "html_url": item.get("html_url"),
                }
            )
            for item in data[:limit_count]
        ]

    @staticmethod
    def _shape(data: dict[str, Any]) -> dict[str, Any]:
        return {
            key: redact(value) if isinstance(value, str) else value
            for key, value in data.items()
        }

    @staticmethod
    def _validate_path(path: str, allow_empty: bool) -> str:
        normalized = path.strip("/")
        if not normalized and not allow_empty:
            raise InvalidInputError("A file path is required")
        if "\x00" in path or any(part in {".", ".."} for part in normalized.split("/")):
            raise InvalidInputError("Invalid GitHub path")
        return normalized
