from types import SimpleNamespace
from unittest.mock import Mock

import docker
import pytest
from fastmcp.exceptions import ToolError

from devops_assistant.config import Settings
from devops_assistant.errors import PolicyDeniedError
from devops_assistant.services.docker_client import DockerClient


def make_container(name: str = "api", logs: bytes = b"ready\n") -> SimpleNamespace:
    attrs = {
        "Name": f"/{name}",
        "State": {
            "Status": "running",
            "Running": True,
            "ExitCode": 0,
            "StartedAt": "2026-01-01T00:00:00Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
        },
        "Config": {"Image": "example/api:latest", "Env": ["PASSWORD=secret-value"]},
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "8080"}]}},
        "Mounts": [
            {
                "Type": "bind",
                "Source": "C:\\private\\host\\path",
                "Destination": "/app",
                "RW": False,
            }
        ],
        "RestartCount": 1,
        "Created": "2026-01-01T00:00:00Z",
    }
    image = SimpleNamespace(tags=["example/api:latest"], short_id="sha256:123")
    container = SimpleNamespace(
        attrs=attrs,
        name=name,
        short_id="abc123",
        status="running",
        image=image,
        logs=Mock(return_value=logs),
        reload=Mock(),
        restart=Mock(),
    )
    return container


def make_client(allowed: set[str] | None = None) -> tuple[DockerClient, Mock]:
    settings = Settings(
        allowed_docker_containers=allowed or set(),
        max_log_lines=20,
        max_output_chars=100,
    )
    client = DockerClient(settings)
    sdk_client = Mock()
    client._client = sdk_client
    return client, sdk_client


def test_list_containers_filters_allowlist_and_stopped_option() -> None:
    client, sdk_client = make_client({"api"})
    sdk_client.containers.list.return_value = [
        make_container("api"),
        make_container("unlisted"),
    ]

    result = client.list_containers(include_stopped=True, limit_count=10)

    sdk_client.containers.list.assert_called_once_with(all=True)
    assert [container["name"] for container in result] == ["api"]


def test_container_access_is_denied_before_docker_lookup() -> None:
    client, sdk_client = make_client({"api"})

    with pytest.raises(PolicyDeniedError, match="allowlist"):
        client.container_status("unlisted")

    sdk_client.containers.get.assert_not_called()


def test_logs_are_bounded_and_redacted() -> None:
    client, sdk_client = make_client({"api"})
    container = make_container(logs=b"token=ghp_example123\n" + b"x" * 150)
    sdk_client.containers.get.return_value = container

    result = client.container_logs("api", tail=10)

    container.logs.assert_called_once_with(tail=10)
    assert "ghp_example123" not in result
    assert len(result) <= client.settings.max_output_chars


def test_invalid_log_tail_is_rejected() -> None:
    client, sdk_client = make_client({"api"})

    with pytest.raises(ToolError, match="tail must be between"):
        client.container_logs("api", tail=21)

    sdk_client.containers.get.assert_not_called()


def test_inspect_omits_environment_and_host_mount_source() -> None:
    client, sdk_client = make_client({"api"})
    sdk_client.containers.get.return_value = make_container()

    result = client.inspect_container("api")
    serialized = str(result)

    assert "PASSWORD" not in serialized
    assert "secret-value" not in serialized
    assert "C:\\private\\host\\path" not in serialized
    assert result["mounts"][0]["destination"] == "/app"


def test_not_found_is_returned_as_safe_tool_error() -> None:
    client, sdk_client = make_client({"api"})
    sdk_client.containers.get.side_effect = docker.errors.NotFound("private details")

    with pytest.raises(ToolError, match="Container 'api' was not found") as error:
        client.container_status("api")

    assert "private details" not in str(error.value)


def test_docker_daemon_failure_is_returned_as_safe_tool_error(monkeypatch) -> None:
    client = DockerClient(Settings())
    monkeypatch.setattr(
        "devops_assistant.services.docker_client.docker.from_env",
        Mock(side_effect=docker.errors.DockerException("private daemon details")),
    )

    with pytest.raises(ToolError, match="Docker is unavailable"):
        client.list_images(10)


def test_restart_requires_explicit_allowlist() -> None:
    client, sdk_client = make_client()

    with pytest.raises(PolicyDeniedError, match="explicit container allowlist"):
        client.prepare_container_restart("api")

    sdk_client.containers.get.assert_not_called()


def test_restart_rejects_production_container_by_name() -> None:
    client, sdk_client = make_client({"api-prod"})
    sdk_client.containers.get.return_value = make_container("api-prod")

    with pytest.raises(PolicyDeniedError, match="production containers"):
        client.prepare_container_restart("api-prod")

    sdk_client.containers.get.return_value.restart.assert_not_called()


def test_restart_requires_confirmation_before_state_change() -> None:
    client, sdk_client = make_client({"api"})
    container = make_container()
    sdk_client.containers.get.return_value = container

    details = client.prepare_container_restart("api")

    assert details["name"] == "api"
    container.restart.assert_not_called()


def test_restart_executes_only_after_confirmed_operation() -> None:
    client, sdk_client = make_client({"api"})
    container = make_container()
    sdk_client.containers.get.return_value = container

    result = client.restart_container("api")

    container.restart.assert_called_once_with(timeout=10)
    assert result == {"name": "api", "status": "restart_requested"}


def test_restart_rejects_production_environment_label() -> None:
    client, sdk_client = make_client({"api"})
    container = make_container()
    container.attrs["Config"]["Labels"] = {"environment": "production"}
    sdk_client.containers.get.return_value = container

    with pytest.raises(PolicyDeniedError, match="production containers"):
        client.prepare_container_restart("api")
