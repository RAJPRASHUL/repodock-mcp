import asyncio
import os

import pytest

from devops_assistant.config import Settings
from devops_assistant.services.github_client import GitHubClient

pytestmark = pytest.mark.integration


def test_read_repository_metadata_from_github() -> None:
    if os.getenv("DEVOPS_RUN_GITHUB_INTEGRATION", "").casefold() != "true":
        pytest.skip(
            "Set DEVOPS_RUN_GITHUB_INTEGRATION=true to enable the live read-only GitHub test"
        )

    settings = Settings.from_env()
    if not settings.github_token:
        pytest.fail("GITHUB_TOKEN is required for the opt-in GitHub integration test")
    if len(settings.allowed_github_repos) != 1:
        pytest.fail(
            "Set ALLOWED_GITHUB_REPOS to exactly one authorized test repository"
        )

    repository = next(iter(settings.allowed_github_repos))
    result = asyncio.run(GitHubClient(settings).get_repository(repository))

    assert result["full_name"].casefold() == repository.casefold()
