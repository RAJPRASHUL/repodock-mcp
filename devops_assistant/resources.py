import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from . import audit
from .config import Settings
from .security import redact

log = logging.getLogger(__name__)
_RECENT_AUDIT_COUNT = 20


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def allowed_targets(settings: Settings) -> str:
    data = {
        "github_repositories": _redact_value(sorted(settings.allowed_github_repos)),
        "github_repositories_unrestricted": not settings.allowed_github_repos,
        "docker_containers": _redact_value(sorted(settings.allowed_docker_containers)),
        "docker_containers_unrestricted": not settings.allowed_docker_containers,
    }
    return _bounded_json(data, settings.max_output_chars)


def recent_audit_entries(
    settings: Settings,
    path: Path | None = None,
) -> str:
    audit_path = path or audit.AUDIT_LOG_PATH
    try:
        if not audit_path.exists():
            return "[]"
        with audit_path.open("r", encoding="utf-8") as handle:
            lines = deque(handle, maxlen=_RECENT_AUDIT_COUNT)
    except OSError as exc:
        log.exception("Unable to read recent audit entries")
        raise ToolError("Recent audit entries are unavailable") from exc

    entries: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ToolError("Audit log contains an invalid entry") from exc
        if not isinstance(item, dict):
            raise ToolError("Audit log contains an invalid entry")
        entries.append(_redact_value(item))

    while entries:
        serialized = json.dumps(entries, ensure_ascii=True, separators=(",", ":"))
        if len(serialized) <= settings.max_output_chars:
            return serialized
        entries.pop(0)
    if not lines:
        return "[]"
    raise ToolError("A recent audit entry exceeds the configured output limit")


def _bounded_json(data: Any, max_chars: int) -> str:
    serialized = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) > max_chars:
        raise ToolError("Resource data exceeds the configured output limit")
    return serialized


def register(mcp: FastMCP, settings: Settings) -> None:
    @mcp.resource(
        "devops://config/allowed-targets",
        name="allowed_targets",
        description="Configured repository and container targets; no credentials are included.",
        mime_type="application/json",
    )
    def allowed_targets_resource() -> str:
        return allowed_targets(settings)

    @mcp.resource(
        "devops://audit/recent",
        name="recent_audit",
        description=f"The most recent {_RECENT_AUDIT_COUNT} audit entries, with secrets redacted.",
        mime_type="application/json",
    )
    def recent_audit_resource() -> str:
        return recent_audit_entries(settings)
