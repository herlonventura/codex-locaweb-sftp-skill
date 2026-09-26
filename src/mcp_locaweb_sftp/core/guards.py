"""Lexical guards shared across platforms; they cannot inspect symlinks or ACLs."""

from collections.abc import Collection, Iterable
from fnmatch import fnmatchcase
import ipaddress
import re
import unicodedata


# These baseline rules are additive: a site's rules cannot remove them.
DEFAULT_BLOCKED_PATTERNS = (
    ".git", ".vscode", ".idea", "node_modules", "backups", "logs", ".openai",
    "__pycache__", ".gitignore", ".env", ".env.*", "wp-config.php",
    "configuration.php", "config.php", ".htaccess", "web.config", "agents.md",
    "*.bak", "*.backup", "*.tmp", "*.temp", "*.log", "*.pem", "*.key",
    "*.pfx", "*.p12", "*.ps1", ".htpasswd", "id_rsa", "id_ed25519",
    "*.age", "*.identity", "credentials", "private-config", ".mcp-locaweb-sftp",
    "sites.yaml", "settings.yaml", "sites.json", "settings.json", "migration.json",
    "deploy-result.json", "backup-result.json", "backup-manifest.json",
)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    prefix + digit for prefix in ("com", "lpt") for digit in "123456789¹²³"
}


def normalize_domain(domain: str) -> str:
    """Allow only a full ASCII DNS name; never guess or correct spelling."""
    if not isinstance(domain, str) or not domain.isascii():
        raise ValueError("Domain must be a DNS name.")
    result = domain.strip().lower()
    labels = result.split(".")
    if (not result.isascii() or len(result) > 253 or len(labels) < 2
            or any(not _DNS_LABEL.fullmatch(label) for label in labels)):
        raise ValueError("Use a complete DNS domain without URL, port or path.")
    try:
        ipaddress.ip_address(result)
    except ValueError:
        return result
    raise ValueError("Select a registered domain, not an IP address.")


def require_registered_domain(domain: str, registered: Collection[str]) -> str:
    """Return the canonical requested name only if that exact key is registered."""
    if isinstance(registered, str):
        raise ValueError("Registry must contain complete domain keys, not a string.")
    result = normalize_domain(domain)
    if result not in registered:
        raise ValueError("Domain is not registered; aliases are not resolved.")
    return result


def validate_relative_path(path: str) -> str:
    """Require an unambiguous relative POSIX file path on all supported OSes."""
    if not isinstance(path, str) or not path:
        raise ValueError("A non-empty relative file path is required.")
    if any(c in path for c in '\\:<>"|?*') or any(
        unicodedata.category(c).startswith("C") for c in path
    ):
        raise ValueError("Path contains non-portable characters.")
    for part in path.split("/"):
        if (not part or part in (".", "..") or part != part.strip()
                or part.endswith(".")):
            raise ValueError("Path must be relative, without empty or dot segments.")
        if part.split(".", 1)[0].casefold() in _DEVICES:
            raise ValueError("Windows device names are not allowed.")
    return path


def validate_blocked_patterns(patterns: Iterable[str]) -> tuple[str, ...]:
    """Support '*' and '?' per segment, plus '/' for a directory rule."""
    if isinstance(patterns, str):
        raise ValueError("Blocked patterns must be a collection, not one string.")
    result = tuple(patterns)
    for pattern in result:
        if not isinstance(pattern, str) or "**" in pattern or "[" in pattern or "]" in pattern:
            raise ValueError("Patterns support '*' and '?', not '**' or character classes.")
        body = pattern[:-1] if pattern.endswith("/") else pattern
        validate_relative_path(body.replace("*", "x").replace("?", "x"))
    return result


def is_blocked_path(path: str, extra_patterns: Iterable[str] = ()) -> bool:
    """Apply the baseline and site additions, case-insensitively on every OS.

    A single segment matches at any depth. Multiple segments are root-anchored.
    A trailing slash matches directories and their descendants only.
    """
    parts = validate_relative_path(path).casefold().split("/")
    patterns = DEFAULT_BLOCKED_PATTERNS + validate_blocked_patterns(extra_patterns)
    for pattern in patterns:
        directory = pattern.endswith("/")
        rule = pattern.rstrip("/").casefold().split("/")
        if len(rule) == 1:
            targets = parts[:-1] if directory else parts
            if any(fnmatchcase(part, rule[0]) for part in targets):
                return True
        elif ((len(parts) > len(rule) if directory else len(parts) == len(rule))
              and all(fnmatchcase(part, pat) for part, pat in zip(parts, rule))):
            return True
    return False
