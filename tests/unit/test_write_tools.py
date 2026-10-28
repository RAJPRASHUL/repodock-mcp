import asyncio

from fastmcp import FastMCP

from devops_assistant.config import Settings
from devops_assistant.services.docker_client import DockerClient
from devops_assistant.services.github_client import GitHubClient
from devops_assistant.tools.write import register


def test_write_tools_are_not_registered_by_default() -> None:
    mcp = FastMCP("test")
    settings = Settings(enable_write_tools=False)

    register(mcp, settings, GitHubClient(settings), DockerClient(settings))

    assert asyncio.run(mcp.list_tools()) == []


def test_write_tools_are_registered_only_when_enabled() -> None:
    mcp = FastMCP("test")
    settings = Settings(
        enable_write_tools=True,
        github_token="read-token",
        github_write_token="write-token",
        allowed_github_repos={"owner/repo"},
        allowed_docker_containers={"api"},
    )

    register(mcp, settings, GitHubClient(settings), DockerClient(settings))

    assert {tool.name for tool in asyncio.run(mcp.list_tools())} == {
        "github_create_branch",
        "github_commit_file",
        "github_create_pull_request",
        "docker_restart_container",
        "confirm_action",
    }
