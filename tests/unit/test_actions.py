import asyncio
import json

import pytest
from fastmcp.exceptions import ToolError

from devops_assistant.actions import ActionManager


def ignore_audit(*_: object) -> None:
    pass


def test_confirmation_executes_staged_action_once(monkeypatch) -> None:
    manager = ActionManager()
    executed: list[str] = []
    monkeypatch.setattr("devops_assistant.actions.audit_tool_call", ignore_audit)
    staged = json.loads(
        manager.stage(
            "test_write",
            "owner/repo:ai/change",
            "Make one change",
            {"content": "exact content"},
            lambda: executed.append("done") or {"ok": True},
            1000,
        )
    )

    result = json.loads(
        asyncio.run(manager.confirm(staged["confirmation_token"], enabled=True))
    )

    assert result["status"] == "success"
    assert result["result"] == {"ok": True}
    assert executed == ["done"]
    with pytest.raises(ToolError, match="invalid or expired"):
        asyncio.run(manager.confirm(staged["confirmation_token"], enabled=True))
    assert executed == ["done"]


def test_confirmation_is_disabled_without_execution(monkeypatch) -> None:
    manager = ActionManager()
    executed = False
    monkeypatch.setattr("devops_assistant.actions.audit_tool_call", ignore_audit)
    staged = json.loads(
        manager.stage(
            "test_write",
            "target",
            "Make one change",
            {},
            lambda: {"executed": True},
            1000,
        )
    )

    with pytest.raises(ToolError, match="Write tools are disabled"):
        asyncio.run(manager.confirm(staged["confirmation_token"], enabled=False))
    assert executed is False


def test_expired_confirmation_cannot_execute(monkeypatch) -> None:
    manager = ActionManager()
    current_time = 100.0
    monkeypatch.setattr("devops_assistant.actions.time.monotonic", lambda: current_time)
    monkeypatch.setattr("devops_assistant.actions.audit_tool_call", ignore_audit)
    staged = json.loads(manager.stage("test", "target", "action", {}, dict, 1000))
    current_time += 301

    with pytest.raises(ToolError, match="invalid or expired"):
        asyncio.run(manager.confirm(staged["confirmation_token"], enabled=True))


def test_confirmation_preview_must_fit_configured_output_limit() -> None:
    manager = ActionManager()

    with pytest.raises(ToolError, match="exceed the configured output limit"):
        manager.stage(
            "test",
            "target",
            "action",
            {"proposed_content": "x" * 100},
            dict,
            20,
        )


def test_audit_arguments_omit_tokens_and_write_payloads() -> None:
    from devops_assistant.audit import redact_arguments

    safe = redact_arguments(
        {
            "confirmation_token": "private-token",
            "content": "private file contents",
            "repository": "owner/repo",
        }
    )

    assert safe == {
        "confirmation_token": "[OMITTED]",
        "content": "[OMITTED]",
        "repository": "owner/repo",
    }
