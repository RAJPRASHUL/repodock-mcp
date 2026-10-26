import base64

import httpx
import pytest
import respx
from fastmcp.exceptions import ToolError

from devops_assistant.config import Settings
from devops_assistant.services.github_client import GitHubClient


def make_client(**overrides) -> GitHubClient:
    settings = Settings(
        github_token="test-token",
        allowed_github_repos={"owner/repo"},
        max_output_chars=64,
        **overrides,
    )
    return GitHubClient(settings)


@respx.mock
def test_list_repositories_filters_to_allowlist() -> None:
    route = respx.get("https://api.github.com/user/repos").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"full_name": "owner/repo", "private": True},
                {"full_name": "someone/other", "private": False},
            ],
        )
    )

    repositories = make_client().list_repositories(30)

    assert [repo["full_name"] for repo in repositories] == ["owner/repo"]
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"


@respx.mock
def test_read_file_redacts_and_bounds_content() -> None:
    content = "token=ghp_examplesecret\n" + ("a" * 100)
    encoded = base64.b64encode(content.encode()).decode()
    respx.get("https://api.github.com/repos/owner/repo/contents/README.md").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": encoded})
    )

    result = make_client().read_file("owner/repo", "README.md", None)

    assert "ghp_examplesecret" not in result
    assert len(result) == 64
    assert "truncated" in result


@respx.mock
def test_allowlist_denies_repository_before_network_request() -> None:
    client = make_client()

    with pytest.raises(ToolError, match="allowlist"):
        client.get_repository("someone/other")

    assert not respx.calls


@respx.mock
def test_api_errors_are_mapped_to_safe_messages() -> None:
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(404, json={"message": "private repository details"})
    )

    with pytest.raises(ToolError, match="not found or is not accessible") as error:
        make_client().get_repository("owner/repo")

    assert "private repository details" not in str(error.value)


@respx.mock
def test_rate_limit_errors_are_mapped() -> None:
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0"},
            json={"message": "rate limit details"},
        )
    )

    with pytest.raises(ToolError, match="rate limit exceeded"):
        make_client().get_repository("owner/repo")


@respx.mock
def test_network_timeouts_are_mapped() -> None:
    respx.get("https://api.github.com/repos/owner/repo").mock(
        side_effect=httpx.ConnectTimeout("request timed out")
    )

    with pytest.raises(ToolError, match="request timed out"):
        make_client().get_repository("owner/repo")


def test_path_traversal_is_rejected() -> None:
    with pytest.raises(ToolError, match="Invalid GitHub path"):
        make_client().read_file("owner/repo", "../secrets.txt", None)


def test_missing_token_fails_without_network_access() -> None:
    client = GitHubClient(Settings(github_token=None))

    with pytest.raises(ToolError, match="GITHUB_TOKEN is required"):
        client.get_repository("owner/repo")
