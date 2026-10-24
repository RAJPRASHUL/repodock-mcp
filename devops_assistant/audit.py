import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

AUDIT_LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "audit.jsonl"


def ensure_audit_log() -> None:
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_PATH.touch(exist_ok=True)


def audit_tool_call(tool_name: str, arguments: dict[str, Any], decision: str, status: str, error: str | None = None, latency_ms: int | None = None) -> None:
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
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = getattr(func, "__name__", func.__class__.__name__)
        started = time.perf_counter()
        decision = "allowed"
        try:
            result = func(*args, **kwargs)
            status = "success"
            error = None
            return result
        except Exception as exc:  # pragma: no cover - fallback
            status = "error"
            error = str(exc)
            raise
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            try:
                audit_tool_call(tool_name, {**kwargs}, decision, status, error, latency_ms)
            except Exception:
                log.exception("Failed to write audit log for %s", tool_name)

    return wrapper
