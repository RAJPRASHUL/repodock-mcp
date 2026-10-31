import asyncio
import threading
import time

import httpx
import respx
from fastmcp import FastMCP

from devops_assistant.config import Settings
from devops_assistant.services.docker_client import DockerClient
from devops_assistant.services.github_client import GitHubClient
from devops_assistant.tools.docker_read import register as register_docker_tools


@respx.mock
async def test_github_requests_can_overlap() -> None:
    both_requests_started = asyncio.Event()
    active_requests = 0

    async def delayed_response(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests
        assert request.method == "GET"
        active_requests += 1
        if active_requests == 2:
            both_requests_started.set()
        await asyncio.wait_for(both_requests_started.wait(), timeout=1)
        active_requests -= 1
        return httpx.Response(200, json={"full_name": "owner/repo"})

    respx.get("https://api.github.com/repos/owner/repo").mock(
        side_effect=delayed_response
    )
    client = GitHubClient(Settings(github_token="read-token"))

    results = await asyncio.gather(
        client.get_repository("owner/repo"),
        client.get_repository("owner/repo"),
    )

    assert both_requests_started.is_set()
    assert all(result["full_name"] == "owner/repo" for result in results)


async def test_docker_read_tools_offload_sdk_calls_to_worker_threads() -> None:
    sdk_call_finished = threading.Event()

    class SlowDockerClient(DockerClient):
        def list_images(self, limit_count: int) -> list[dict[str, str]]:
            assert limit_count == 1
            time.sleep(0.1)
            sdk_call_finished.set()
            return [{"id": "test-image"}]

    docker_client = SlowDockerClient(Settings(max_output_chars=1000))
    mcp = FastMCP("async-docker-test")
    register_docker_tools(mcp, docker_client)

    async def event_loop_probe() -> bool:
        await asyncio.sleep(0.02)
        return not sdk_call_finished.is_set()

    result, event_loop_remained_responsive = await asyncio.gather(
        mcp.call_tool("docker_list_images", {"limit": 1}),
        event_loop_probe(),
    )

    assert event_loop_remained_responsive
    assert sdk_call_finished.is_set()
    assert not result.is_error
