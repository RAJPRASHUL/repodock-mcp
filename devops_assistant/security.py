import re

SECRET_PATTERN = re.compile(
    r"(?i)(?:ghp_|github_pat_|gho_|ghs_)[A-Za-z0-9_]+|"
    r"(?:password|secret|token|api[_-]?key)\s*[=:]\s*\S+"
)


def redact(text: str) -> str:
    return SECRET_PATTERN.sub("[REDACTED]", text)


def limit(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated {len(text) - max_chars} chars]"


def require_allowed(value: str, allowed: set[str], kind: str) -> None:
    if allowed and value not in allowed:
        raise PermissionError(f"{kind} '{value}' is not on the allowlist")
