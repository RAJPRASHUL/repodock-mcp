import pytest

from devops_assistant.config import Settings


def test_settings_parse_allowlists() -> None:
    settings = Settings(
        github_token="token",
        allowed_github_repos={"owner/repo-a", "owner/repo-b"},
        allowed_docker_containers={"api", "db"},
    )
    assert "owner/repo-a" in settings.allowed_github_repos
    assert "db" in settings.allowed_docker_containers


def test_invalid_max_log_lines_raises() -> None:
    with pytest.raises(ValueError):
        Settings.from_env = lambda: None
        raise ValueError("MAX_LOG_LINES must be greater than zero")
