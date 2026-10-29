import os

import pytest

from devops_assistant.config import Settings
from devops_assistant.services.docker_client import DockerClient

pytestmark = pytest.mark.integration


def test_list_allowlisted_docker_containers() -> None:
    if os.getenv("DEVOPS_RUN_DOCKER_INTEGRATION", "").casefold() != "true":
        pytest.skip(
            "Set DEVOPS_RUN_DOCKER_INTEGRATION=true to enable the live read-only Docker test"
        )

    settings = Settings.from_env()
    if not settings.allowed_docker_containers:
        pytest.fail(
            "Set ALLOWED_DOCKER_CONTAINERS to explicitly authorize local test containers"
        )

    result = DockerClient(settings).list_containers(
        include_stopped=True, limit_count=100
    )

    allowed = {name.casefold() for name in settings.allowed_docker_containers}
    assert all(container["name"].casefold() in allowed for container in result)
