"""Persistent TOML configuration and strict filesystem boundaries."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib


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
    database: Path
    # Required in config.toml: Sonarr's address as seen from this container.
    sonarr_url: str = ""
    api_version: str = "v3"
    api_key_file: Path = Path("/config/sonarr-api-key")
    webhook_token_file: Path = Path("/config/webhook-token")
    dry_run: bool = True
    port: int = 8787
    rules: list[dict] = field(default_factory=list)
    mappings: dict[str, list[int]] = field(default_factory=dict)
    max_files: int = 20000
    # Used when neither folders, file tags nor rules name a language.
    default_language: str = "und"

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = tomllib.loads(path.read_text())
        for key in ("download_roots", "library_roots"):
            data[key] = [Path(x) for x in data[key]]
        for key in ("font_root", "database", "api_key_file", "webhook_token_file"):
            if key in data:
                data[key] = Path(data[key])
        config = cls(**data)
        if not config.download_roots or not config.library_roots:
            raise ValueError("At least one download and library root is required")
        for root in config.download_roots + config.library_roots + [config.font_root]:
            confined(root, [root])
        for source in config.download_roots:
            for dest in config.library_roots + [config.font_root]:
                if source.is_relative_to(dest) or dest.is_relative_to(source):
                    raise ValueError("Download and destination roots must not overlap")
        if not config.sonarr_url.startswith(("http://", "https://")):
            raise ValueError("sonarr_url must be set to an http(s) address of Sonarr")
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
