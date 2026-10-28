from devops_assistant.config import Settings


def test_settings_parse_allowlists(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("ALLOWED_GITHUB_REPOS", "owner/repo-a, owner/repo-b")
    monkeypatch.setenv("ALLOWED_DOCKER_CONTAINERS", "api,db")

    settings = Settings.from_env()

    assert settings.github_token == "test-token"
    assert settings.allowed_github_repos == {"owner/repo-a", "owner/repo-b"}
    assert settings.allowed_docker_containers == {"api", "db"}


def test_invalid_max_log_lines_raises(monkeypatch) -> None:
    monkeypatch.setenv("MAX_LOG_LINES", "0")

    try:
        Settings.from_env()
        assert False, "ValueError should have been raised"
    except ValueError as error:
        assert "MAX_LOG_LINES" in str(error)


def test_invalid_github_api_url_raises(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_API_BASE_URL", "file:///etc/passwd")

    try:
        Settings.from_env()
        assert False, "ValueError should have been raised"
    except ValueError as error:
        assert "HTTP or HTTPS" in str(error)


def test_write_tools_are_disabled_and_workflow_path_blocked_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_WRITE_TOOLS", raising=False)
    monkeypatch.delenv("BLOCKED_GITHUB_PATHS", raising=False)

    settings = Settings.from_env()

    assert settings.enable_write_tools is False
    assert settings.blocked_github_paths == {".github/workflows"}


def test_invalid_write_tools_flag_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_WRITE_TOOLS", "yes")

    try:
        Settings.from_env()
        assert False, "ValueError should have been raised"
    except ValueError as error:
        assert "ENABLE_WRITE_TOOLS" in str(error)
