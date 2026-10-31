import asyncio

import docker

from devops_assistant import app
from devops_assistant.config import Settings


def test_health_check_reports_unavailable_services_without_secrets(monkeypatch) -> None:
    settings = Settings(github_token=None)

    def fail_docker_connection():
        raise docker.errors.DockerException("private daemon details")

    monkeypatch.setattr(app, "load_settings", lambda: settings)
    monkeypatch.setattr(app.docker, "from_env", fail_docker_connection)

    result = asyncio.run(app.health_check.__wrapped__())

    assert "GitHub token missing" in result
    assert "Docker unavailable" in result
    assert "private daemon details" not in result
