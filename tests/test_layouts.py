"""Real-world torrent layouts: every release imports readable Jellyfin sidecars."""

from pathlib import Path

import pytest

from sidecar.config import Config
from sidecar.engine import Engine
from sidecar.state import State
from tests.test_engine import make_font

EPISODES = 3


class LayoutSonarr:
    def __init__(self, library: Path, history: dict | None = None):
        self.library = library
        self.history = history or {}
        self.files = []
        for number in range(1, EPISODES + 1):
            video = library / f"Show - S01E{number:02d} - Title [Bluray-1080p].mkv"
            video.write_bytes(b"imported %d" % number)
            self.files.append({"id": 100 + number, "path": str(video)})

    def get(self, route, **params):
        if route == "series/1":
            return {"path": str(self.library)}
        if route == "episode":
            return [
                dict(
                    id=n,
                    seasonNumber=1,
                    episodeNumber=n,
                    absoluteEpisodeNumber=n,
                    episodeFileId=100 + n,
                )
                for n in range(1, EPISODES + 1)
            ]
        if route == "episodefile":
            return self.files
        if route == "history/series":
            return [
                {"data": {"fileId": str(file_id), "droppedPath": str(path)}}
                for file_id, path in self.history.items()
            ]
        raise AssertionError(route)


def build(tmp_path, release_name, video, files, default_language="ru"):
    """Create a release from name templates where {n} is the two-digit episode number."""
    root = tmp_path.resolve()
    release = root / "downloads" / release_name
    library = root / "library"
    library.mkdir(parents=True)
    release.mkdir(parents=True)
    for template in [video, *files]:
        for number in range(1, EPISODES + 1):
            path = release / template.format(n=f"{number:02d}")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".ttf":
                make_font(path, family=path.stem)
            else:
                path.write_bytes(f"{template} {number}".encode())
    cfg = Config(
        [root / "downloads"],
        [library],
        root / "fonts",
        root / "state.db",
        dry_run=False,
        default_language=default_language,
    )
    sources = {100 + n: release / video.format(n=f"{n:02d}") for n in range(1, EPISODES + 1)}
    return cfg, LayoutSonarr(library, sources), release, sources


def imported(cfg, api, payload):
    engine = Engine(cfg, api, State(cfg.database))
    report = engine.process(payload)
    first = Path(api.files[0]["path"]).stem
    names = {p.name[len(first) :] for p in api.library.iterdir() if p.name.startswith(first + ".")}
    names.discard(".mkv")
    return names, report


def per_file(sources):
    return {
        "eventType": "Download",
        "series": {"id": 1},
        "episodeFiles": [{"id": i, "sourcePath": str(p)} for i, p in sources.items()],
    }


LAYOUTS = {
    "futari_solo_camp": (
        "Futari Solo Camp [WEB-DL 1080p]",
        "Futari Solo Camp - {n} [1080p AVC EAC3].mkv",
        [
            "RUS Sound [AniStar]/надписи/Futari Solo Camp - {n} [1080p AVC EAC3].signs.ass",
            "RUS Sound [AniStar]/Futari Solo Camp - {n} [1080p AVC EAC3].AniStar.mka",
        ],
        "und",
        {".AniStar.ru.mka", ".AniStar Signs.ru.ass"},
    ),
    "chiisana_eiyuu": (
        "Chiisana Eiyuu (BD 1920x1040p AVC FLAC SUP)",
        "[UCCUSS] {n}. Short Film (BD 1920x1040p AVC FLAC SUP).mkv",
        [
            "Audio 5.1 [DTS XLL]/[UCCUSS] {n}. Short Film (BD 1920x1040p AVC FLAC SUP).mka",
            "[UCCUSS] {n}. Short Film (BD 1920x1040p AVC FLAC SUP).ass",
        ],
        "ja",
        {".ja.mka", ".ja.ass"},
    ),
    "relife": (
        "[Beatrice-Raws] ReLIFE [BDRip 1920x1080 x264 FLAC]",
        "[Beatrice-Raws] ReLIFE {n} [BDRip 1920x1080 x264 FLAC].mkv",
        [
            "RUS Sound/[Beatrice-Raws] ReLIFE {n} [BDRip 1920x1080 x264 FLAC].mka",
            "RUS Subs/[Beatrice-Raws] ReLIFE {n} [BDRip 1920x1080 x264 FLAC].ass",
            "RUS Subs/fonts/ReLIFE Font {n}.ttf",
            "BD menu/Menu {n}.mkv",
            "Scan/Scan {n}.jpg",
        ],
        "und",
        {".ru.mka", ".ru.ass"},
    ),
    "frieren_special_duplicates": (
        "[Beatrice-Raws] Sousou no Frieren [BDRip 1080p x265 Dolby TrueHD]",
        "[Beatrice-Raws] Sousou no Frieren - {n} [BDRip 1080p x265 Dolby TrueHD].mkv",
        [
            "Special/[Beatrice-Raws] Sousou no Frieren - {n} [BDRip 1080p x265 Dolby TrueHD].mkv",
            "Rus subs/[Beatrice-Raws] Sousou no Frieren - {n} [BDRip 1080p x265 Dolby TrueHD].ass",
            "Special/Rus subs/Commentary {n}.ass",
        ],
        "und",
        {".ru.ass"},
    ),
    "dr_stone": (
        "Dr.Stone.S01.BDRip.1080p.HEVC.FLAC",
        "[sam] Dr. STONE - {n} [BD 1080p FLAC].mkv",
        [
            "ENG Subs/Crunchyroll ENG/[sam] Dr. STONE - {n} [BD 1080p FLAC].ass",
            "Extras/RUS Sound/NCOP {n}.mka",
            "RUS Sound/AniLibria/[sam] Dr. STONE - {n} [BD 1080p FLAC].mka",
            "RUS Sound/Crunchyroll/[sam] Dr. STONE - {n} [BD 1080p FLAC].mka",
            "RUS Sound/JAM Club/[sam] Dr. STONE - {n} [BD 1080p FLAC].mka",
            "RUS Subs/CafeSubs/[sam] Dr. STONE - {n} [BD 1080p FLAC].ass",
            "RUS Subs/Crunchyroll/[sam] Dr. STONE - {n} [BD 1080p FLAC].ass",
            "RUS Subs/SovetRomantica/[sam] Dr. STONE - {n} [BD 1080p FLAC].ass",
            "RUS Subs/SovetRomantica/fonts/Stone Font {n}.ttf",
        ],
        "und",
        {
            ".Crunchyroll.en.ass",
            ".AniLibria.ru.mka",
            ".Crunchyroll.ru.mka",
            ".JAM Club.ru.mka",
            ".CafeSubs.ru.ass",
            ".Crunchyroll.ru.ass",
            ".SovetRomantica.ru.ass",
        },
    ),
    "heion_sedai": (
        "[IrizaRaws] Heion Sedai no Idaten-tachi [BDRip] [1080p]",
        "[IrizaRaws] Heion Sedai no Idaten-tachi - {n} (BDRip 1920x1080 x264 10bit FLAC).mkv",
        [
            "RU Sounds/AniLibria/[IrizaRaws] Heion Sedai no Idaten-tachi - {n} (BDRip 1920x1080 x264 10bit FLAC).mka",
            "RU Subs/Crunchyroll/[IrizaRaws] Heion Sedai no Idaten-tachi - {n} (BDRip 1920x1080 x264 10bit FLAC).ass",
            "RU Subs/Надписи/[IrizaRaws] Heion Sedai no Idaten-tachi - {n} (BDRip 1920x1080 x264 10bit FLAC).ass",
        ],
        "und",
        {".AniLibria.ru.mka", ".Crunchyroll.ru.ass", ".Signs.ru.ass"},
    ),
    "pluto": (
        "[VARYG] Pluto [WEB-DL 1080p x264 DDP]",
        "[VARYG] Pluto - {n} [WEB-DL 1080p x264 DDP].mkv",
        [
            "Sounds/AniLibria [rus]/[VARYG] Pluto - {n} [WEB-DL 1080p x264 DDP].mka",
            "Sounds/Netflix [eng]/[VARYG] Pluto - {n} [WEB-DL 1080p x264 DDP].mka",
        ],
        "und",
        {".AniLibria.ru.mka", ".Netflix.en.mka"},
    ),
    "haibane_renmei": (
        "[ANK-Raws] Haibane Renmei (BDrip 1920x1080 x264 FLAC Hi10P)",
        "[ANK-Raws] Haibane Renmei - {n} (BDrip 1920x1080 x264 FLAC Hi10P).mkv",
        [
            "BDCG/{n}.png",
            "Russian Sound [Reanimedia]/[ANK-Raws] Haibane Renmei - {n} (BDrip 1920x1080 x264 FLAC Hi10P).Reanimedia.mka",
            "Russian Subtitles [Reanimedia]/Fonts/Haibane Font {n}.ttf",
            "Russian Subtitles [Reanimedia]/Full [ass]/[ANK-Raws] Haibane Renmei - {n} (BDrip 1920x1080 x264 FLAC Hi10P).Reanimedia_full.ass",
            "Russian Subtitles [Reanimedia]/Inscriptions [ass]/[ANK-Raws] Haibane Renmei - {n} (BDrip 1920x1080 x264 FLAC Hi10P).Reanimedia_inscriptions.ass",
        ],
        "und",
        {".Reanimedia.ru.mka", ".Reanimedia.ru.ass", ".Reanimedia Signs.ru.ass"},
    ),
    "hataraku_saibou": (
        "[Erai-raws] Hataraku Saibou [1080p]",
        "[Erai-raws] Hataraku Saibou - {n} [1080p].mkv",
        [
            "Sound/AniLibria/[Erai-raws] Hataraku Saibou - {n} [1080p].mka",
            "Sound/StudioBand/[Erai-raws] Hataraku Saibou - {n} [1080p].mka",
            "Subs/Iaci/[Erai-raws] Hataraku Saibou - {n} [1080p].ass",
            "Subs/SovetRomantica/Fonts/Cells Font {n}.ttf",
            "Subs/SovetRomantica/Надписи/[Erai-raws] Hataraku Saibou - {n} [1080p].ass",
            "Subs/SovetRomantica/[Erai-raws] Hataraku Saibou - {n} [1080p].ass",
        ],
        "ru",
        {
            ".AniLibria.ru.mka",
            ".StudioBand.ru.mka",
            ".Iaci.ru.ass",
            ".SovetRomantica.ru.ass",
            ".SovetRomantica Signs.ru.ass",
        },
    ),
    "renamed_sidecars_by_sibling_number": (
        "[X] Show [BD]",
        "[X] Show {n} [BD 1080p].mkv",
        ["Subs/show_ep{n}_final.ass"],
        "ru",
        {".ru.ass"},
    ),
}


@pytest.mark.parametrize("name", LAYOUTS)
def test_layout(tmp_path, name):
    release_name, video, files, default, expected = LAYOUTS[name]
    cfg, api, release, sources = build(tmp_path, release_name, video, files, default)
    names, report = imported(cfg, api, per_file(sources))
    assert names == expected, report["notices"]
    for number in range(2, EPISODES + 1):
        stem = Path(api.files[number - 1]["path"]).stem
        assert (
            len([p for p in api.library.iterdir() if p.name.startswith(stem + ".")])
            == len(expected) + 1
        )
    fonts = [f for f in files if f.endswith(".ttf")]
    if fonts:
        assert len(list((api.library / "fonts").iterdir())) == EPISODES * len(fonts)


@pytest.mark.parametrize("name", ["relife", "dr_stone", "chiisana_eiyuu"])
def test_directory_import_uses_sonarr_history(tmp_path, name):
    """Import-complete payloads with only the release folder still find each source video."""
    release_name, video, files, default, expected = LAYOUTS[name]
    cfg, api, release, sources = build(tmp_path, release_name, video, files, default)
    payload = {
        "eventType": "Download",
        "series": {"id": 1},
        "sourcePath": str(release),
        "episodeFiles": [{"id": i} for i in sources],
    }
    names, report = imported(cfg, api, payload)
    assert names == expected, report["notices"]


def test_one_scan_and_quiet_report_for_season_pack(tmp_path):
    """A season pack reports neither per-episode noise nor repeated font parsing."""
    release_name, video, files, default, expected = LAYOUTS["hataraku_saibou"]
    cfg, api, release, sources = build(tmp_path, release_name, video, files, default)
    names, report = imported(cfg, api, per_file(sources))
    assert report["notices"] == []
    engine = Engine(cfg, api, State(cfg.database))
    cached = engine.state.db.execute("SELECT COUNT(*) FROM font_cache").fetchone()[0]
    assert cached == EPISODES


def test_broken_font_in_shared_folder_does_not_block_imports(tmp_path):
    release_name, video, files, default, expected = LAYOUTS["relife"]
    cfg, api, release, sources = build(tmp_path, release_name, video, files, default)
    cfg.font_root.mkdir()
    (cfg.font_root / "broken.ttf").write_bytes(b"not a font")
    names, report = imported(cfg, api, per_file(sources))
    assert names == expected
    assert any("Invalid font" in n and "broken.ttf" in n for n in report["notices"])
