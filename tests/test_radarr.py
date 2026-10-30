"""Radarr: the same release scanning and linking, owned by a movie instead of episodes."""

import json
import logging
from pathlib import Path

import pytest

from sidecar.config import Config
from sidecar.engine import Engine
from sidecar.state import State
from tests.test_engine import make_font
from tests.test_server import request


class FakeRadarr:
    """In-memory Radarr REST fixture."""

    def __init__(self, folder: Path, videos: dict[int, Path], history=None):
        self.folder, self.videos, self.history = folder, videos, history or []

    def get(self, route, **params):
        if route == "movie/7":
            return {"path": str(self.folder)}
        if route == "moviefile":
            return [{"id": i, "path": str(p)} for i, p in self.videos.items()]
        if route == "history/movie":
            return self.history
        raise AssertionError(route)


def setup(tmp_path, release_name, files, dry_run=False):
    root = tmp_path.resolve()
    release = root / "downloads" / "radarr" / release_name
    folder = root / "movies" / "Akira (1988)"
    folder.mkdir(parents=True)
    for name in files:
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".ttf":
            make_font(path, family=path.stem)
        else:
            path.write_bytes(name.encode())
    video = folder / "Akira (1988) [Bluray-1080p].mkv"
    video.write_bytes(b"imported movie")
    cfg = Config(
        [root / "downloads" / "radarr"],
        [root / "movies"],
        root / "fonts",
        root / "state.db",
        radarr_url="http://radarr:7878",
        dry_run=dry_run,
        default_language="und",
    )
    api = FakeRadarr(folder, {70: video})
    return Engine(cfg, None, State(cfg.database), api), api, release, video


def event(source, **extra):
    return {
        "eventType": "Download",
        "_app": "radarr",
        "movie": {"id": 7, "title": "Akira", "year": 1988},
        "movieFile": {"id": 70, "sourcePath": str(source), **extra},
    }


def sidecars(video: Path) -> set[str]:
    return {
        p.name[len(video.stem) :]
        for p in video.parent.iterdir()
        if p.name.startswith(video.stem + ".") and p != video
    }


MOVIE = [
    "Akira.1988.1080p.BluRay.x264.mkv",
    "RUS Sound/AniDub/Akira.mka",
    "Subs/Akira.eng.srt",
    "Subs/Signs/Akira.rus.ass",
    "Fonts/Akira Font.ttf",
    "Extras/Making of Akira.mkv",
    "Extras/Commentary.mka",
]


def test_movie_release_links_every_track_to_its_only_video(tmp_path, caplog):
    """Track names need not match the video: a movie release has one regular video."""
    engine, api, release, video = setup(tmp_path, "Akira (1988) [BDRip 1080p]", MOVIE)
    with caplog.at_level(logging.INFO, logger="sidecar.engine"):
        report = engine.process(event(release / MOVIE[0]), "job=1")
    assert sidecars(video) == {".AniDub.ru.mka", ".en.srt", ".Signs.ru.ass"}
    assert len(list((video.parent / "fonts").iterdir())) == 1
    assert report["notices"] == []
    assert caplog.records[0].getMessage() == (
        "job=1 Download Akira (1988): linked 3 tracks, 1 fonts"
    )
    assert engine.state.links("movie:7")


def test_collection_matches_by_name_and_skips_the_rest(tmp_path):
    """Several movies in one release: only exact names match, the rest is reported."""
    files = [
        "Movie A (1990).mkv",
        "Movie B (1992).mkv",
        "Subs/Movie A (1990).ru.ass",
        "Subs/Movie B (1992).ru.ass",
        "Subs/Bonus commentary.ass",
    ]
    engine, api, release, video = setup(tmp_path, "Collection", files)
    report = engine.process(event(release / files[0]))
    assert sidecars(video) == {".ru.ass"}
    assert report["notices"] == ["Unrecognized sidecar, add a mapping: Subs/Bonus commentary.ass"]


def test_rename_moves_movie_tracks(tmp_path):
    engine, api, release, video = setup(tmp_path, "Akira (1988) [BDRip 1080p]", MOVIE)
    engine.process(event(release / MOVIE[0]))
    renamed = video.rename(video.with_name("Akira (1988) [Remux-1080p].mkv"))
    api.videos = {70: renamed}
    engine.process({"eventType": "Rename", "_app": "radarr", "movie": {"id": 7}})
    assert sidecars(renamed) == {".AniDub.ru.mka", ".en.srt", ".Signs.ru.ass"}
    assert sidecars(video) == set()


@pytest.mark.parametrize("key", ["fileId", "importedPath"])
def test_directory_import_uses_radarr_history(tmp_path, key):
    """Radarr history (DownloadFolderImported) ties the file back to its download."""
    engine, api, release, video = setup(tmp_path, "Akira (1988) [BDRip 1080p]", MOVIE)
    value = "70" if key == "fileId" else str(video)
    api.history = [{"data": {key: value, "droppedPath": str(release / MOVIE[0])}}]
    payload = event(release)
    payload["movieFile"].pop("sourcePath")
    payload["sourcePath"] = str(release)
    engine.process(payload)
    assert sidecars(video) == {".AniDub.ru.mka", ".en.srt", ".Signs.ru.ass"}


def test_single_file_torrent_has_nothing_to_import(tmp_path, caplog):
    """A video straight in the category is a release of its own, not a failure."""
    engine, api, release, video = setup(tmp_path, "Other release", ["Other.mkv", "Other.srt"])
    loose = release.parent / "Akira.1988.1080p.BluRay.mkv"
    loose.write_bytes(b"movie")
    with caplog.at_level(logging.INFO, logger="sidecar.engine"):
        report = engine.process(event(loose), "job=1")
    assert sidecars(video) == set()
    assert report["notices"] == []
    assert [r.getMessage() for r in caplog.records][:2] == [
        "job=1 Download Akira (1988): nothing to import, release has only video",
        "job=1   release: Akira.1988.1080p.BluRay.mkv",
    ]


def test_sonarr_event_without_sonarr_configured_fails_clearly(tmp_path):
    engine, api, release, video = setup(tmp_path, "Akira (1988) [BDRip 1080p]", MOVIE)
    with pytest.raises(ValueError, match="BARNACLARR_SONARR_URL is not set"):
        engine.process({"eventType": "Download", "series": {"id": 1}, "episodeFile": {"id": 1}})


def test_radarr_webhook_is_queued_as_radarr(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        config, output = request(
            tmp_path,
            {"eventType": "Download", "movie": {"id": 7, "title": "Akira", "year": 1988}},
            path="/webhook/radarr",
        )
    assert b"202" in output
    state = State(config.database)
    assert json.loads(state.next_job()["payload"])["_app"] == "radarr"
    state.close()
    assert "[radarr] job=1 queued: Download Akira (1988)" in [
        r.getMessage() for r in caplog.records
    ]
    config, output = request(tmp_path, {"eventType": "Download"}, path="/webhook/radarr")
    assert b"400" in output


def test_radarr_only_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr("sidecar.config.DEFAULT_CONFIG", tmp_path / "missing.toml")
    env = {
        "BARNACLARR_CONFIG": "",
        "BARNACLARR_DOWNLOAD_ROOTS": "/mnt/media/downloads/radarr",
        "BARNACLARR_LIBRARY_ROOTS": "/mnt/media/Movies",
        "BARNACLARR_FONT_ROOT": "/mnt/media/.fonts",
        "BARNACLARR_RADARR_URL": "http://radarr:7878",
        "BARNACLARR_RADARR_API_KEY": "r4darr-key",
    }
    config = Config.load(environ=env)
    assert (config.sonarr_url, config.radarr_key()) == ("", "r4darr-key")
    with pytest.raises(ValueError, match="BARNACLARR_SONARR_URL and/or BARNACLARR_RADARR_URL"):
        Config.load(environ={**env, "BARNACLARR_RADARR_URL": ""})
