import asyncio
import inspect
import json
import logging
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError

from .audit import audit_tool_call
from .security import redact

_ACTION_TTL_SECONDS = 300
_MAX_PENDING_ACTIONS = 100
log = logging.getLogger(__name__)


@dataclass
class PendingAction:
    operation: str
    target: str
    description: str
    preview: dict[str, Any]
    execute: Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]]
    expires_at: float


class ActionManager:
    def __init__(self) -> None:
        self._pending: dict[str, PendingAction] = {}
        self._lock = threading.Lock()

    def stage(
        self,
        operation: str,
        target: str,
        description: str,
        preview: dict[str, Any],
        execute: Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]],
        max_output_chars: int,
    ) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = time.monotonic() + _ACTION_TTL_SECONDS
        response = {
            "confirmation_token": token,
            "expires_in_seconds": _ACTION_TTL_SECONDS,
            "action": description,
            "details": preview,
            "confirmation_instructions": "Call confirm_action with this token to perform exactly this action.",
        }
        serialized = json.dumps(response, ensure_ascii=True, separators=(",", ":"))
        if len(serialized) > max_output_chars:
            raise ToolError("Confirmation details exceed the configured output limit")

        pending = PendingAction(
            operation, target, description, preview, execute, expires_at
        )
        with self._lock:
            self._remove_expired(time.monotonic())
            if len(self._pending) >= _MAX_PENDING_ACTIONS:
                raise ToolError(
                    "Too many pending actions; wait for existing confirmations to expire"
                )
            self._pending[token] = pending
        return serialized

    async def confirm(self, token: str, enabled: bool) -> str:
        if not enabled:
            self._audit_invalid_confirmation()
            raise ToolError("Write tools are disabled")
        if not token or len(token) > 256:
            self._audit_invalid_confirmation()
            raise ToolError("Confirmation token is invalid or expired")

        with self._lock:
            action = self._pending.pop(token, None)

        if action is None or action.expires_at <= time.monotonic():
            self._audit_invalid_confirmation()
            raise ToolError("Confirmation token is invalid or expired")

        started = time.perf_counter()
        try:
            if inspect.iscoroutinefunction(action.execute):
                result = await action.execute()
            else:
                result = await asyncio.to_thread(action.execute)
                if inspect.isawaitable(result):
                    result = await result
        except Exception as exc:
            self._audit_action(action, "error", redact(str(exc)), started)
            log.warning(
                "Confirmed %s action failed for %s", action.operation, action.target
            )
            raise

        self._audit_action(action, "success", None, started)
        return json.dumps(
            {"status": "success", "action": action.description, "result": result},
            ensure_ascii=True,
            separators=(",", ":"),
        )

    def _remove_expired(self, now: float) -> None:
        expired = [
            token for token, action in self._pending.items() if action.expires_at <= now
        ]
        for token in expired:
            del self._pending[token]

    @staticmethod
    def _audit_invalid_confirmation() -> None:
        try:
            audit_tool_call(
                "confirm_action",
                {"target": "unknown"},
                "denied",
                "error",
                "Confirmation token is invalid or expired",
                0,
            )
        except Exception:
            log.exception("Failed to audit invalid action confirmation")

    @staticmethod
    def _audit_action(
        action: PendingAction,
        status: str,
        error: str | None,
        started: float,
    ) -> None:
        try:
            latency_ms = int((time.perf_counter() - started) * 1000)
            audit_tool_call(
                action.operation,
                {"target": redact(action.target)},
                "allowed",
                status,
                error,
                latency_ms,
            )
        except Exception:
            log.exception("Failed to audit confirmed %s action", action.operation)


action_manager = ActionManager()
