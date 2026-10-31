import asyncio
import json
from typing import Any

from fastmcp import FastMCP

from ..audit import audit_tool
from ..security import limit
from ..services.docker_client import DockerClient


def _json_result(data: Any, max_chars: int) -> str:
    return limit(json.dumps(data, ensure_ascii=True, separators=(",", ":")), max_chars)


def register(mcp: FastMCP, client: DockerClient) -> None:
    @mcp.tool
    @audit_tool
    async def docker_list_containers(
        include_stopped: bool = False, limit: int = 50
    ) -> str:
        """List local containers; results are restricted to the configured container allowlist when set."""
        containers = await asyncio.to_thread(
            client.list_containers, include_stopped, limit
        )
        return _json_result(containers, client.settings.max_output_chars)

    @mcp.tool
    @audit_tool
    async def docker_list_images(limit: int = 50) -> str:
        """List local Docker images with a bounded result count."""
        images = await asyncio.to_thread(client.list_images, limit)
        return _json_result(images, client.settings.max_output_chars)

    @mcp.tool
    @audit_tool
    async def docker_container_status(name: str) -> str:
        """Return selected status metadata for an allowlisted container."""
        return _json_result(
            await asyncio.to_thread(client.container_status, name),
            client.settings.max_output_chars,
        )

    @mcp.tool
    @audit_tool
    async def docker_container_logs(name: str, tail: int = 100) -> str:
        """Return a bounded and redacted tail of logs from an allowlisted container."""
        return await asyncio.to_thread(client.container_logs, name, tail)

    @mcp.tool
    @audit_tool
    async def docker_inspect_container(name: str) -> str:
        """Return filtered container metadata without environment variables or host source paths."""
        return _json_result(
            await asyncio.to_thread(client.inspect_container, name),
            client.settings.max_output_chars,
        )
