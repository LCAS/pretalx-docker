"""Configuration loading for the pretalx CLI.

Precedence: CLI flag > environment variable > TOML profile > (missing, raised
lazily by ``Config.require()`` when a command actually needs it).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pretalx_client.exceptions import ConfigError

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "pretalx-client" / "config.toml"
DEFAULT_PROFILE = "default"
DEFAULT_CONTENT_LOCALE = "en_gb"


@dataclass
class Config:
    url: Optional[str] = None
    token: Optional[str] = None
    event: Optional[str] = None
    api_version: Optional[str] = None
    content_locale: str = DEFAULT_CONTENT_LOCALE
    verify_ssl: bool = True
    profile: str = DEFAULT_PROFILE
    config_file: Path = DEFAULT_CONFIG_PATH

    def require(self) -> "Config":
        """Validate that the minimum fields needed to call the API are set."""
        missing = []
        if not self.url:
            missing.append("url (--url, PRETALX_URL, or 'url' in the config file)")
        if not self.token:
            missing.append("token (--token, PRETALX_TOKEN, or 'token' in the config file)")
        if missing:
            raise ConfigError(
                "Missing required configuration: "
                + "; ".join(missing)
                + f"\nProfile: '{self.profile}', config file: {self.config_file}"
            )
        return self


def _read_profile(config_file: Path, profile: str) -> dict:
    if not config_file.is_file():
        return {}
    with config_file.open("rb") as f:
        data = tomllib.load(f)
    return data.get("profiles", {}).get(profile, {})


def _resolve_config_file(config_file: Optional[Path]) -> Path:
    """Pick the effective config file path.

    If no explicit path is provided, prefer the default user config file. When
    that file is missing, fall back to ./config.toml from the current working
    directory.
    """
    if config_file is not None:
        return config_file
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    cwd_config_path = Path.cwd() / "config.toml"
    if cwd_config_path.is_file():
        return cwd_config_path
    return DEFAULT_CONFIG_PATH


def load_config(
    url: Optional[str] = None,
    token: Optional[str] = None,
    event: Optional[str] = None,
    profile: Optional[str] = None,
    config_file: Optional[Path] = None,
    api_version: Optional[str] = None,
    verify_ssl: bool = True,
) -> Config:
    """Resolve configuration with precedence: CLI flag > env var > TOML profile."""
    resolved_config_file = _resolve_config_file(config_file)
    resolved_profile = profile or os.environ.get("PRETALX_PROFILE") or DEFAULT_PROFILE
    file_values = _read_profile(resolved_config_file, resolved_profile)

    resolved_url = url or os.environ.get("PRETALX_URL") or file_values.get("url")
    resolved_token = token or os.environ.get("PRETALX_TOKEN") or file_values.get("token")
    resolved_event = event or os.environ.get("PRETALX_EVENT") or file_values.get("event")
    resolved_api_version = (
        api_version
        or os.environ.get("PRETALX_API_VERSION")
        or file_values.get("api_version")
    )
    resolved_content_locale = (
        os.environ.get("PRETALX_CONTENT_LOCALE")
        or file_values.get("content_locale")
        or DEFAULT_CONTENT_LOCALE
    )

    return Config(
        url=resolved_url.rstrip("/") if resolved_url else None,
        token=resolved_token,
        event=resolved_event,
        api_version=resolved_api_version,
        content_locale=str(resolved_content_locale),
        verify_ssl=verify_ssl,
        profile=resolved_profile,
        config_file=resolved_config_file,
    )
