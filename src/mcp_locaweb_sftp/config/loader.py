"""Bounded strict YAML/JSON loading; error messages never echo source values."""

import json
from pathlib import Path
import re

from pydantic import ValidationError
import yaml

from ..core.guards import normalize_domain
from ..local_files import read_limited
from .schema import Settings, Site


class ConfigError(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        self._depth = getattr(self, "_depth", 0) + 1
        if self._depth > 32:
            raise ValueError("Nesting limit.")
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        pairs = []
        for key, value in node.value:
            if key.tag != "tag:yaml.org,2002:str":
                raise ValueError("Only string mapping keys are accepted.")
            pairs.append((self.construct_object(key, deep=deep), self.construct_object(value, deep=deep)))
        return _unique(pairs)


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in output:
            raise ValueError("Duplicate or non-string key.")
        output[key] = value
    return output


def parse_document(data: bytes, *, json_format: bool = False) -> dict:
    try:
        if len(data) > 1024 * 1024:
            raise ValueError("Size limit.")
        text = data.decode("utf-8-sig")
        if json_format:
            def invalid_constant(_):
                raise ValueError("Nonfinite JSON number.")
            result = json.loads(text, object_pairs_hook=_unique, parse_constant=invalid_constant)
        else:
            for index, token in enumerate(yaml.scan(text)):
                if index > 50000 or isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)):
                    raise ValueError("Aliases, anchors or excessive token count.")
            result = yaml.load(text, Loader=_UniqueLoader)
        if not isinstance(result, dict):
            raise ValueError("Expected mapping.")
        return result
    except (ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ConfigError("Invalid configuration document; check structure, types and duplicate keys.") from None


def read_document(path: Path) -> dict:
    try:
        data = read_limited(path)
    except (OSError, ValueError):
        raise ConfigError("Cannot read a regular configuration file within size limits.") from None
    return parse_document(data, json_format=path.suffix.lower() == ".json")


def legacy_site(values: dict) -> dict:
    allowed = {"protocol", "host", "port", "username", "remoteRoot", "localRoot", "sshHostKeyFingerprint", "allowPlainFtp"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ConfigError("Unsupported legacy site fields; secrets must not be in configuration.")
    if values.get("allowPlainFtp", False) is not False:
        raise ConfigError("Plain FTP is not supported; configure SFTP or explicit FTPS.")
    names = {"username": "user", "remoteRoot": "remote_root", "localRoot": "local_root",
             "sshHostKeyFingerprint": "ssh_fingerprint"}
    output = {names.get(k, k): v for k, v in values.items() if k != "allowPlainFtp"}
    pin = output.get("ssh_fingerprint")
    if pin in (None, ""):
        output["ssh_fingerprint"] = None
    elif isinstance(pin, str):
        match = re.fullmatch(r"(?:[A-Za-z0-9@._+-]+ [0-9]+ )?(SHA256:[A-Za-z0-9+/]{43})", pin)
        if not match:
            raise ConfigError("Legacy SSH fingerprint must be a single confirmed SHA256 key.")
        output["ssh_fingerprint"] = match[1]
    return output


def sites_from_mapping(values: dict, *, legacy: bool = False) -> dict[str, Site]:
    try:
        sites = {}
        for domain, entry in values.items():
            canonical = normalize_domain(domain)
            if canonical in sites:
                raise ValueError("Domain collision.")
            sites[canonical] = Site.model_validate(legacy_site(entry) if legacy else entry)
        if not sites:
            raise ValueError("Empty registry.")
        return sites
    except (ValueError, TypeError, AttributeError):
        raise ConfigError("Invalid site configuration; check domains, allowed fields and connection settings.") from None


def load_sites(path: Path) -> dict[str, Site]:
    path = Path(path)
    return sites_from_mapping(read_document(path), legacy=path.suffix.lower() == ".json")


def load_settings(path: Path) -> Settings:
    path = Path(path)
    data = read_document(path)
    try:
        if path.suffix.lower() == ".json":
            if set(data) - {"winscpDirectory", "publishEnabled"}:
                raise ValueError("Unknown legacy settings.")
            if "publishEnabled" in data and not isinstance(data["publishEnabled"], bool):
                raise ValueError("Invalid legacy publication flag.")
            # Reading legacy settings never enables publication in the new app.
            data = {}
        return Settings.model_validate(data)
    except (ValueError, ValidationError):
        raise ConfigError("Invalid settings; values must match the documented schema.") from None
