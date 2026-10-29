from fastmcp import FastMCP

from ..actions import action_manager
from ..audit import audit_tool
from ..config import Settings
from ..errors import InvalidInputError, PolicyDeniedError
from ..security import redact
from ..services.docker_client import DockerClient
from ..services.github_client import GitHubClient


def register(
    mcp: FastMCP,
    settings: Settings,
    github: GitHubClient,
    docker: DockerClient,
) -> None:
    if not settings.enable_write_tools:
        return

    @mcp.tool
    @audit_tool
    def github_create_branch(repository: str, branch: str, base_branch: str) -> str:
        """Stage creation of an allowlisted GitHub branch; confirmation is required to create it."""
        github.require_write_token()
        details = github.prepare_branch_creation(repository, branch, base_branch)
        description = (
            f"Create branch {details['branch']} in {details['repository']} "
            f"from {details['base_branch']} ({details['base_sha']})"
        )
        return action_manager.stage(
            "github_create_branch",
            f"{repository}:{branch}",
            description,
            {
                "repository": repository,
                "branch": branch,
                "base_branch": base_branch,
                "base_commit": details["base_sha"],
            },
            lambda: github.create_branch(repository, branch, details["base_sha"]),
            settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    def github_commit_file(
        repository: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> str:
        """Stage one file create/update on an allowlisted prefixed branch; exact content is shown for confirmation."""
        github.require_write_token()
        if redact(message) != message:
            raise PolicyDeniedError("Commit message appears to contain a secret")
        details = github.prepare_file_update(repository, path, branch, content, message)
        operation = "Update" if details["sha"] else "Create"
        description = (
            f"{operation} {details['path']} in {repository} on branch {branch}"
        )
        return action_manager.stage(
            "github_commit_file",
            f"{repository}:{branch}:{path}",
            description,
            {
                "repository": repository,
                "branch": branch,
                "path": details["path"],
                "commit_message": message,
                "existing_file": details["sha"] is not None,
                "proposed_content": content,
            },
            lambda: github.commit_file(
                repository,
                details["path"],
                branch,
                content,
                message,
                details["sha"],
            ),
            settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    def github_create_pull_request(
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str = "",
    ) -> str:
        """Stage a pull request from an allowlisted prefixed branch for human review; no merge operation is available."""
        github.require_write_token()
        if not title.strip() or len(title) > 256:
            raise InvalidInputError(
                "Pull request title must contain 1 to 256 characters"
            )
        if len(body) > settings.max_output_chars // 2:
            raise InvalidInputError(
                "Pull request body is too large to show in the confirmation response"
            )
        if redact(title) != title or redact(body) != body:
            raise PolicyDeniedError("Pull request details appear to contain a secret")
        github.validate_write_branch(repository, head)
        github.get_branch_sha(repository, base)
        description = f"Open pull request in {repository}: {head} -> {base} ({title})"
        return action_manager.stage(
            "github_create_pull_request",
            f"{repository}:{head}->{base}",
            description,
            {
                "repository": repository,
                "head": head,
                "base": base,
                "title": title,
                "body": body,
            },
            lambda: github.create_pull_request(repository, head, base, title, body),
            settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    def docker_restart_container(name: str) -> str:
        """Stage restart of an explicitly allowlisted non-production container; confirmation is required."""
        details = docker.prepare_container_restart(name)
        description = f"Restart allowlisted development container {details['name']}"
        return action_manager.stage(
            "docker_restart_container",
            details["name"],
            description,
            details,
            lambda: docker.restart_container(name),
            settings.max_output_chars,
        )

    @mcp.tool
    def confirm_action(token: str) -> str:
        """Perform exactly one staged action if its confirmation token is valid and unexpired."""
        return action_manager.confirm(token, settings.enable_write_tools)
