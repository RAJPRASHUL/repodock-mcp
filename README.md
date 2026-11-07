# AI DevOps Assistant

AI DevOps Assistant is a local [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that gives compatible AI clients controlled access to GitHub and Docker. It is designed for VS Code Copilot Chat and can also be started by other MCP clients that support stdio.

Use natural-language requests to inspect repositories, issues, pull requests, commits, files, Docker containers, images, logs, and container status. Optional GitHub changes and container restarts are disabled by default and require explicit configuration and confirmation.

> **Safety:** GitHub and Docker access uses the credentials and permissions available to the server. Use least-privilege credentials and explicit allowlists. Never commit `.env` or share tokens.

## Features

- Read GitHub repositories, files, commits, issues, and pull requests with bounded results.
- Inspect local Docker images and containers, including status, logs, and filtered metadata.
- Ask the `diagnose_failure` prompt to guide evidence-based repository and container troubleshooting.
- Keep an audit log of tool calls, with sensitive values redacted.
- Optionally stage supported GitHub changes or Docker restarts for separate human confirmation.
- Run locally with Python or `uv`, or launch the published package with `uvx`.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) for `uvx` and the recommended development workflow
- VS Code with GitHub Copilot Chat and MCP support
- A fine-grained GitHub token for live GitHub access
- Docker Desktop or another authorized Docker Engine for live Docker access

## Quick start (Windows PowerShell)

### Run the published package in VS Code

Install [uv](https://docs.astral.sh/uv/), then in the folder you want to use, create `.vscode\mcp.json`:

```json
{
  "servers": {
    "devopsAssistant": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-devops-assistant"],
      "cwd": "${workspaceFolder}",
      "envFile": "${workspaceFolder}\\.env"
    }
  }
}
```

Create a `.env` file in that same folder. To enable GitHub tools, add a fine-grained GitHub token and specify the repositories the server may access:

```dotenv
GITHUB_TOKEN=your_fine_grained_read_token
ALLOWED_GITHUB_REPOS=owner/repository
ALLOWED_DOCKER_CONTAINERS=container-name
ENABLE_WRITE_TOOLS=false
```

Replace the example repository and container names with your own. The GitHub token must have the read permissions needed for the tools you plan to use. For Docker tools, Docker Desktop or another authorized Docker Engine must be running. Do not put real credentials in `mcp.json` or commit `.env`.

In VS Code, run **MCP: List Servers**, start `devopsAssistant`, and use Copilot Chat in **Agent** mode. Example requests:

- “List my allowed GitHub repositories.”
- “Read the README from `owner/repository`.”
- “Show recent commits for `owner/repository`.”
- “List Docker containers and show the status of `container-name`.”
- “Use `diagnose_failure` with `owner/repository` and `container-name`.”

### Run from a terminal

To start the published package directly:

```powershell
uvx mcp-devops-assistant
```

This starts the MCP server over stdio; it is intended to be launched by an MCP client. It may appear to wait for input when run by itself. Configure the client as shown above to use the server.

### Install from source for development

Install [uv](https://docs.astral.sh/uv/), clone the repository, and run these commands from its root:

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run mcp-devops-assistant
```

Edit `.env` before connecting to GitHub or Docker. The `dev` extra installs the test and lint tools.

For local development in VS Code, the repository's [`.vscode/mcp.json`](.vscode/mcp.json) starts `server.py` using the virtual environment and loads the repository `.env`.

## Run from GitHub with uvx

To run the latest code in the GitHub repository without cloning it:

```powershell
uvx --from git+https://github.com/RAJPRASHUL/repodock-mcp mcp-devops-assistant
```

For an MCP client, replace the `args` in the configuration above with:

```json
["--from", "git+https://github.com/RAJPRASHUL/repodock-mcp", "mcp-devops-assistant"]
```

The published PyPI package (`uvx mcp-devops-assistant`) uses a released version. The GitHub command uses repository code. Both require the same local credentials and configuration.

## Publish a new PyPI release

The GitHub Actions workflow in `.github/workflows/publish.yml` builds the package and publishes it to PyPI when a GitHub Release is published. PyPI Trusted Publishing must be configured for this repository and workflow; no PyPI API token is stored in the repository.

For each release:

1. Update `version` in `pyproject.toml` to a version that has not been published before.
2. Commit and push the changes, including `README.md`. The README is used as the PyPI project description.
3. Create and publish a GitHub Release with a matching version tag, such as `v0.1.1`.
4. Check the repository's **Actions** tab and confirm the publishing workflow succeeds.

PyPI does not allow replacing an existing version. To update the package page or package contents, publish a new version.

## Architecture

```text
Copilot Chat (Agent mode)
        | MCP over local stdio
        v
server.py -> devops_assistant.app (FastMCP)
        | validate arguments and server-side policy
        | write audit event
        +---------------------+
        v                     v
 GitHub REST API        Docker Engine
        +---------------------+
        | shape, redact, bound output
        v
 Copilot explains returned evidence
```

Copilot chooses tools and explains results. The server validates arguments, enforces policy, calls GitHub or Docker, sanitizes output, and records tool calls. Repository content, issue/PR text, commit messages, logs, and AI-provided arguments are untrusted data; they cannot override server-side policy.

GitHub API tools use asynchronous `httpx` requests, so waiting for GitHub does not block the MCP event loop. Docker's Python SDK is synchronous; Docker tool calls run in worker threads with `asyncio.to_thread()` so the event loop can continue handling other work. Audit logging supports both synchronous and asynchronous tools.

## Tool catalog

All tools below are registered by the local `devopsAssistant` MCP server unless marked optional. Results and text outputs are bounded by configuration.

### Diagnostics

| Tool | Purpose |
|---|---|
| `health_check` | Reports whether the server, GitHub token, GitHub API, and Docker daemon are available without returning secret values. |
| `config_status` | Reports whether credentials are configured and the number of allowlisted targets; never returns credential values. |

### GitHub read-only tools

| Tool | Purpose and safeguards |
|---|---|
| `github_list_repositories` | Lists token-accessible repositories; filters by `ALLOWED_GITHUB_REPOS` when set. |
| `github_get_repository` | Reads selected repository metadata. |
| `github_list_files` | Lists a repository directory for an optional path/ref with a bounded result count. |
| `github_read_file` | Reads a UTF-8 text file; redacts secrets and bounds output. Binary or oversized API content is rejected. |
| `github_list_commits` | Lists recent commits, optionally for a branch. |
| `github_list_issues` | Lists issues, excluding pull requests; state is `open`, `closed`, or `all`. |
| `github_list_pull_requests` | Lists pull requests; state is `open`, `closed`, or `all`. |

Repository-specific tools enforce the repository allowlist when it is non-empty. GitHub API calls use timeouts, bounded pagination, and safe error messages.

### Docker read-only tools

| Tool | Purpose and safeguards |
|---|---|
| `docker_list_containers` | Lists running containers or includes stopped containers when requested; filters by the container allowlist when set. |
| `docker_list_images` | Lists bounded image metadata and tags. |
| `docker_container_status` | Returns selected state metadata for a container. |
| `docker_container_logs` | Returns a configured bounded tail of logs, redacted and size-limited. |
| `docker_inspect_container` | Returns selected inspect data, omitting environment variables and host mount source paths. |

The server does not provide general shell access, container exec, container/image deletion, or container start/stop tools. Docker daemon access is highly privileged. Connect only to a daemon you are authorized to administer; do not expose its socket/API to untrusted networks.

### MCP resources and prompt

| Name | Type | Purpose |
|---|---|---|
| `devops://config/allowed-targets` | Resource | Shows configured GitHub/Docker targets and indicates when an empty read allowlist is unrestricted. Does not expose credentials. |
| `devops://audit/recent` | Resource | Returns up to 20 recent audit entries, bounded and redacted. |
| `diagnose_failure(repo, container)` | Prompt | Guides Copilot to read repository files, check container status/logs, distinguish observations from hypotheses, and treat retrieved data as untrusted. |

### Optional write tools

These are not registered or advertised unless `ENABLE_WRITE_TOOLS=true`.

| Tool | Action |
|---|---|
| `github_create_branch` | Stages creation of a branch from a selected base branch. |
| `github_commit_file` | Stages a single-file create/update on a prefixed branch; the proposed content is shown in the confirmation preview. |
| `github_create_pull_request` | Stages a pull request for human review. No merge tool exists. |
| `docker_restart_container` | Stages restart of an explicitly allowlisted container not identified as production. |
| `confirm_action` | Consumes a pending action token once and performs exactly the staged action. |

## Configuration and secrets

Copy [`.env.example`](.env.example) to `.env` and set only the values you need. Available settings:

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | GitHub token used by read tools. Use a fine-grained token with only the required permissions. |
| `GITHUB_API_BASE_URL` | GitHub API URL (default `https://api.github.com`). |
| `ALLOWED_GITHUB_REPOS` | Comma-separated `owner/repository` allowlist. Empty means no extra server-side repository restriction; token permissions still apply. |
| `ALLOWED_DOCKER_CONTAINERS` | Comma-separated container-name allowlist for container-specific tools. Empty leaves Docker reads unrestricted by this server within the connected daemon. |
| `MAX_LOG_LINES` | Maximum Docker log tail size (default `200`). |
| `MAX_OUTPUT_CHARS` | Maximum result size in characters (default `8000`). |
| `ENABLE_WRITE_TOOLS` | Enables optional write tools only when set to `true` (default `false`). |
| `GITHUB_WRITE_TOKEN` | Separate GitHub token for optional write tools. Never reuse the read token. |
| `GITHUB_WRITE_BRANCH_PREFIX` | Required branch prefix for GitHub file changes (default `ai/`). |
| `BLOCKED_GITHUB_PATHS` | Additional comma-separated paths blocked from GitHub file writes; `.github/workflows/` is always blocked. |

An empty allowlist does not mean “deny all”: it means no additional allowlist restriction. For safety, set explicit allowlists, especially for Docker. Keep `.env` and `logs/audit.jsonl` out of source control. If a token may have been exposed, revoke it immediately. VS Code loads `.env` through the `envFile` setting; terminal environment variables can also be used.

## Optional write safety

Writes should remain disabled for normal testing. To enable them, set `ENABLE_WRITE_TOOLS=true` only when you have disposable test resources.

Required controls:

1. Configure an explicit, narrow `ALLOWED_GITHUB_REPOS` and/or `ALLOWED_DOCKER_CONTAINERS`; writes are denied if the relevant allowlist is empty.
2. Provide a **separate** `GITHUB_WRITE_TOKEN`, limited to the test repository with only the needed Contents read/write and Pull requests read/write permissions. Never reuse the read token.
3. Use `GITHUB_WRITE_BRANCH_PREFIX` (default `ai/`). GitHub file writes are restricted to prefixed branches, not the default branch.
4. Every write is staged first. The tool returns an exact action preview and a random, single-use token that expires after five minutes. Call `confirm_action(token)` separately to execute it; targets are revalidated at execution.
5. `.github/workflows/`, common credential files, and content detected as secrets are blocked. Pull requests are for human review; no merge operation is available.
6. Docker restart is allowed only for explicitly allowlisted containers. Production-like names and selected environment labels are rejected.

The production-name/label check is only a heuristic; it cannot prove a target is non-production. Do not use these tools to mutate production systems. Do not add unrestricted shell execution, container exec, image/container deletion, or merge operations.

## Audit behavior

Tool calls are recorded as JSON Lines in `logs/audit.jsonl`, including denied calls and outcomes. Token-like arguments and write payload fields are omitted or redacted. The recent-audit resource also redacts data and bounds its output. Audit entries can still contain operational target names and should be treated as sensitive local data.

With `stdio`, stdout is the MCP protocol channel. Do not use `print()` in server code; use logging to stderr.

## Testing

### Unit tests

Run the mocked unit suite; it requires neither GitHub credentials nor a running Docker daemon and performs no live writes. It also checks that GitHub requests can overlap and Docker calls do not block the event loop:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Opt-in read-only integration tests

Integration tests are skipped unless enabled explicitly. Use test resources and read-only credentials only.

GitHub (PowerShell):

```powershell
$env:DEVOPS_RUN_GITHUB_INTEGRATION = "true"
$env:GITHUB_TOKEN = "<read-only fine-grained token>"
$env:ALLOWED_GITHUB_REPOS = "owner/test-repo"
.\.venv\Scripts\python.exe -m pytest -m integration tests\integration\test_github_connection.py -v
```

Docker (PowerShell):

```powershell
$env:DEVOPS_RUN_DOCKER_INTEGRATION = "true"
$env:ALLOWED_DOCKER_CONTAINERS = "test-container"
.\.venv\Scripts\python.exe -m pytest -m integration tests\integration\test_docker_connection.py -v
```

These are read-only checks. Unset the temporary environment variables when done; never use production resources.

## Troubleshooting

| Symptom | Likely cause or fix |
|---|---|
| MCP server fails to start | Check that `.vscode/mcp.json` points to `.venv\Scripts\python.exe` and `server.py`; inspect **MCP: List Servers → Show Output**. |
| Protocol/JSON parse errors | Something wrote to stdout. Remove `print()` calls and use logging. |
| New/renamed tools do not appear | Run **MCP: Reset Cached Tools**, then restart the server. |
| GitHub token missing or rejected | Check `.env` and `envFile`; verify token permissions and selected repository access. |
| GitHub returns 404 for a private repo | The token may lack access; GitHub also returns 404 for inaccessible private repositories. |
| Docker tools fail | Start Docker Desktop and verify `docker version` and `docker ps` in PowerShell. |
| PowerShell blocks venv activation | No activation is needed; invoke `.\.venv\Scripts\python.exe` directly. |
| Editable package install fails | Re-run `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` after confirming package discovery settings in `pyproject.toml`. |

## Demo recording checklist

Keep `ENABLE_WRITE_TOOLS=false` during a portfolio demo.

1. Use test repository/container allowlists and ensure Docker is running if demonstrating Docker.
2. Check that `.env` and `logs/` are not staged or committed; do not show tokens, personal data, private content, or sensitive logs.
3. Start `devopsAssistant` using **MCP: List Servers**.
4. Call `health_check`, list the allowlisted repositories, and read a test README.
5. List containers and read a bounded log tail for an allowlisted test container.
6. Run `diagnose_failure` and ask Copilot to distinguish facts from hypotheses.
7. Show the allowed-targets and recent-audit resources, then briefly show the local audit log.

Do not invoke write tools in a portfolio demo. Any separate confirmation-flow demonstration should use only a disposable repository and development container, with the exact preview visible before confirmation.

## Project layout

```text
.
├── .vscode/mcp.json
├── devops_assistant/
│   ├── app.py
│   ├── actions.py
│   ├── audit.py
│   ├── config.py
│   ├── resources.py
│   ├── prompts.py
│   ├── services/             # GitHub and Docker clients
│   └── tools/                # Read-only and opt-in write tools
├── tests/
│   ├── unit/
│   └── integration/          # Opt-in and read-only
├── logs/                     # Created at runtime; Git-ignored
├── server.py                 # Stable VS Code entry point
├── pyproject.toml
└── README.md
```

## Repositories

Repositories associated with [RAJPRASHUL](https://github.com/RAJPRASHUL):

- [100problems](https://github.com/RAJPRASHUL/100problems)
- [api-gateway](https://github.com/RAJPRASHUL/api-gateway)
- [Automated-Product-Price-Monitoring-Dashboard](https://github.com/RAJPRASHUL/Automated-Product-Price-Monitoring-Dashboard)
- [BroadWave](https://github.com/RAJPRASHUL/BroadWave)
- [collab-board](https://github.com/RAJPRASHUL/collab-board)
- [EventEats](https://github.com/RAJPRASHUL/EventEats)
- [expense-tracker-api](https://github.com/RAJPRASHUL/expense-tracker-api)
- [FormCraft-](https://github.com/RAJPRASHUL/FormCraft-)
- [insurance-premium-prediction](https://github.com/RAJPRASHUL/insurance-premium-prediction)
- [plan_Remainder](https://github.com/RAJPRASHUL/plan_Remainder)
- [portfolio](https://github.com/RAJPRASHUL/portfolio)
- [problem-solving-log](https://github.com/RAJPRASHUL/problem-solving-log)
- [progressMap](https://github.com/RAJPRASHUL/progressMap)
- [RAJPRASHUL](https://github.com/RAJPRASHUL/RAJPRASHUL)
- [repodock-mcp](https://github.com/RAJPRASHUL/repodock-mcp)
- [ShigotoFlow](https://github.com/RAJPRASHUL/ShigotoFlow)
- [small-projects](https://github.com/RAJPRASHUL/small-projects)
- [url_shortner](https://github.com/RAJPRASHUL/url_shortner)

## References

- [FastMCP documentation](https://gofastmcp.com/)
- [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
- [VS Code: Add MCP servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [GitHub: Extend Copilot Chat with MCP](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/extend-copilot-chat-with-mcp)
- [GitHub REST API: Git references](https://docs.github.com/en/rest/git/refs)
- [GitHub REST API: Repository contents](https://docs.github.com/en/rest/repos/contents)
- [GitHub REST API: Pull requests](https://docs.github.com/en/rest/pulls/pulls)
- [Docker SDK for Python](https://docker-py.readthedocs.io/)
