from pathlib import Path

import pytest

from sidecar.config import Config

BASE = {
    "SIDECAR_DOWNLOAD_ROOTS": "/mnt/media/downloads/anime,/mnt/media/downloads/sonarr",
    "SIDECAR_LIBRARY_ROOTS": "/mnt/media/anime",
    "SIDECAR_FONT_ROOT": "/mnt/media/.anime-sidecar-fonts",
    "SIDECAR_SONARR_URL": "http://sonarr:8989",
}


def load(tmp_path, environ, toml=None):
    environ = {"SIDECAR_CONFIG": "", **environ}
    path = None
    if toml is not None:
        path = tmp_path / "config.toml"
        path.write_text(toml)
    return Config.load(path, environ=environ)


def test_environment_only(tmp_path, monkeypatch):
    """A catalog install needs no config file: variables alone are enough."""
    monkeypatch.setattr("sidecar.config.DEFAULT_CONFIG", tmp_path / "missing.toml")
    config = load(
        tmp_path,
        {**BASE, "SIDECAR_DRY_RUN": "false", "SIDECAR_DEFAULT_LANGUAGE": "ru"},
    )
    assert config.download_roots == [
        Path("/mnt/media/downloads/anime"),
        Path("/mnt/media/downloads/sonarr"),
    ]
    assert config.library_roots == [Path("/mnt/media/anime")]
    assert config.database == Path("/state/sidecar.sqlite3")
    assert config.dry_run is False
    assert config.default_language == "ru"
    assert config.rules == [] and config.mappings == {}


def test_environment_overrides_file_and_keeps_rules(tmp_path):
    """TOML keeps rules/mappings; variables win for scalar settings."""
    config = load(
        tmp_path,
        {"SIDECAR_SONARR_URL": "http://other:1", "SIDECAR_LIBRARY_ROOTS": '["/mnt/media/a, b"]'},
        toml="""
download_roots = ["/mnt/media/downloads/anime"]
library_roots = ["/mnt/media/anime"]
font_root = "/mnt/media/fonts"
sonarr_url = "http://sonarr:8989"
dry_run = false

[[rules]]
pattern = "AniDub"
group = "AniDub"

[mappings]
"Release/OVA.ass" = [12]
""",
    )
    assert config.sonarr_url == "http://other:1"
    assert config.library_roots == [Path("/mnt/media/a, b")]
    assert config.dry_run is False
    assert config.rules == [{"pattern": "AniDub", "group": "AniDub"}]
    assert config.mappings == {"Release/OVA.ass": [12]}


def test_secrets_from_values_or_files(tmp_path, monkeypatch):
    """Secrets come from variables directly, or from files like Docker secrets."""
    monkeypatch.setattr("sidecar.config.DEFAULT_CONFIG", tmp_path / "missing.toml")
    direct = load(
        tmp_path,
        {**BASE, "SIDECAR_SONARR_API_KEY": "s3cr3t-api", "SIDECAR_WEBHOOK_TOKEN": "t" * 32},
    )
    assert (direct.api_key(), direct.token()) == ("s3cr3t-api", "t" * 32)
    assert "s3cr3t-api" not in repr(direct) and "t" * 32 not in repr(direct)
    (tmp_path / "api").write_text("file-key\n")
    (tmp_path / "token").write_text("f" * 32 + "\n")
    files = load(
        tmp_path,
        {
            **BASE,
            "SIDECAR_SONARR_API_KEY_FILE": str(tmp_path / "api"),
            "SIDECAR_WEBHOOK_TOKEN_FILE": str(tmp_path / "token"),
        },
    )
    assert (files.api_key(), files.token()) == ("file-key", "f" * 32)


def test_default_file_is_read_when_present(tmp_path, monkeypatch):
    """Existing installs with /config/config.toml keep working unchanged."""
    default = tmp_path / "config.toml"
    default.write_text(
        'download_roots = ["/mnt/media/dl"]\nlibrary_roots = ["/mnt/media/lib"]\n'
        'font_root = "/mnt/media/fonts"\nsonarr_url = "http://sonarr:8989"\n'
    )
    monkeypatch.setattr("sidecar.config.DEFAULT_CONFIG", default)
    assert load(tmp_path, {}).library_roots == [Path("/mnt/media/lib")]


@pytest.mark.parametrize(
    "override,message",
    [
        ({"SIDECAR_LIBRARY_ROOTS": ""}, "library_roots is required"),
        ({"SIDECAR_SONARR_URL": "sonarr:8989"}, "SIDECAR_SONARR_URL"),
        ({"SIDECAR_DRY_RUN": "maybe"}, "SIDECAR_DRY_RUN"),
        ({"SIDECAR_DEFAULT_LANGUAGE": "Russian"}, "default_language"),
        ({"SIDECAR_LIBRARY_ROOTS": "/mnt/media/downloads/anime/x"}, "must not overlap"),
    ],
)
def test_invalid_environment(tmp_path, monkeypatch, override, message):
    monkeypatch.setattr("sidecar.config.DEFAULT_CONFIG", tmp_path / "missing.toml")
    with pytest.raises(ValueError, match=message):
        load(tmp_path, {**BASE, **override})


def test_explicit_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        Config.load(tmp_path / "absent.toml", environ=BASE)


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown configuration keys: sonar_url"):
        load(tmp_path, BASE, toml='sonar_url = "http://typo"\n')
