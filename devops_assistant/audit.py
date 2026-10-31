import inspect
import json
import logging
import re
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from .errors import InvalidInputError, PolicyDeniedError
from .security import redact

log = logging.getLogger(__name__)

AUDIT_LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "audit.jsonl"


def ensure_audit_log() -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_PATH.touch(exist_ok=True)


def audit_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    decision: str,
    status: str,
    error: str | None = None,
    latency_ms: int | None = None,
) -> None:
    ensure_audit_log()
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool_name,
        "arguments": arguments,
        "decision": decision,
        "status": status,
        "latency_ms": latency_ms,
        "error": error,
    }
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def audit_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name = getattr(func, "__name__", func.__class__.__name__)
            started = time.perf_counter()
            decision = "allowed"
            status = "error"
            error: str | None = None
            try:
                result = await func(*args, **kwargs)
                status = "success"
                return result
            except Exception as exc:
                if isinstance(
                    exc, (InvalidInputError, PolicyDeniedError, PermissionError)
                ):
                    decision = "denied"
                error = redact(str(exc))
                raise
            finally:
                latency_ms = int((time.perf_counter() - started) * 1000)
                try:
                    audit_tool_call(
                        tool_name,
                        redact_arguments(kwargs),
                        decision,
                        status,
                        error,
                        latency_ms,
                    )
                except Exception:
                    log.exception("Failed to write audit log for %s", tool_name)

        return async_wrapper

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = getattr(func, "__name__", func.__class__.__name__)
        started = time.perf_counter()
        decision = "allowed"
        status = "error"
        error: str | None = None
        try:
            result = func(*args, **kwargs)
            status = "success"
            return result
        except Exception as exc:
            status = "error"
            if isinstance(exc, (InvalidInputError, PolicyDeniedError, PermissionError)):
                decision = "denied"
            error = redact(str(exc))
            raise
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            try:
                audit_tool_call(
                    tool_name,
                    redact_arguments(kwargs),
                    decision,
                    status,
                    error,
                    latency_ms,
                )
            except Exception:
                log.exception("Failed to write audit log for %s", tool_name)

    return wrapper


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    sensitive_key = re.compile(
        r"token|secret|password|content|body|payload|message|title|description",
        re.IGNORECASE,
    )
    return {
        key: "[OMITTED]"
        if sensitive_key.search(key)
        else redact(value)
        if isinstance(value, str)
        else value
        for key, value in arguments.items()
    }
