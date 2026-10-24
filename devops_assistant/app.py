import logging

import docker
import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .audit import audit_tool
from .config import load_settings

log = logging.getLogger(__name__)
mcp = FastMCP("DevOps Assistant")


@mcp.tool
@audit_tool
def health_check(name: str = "developer") -> str:
    """Verify that the DevOps MCP server is running."""
    settings = load_settings()
    checks: list[str] = []

    checks.append("GitHub token configured" if settings.github_token else "GitHub token missing")
    checks.append("GitHub API base URL configured" if settings.github_api_base_url else "GitHub API base URL missing")

    docker_ok = False
    docker_status = "Docker unavailable"
    try:
        docker_ok = docker.from_env().ping()
        docker_status = "Docker reachable" if docker_ok else "Docker ping failed"
    except Exception:
        log.exception("Docker connectivity check failed")
        docker_status = "Docker unavailable"
    checks.append(docker_status)

    github_ok = False
    github_status = "GitHub token invalid"
    if settings.github_token:
        try:
            response = httpx.get(
                f"{settings.github_api_base_url}/user",
                headers={"Authorization": f"Bearer {settings.github_token}", "Accept": "application/vnd.github+json"},
                timeout=10.0,
            )
            if response.status_code == 200:
                github_ok = True
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

    summary = f"Hello, {name}! Your MCP server is working. Checks: {', '.join(checks)}."
    if not (settings.github_token and docker_ok and github_ok):
        raise ToolError("One or more required checks failed; see the health summary for details.")
    return summary


@mcp.tool
@audit_tool
def config_status() -> dict[str, str | bool]:
    """Return safe configuration status without exposing secret values."""
    settings = load_settings()
    return {
        "github_token_configured": bool(settings.github_token),
        "allowed_github_repos": len(settings.allowed_github_repos),
        "allowed_docker_containers": len(settings.allowed_docker_containers),
        "enable_write_tools": settings.enable_write_tools,
        "github_api_base_url": settings.github_api_base_url,
    }
