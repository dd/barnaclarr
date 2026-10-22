import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from sidecar.config import Config, UnsafePath
from sidecar.engine import Engine
from sidecar.filesystem import signature
from sidecar.state import State


class FakeSonarr:
    """In-memory REST fixture; tests never contact a real Sonarr."""

    def __init__(self, library, video):
        self.library = library
        self.video = video
        self.file_id = 20

    def get(self, route, **params):
        if route == "series/1":
            return {"path": str(self.library)}
        if route == "episode":
            return [
                dict(
                    id=10,
                    seasonNumber=1,
                    episodeNumber=1,
                    absoluteEpisodeNumber=1,
                    episodeFileId=self.file_id,
                ),
                dict(
                    id=11, seasonNumber=1, episodeNumber=2, absoluteEpisodeNumber=2, episodeFileId=0
                ),
            ]
        if route == "episodefile":
            return [{"id": self.file_id, "path": str(self.video)}]
        if route == "history/series":
            return []
        raise AssertionError(route)


def setup(tmp_path, dry_run=False):
    """Build explicit isolated download/library/state directories for each test."""
    root = tmp_path.resolve()
    downloads, library = root / "downloads", root / "library"
    release = downloads / "Release"
    release.mkdir(parents=True)
    library.mkdir()
    source = release / "Show - 01.mkv"
    source.write_bytes(b"original video")
    video = library / "S01E01 - Sonarr Actual Name.mkv"
    video.write_bytes(b"imported video")
    nested = release / "RUS Subs" / "Full"
    nested.mkdir(parents=True)
    subtitle = nested / "Show - 01.ass"
    subtitle.write_bytes(b"[Script Info]\n")
    cfg = Config([downloads], [library], root / "fonts", root / "state.db", dry_run=dry_run)
    state = State(cfg.database)
    api = FakeSonarr(library, video)
    engine = Engine(cfg, api, state)
    event = {
        "eventType": "Download",
        "series": {"id": 1},
        "episodeFile": {"id": 20, "sourcePath": str(source)},
    }
    return engine, event, subtitle, api


def test_hardlinks_and_idempotence(tmp_path):
    """Repeat import creates exactly one sidecar using the actual Sonarr basename."""
    engine, event, subtitle, api = setup(tmp_path)
    before = subtitle.read_bytes()
    engine.process(event)
    engine.process(event)
    output = list(api.library.glob("*.ass"))
    assert len(output) == 1
    assert output[0].name.startswith(api.video.stem + ".")
    assert signature(output[0]) == signature(subtitle)
    assert subtitle.read_bytes() == before
    assert subtitle.stat().st_nlink == 2


def test_dry_run_creates_no_media_or_journal_entries(tmp_path):
    """Planning creates neither media links nor link ownership records."""
    engine, event, subtitle, api = setup(tmp_path, dry_run=True)
    report = engine.process(event)
    assert report["actions"] and report["dry_run"]
    assert not list(api.library.glob("*.ass"))
    assert not engine.state.links("1:10")
    assert subtitle.stat().st_nlink == 1


def test_conflict_leaves_existing_file(tmp_path):
    """An occupied destination aborts before any link is written."""
    engine, event, subtitle, api = setup(tmp_path)
    dest = Path(engine.plan(event).actions[0].destination)
    dest.write_bytes(b"user subtitle")
    with pytest.raises(FileExistsError):
        engine.process(event)
    assert dest.read_bytes() == b"user subtitle"
    assert subtitle.stat().st_nlink == 1


def test_rename_after_torrent_removed(tmp_path):
    """Renaming uses the managed inode even when the torrent source is gone."""
    engine, event, subtitle, api = setup(tmp_path)
    engine.process(event)
    old = next(api.library.glob("*.ass"))
    subtitle.unlink()
    api.video = api.video.rename(api.library / "New Name.mkv")
    engine.process({"eventType": "Rename", "series": {"id": 1}})
    assert not old.exists()
    assert len(list(api.library.glob("New Name.*.ass"))) == 1
    engine.process({"eventType": "Rename", "series": {"id": 1}})
    assert len(list(api.library.glob("*.ass"))) == 1


def test_upgrade_retires_old_owned_links(tmp_path):
    """Replacing a torrent sidecar creates a new inode link and retires the old one."""
    engine, event, subtitle, api = setup(tmp_path)
    engine.process(event)
    old = next(api.library.glob("*.ass"))
    subtitle.unlink()
    subtitle.write_bytes(b"new release")
    engine.process(event)
    output = list(api.library.glob("*.ass"))
    assert output == [old] and old.read_bytes() == b"new release"
    assert signature(old) == signature(subtitle)
    assert len(engine.state.links("1:10")) == 1


def test_preserve_manually_replaced_old_link(tmp_path):
    """An old path replaced by a user is never overwritten on upgrade."""
    engine, event, subtitle, api = setup(tmp_path)
    engine.process(event)
    old = next(api.library.glob("*.ass"))
    old.unlink()
    old.write_bytes(b"user-owned")
    subtitle.unlink()
    subtitle.write_bytes(b"new release")
    with pytest.raises(FileExistsError):
        engine.process(event)
    assert old.read_bytes() == b"user-owned"


def test_idx_sub_pair_has_identical_stem(tmp_path):
    """Paired VobSub files retain the identical Jellyfin basename."""
    engine, event, subtitle, api = setup(tmp_path)
    subtitle.with_suffix(".idx").write_bytes(b"index")
    subtitle.with_suffix(".sub").write_bytes(b"bitmap")
    engine.process(event)
    assert next(api.library.glob("*.idx")).stem == next(api.library.glob("*.sub")).stem


def test_source_symlink_is_skipped(tmp_path):
    """A symlink in a torrent cannot import an outside file."""
    engine, event, subtitle, api = setup(tmp_path)
    subtitle.unlink()
    subtitle.symlink_to(api.video)
    report = engine.process(event)
    assert not report["actions"]
    assert any("symlink" in n for n in report["notices"])


def test_destination_symlink_rejected(tmp_path):
    """A symlinked destination directory fails containment checks."""
    engine, event, subtitle, api = setup(tmp_path)
    target = api.library / "redirect"
    target.symlink_to(subtitle.parent, target_is_directory=True)
    api.video = target / subtitle.name
    with pytest.raises(UnsafePath):
        engine.process(event)


def test_cross_device_failure_never_copies(tmp_path):
    """EXDEV leaves the original untouched and the journal retryable."""
    engine, event, subtitle, api = setup(tmp_path)
    with patch("sidecar.filesystem.os.link", side_effect=OSError(errno.EXDEV, "cross-device")):
        with pytest.raises(OSError) as exc:
            engine.process(event)
    assert exc.value.errno == errno.EXDEV
    assert not list(api.library.glob("*.ass"))
    assert subtitle.exists()
    engine.process(event)
    assert len(list(api.library.glob("*.ass"))) == 1


def test_pending_journal_recovers_after_link_before_commit(tmp_path):
    """A crash after link creation is recovered by verifying the stored inode."""
    engine, event, subtitle, api = setup(tmp_path)
    engine.process(event)
    engine.state.db.execute("UPDATE links SET status='pending'")
    engine.state.db.commit()
    engine.process(event)
    assert engine.state.links("1:10")[0]["status"] == "linked"
    assert len(list(api.library.glob("*.ass"))) == 1


def test_unmanaged_existing_link_is_not_claimed(tmp_path):
    """A preexisting same-inode sidecar stays outside ownership tracking."""
    import os

    engine, event, subtitle, api = setup(tmp_path)
    dest = Path(engine.plan(event).actions[0].destination)
    os.link(subtitle, dest)
    engine.process(event)
    assert not engine.state.links("1:10")


def test_stale_webhook_is_ignored(tmp_path):
    """A delayed import for a replaced Sonarr file cannot restore obsolete tracks."""
    engine, event, subtitle, api = setup(tmp_path)
    api.file_id = 21
    assert not engine.process(event)["actions"]


def test_batch_download_payload(tmp_path):
    """Import-complete payloads with directory sourcePath resolve numbered sidecars."""
    engine, event, subtitle, api = setup(tmp_path)
    original = event.pop("episodeFile")
    event["episodeFiles"] = [{"id": original["id"]}]
    event["sourcePath"] = str(Path(original["sourcePath"]).parent)
    engine.process(event)
    assert len(list(api.library.glob("*.ass"))) == 1


def test_source_changes_between_plan_and_apply(tmp_path):
    """Mutation between planning and linking aborts without importing stale bytes."""
    engine, event, subtitle, api = setup(tmp_path)
    plan = engine.plan(event)
    subtitle.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Source changed"):
        engine.apply(plan)
    assert not list(api.library.glob("*.ass"))


def test_other_numbered_episode_does_not_block_upgrade_cleanup(tmp_path):
    """A batch's other numbered episode does not retain outdated managed links."""
    engine, event, subtitle, api = setup(tmp_path)
    (subtitle.parent / "Show - 02.ass").write_bytes(b"episode 2")
    engine.process(event)
    old = next(api.library.glob("*.ass"))
    subtitle.unlink()
    subtitle.write_bytes(b"new release")
    engine.process(event)
    assert list(api.library.glob("*.ass")) == [old] and old.read_bytes() == b"new release"


def test_unmatched_upgrade_preserves_old_tracks(tmp_path):
    """Uncertain replacement sidecars retain old managed files for manual review."""
    engine, event, subtitle, api = setup(tmp_path)
    engine.process(event)
    old = next(api.library.glob("*.ass"))
    subtitle.rename(subtitle.with_name("unknown.ass"))
    engine.process(event)
    assert old.exists()


def make_font(path, family="Test Family", style="Regular", version="Version 1.0"):
    """Generate a tiny valid font fixture without using installed/user fonts."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef"])
    builder.setupCharacterMap({})
    pen = TTGlyphPen(None)
    builder.setupGlyf({".notdef": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (500, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": style,
            "uniqueFontIdentifier": family + style + version,
            "fullName": family + " " + style,
            "psName": family.replace(" ", "") + "-" + style,
            "version": version,
        }
    )
    builder.setupOS2()
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


def test_font_family_and_hardlinks(tmp_path):
    """Internal font family/style names determine both local/global hardlink names."""
    engine, event, subtitle, api = setup(tmp_path)
    font = subtitle.parent / "12345.ttf"
    make_font(font)
    original = font.read_bytes()
    engine.process(event)
    local = next((api.library / "fonts").glob("Test-Family-Regular-*.ttf"))
    shared = next(engine.config.font_root.glob("Test-Family-Regular-*.ttf"))
    assert signature(local) == signature(shared) == signature(font)
    assert font.read_bytes() == original
    engine.process(event)
    assert len(list(engine.config.font_root.iterdir())) == 1


def test_font_family_conflict_stays_local(tmp_path):
    """Different bytes for the same family/style never override the global font."""
    engine, event, subtitle, api = setup(tmp_path)
    make_font(subtitle.parent / "a.ttf")
    make_font(subtitle.parent / "b.ttf", version="Version 2.0")
    report = engine.process(event)
    assert len(list(engine.config.font_root.iterdir())) == 1
    assert len(list((api.library / "fonts").iterdir())) == 2
    assert any("family/style conflict" in n for n in report["notices"])


def test_identical_fonts_deduplicated(tmp_path):
    """Separate source inodes with identical font bytes share a single destination."""
    engine, event, subtitle, api = setup(tmp_path)
    font = subtitle.parent / "a.ttf"
    make_font(font)
    (subtitle.parent / "b.ttf").write_bytes(font.read_bytes())
    engine.process(event)
    assert len(list(engine.config.font_root.iterdir())) == 1
    assert len(list((api.library / "fonts").iterdir())) == 1


def test_ttc_all_faces(tmp_path):
    """A TTC exposes every face while the collection remains a single hardlink."""
    from fontTools.ttLib import TTCollection, TTFont
    from sidecar.fonts import identities

    engine, event, subtitle, api = setup(tmp_path)
    first, second = tmp_path / "one.ttf", tmp_path / "two.ttf"
    make_font(first, family="First")
    make_font(second, family="Second")
    collection = TTCollection()
    collection.fonts = [TTFont(first), TTFont(second)]
    target = subtitle.parent / "collection.ttc"
    collection.save(target)
    collection.close()
    assert identities(target) == [("First", "Regular"), ("Second", "Regular")]
    engine.process(event)
    assert signature(next(engine.config.font_root.glob("*.ttc"))) == signature(target)


def test_invalid_font_skipped(tmp_path):
    """Malformed font data produces a notice without preventing subtitle import."""
    engine, event, subtitle, api = setup(tmp_path)
    (subtitle.parent / "bad.ttf").write_bytes(b"invalid font")
    report = engine.process(event)
    assert any("Invalid font" in n for n in report["notices"])
    assert len(list(api.library.glob("*.ass"))) == 1


def test_ambiguous_two_video_release(tmp_path):
    """Two videos matching one episode prevent importing a generic numbered track."""
    engine, event, subtitle, api = setup(tmp_path)
    source = Path(event["episodeFile"]["sourcePath"])
    source.with_name("Other - 01.mkv").write_bytes(b"other cut")
    subtitle.rename(subtitle.with_name("01.ass"))
    report = engine.process(event)
    assert not list(api.library.glob("*.ass"))
    assert any("Ambiguous" in n for n in report["notices"])


def test_uppercase_vobsub_pair(tmp_path):
    """Uppercase IDX/SUB extensions still share one Jellyfin filename stem."""
    engine, event, subtitle, api = setup(tmp_path)
    subtitle.with_suffix(".IDX").write_bytes(b"index")
    subtitle.with_suffix(".SUB").write_bytes(b"\x00\x00\x01\xba")
    engine.process(event)
    assert next(api.library.glob("*.idx")).stem == next(api.library.glob("*.sub")).stem


def test_unpaired_binary_vobsub_skipped(tmp_path):
    """Binary VobSub without its index is not imported as a standalone subtitle."""
    engine, event, subtitle, api = setup(tmp_path)
    subtitle.with_suffix(".sub").write_bytes(b"\x00\x00\x01\xba")
    engine.process(event)
    assert not list(api.library.glob("*.sub"))


def test_permission_failure_never_copies(tmp_path):
    """Permission errors propagate instead of copying files or changing modes."""
    engine, event, subtitle, api = setup(tmp_path)
    with patch("sidecar.filesystem.os.link", side_effect=PermissionError("denied")):
        with pytest.raises(PermissionError):
            engine.process(event)
    assert subtitle.stat().st_nlink == 1
    assert not list(api.library.glob("*.ass"))


def test_rename_relinks_local_fonts_to_new_season(tmp_path):
    """A tracked episode moved to a new directory carries its font hardlinks too."""
    engine, event, subtitle, api = setup(tmp_path)
    font = subtitle.parent / "font.ttf"
    make_font(font)
    engine.process(event)
    expected = signature(font)
    font.unlink()
    new_season = api.library / "New Season"
    new_season.mkdir()
    api.video = api.video.rename(new_season / api.video.name)
    engine.process({"eventType": "Rename", "series": {"id": 1}})
    assert signature(next((new_season / "fonts").iterdir())) == expected


def test_vobsub_bitmap_upgrade_keeps_pair_in_sync(tmp_path):
    """Changing only SUB data still renames both members as one replacement pair."""
    engine, event, subtitle, api = setup(tmp_path)
    subtitle.with_suffix(".idx").write_bytes(b"index")
    bitmap = subtitle.with_suffix(".sub")
    bitmap.write_bytes(b"bitmap")
    engine.process(event)
    old_index = next(api.library.glob("*.idx"))
    bitmap.unlink()
    bitmap.write_bytes(b"new bitmap")
    engine.process(event)
    assert next(api.library.glob("*.sub")).read_bytes() == b"new bitmap"
    assert list(api.library.glob("*.idx")) == [old_index]
    assert len(list(api.library.glob("*.sub"))) == 1
    assert next(api.library.glob("*.sub")).stem == next(api.library.glob("*.idx")).stem
