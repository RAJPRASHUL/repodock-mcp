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

    def require_write_token(self) -> None:
        if not self.settings.github_write_token:
            raise ToolError(
                "GITHUB_WRITE_TOKEN is required for GitHub write operations"
            )
        if self.settings.github_write_token == self.settings.github_token:
            raise ToolError("GitHub writes require a separate GITHUB_WRITE_TOKEN")

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

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.settings.github_token:
            raise ToolError("GITHUB_TOKEN is required for GitHub tools")

        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False
            ) as client:
                response = await client.get(
                    f"{self.base_url}/{path.lstrip('/')}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {self.settings.github_token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params=params,
                )
        except httpx.TimeoutException as exc:
            raise ToolError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise ToolError("GitHub API is unavailable") from exc

        if response.status_code == 401:
            raise ToolError("GitHub rejected the configured token")
        if response.status_code == 403:
            if response.headers.get(
                "X-RateLimit-Remaining"
            ) == "0" or response.headers.get("Retry-After"):
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

    async def _paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        query = dict(params or {})
        query["per_page"] = _PER_PAGE
        results: list[dict[str, Any]] = []

        for page in range(1, _MAX_PAGES + 1):
            query["page"] = page
            data = await self._request(path, query)
            if not isinstance(data, list):
                raise ToolError("GitHub API returned an unexpected response")
            results.extend(item for item in data if isinstance(item, dict))
            if len(data) < _PER_PAGE:
                break

        return results

    async def list_repositories(self, limit_count: int) -> list[dict[str, Any]]:
        repositories = await self._paginate(
            "user/repos", {"sort": "updated", "direction": "desc"}
        )
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

    async def get_repository(self, repository: str) -> dict[str, Any]:
        repository = self.validate_repository(repository)
        data = await self._request(f"repos/{repository}")
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

    async def list_files(
        self, repository: str, path: str, ref: str | None, limit_count: int
    ) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        path = self._validate_path(path, allow_empty=True)
        encoded_path = quote(path, safe="/")
        params = {"ref": ref} if ref else None
        data = await self._request(
            f"repos/{repository}/contents/{encoded_path}", params
        )
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

    async def read_file(self, repository: str, path: str, ref: str | None) -> str:
        repository = self.validate_repository(repository)
        path = self._validate_path(path, allow_empty=False)
        encoded_path = quote(path, safe="/")
        params = {"ref": ref} if ref else None
        data = await self._request(
            f"repos/{repository}/contents/{encoded_path}", params
        )
        if not isinstance(data, dict):
            raise ToolError("The requested GitHub path is not a file")
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise ToolError("The requested file is binary or too large to read")

        try:
            content = base64.b64decode(data["content"], validate=False).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ToolError("The requested file is not valid UTF-8 text") from exc
        return limit(redact(content), self.settings.max_output_chars)

    async def get_branch_sha(self, repository: str, branch: str) -> str:
        repository = self.validate_repository(repository)
        self._validate_branch_name(branch)
        data = await self._request(
            f"repos/{repository}/git/ref/heads/{quote(branch, safe='')}"
        )
        sha = (data.get("object") or {}).get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or not sha:
            raise ToolError("GitHub API returned an invalid branch reference")
        return sha

    async def get_default_branch(self, repository: str) -> str:
        repository = self.validate_repository(repository)
        data = await self._request(f"repos/{repository}")
        default_branch = data.get("default_branch") if isinstance(data, dict) else None
        if not isinstance(default_branch, str) or not default_branch:
            raise ToolError("GitHub API returned invalid repository metadata")
        return default_branch

    async def validate_write_branch(
        self, repository: str, branch: str, require_existing: bool = True
    ) -> str:
        repository = self.validate_write_repository(repository)
        self._validate_branch_name(branch)
        prefix = self.settings.github_write_branch_prefix
        if not branch.startswith(prefix):
            raise PolicyDeniedError(f"Write branches must start with '{prefix}'")
        if branch.casefold() == (await self.get_default_branch(repository)).casefold():
            raise PolicyDeniedError(
                "Writing to the repository default branch is denied"
            )
        if require_existing:
            await self.get_branch_sha(repository, branch)
        return branch

    async def prepare_branch_creation(
        self, repository: str, branch: str, base_branch: str
    ) -> dict[str, str]:
        repository = self.validate_write_repository(repository)
        self._validate_branch_name(branch)
        if not branch.startswith(self.settings.github_write_branch_prefix):
            raise PolicyDeniedError(
                f"New branches must start with '{self.settings.github_write_branch_prefix}'"
            )
        self._validate_branch_name(base_branch)
        base_sha = await self.get_branch_sha(repository, base_branch)
        return {
            "repository": repository,
            "branch": branch,
            "base_branch": base_branch,
            "base_sha": base_sha,
        }

    async def create_branch(
        self, repository: str, branch: str, base_sha: str
    ) -> dict[str, Any]:
        repository = self.validate_write_repository(repository)
        self._validate_branch_name(branch)
        if not branch.startswith(self.settings.github_write_branch_prefix):
            raise PolicyDeniedError(
                f"New branches must start with '{self.settings.github_write_branch_prefix}'"
            )
        data = await self._write_request(
            "POST",
            f"repos/{repository}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        return self._shape(
            {
                "ref": data.get("ref") if isinstance(data, dict) else None,
                "sha": (
                    (data.get("object") or {}).get("sha")
                    if isinstance(data, dict)
                    else None
                ),
            }
        )

    async def prepare_file_update(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
    ) -> dict[str, Any]:
        repository = self.validate_write_repository(repository)
        path = self._validate_writable_file_path(path)
        self._validate_blocked_path(path)
        if self._is_sensitive_path(path):
            raise PolicyDeniedError(
                "Writes to sensitive credential files are blocked by policy"
            )
        if not message.strip() or len(message) > 200:
            raise InvalidInputError("Commit message must contain 1 to 200 characters")
        if redact(content) != content:
            raise PolicyDeniedError(
                "File content appears to contain a secret and cannot be staged"
            )
        if len(content) > max(1, self.settings.max_output_chars // 2):
            raise InvalidInputError(
                "File content is too large to show in the confirmation response"
            )
        await self.validate_write_branch(repository, branch)

        encoded_path = quote(path, safe="/")
        sha: str | None = None
        try:
            data = await self._request(
                f"repos/{repository}/contents/{encoded_path}",
                {"ref": branch},
            )
            sha = data.get("sha") if isinstance(data, dict) else None
            if sha is not None and not isinstance(sha, str):
                raise ToolError("GitHub API returned an invalid file reference")
        except ToolError as exc:
            if "not found or is not accessible" not in str(exc):
                raise

        return {"repository": repository, "path": path, "branch": branch, "sha": sha}

    async def commit_file(
        self,
        repository: str,
        path: str,
        branch: str,
        content: str,
        message: str,
        sha: str | None,
    ) -> dict[str, Any]:
        repository = self.validate_write_repository(repository)
        path = self._validate_writable_file_path(path)
        self._validate_blocked_path(path)
        if self._is_sensitive_path(path):
            raise PolicyDeniedError(
                "Writes to sensitive credential files are blocked by policy"
            )
        if redact(content) != content or redact(message) != message:
            raise PolicyDeniedError(
                "File content or commit message appears to contain a secret"
            )
        if not message.strip() or len(message) > 200:
            raise InvalidInputError("Commit message must contain 1 to 200 characters")
        if len(content) > max(1, self.settings.max_output_chars // 2):
            raise InvalidInputError(
                "File content is too large to show in the confirmation response"
            )
        await self.validate_write_branch(repository, branch)
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        data = await self._write_request(
            "PUT",
            f"repos/{repository}/contents/{quote(path, safe='/')}",
            payload,
        )
        commit = data.get("commit") if isinstance(data, dict) else {}
        return self._shape(
            {
                "commit_sha": (commit or {}).get("sha"),
                "html_url": (commit or {}).get("html_url"),
                "content_path": (
                    (data.get("content") or {}).get("path")
                    if isinstance(data, dict)
                    else None
                ),
                "branch": branch,
            }
        )

    async def create_pull_request(
        self,
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        repository = self.validate_write_repository(repository)
        if not title.strip() or len(title) > 256:
            raise InvalidInputError(
                "Pull request title must contain 1 to 256 characters"
            )
        if len(body) > self.settings.max_output_chars // 2:
            raise InvalidInputError(
                "Pull request body is too large to show in the confirmation response"
            )
        if redact(title) != title or redact(body) != body:
            raise PolicyDeniedError("Pull request details appear to contain a secret")
        await self.validate_write_branch(repository, head)
        await self.get_branch_sha(repository, base)
        data = await self._write_request(
            "POST",
            f"repos/{repository}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )
        return self._shape(
            {
                "number": data.get("number") if isinstance(data, dict) else None,
                "html_url": data.get("html_url") if isinstance(data, dict) else None,
                "state": data.get("state") if isinstance(data, dict) else None,
            }
        )

    async def list_commits(
        self, repository: str, branch: str | None, limit_count: int
    ) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        params: dict[str, Any] = {}
        if branch:
            params["sha"] = branch
        data = await self._paginate(f"repos/{repository}/commits", params)
        return [
            self._shape(
                {
                    "sha": item.get("sha"),
                    "message": (item.get("commit") or {}).get("message"),
                    "author": ((item.get("commit") or {}).get("author") or {}).get(
                        "name"
                    ),
                    "date": ((item.get("commit") or {}).get("author") or {}).get(
                        "date"
                    ),
                    "html_url": item.get("html_url"),
                }
            )
            for item in data[:limit_count]
        ]

    async def list_issues(
        self, repository: str, state: str, limit_count: int
    ) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        data = await self._paginate(
            f"repos/{repository}/issues", {"state": state, "sort": "updated"}
        )
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

    async def list_pull_requests(
        self, repository: str, state: str, limit_count: int
    ) -> list[dict[str, Any]]:
        repository = self.validate_repository(repository)
        data = await self._paginate(
            f"repos/{repository}/pulls", {"state": state, "sort": "updated"}
        )
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
        if (
            "\x00" in path
            or "\\" in path
            or any(part in {".", ".."} for part in normalized.split("/"))
        ):
            raise InvalidInputError("Invalid GitHub path")
        return normalized

    @staticmethod
    def _validate_branch_name(branch: str) -> None:
        if (
            not branch
            or len(branch) > 240
            or branch.startswith(("/", "."))
            or branch.endswith(("/", "."))
            or ".." in branch
            or "@{" in branch
            or "//" in branch
            or any(char.isspace() or char in "~^:?*[\\" for char in branch)
            or any(part.endswith(".lock") for part in branch.split("/"))
        ):
            raise InvalidInputError("Invalid Git branch name")

    @classmethod
    def _validate_writable_file_path(cls, path: str) -> str:
        if path.startswith("/") or path.endswith("/") or "//" in path:
            raise InvalidInputError(
                "Write path must be a normalized repository-relative file path"
            )
        return cls._validate_path(path, allow_empty=False)

    def _validate_blocked_path(self, path: str) -> None:
        normalized = path.casefold()
        blocked = {".github/workflows"} | {
            item.casefold().strip("/") for item in self.settings.blocked_github_paths
        }
        if any(
            normalized == item or normalized.startswith(f"{item}/") for item in blocked
        ):
            raise PolicyDeniedError("Writes to this GitHub path are blocked by policy")

    @staticmethod
    def _is_sensitive_path(path: str) -> bool:
        name = path.rsplit("/", 1)[-1].casefold()
        return (
            name == ".env"
            or name.startswith(".env.")
            or name.endswith((".pem", ".key", ".p12", ".pfx"))
            or name in {"id_rsa", "id_ed25519"}
        )

    def validate_write_repository(self, repository: str) -> str:
        repository = self.validate_repository(repository)
        if not self.settings.allowed_github_repos:
            raise PolicyDeniedError(
                "GitHub writes require an explicit repository allowlist"
            )
        return repository

    async def _write_request(
        self, method: str, path: str, payload: dict[str, Any]
    ) -> Any:
        self.require_write_token()
        token = self.settings.github_write_token
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ToolError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise ToolError("GitHub API is unavailable") from exc

        if response.status_code == 401:
            raise ToolError("GitHub rejected the configured write token")
        if response.status_code == 403:
            if response.headers.get(
                "X-RateLimit-Remaining"
            ) == "0" or response.headers.get("Retry-After"):
                raise ToolError("GitHub API rate limit exceeded; try again later")
            raise ToolError("GitHub denied the write; check write token permissions")
        if response.status_code == 404:
            raise ToolError("GitHub resource was not found or is not accessible")
        if response.status_code == 429:
            raise ToolError("GitHub API rate limit exceeded; try again later")
        if response.is_error:
            raise ToolError(f"GitHub API returned HTTP {response.status_code}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ToolError("GitHub API returned an invalid response") from exc
