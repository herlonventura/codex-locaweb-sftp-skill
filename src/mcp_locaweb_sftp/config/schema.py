"""Configuration values are data; no secrets, interpolation or dynamic imports."""

import ipaddress
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.guards import normalize_domain, validate_blocked_patterns, validate_relative_path


def local_path(value: str) -> str:
    if not value or any(ord(char) < 32 for char in value) or "\x7f" in value:
        raise ValueError("Invalid local path.")
    # Keep foreign-OS paths intact for migration; never interpret C:\ as a
    # relative Linux path. Runtime conversion must use native_path below.
    if value.startswith("~/"):
        parts = PurePosixPath(value[2:]).parts
    elif re.match(r"^[A-Za-z]:[\\/]", value):
        parts = PureWindowsPath(value).parts[1:]
    elif value.startswith("/") and not value.startswith("//"):
        parts = PurePosixPath(value).parts[1:]
    else:
        raise ValueError("Use an absolute local path or ~/path; UNC paths are unsupported.")
    if ".." in parts:
        raise ValueError("Parent traversal is not allowed in local paths.")
    if parts:
        validate_relative_path("/".join(parts))
    return value


def native_path(value: str) -> Path:
    local_path(value)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Local path belongs to another OS; provide an explicit path mapping.")
    return path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Site(StrictModel):
    protocol: Literal["sftp", "ftps"] = "sftp"
    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str
    remote_root: str
    local_root: str
    ssh_fingerprint: str | None = None
    ca_file: str | None = None
    publish_enabled: bool = False
    credential_store: Literal["keyring", "age", "env"] = "keyring"
    blocked_paths: tuple[str, ...] = ()

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            if value == "localhost":
                return value
            return normalize_domain(value)

    @field_validator("user")
    @classmethod
    def valid_user(cls, value):
        if not value or len(value) > 256 or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid username.")
        return value

    @field_validator("remote_root")
    @classmethod
    def valid_remote_root(cls, value):
        if not value.startswith("/"):
            raise ValueError("Remote root must be absolute.")
        if value != "/":
            validate_relative_path(value[1:])
        return value

    @field_validator("local_root", "ca_file")
    @classmethod
    def valid_local_path(cls, value):
        return None if value is None else local_path(value)

    @field_validator("ssh_fingerprint")
    @classmethod
    def valid_pin(cls, value):
        if value is not None and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", value):
            raise ValueError("A single OpenSSH SHA256 fingerprint is required.")
        return value

    @field_validator("blocked_paths", mode="before")
    @classmethod
    def valid_patterns(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("Expected a list of blocked patterns.")
        return validate_blocked_patterns(value)

    @model_validator(mode="after")
    def protocol_fields(self):
        if self.protocol == "sftp" and self.ca_file is not None:
            raise ValueError("TLS CA file applies only to FTPS.")
        if self.protocol == "ftps" and self.ssh_fingerprint is not None:
            raise ValueError("SSH fingerprint applies only to SFTP.")
        if self.port is None:
            object.__setattr__(self, "port", 22 if self.protocol == "sftp" else 21)
        return self

    def require_connection_ready(self) -> None:
        if self.protocol == "sftp" and self.ssh_fingerprint is None:
            raise ValueError("Confirm the SSH fingerprint before connecting.")


class AgeSettings(StrictModel):
    directory: str
    executable: str
    identity: str | None = None
    recipient: str | None = None

    @field_validator("directory", "executable", "identity")
    @classmethod
    def valid_path(cls, value):
        return None if value is None else local_path(value)

    @field_validator("recipient")
    @classmethod
    def native_recipient(cls, value):
        if value is not None and not re.fullmatch(r"age1[0-9a-z]{58}", value):
            raise ValueError("Only an X25519 age recipient is supported.")
        return value


class Settings(StrictModel):
    schema_version: Literal[1] = 1
    publish_enabled: bool = False
    timeout_seconds: float = Field(default=15.0, gt=0, le=120, allow_inf_nan=False)
    age: AgeSettings | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_is_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Schema version must be an integer.")
        return value
