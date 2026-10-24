import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"
    allowed_github_repos: set[str] = field(default_factory=set)
    allowed_docker_containers: set[str] = field(default_factory=set)
    max_log_lines: int = 200
    max_output_chars: int = 8000
    github_write_branch_prefix: str = "ai/"
    blocked_github_paths: set[str] = field(default_factory=set)
    enable_write_tools: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        raw_allowed_github = os.getenv("ALLOWED_GITHUB_REPOS", "")
        raw_allowed_docker = os.getenv("ALLOWED_DOCKER_CONTAINERS", "")
        raw_blocked_paths = os.getenv("BLOCKED_GITHUB_PATHS", "")

        settings = cls(
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_base_url=os.getenv("GITHUB_API_BASE_URL", "https://api.github.com"),
            allowed_github_repos={item.strip() for item in raw_allowed_github.split(",") if item.strip()},
            allowed_docker_containers={item.strip() for item in raw_allowed_docker.split(",") if item.strip()},
            max_log_lines=int(os.getenv("MAX_LOG_LINES", "200")),
            max_output_chars=int(os.getenv("MAX_OUTPUT_CHARS", "8000")),
            github_write_branch_prefix=os.getenv("GITHUB_WRITE_BRANCH_PREFIX", "ai/"),
            blocked_github_paths={item.strip() for item in raw_blocked_paths.split(",") if item.strip()},
            enable_write_tools=os.getenv("ENABLE_WRITE_TOOLS", "false").lower() == "true",
        )

        if settings.max_log_lines <= 0:
            raise ValueError("MAX_LOG_LINES must be greater than zero")
        if settings.max_output_chars <= 0:
            raise ValueError("MAX_OUTPUT_CHARS must be greater than zero")
        if not settings.github_api_base_url:
            raise ValueError("GITHUB_API_BASE_URL cannot be empty")

        return settings


def load_settings() -> Settings:
    return Settings.from_env()
