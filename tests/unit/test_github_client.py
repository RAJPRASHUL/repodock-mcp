import base64

import httpx
import pytest
import respx
from fastmcp.exceptions import ToolError

from devops_assistant.config import Settings
from devops_assistant.services.github_client import GitHubClient


def make_client(**overrides) -> GitHubClient:
    values = {
        "github_token": "test-token",
        "allowed_github_repos": {"owner/repo"},
        "max_output_chars": 64,
        **overrides,
    }
    settings = Settings(**values)
    return GitHubClient(settings)


@respx.mock
async def test_list_repositories_filters_to_allowlist() -> None:
    route = respx.get("https://api.github.com/user/repos").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"full_name": "owner/repo", "private": True},
                {"full_name": "someone/other", "private": False},
            ],
        )
    )

    repositories = await make_client().list_repositories(30)

    assert [repo["full_name"] for repo in repositories] == ["owner/repo"]
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_read_file_redacts_and_bounds_content() -> None:
    content = "token=ghp_examplesecret\n" + ("a" * 100)
    encoded = base64.b64encode(content.encode()).decode()
    respx.get("https://api.github.com/repos/owner/repo/contents/README.md").mock(
        return_value=httpx.Response(
            200, json={"encoding": "base64", "content": encoded}
        )
    )

    result = await make_client().read_file("owner/repo", "README.md", None)

    assert "ghp_examplesecret" not in result
    assert len(result) == 64
    assert "truncated" in result


@respx.mock
async def test_allowlist_denies_repository_before_network_request() -> None:
    client = make_client()

    with pytest.raises(ToolError, match="allowlist"):
        await client.get_repository("someone/other")

    assert not respx.calls


@respx.mock
async def test_api_errors_are_mapped_to_safe_messages() -> None:
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(404, json={"message": "private repository details"})
    )

    with pytest.raises(ToolError, match="not found or is not accessible") as error:
        await make_client().get_repository("owner/repo")

    assert "private repository details" not in str(error.value)


@respx.mock
async def test_rate_limit_errors_are_mapped() -> None:
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0"},
            json={"message": "rate limit details"},
        )
    )

    with pytest.raises(ToolError, match="rate limit exceeded"):
        await make_client().get_repository("owner/repo")


@respx.mock
async def test_network_timeouts_are_mapped() -> None:
    respx.get("https://api.github.com/repos/owner/repo").mock(
        side_effect=httpx.ConnectTimeout("request timed out")
    )

    with pytest.raises(ToolError, match="request timed out"):
        await make_client().get_repository("owner/repo")


async def test_path_traversal_is_rejected() -> None:
    with pytest.raises(ToolError, match="Invalid GitHub path"):
        await make_client().read_file("owner/repo", "../secrets.txt", None)


async def test_missing_token_fails_without_network_access() -> None:
    client = GitHubClient(Settings(github_token=None))

    with pytest.raises(ToolError, match="GITHUB_TOKEN is required"):
        await client.get_repository("owner/repo")


@respx.mock
async def test_file_commit_requires_separate_write_token_and_prefixed_branch() -> None:
    client = make_client(github_write_token="write-token")
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    respx.get("https://api.github.com/repos/owner/repo/git/ref/heads/ai%2Fchange").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "branch-sha"}})
    )
    respx.get("https://api.github.com/repos/owner/repo/contents/README.md").mock(
        return_value=httpx.Response(200, json={"sha": "file-sha"})
    )
    put_route = respx.put(
        "https://api.github.com/repos/owner/repo/contents/README.md"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "commit": {
                    "sha": "commit-sha",
                    "html_url": "https://github.com/owner/repo/commit/commit-sha",
                },
                "content": {"path": "README.md"},
            },
        )
    )

    details = await client.prepare_file_update(
        "owner/repo", "README.md", "ai/change", "safe content", "Update"
    )
    result = await client.commit_file(
        "owner/repo",
        "README.md",
        "ai/change",
        "safe content",
        "Update",
        details["sha"],
    )

    assert details["sha"] == "file-sha"
    assert result["commit_sha"] == "commit-sha"
    assert put_route.calls[0].request.headers["Authorization"] == "Bearer write-token"
    assert put_route.calls[0].request.read().find(b"file-sha") >= 0


@respx.mock
async def test_branch_creation_uses_only_confirmation_stage_data() -> None:
    client = make_client(github_write_token="write-token")
    respx.get("https://api.github.com/repos/owner/repo/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "base-sha"}})
    )
    post_route = respx.post("https://api.github.com/repos/owner/repo/git/refs").mock(
        return_value=httpx.Response(
            201,
            json={"ref": "refs/heads/ai/change", "object": {"sha": "new-branch-sha"}},
        )
    )

    details = await client.prepare_branch_creation("owner/repo", "ai/change", "main")
    result = await client.create_branch("owner/repo", "ai/change", details["base_sha"])

    assert result["sha"] == "new-branch-sha"
    assert post_route.calls[0].request.headers["Authorization"] == "Bearer write-token"
    assert b"base-sha" in post_route.calls[0].request.read()


@respx.mock
async def test_pull_request_creation_uses_prefixed_branch_and_separate_token() -> None:
    client = make_client(github_write_token="write-token")
    respx.get("https://api.github.com/repos/owner/repo").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    respx.get("https://api.github.com/repos/owner/repo/git/ref/heads/ai%2Fchange").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "head-sha"}})
    )
    respx.get("https://api.github.com/repos/owner/repo/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "base-sha"}})
    )
    post_route = respx.post("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(
            201,
            json={
                "number": 12,
                "html_url": "https://github.com/owner/repo/pull/12",
                "state": "open",
            },
        )
    )

    result = await client.create_pull_request(
        "owner/repo", "ai/change", "main", "Title", "Body"
    )

    assert result["number"] == 12
    assert post_route.calls[0].request.headers["Authorization"] == "Bearer write-token"
    assert b'"head":"ai/change"' in post_route.calls[0].request.read()


def test_write_token_must_be_separate_from_read_token() -> None:
    client = make_client(github_write_token="test-token")

    with pytest.raises(ToolError, match="separate GITHUB_WRITE_TOKEN"):
        client.require_write_token()


def test_write_operations_require_explicit_repository_allowlist() -> None:
    client = make_client(allowed_github_repos=set(), github_write_token="write-token")

    with pytest.raises(ToolError, match="explicit repository allowlist"):
        client.validate_write_repository("owner/repo")


def test_sensitive_workflow_paths_are_blocked() -> None:
    client = make_client(github_write_token="write-token")

    with pytest.raises(ToolError, match="Writes to this GitHub path are blocked"):
        client._validate_blocked_path(".github/workflows/deploy.yml")


async def test_credential_files_and_secret_content_are_rejected_before_network() -> (
    None
):
    client = make_client(github_write_token="write-token")

    with pytest.raises(ToolError, match="credential files"):
        await client.prepare_file_update(
            "owner/repo", ".env", "ai/change", "safe", "Update"
        )
    with pytest.raises(ToolError, match="appears to contain a secret"):
        await client.prepare_file_update(
            "owner/repo",
            "README.md",
            "ai/change",
            "GITHUB_TOKEN=ghp_example123",
            "Update",
        )
    assert not respx.calls


def test_write_path_must_be_normalized_and_relative() -> None:
    client = make_client(github_write_token="write-token")

    for path in ("/README.md", "README.md/", "docs//README.md"):
        with pytest.raises(ToolError, match="normalized repository-relative"):
            client._validate_writable_file_path(path)


async def test_non_prefixed_branch_is_rejected() -> None:
    client = make_client(github_write_token="write-token")

    with pytest.raises(ToolError, match="Write branches must start"):
        await client.validate_write_branch("owner/repo", "main", require_existing=False)
