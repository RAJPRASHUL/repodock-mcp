from fastmcp.exceptions import ToolError


class ConfigurationError(ToolError):
    """Raised when configuration is missing or invalid."""


class GitHubApiError(ToolError):
    """Raised when a GitHub API request fails."""


class DockerError(ToolError):
    """Raised when a Docker operation fails."""


class PolicyDeniedError(ToolError):
    """Raised when a request is blocked by server-side policy."""


class InvalidInputError(ToolError):
    """Raised when a tool argument is invalid."""
