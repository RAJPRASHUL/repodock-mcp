import asyncio
import json

import httpx
import respx
from fastmcp import FastMCP

from devops_assistant.actions import ActionManager
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
        "github_create_repository",
        "github_create_branch",
        "github_commit_file",
        "github_create_pull_request",
        "docker_restart_container",
        "confirm_action",
    }


@respx.mock
async def test_repository_creation_is_staged_until_confirmation(monkeypatch) -> None:
    settings = Settings(
        enable_write_tools=True,
        github_token="read-token",
        github_write_token="write-token",
        allowed_github_repos={"owner/new-repo"},
    )
    manager = ActionManager()
    monkeypatch.setattr("devops_assistant.tools.write.action_manager", manager)
    mcp = FastMCP("test")
    register(mcp, settings, GitHubClient(settings), DockerClient(settings))

    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, json={"login": "owner"})
    )
    respx.get("https://api.github.com/repos/owner/new-repo").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    create_route = respx.post("https://api.github.com/user/repos").mock(
        return_value=httpx.Response(
            201,
            json={
                "full_name": "owner/new-repo",
                "html_url": "https://github.com/owner/new-repo",
                "private": True,
                "default_branch": "main",
            },
        )
    )

    staged_result = await mcp.call_tool(
        "github_create_repository",
        {"repository": "owner/new-repo", "description": "A test repository"},
    )
    staged = json.loads(staged_result.content[0].text)

    assert not create_route.called
    assert staged["details"]["private"] is True
    assert staged["details"]["initialize_with_readme"] is True

    confirmation = await mcp.call_tool(
        "confirm_action", {"token": staged["confirmation_token"]}
    )

    assert not confirmation.is_error
    assert create_route.called
