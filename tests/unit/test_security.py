from devops_assistant.security import limit, redact, require_allowed


def test_redact_covers_common_secrets() -> None:
    text = "token=ghp_abc123 password=supersecret"
    result = redact(text)
    assert "ghp_abc123" not in result
    assert "supersecret" not in result
    assert "[REDACTED]" in result


def test_limit_truncates_long_output() -> None:
    text = "x" * 50
    result = limit(text, 10)
    assert result.startswith("xxxxxxxxxx")
    assert "truncated" in result


def test_require_allowed_rejects_values_outside_allowlist() -> None:
    try:
        require_allowed("repo-b", {"repo-a"}, "Repository")
        assert False, "PermissionError should have been raised"
    except PermissionError:
        pass
