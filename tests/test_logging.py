"""Readable job logs: one summary line per job, details below it."""

import logging

import pytest

from sidecar.engine import Engine, summarize
from sidecar.state import State
from tests.test_layouts import LAYOUTS, build


def report(**overrides):
    base = {
        "actions": [],
        "notices": [],
        "retire_owners": [],
        "releases": [],
        "videos": [],
        "extras": 0,
        "origins": {},
        "dry_run": False,
        "linked": [],
        "unchanged": 0,
        "removed": [],
    }
    return {**base, **overrides}


PAYLOAD = {
    "eventType": "Download",
    "series": {"id": 1, "title": "Black Lagoon"},
    "episodes": [{"seasonNumber": 1, "episodeNumber": 3}],
}
VIDEO = "S01E03 - Ring-Ring Ship Chase.mkv"


@pytest.mark.parametrize(
    "extras,reason",
    [
        (0, "nothing to import, release has only video"),
        (24, "nothing to import, 24 extra files in release, none for this episode"),
    ],
)
def test_nothing_to_import_says_why(extras, reason):
    lines = summarize(
        PAYLOAD, report(releases=["Black Lagoon"], videos=[VIDEO], extras=extras), "job=7"
    )
    assert lines == [
        f"job=7 Download Black Lagoon S01E03: {reason}",
        "job=7   release: Black Lagoon",
    ]


def test_linked_tracks_with_origin_fonts_and_notices():
    actions = [
        {
            "kind": "sidecar",
            "source": "/d/Black Lagoon/RuSound/AZAZELo/BL 03.mka",
            "destination": "/l/S01E03 - Ring-Ring Ship Chase.AZAZELo.ru.mka",
            "suffix": ".AZAZELo.ru.mka",
        },
        {"kind": "font", "source": "/d/f.ttf", "destination": "/l/fonts/F.ttf", "suffix": "F.ttf"},
        {"kind": "font", "source": "/d/f.ttf", "destination": "/fonts/F.ttf", "suffix": "F.ttf"},
    ]
    lines = summarize(
        PAYLOAD,
        report(
            actions=actions,
            releases=["Black Lagoon"],
            videos=[VIDEO],
            extras=5,
            origins={actions[0]["destination"]: "RuSound/AZAZELo/BL 03.mka"},
            linked=[a["destination"] for a in actions],
            notices=["Invalid font Fonts/x.ttf: TTLibError"],
        ),
        "job=7",
    )
    assert lines == [
        "job=7 Download Black Lagoon S01E03: linked 1 tracks, 1 fonts, 1 notices",
        "job=7   release: Black Lagoon",
        f"job=7   video:   {VIDEO}",
        "job=7   + .AZAZELo.ru.mka <- RuSound/AZAZELo/BL 03.mka",
        "job=7   ! Invalid font Fonts/x.ttf: TTLibError",
    ]


def test_dry_run_batch_shows_full_names():
    payload = {
        **PAYLOAD,
        "episodes": [{"seasonNumber": 1, "episodeNumber": n} for n in (3, 1, 2)],
    }
    actions = [
        {
            "kind": "sidecar",
            "source": "/d/a.ass",
            "destination": "/l/E01.ru.ass",
            "suffix": ".ru.ass",
        }
    ]
    lines = summarize(
        payload,
        report(
            actions=actions,
            dry_run=True,
            releases=["Black Lagoon"],
            videos=["E01.mkv", "E02.mkv", "E03.mkv"],
            origins={"/l/E01.ru.ass": "Sub/BL 01.ass"},
        ),
        "event",
    )
    assert lines == [
        "event [dry-run] Download Black Lagoon S01E01-S01E03 (3 episodes): "
        "would link 1 tracks, 0 fonts",
        "event   release: Black Lagoon",
        "event   video:   3 files",
        "event   + E01.ru.ass <- Sub/BL 01.ass",
    ]


def test_rename_lists_moved_and_removed_tracks():
    lines = summarize(
        {"eventType": "Rename", "series": {"id": 4}},
        report(unchanged=2, removed=["/l/Old Name.ru.ass"], videos=["New Name.mkv"]),
        "job=9",
    )
    assert lines == [
        "job=9 Rename series 4: 2 already in place, removed 1 old tracks",
        "job=9   video:   New Name.mkv",
        "job=9   - Old Name.ru.ass",
    ]
    assert summarize({"eventType": "Rename", "series": {"id": 4}}, report(), "job=9") == [
        "job=9 Rename series 4: no managed tracks to move"
    ]


def test_worker_logs_summary_then_quiet_repeat(tmp_path, caplog):
    """A real import logs its tracks; the same event again reports them in place."""
    release_name, video, files, default, expected = LAYOUTS["heion_sedai"]
    cfg, api, release, sources = build(tmp_path, release_name, video, files, default)
    file_id, source = next(iter(sources.items()))
    payload = {
        "eventType": "Download",
        "series": {"id": 1, "title": "Heion Sedai"},
        "episodes": [{"seasonNumber": 1, "episodeNumber": 1}],
        "episodeFile": {"id": file_id, "sourcePath": str(source)},
    }
    engine = Engine(cfg, api, State(cfg.database))
    with caplog.at_level(logging.INFO, logger="sidecar.engine"):
        engine.process(payload, "job=1")
        engine.process(payload, "job=2")
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == "job=1 Download Heion Sedai S01E01: linked 3 tracks, 0 fonts"
    assert lines[1] == f"job=1   release: {release_name}"
    assert sum(line.startswith("job=1   + ") and " <- RU " in line for line in lines) == 3
    assert "job=2 Download Heion Sedai S01E01: 3 already in place" in lines
    assert not any("{" in line for line in lines)
