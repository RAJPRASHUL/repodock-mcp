from typing import Any

import docker
from docker.errors import APIError, DockerException, NotFound
from fastmcp.exceptions import ToolError

from ..config import Settings
from ..errors import PolicyDeniedError
from ..security import limit, redact

_MAX_RESULTS = 100


class DockerClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: docker.DockerClient | None = None

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env(timeout=10)
            except DockerException as exc:
                raise ToolError("Docker is unavailable; check the daemon and connection settings") from exc
            except OSError as exc:
                raise ToolError("Docker connection settings are invalid or unavailable") from exc
        return self._client

    def list_containers(self, include_stopped: bool, limit_count: int) -> list[dict[str, Any]]:
        self._validate_limit(limit_count)
        try:
            containers = self._get_client().containers.list(all=include_stopped)
            results: list[dict[str, Any]] = []
            allowed = {name.casefold() for name in self.settings.allowed_docker_containers}
            for container in containers:
                name = self._container_name(container)
                if allowed and name.casefold() not in allowed:
                    continue
                results.append(
                    self._shape(
                        {
                            "name": name,
                            "id": getattr(container, "short_id", None),
                            "image": self._image_name(container),
                            "status": getattr(container, "status", None),
                        }
                    )
                )
                if len(results) >= limit_count:
                    break
            return results
        except DockerException as exc:
            raise self._map_docker_error(exc) from exc

    def list_images(self, limit_count: int) -> list[dict[str, Any]]:
        self._validate_limit(limit_count)
        try:
            images = self._get_client().images.list()
            return [
                self._shape(
                    {
                        "id": getattr(image, "short_id", None),
                        "tags": list(getattr(image, "tags", []) or []),
                        "created": getattr(image, "attrs", {}).get("Created"),
                        "size": getattr(image, "attrs", {}).get("Size"),
                    }
                )
                for image in images[:limit_count]
            ]
        except DockerException as exc:
            raise self._map_docker_error(exc) from exc

    def container_status(self, name: str) -> dict[str, Any]:
        container = self._get_container(name)
        try:
            container.reload()
        except DockerException as exc:
            raise self._map_docker_error(exc) from exc

        attrs = getattr(container, "attrs", {})
        state = attrs.get("State") or {}
        return self._shape(
            {
                "name": self._container_name(container),
                "id": getattr(container, "short_id", None),
                "image": self._image_name(container),
                "status": state.get("Status", getattr(container, "status", None)),
                "running": state.get("Running"),
                "exit_code": state.get("ExitCode"),
                "started_at": state.get("StartedAt"),
                "finished_at": state.get("FinishedAt"),
                "restart_count": attrs.get("RestartCount"),
            }
        )

    def container_logs(self, name: str, tail: int) -> str:
        if not 1 <= tail <= self.settings.max_log_lines:
            raise ToolError(f"tail must be between 1 and {self.settings.max_log_lines}")
        container = self._get_container(name)
        try:
            raw_logs = container.logs(tail=tail)
        except DockerException as exc:
            raise self._map_docker_error(exc) from exc
        if isinstance(raw_logs, bytes):
            logs = raw_logs.decode("utf-8", "replace")
        else:
            logs = str(raw_logs)
        return limit(redact(logs), self.settings.max_output_chars)

    def inspect_container(self, name: str) -> dict[str, Any]:
        container = self._get_container(name)
        try:
            container.reload()
        except DockerException as exc:
            raise self._map_docker_error(exc) from exc

        attrs = getattr(container, "attrs", {})
        state = attrs.get("State") or {}
        config = attrs.get("Config") or {}
        return self._shape(
            {
                "name": self._container_name(container),
                "id": getattr(container, "short_id", None),
                "image": config.get("Image") or self._image_name(container),
                "created": attrs.get("Created"),
                "state": {
                    "status": state.get("Status"),
                    "running": state.get("Running"),
                    "exit_code": state.get("ExitCode"),
                    "started_at": state.get("StartedAt"),
                    "finished_at": state.get("FinishedAt"),
                },
                "ports": (attrs.get("NetworkSettings") or {}).get("Ports") or {},
                "mounts": [
                    {
                        "type": mount.get("Type"),
                        "destination": mount.get("Destination"),
                        "read_only": not mount.get("RW", True),
                    }
                    for mount in attrs.get("Mounts", [])
                    if isinstance(mount, dict)
                ],
            }
        )

    def _get_container(self, name: str) -> Any:
        self._require_allowed(name)
        try:
            return self._get_client().containers.get(name)
        except NotFound as exc:
            raise ToolError(f"Container '{name}' was not found") from exc
        except DockerException as exc:
            raise self._map_docker_error(exc) from exc

    def _require_allowed(self, name: str) -> None:
        if not name or not name.strip():
            raise ToolError("A container name or ID is required")
        allowed = {item.casefold() for item in self.settings.allowed_docker_containers}
        if allowed and name.casefold() not in allowed:
            raise PolicyDeniedError("Container is not on the configured allowlist")

    @staticmethod
    def _validate_limit(limit_count: int) -> None:
        if not 1 <= limit_count <= _MAX_RESULTS:
            raise ToolError(f"limit must be between 1 and {_MAX_RESULTS}")

    @staticmethod
    def _container_name(container: Any) -> str:
        names = getattr(container, "attrs", {}).get("Name")
        return str(names or getattr(container, "name", "")).lstrip("/")

    @staticmethod
    def _image_name(container: Any) -> str | None:
        image = getattr(container, "image", None)
        tags = getattr(image, "tags", None)
        if tags:
            return redact(str(tags[0]))
        return getattr(image, "short_id", None)

    @staticmethod
    def _shape(data: dict[str, Any]) -> dict[str, Any]:
        return {
            key: redact(value) if isinstance(value, str) else value
            for key, value in data.items()
        }

    @staticmethod
    def _map_docker_error(error: DockerException) -> ToolError:
        if isinstance(error, NotFound):
            return ToolError("Docker resource was not found")
        if isinstance(error, APIError) and getattr(error, "status_code", None) in {401, 403}:
            return ToolError("Docker denied the request; check daemon permissions")
        return ToolError("Docker is unavailable or returned an error")
