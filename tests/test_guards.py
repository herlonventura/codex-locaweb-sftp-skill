import pytest

from mcp_locaweb_sftp.core.guards import (
    is_blocked_path,
    normalize_domain,
    require_registered_domain,
    validate_blocked_patterns,
    validate_relative_path,
)


def test_exact_domain_selection_allows_dns_case_and_outer_whitespace():
    assert require_registered_domain(" EXAMPLE.COM ", {"example.com"}) == "example.com"
    assert normalize_domain("shop.example.com") == "shop.example.com"
    assert normalize_domain("xn--exmple-cua.com") == "xn--exmple-cua.com"


@pytest.mark.parametrize("domain", [
    "https://example.com", "example.com/path", "example.com:22", "example.com.",
    "user@example.com", "example", "example..com", "-example.com", "example-.com",
    "example_com.org", "127.0.0.1", "exámple.com", "K.example", "", None,
    "a" * 64 + ".com", ".".join(["a" * 63] * 4), "ex\nample.com",
])
def test_reject_non_domain_or_ambiguous_identifiers(domain):
    with pytest.raises(ValueError):
        normalize_domain(domain)


@pytest.mark.parametrize("domain", ["exampel.com", "www.example.com", "example.net"])
def test_never_choose_a_similar_registered_domain(domain):
    with pytest.raises(ValueError):
        require_registered_domain(domain, {"example.com"})


def test_registry_string_cannot_authorize_a_substring():
    with pytest.raises(ValueError):
        require_registered_domain("example.com", "notexample.com")


@pytest.mark.parametrize("path", [
    "index.html", "assets/logo branca.png", "serviços/index.html", "a-b/é.png",
])
def test_preserve_valid_path_spelling(path):
    assert validate_relative_path(path) == path


@pytest.mark.parametrize("path", [
    "", None, "/etc/passwd", "../secret", "a/../secret", "./index.html", "a/./b",
    "a//b", "a/", "C:/secret", "C:secret", "a\\..\\secret", "\\\\host\\share",
    "file.txt:secret", "file.", "file ", " file", "a/ b", "a\x00b", "a\nb",
    "a\u202eb", "a\u200bb", "file?", "*.html", 'a"b', "a<b", "a|b",
    "CON", "con.txt", "assets/NUL.js", "COM1", "LPT9.txt", "COM¹.txt", "CONIN$",
])
def test_paths_cannot_escape_or_alias_a_file_on_another_os(path):
    with pytest.raises(ValueError):
        validate_relative_path(path)


@pytest.mark.parametrize("path", [
    ".env", ".env.production", "a/.ENV.local", "a/wp-config.php", "cert.KEY",
    "a/b/cert.pem", ".git/config", "a/.git/HEAD", "backups/site.zip", "a/backups/x",
    "config.php", "web.config", ".htaccess", "logs/run.txt", "a/node_modules/lib.js",
    ".htpasswd", "id_ed25519", "README.bak", "script.ps1", ".git",
])
def test_baseline_sensitive_paths_are_blocked_at_any_depth(path):
    assert is_blocked_path(path)


@pytest.mark.parametrize("path", [
    "index.html", "assets/style.css", "assets/logo branca.png", "environment.html",
    "backup-guide.html", "git-guide.html", "config.json",
])
def test_regular_site_content_remains_eligible(path):
    assert not is_blocked_path(path)


def test_site_rules_add_restrictions_without_weakening_baseline():
    rules = ("*.sql", "private/", "downloads/internal/*.pdf")
    assert is_blocked_path("database.SQL", rules)
    assert is_blocked_path("assets/private/document.pdf", rules)
    assert is_blocked_path("downloads/internal/report.pdf", rules)
    assert not is_blocked_path("downloads/public/report.pdf", rules)
    assert is_blocked_path("wp-config.php", rules)


def test_patterns_are_segment_scoped_and_directory_rules_are_explicit():
    assert is_blocked_path("a/b/secret.txt", ("a/b/",))
    assert not is_blocked_path("a/b.txt", ("a/b/",))
    assert not is_blocked_path("a/b", ("a/b/",))
    assert is_blocked_path("a/b/file.txt", ("a/*/",))
    assert is_blocked_path("file1.txt", ("file?.txt",))
    assert not is_blocked_path("x/a/b/file.txt", ("a/b/",))
    assert not is_blocked_path("a/b/c.pdf", ("a/*.pdf",))


@pytest.mark.parametrize("patterns", [
    ".env", ("",), ("/root",), ("../file",), ("a//b",), ("a\\b",),
    ("a/**",), ("[ab].txt",), ("x].txt",), (None,),
])
def test_invalid_site_rules_fail_instead_of_being_ignored(patterns):
    with pytest.raises(ValueError):
        validate_blocked_patterns(patterns)
