import asyncio
import logging

import docker
import httpx
from fastmcp import FastMCP

from .audit import audit_tool
from .config import load_settings
from .prompts import register as register_prompts
from .resources import register as register_resources
from .services.docker_client import DockerClient
from .services.github_client import GitHubClient
from .tools.docker_read import register as register_docker_tools
from .tools.github_read import register as register_github_tools
from .tools.write import register as register_write_tools

log = logging.getLogger(__name__)
mcp = FastMCP("DevOps Assistant")
settings = load_settings()
register_github_tools(mcp, GitHubClient(settings))
register_docker_tools(mcp, DockerClient(settings))
register_resources(mcp, settings)
register_prompts(mcp)
register_write_tools(mcp, settings, GitHubClient(settings), DockerClient(settings))


@mcp.tool
@audit_tool
async def health_check(name: str = "developer") -> str:
    """Verify that the DevOps MCP server is running."""
    settings = load_settings()
    checks: list[str] = []

    checks.append(
        "GitHub token configured" if settings.github_token else "GitHub token missing"
    )
    checks.append(
        "GitHub API base URL configured"
        if settings.github_api_base_url
        else "GitHub API base URL missing"
    )

    docker_ok = False
    docker_status = "Docker unavailable"
    try:
        docker_ok = await asyncio.to_thread(lambda: docker.from_env().ping())
        docker_status = "Docker reachable" if docker_ok else "Docker ping failed"
    except (docker.errors.DockerException, OSError):
        log.exception("Docker connectivity check failed")
        docker_status = "Docker unavailable"
    checks.append(docker_status)

    github_status = "GitHub token not checked"
    if settings.github_token:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.github_api_base_url}/user",
                    headers={
                        "Authorization": f"Bearer {settings.github_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            if response.status_code == 200:
                github_status = "GitHub token valid"
            elif response.status_code == 401:
                github_status = "GitHub token invalid"
            else:
                github_status = f"GitHub API status {response.status_code}"
        except httpx.HTTPError:
            github_status = "GitHub API unreachable"
    else:
        github_status = "GitHub token missing"
    checks.append(github_status)

    return f"Hello, {name}! Checks: {', '.join(checks)}."


@mcp.tool
@audit_tool
def config_status() -> dict[str, str | int | bool]:
    """Return safe configuration status without exposing secret values."""
    settings = load_settings()
    return {
        "github_token_configured": bool(settings.github_token),
        "github_write_token_configured": bool(settings.github_write_token),
        "allowed_github_repos": len(settings.allowed_github_repos),
        "allowed_docker_containers": len(settings.allowed_docker_containers),
        "enable_write_tools": settings.enable_write_tools,
        "github_api_base_url": settings.github_api_base_url,
    }
