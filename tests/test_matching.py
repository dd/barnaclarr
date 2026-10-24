from pathlib import Path

import pytest

from sidecar.matching import Track, describe, episode_ids, is_extra_dir, match, sequence_numbers

EPISODES = [
    {"id": 10, "seasonNumber": 1, "episodeNumber": 1, "absoluteEpisodeNumber": 1},
    {"id": 11, "seasonNumber": 2, "episodeNumber": 1, "absoluteEpisodeNumber": 13},
    {"id": 12, "seasonNumber": 0, "episodeNumber": 1, "absoluteEpisodeNumber": None},
]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Show.S02E01.ass", {11}),
        ("[Moozzi2] Show - 13 (BD 1920x1080 x265).ass", {11}),
        ("01. Title.eng.srt", {10}),
        ("Show.S00E01.ass", {12}),
        ("Show - 13 OVA.ass", set()),
        ("Show.S01E01-E02.ass", set()),
        ("Show.S01E01-02.ass", set()),
        ("Show - 01-02.ass", set()),
        ("1080p.ass", set()),
        ("Show 2024 [1080p].ass", set()),
    ],
)
def test_episode_identifiers(name, expected):
    """Explicit seasons/absolute IDs match; specials and ranges are never guessed."""
    assert episode_ids(name, EPISODES) == expected


def test_exact_source_stem():
    """An unnumbered source filename is a valid explicit association."""
    assert match(Path("Pilot.rus.ass"), Path("Pilot.mkv"), "Pilot.rus.ass", EPISODES, {10}, {})
    assert not match(Path("Pilot2.ass"), Path("Pilot.mkv"), "Pilot2.ass", EPISODES, {10}, {})


def test_manual_special_mapping():
    """Explicit Sonarr episode IDs resolve nonstandard OVA production numbering."""
    assert match(
        Path("Show - 13 OVA.ass"), Path("other.mkv"), "OVA.ass", EPISODES, {12}, {"OVA.ass": [12]}
    )


def test_describe_and_override():
    """Folder labels give language, group and signs; rules override the group."""
    assert describe("RUS Subs/Надписи/Show - 01.ass", None, []) == Track("ru", "Signs", [])
    assert describe(
        "Audio AniDub/01.mka", None, [{"pattern": "AniDub", "language": "ru", "group": "AniDub"}]
    ) == Track("ru", "AniDub", [])
    assert describe("Show.eng.sdh.srt", None, []) == Track("en", "", ["sdh"])


def test_default_and_ambiguous_language():
    """Conflicting or missing language tags fall back to the configured default."""
    assert describe("rus-eng/01.mka", None, []).language == "und"
    assert describe("Sound/StudioBand/01.mka", None, [], "ru") == Track("ru", "StudioBand", [])
    assert describe("Audio 5.1 [DTS XLL]/01.mka", None, [], "ja") == Track("ja", "", [])


def test_title_ignores_episode_title_words():
    """Without an exact stem match the filename only contributes explicit tags."""
    track = describe("Subs/Show - 01 It Begins [RUS].ass", None, [], "und")
    assert track == Track("ru", "", [])


def test_jellyfin_suffix_order():
    """Title precedes the language; only Jellyfin flags follow it."""
    assert Track("ru", "AniStar Signs", ["forced"]).suffix("ass") == ".AniStar Signs.ru.forced.ass"
    assert Track("ru", "", []).suffix("mka") == ".ru.mka"


@pytest.mark.parametrize(
    "stems,expected",
    [
        (["[B] ReLIFE 01 [BD]", "[B] ReLIFE 02 [BD]", "[B] ReLIFE 10 [BD]"], [1, 2, 10]),
        (["S - 12 [1080p]", "S - 13 END [1080p]"], [12, 13]),
        (["[U] 01. Ponoc (BD)", "[U] 02. Kanini (BD)"], [1, 2]),
        (["Mob Psycho 100 - 01", "Mob Psycho 100 - 02"], [1, 2]),
        (["Show - 01", "Show - 01.signs"], [None, None]),
        (["Only"], [None]),
    ],
)
def test_sequence_numbers(stems, expected):
    """The number in the varying part of sibling names identifies the episode."""
    numbers = sequence_numbers(stems)
    assert [numbers.get(s) for s in stems] == expected


@pytest.mark.parametrize(
    "name,extra",
    [("BD menu", True), ("Special", True), ("Scan", True), ("BDCG", True), ("Extras", True),
     ("SPs", True), ("Omake", True), ("RUS Sound", False), ("RuSound", False),
     ("Fonts", False), ("Надписи", False)],
)  # fmt: skip
def test_extra_dirs(name, extra):
    assert is_extra_dir(name) is extra


@pytest.mark.parametrize(
    "relative,suffix",
    [
        ("RuSound/AZAZELo/x.mka", ".AZAZELo.ru.mka"),
        ("EngSubs/x.ass", ".en.ass"),
        ("SubsRus/x.ass", ".ru.ass"),
        ("[Subtitle]/AleX_MytH & Hollow/x.ass", ".AleX_MytH & Hollow.und.ass"),
        ("RuSound/Suzaku&SPAWN2/x.mka", ".Suzaku&SPAWN2.ru.mka"),
        ("Subs/GJM/Signs & Songs/x.ass", ".GJM Signs.und.ass"),
        ("Crunchyroll - ENG/x.ass", ".Crunchyroll.en.ass"),
        ("Audio 5.1 [DTS XLL]/x.mka", ".und.mka"),
    ],
)
def test_group_names_keep_their_spelling(relative, suffix):
    """Glued language/container words split; group names keep punctuation."""
    ext = relative.rsplit(".", 1)[1]
    assert describe(relative, "", [], "und").suffix(ext) == suffix
