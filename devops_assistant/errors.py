from fastmcp.exceptions import ToolError


class ConfigurationError(ToolError):
    """Raised when configuration is missing or invalid."""


class GitHubApiError(ToolError):
    """Raised when a GitHub API request fails."""


class DockerError(ToolError):
    """Raised when a Docker operation fails."""
