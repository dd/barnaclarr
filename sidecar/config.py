"""Configuration from SIDECAR_* environment variables and an optional TOML file."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tomllib

DEFAULT_CONFIG = Path("/config/config.toml")
# SIDECAR_<NAME> -> (config field, parser). Environment overrides the TOML file;
# rules and mappings are structured and stay in TOML only.
ENVIRONMENT = {
    "DOWNLOAD_ROOTS": ("download_roots", "paths"),
    "LIBRARY_ROOTS": ("library_roots", "paths"),
    "FONT_ROOT": ("font_root", "path"),
    "DATABASE": ("database", "path"),
    "SONARR_URL": ("sonarr_url", "str"),
    "SONARR_API_KEY": ("sonarr_api_key", "str"),
    "SONARR_API_KEY_FILE": ("api_key_file", "path"),
    "RADARR_URL": ("radarr_url", "str"),
    "RADARR_API_KEY": ("radarr_api_key", "str"),
    "RADARR_API_KEY_FILE": ("radarr_api_key_file", "path"),
    "API_VERSION": ("api_version", "str"),
    "WEBHOOK_TOKEN": ("webhook_token", "str"),
    "WEBHOOK_TOKEN_FILE": ("webhook_token_file", "path"),
    "DRY_RUN": ("dry_run", "bool"),
    "PORT": ("port", "int"),
    "MAX_FILES": ("max_files", "int"),
    "DEFAULT_LANGUAGE": ("default_language", "str"),
}


class UnsafePath(ValueError):
    """A path escapes the configured boundary or traverses a symlink."""


def confined(path: Path, roots: list[Path], *, exists: bool = False) -> Path:
    """Reject traversal and symlinks, including existing destination ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise UnsafePath(f"Not an absolute, normalized path: {path}")
    if not any(path.is_relative_to(root) for root in roots):
        raise UnsafePath(f"Outside allowed roots: {path}")
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise UnsafePath(f"Symlink forbidden: {part}")
    if exists and not path.exists():
        raise FileNotFoundError(path)
    return path


@dataclass
class Config:
    """All media writes are restricted to library_roots and font_root."""

    download_roots: list[Path]
    library_roots: list[Path]
    font_root: Path
    database: Path = Path("/state/sidecar.sqlite3")
    # Sonarr and/or Radarr, as seen from this container; at least one is required.
    sonarr_url: str = ""
    radarr_url: str = ""
    api_version: str = "v3"
    # Secrets are given directly (environment) or read from files.
    sonarr_api_key: str = field(default="", repr=False)
    api_key_file: Path = Path("/config/sonarr-api-key")
    radarr_api_key: str = field(default="", repr=False)
    radarr_api_key_file: Path = Path("/config/radarr-api-key")
    webhook_token: str = field(default="", repr=False)
    webhook_token_file: Path = Path("/config/webhook-token")
    dry_run: bool = True
    port: int = 8787
    rules: list[dict] = field(default_factory=list)
    mappings: dict[str, list[int]] = field(default_factory=dict)
    max_files: int = 20000
    # Used when neither folders, file tags nor rules name a language.
    default_language: str = "und"

    def api_key(self) -> str:
        return self.sonarr_api_key or self.api_key_file.read_text().strip()

    def radarr_key(self) -> str:
        return self.radarr_api_key or self.radarr_api_key_file.read_text().strip()

    def token(self) -> str:
        return self.webhook_token or self.webhook_token_file.read_text().strip()

    @classmethod
    def load(cls, path: Path | None = None, environ=os.environ) -> "Config":
        """Read the TOML file if present, then apply SIDECAR_* variables over it.

        Without an explicit path, SIDECAR_CONFIG or /config/config.toml is used,
        and a missing default file simply means environment-only configuration.
        """
        explicit = path or (
            Path(environ["SIDECAR_CONFIG"]) if environ.get("SIDECAR_CONFIG") else None
        )
        source = explicit or DEFAULT_CONFIG
        data = tomllib.loads(source.read_text()) if explicit or source.exists() else {}
        for name, (key, kind) in ENVIRONMENT.items():
            value = environ.get("SIDECAR_" + name, "").strip()
            if value:
                data[key] = _parse(name, value, kind)
        for key in ("download_roots", "library_roots", "font_root"):
            if not data.get(key):
                raise ValueError(f"{key} is required (SIDECAR_{key.upper()} or config.toml)")
        for key in ("download_roots", "library_roots"):
            data[key] = [Path(x) for x in data[key]]
        for key in (
            "font_root",
            "database",
            "api_key_file",
            "radarr_api_key_file",
            "webhook_token_file",
        ):
            if key in data:
                data[key] = Path(data[key])
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
        config = cls(**data)
        if not config.download_roots or not config.library_roots:
            raise ValueError("At least one download and library root is required")
        for root in config.download_roots + config.library_roots + [config.font_root]:
            confined(root, [root])
        for source in config.download_roots:
            for dest in config.library_roots + [config.font_root]:
                if source.is_relative_to(dest) or dest.is_relative_to(source):
                    raise ValueError("Download and destination roots must not overlap")
        if not config.sonarr_url and not config.radarr_url:
            raise ValueError("Set SIDECAR_SONARR_URL and/or SIDECAR_RADARR_URL")
        for name, url in (("SONARR", config.sonarr_url), ("RADARR", config.radarr_url)):
            if url and not url.startswith(("http://", "https://")):
                raise ValueError(f"SIDECAR_{name}_URL must be an http(s) address")
        if config.api_version not in {"v3", "v5"}:
            raise ValueError("Unsupported Sonarr API version")
        if not isinstance(config.dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        if not re.fullmatch(r"[a-z]{2,3}", config.default_language):
            raise ValueError("default_language must be an ISO language code or und")
        for rule in config.rules:
            re.compile(rule["pattern"])
            if "language" in rule and not re.fullmatch(r"[a-z]{2,3}", rule["language"]):
                raise ValueError("Rule language must be an ISO language code")
        return config

    def release_root(self, source: Path) -> Path:
        """Use the first child of a download root as the release boundary.

        A loose file directly in a category cannot safely identify a release.
        Configure category roots, never a common ancestor of several categories.
        """
        confined(source, self.download_roots, exists=True)
        root = max(
            (r for r in self.download_roots if source.is_relative_to(r)), key=lambda r: len(r.parts)
        )
        relative = source.relative_to(root)
        if not relative.parts or (len(relative.parts) < 2 and not source.is_dir()):
            raise UnsafePath("Loose files require a dedicated release directory")
        return root / relative.parts[0]


def _parse(name: str, value: str, kind: str):
    if kind == "paths":
        # A JSON array allows commas inside paths; otherwise a comma-separated list.
        items = json.loads(value) if value.startswith("[") else value.split(",")
        return [item.strip() for item in items if item.strip()]
    if kind == "bool":
        if value.lower() in {"1", "true", "yes", "on"}:
            return True
        if value.lower() in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"SIDECAR_{name} must be true or false")
    if kind == "int":
        return int(value)
    return value
