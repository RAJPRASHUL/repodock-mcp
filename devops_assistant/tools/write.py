import asyncio

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
    async def github_create_repository(
        repository: str,
        description: str = "",
        private: bool = True,
        initialize_with_readme: bool = True,
    ) -> str:
        """Stage creation of an allowlisted repository under the authenticated personal GitHub account; confirmation is required."""
        github.require_write_token()
        details = await github.prepare_repository_creation(
            repository, description, private, initialize_with_readme
        )
        visibility = "private" if private else "public"
        readme = "with an initial README commit" if initialize_with_readme else "empty"
        action_description = (
            f"Create {visibility} GitHub repository {repository} {readme}"
        )

        async def execute() -> dict:
            return await github.create_repository(
                repository, description, private, initialize_with_readme
            )

        return action_manager.stage(
            "github_create_repository",
            repository,
            action_description,
            details,
            execute,
            settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    async def github_create_branch(
        repository: str, branch: str, base_branch: str
    ) -> str:
        """Stage creation of an allowlisted GitHub branch; confirmation is required to create it."""
        github.require_write_token()
        details = await github.prepare_branch_creation(repository, branch, base_branch)
        description = (
            f"Create branch {details['branch']} in {details['repository']} "
            f"from {details['base_branch']} ({details['base_sha']})"
        )

        async def execute() -> dict:
            return await github.create_branch(repository, branch, details["base_sha"])

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
            execute,
            settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    async def github_commit_file(
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
        details = await github.prepare_file_update(
            repository, path, branch, content, message
        )
        operation = "Update" if details["sha"] else "Create"
        description = (
            f"{operation} {details['path']} in {repository} on branch {branch}"
        )

        async def execute() -> dict:
            return await github.commit_file(
                repository,
                details["path"],
                branch,
                content,
                message,
                details["sha"],
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
            execute,
            settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    async def github_create_pull_request(
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
        await github.validate_write_branch(repository, head)
        await github.get_branch_sha(repository, base)
        description = f"Open pull request in {repository}: {head} -> {base} ({title})"

        async def execute() -> dict:
            return await github.create_pull_request(repository, head, base, title, body)

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
            execute,
            settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    async def docker_restart_container(name: str) -> str:
        """Stage restart of an explicitly allowlisted non-production container; confirmation is required."""
        details = await asyncio.to_thread(docker.prepare_container_restart, name)
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
    async def confirm_action(token: str) -> str:
        """Perform exactly one staged action if its confirmation token is valid and unexpired."""
        return await action_manager.confirm(token, settings.enable_write_tools)
