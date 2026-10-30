"""Plan and reconcile sidecars using current Sonarr episode/file metadata."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path

from .config import Config, confined
from .filesystem import link, remove_owned, signature
from .fonts import identities
from .matching import (
    AUDIO,
    FONTS,
    SUBS,
    VIDEO,
    describe,
    episode_ids,
    extra_root,
    sequence_numbers,
    slug,
    stem_owner,
)
from .state import State

LOG = logging.getLogger(__name__)
FONT_LIMIT = 64 * 1024 * 1024
AMBIGUOUS = object()


@dataclass
class Action:
    source: str
    destination: str
    owner: str
    suffix: str
    kind: str
    signature: str
    replaces: str = ""


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    retire_owners: set[str] = field(default_factory=set)
    # Context for readable logs: release folders, Sonarr videos, extra files seen,
    # and where every planned destination comes from.
    releases: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    extras: int = 0
    origins: dict[str, str] = field(default_factory=dict)

    def note(self, message: str):
        if message not in self.notices:
            self.notices.append(message)

    def report(self) -> dict:
        return {
            "actions": [asdict(x) for x in self.actions],
            "notices": self.notices,
            "retire_owners": sorted(self.retire_owners),
            "releases": self.releases,
            "videos": self.videos,
            "extras": self.extras,
            "origins": self.origins,
        }


@dataclass
class Release:
    """One walk of a torrent folder, shared by every episode file of an event."""

    root: Path
    videos: list[Path]
    sidecars: list[Path]
    fonts: list[Path]
    assignments: dict = field(default_factory=dict)

    def zone(self, path: Path) -> Path | None:
        return extra_root(self.root, path)


@dataclass
class Import:
    """One library file Sonarr or Radarr just imported, and where it came from."""

    file_id: int
    owner: str
    target: Path
    source: str | None
    ids: set[int]


def owner_key(series_id: int, ids: set[int]) -> str:
    return str(series_id) + ":" + ",".join(map(str, sorted(ids)))


class Engine:
    def __init__(self, config: Config, sonarr, state: State, radarr=None):
        self.config, self.sonarr, self.state, self.radarr = config, sonarr, state, radarr

    def _add(self, plan: Plan, source: Path, dest: Path, owner: str, suffix: str, kind: str):
        confined(dest, self.config.library_roots + [self.config.font_root])
        sig = signature(source)
        ancestor = dest.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        if source.stat().st_dev != ancestor.stat().st_dev:
            raise ValueError(f"Cross-filesystem hardlink forbidden: {source} -> {dest}")
        if kind == "font":
            # Identical fonts may be separate inodes in different torrents.
            other = next((a for a in plan.actions if a.destination == str(dest)), None)
            existing = dest if dest.exists() else (Path(other.source) if other else None)
            if existing and (
                os.path.samefile(existing, source)
                or (
                    existing.stat().st_size == source.stat().st_size
                    and existing.read_bytes() == source.read_bytes()
                )
            ):
                return
        replaces = ""
        if dest.exists() and signature(dest) != sig:
            # A new version of a track keeps its name: replace only our unchanged link.
            row = self._managed(dest)
            if not (kind == "sidecar" and row and row["owner"] == owner):
                raise FileExistsError(f"Destination conflict: {dest}")
            replaces = row["signature"]
        action = Action(str(source), str(dest), owner, suffix, kind, sig, replaces)
        if action not in plan.actions:
            plan.actions.append(action)

    def _managed(self, dest: Path) -> dict | None:
        """Journal row for a destination whose current inode is still the recorded one."""
        row = self.state.db.execute(
            "SELECT * FROM links WHERE destination=?", (str(dest),)
        ).fetchone()
        if row and dest.exists() and signature(dest) != row["signature"]:
            return None
        return dict(row) if row else None

    def _font(self, plan: Plan, path: Path, label: str) -> tuple[str, list[tuple]] | None:
        """Digest and faces, parsed once per inode and remembered across events."""
        sig = signature(path)
        cached = self.state.font(sig)
        if cached is None:
            try:
                if path.stat().st_size > FONT_LIMIT:
                    raise ValueError("exceeds 64 MiB safety limit")
                faces = identities(path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.state.cache_font(sig, digest, faces)
                cached = {"digest": digest, "faces": json.dumps(faces), "error": None}
            except Exception as exc:
                # Any parser failure only disqualifies this font, never the import.
                error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                self.state.cache_font(sig, None, None, error)
                cached = {"error": error}
        if cached["error"]:
            plan.note(f"Invalid font {label}: {cached['error']}")
            return None
        return cached["digest"], [tuple(face) for face in json.loads(cached["faces"])]

    def _global_faces(self, plan: Plan) -> dict:
        faces = {}
        if not self.config.font_root.exists():
            return faces
        confined(self.config.font_root, [self.config.font_root])
        for existing in sorted(self.config.font_root.iterdir()):
            if existing.suffix.lower() not in FONTS or existing.is_symlink():
                continue
            confined(existing, [self.config.font_root], exists=True)
            info = self._font(plan, existing, str(existing))
            if info:
                for face in info[1]:
                    faces[face] = info[0]
        return faces

    def _scan(self, plan: Plan, root: Path) -> Release:
        files = []
        for walk_root, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(walk_root) / d).is_symlink())
            for name in sorted(names):
                path = Path(walk_root) / name
                if path.is_symlink():
                    plan.note(f"Skipped symlink: {path}")
                    continue
                if not path.is_file():
                    plan.note(f"Skipped non-regular file: {path}")
                    continue
                files.append(path)
                if len(files) > self.config.max_files:
                    raise ValueError("Release exceeds max_files")
        release = Release(root, [], [], [])
        for path in files:
            ext = path.suffix.lower()
            if ext in VIDEO:
                release.videos.append(path)
            elif ext in AUDIO | SUBS:
                release.sidecars.append(confined(path, [root], exists=True))
            elif ext in FONTS:
                release.fonts.append(confined(path, [root], exists=True))
        return release

    def _assignments(self, release: Release, zone: Path | None) -> dict:
        """Map every sidecar of a zone to its video by stem or by sibling numbering."""
        if zone in release.assignments:
            return release.assignments[zone]
        pool = [v for v in release.videos if release.zone(v) == zone]
        video_numbers = {}
        if len({v.parent for v in pool}) == 1:
            numbers = sequence_numbers([v.stem for v in pool])
            video_numbers = {v: numbers[v.stem] for v in pool if v.stem in numbers}
        groups = {}
        for path in release.sidecars:
            if release.zone(path) == zone:
                groups.setdefault((path.parent, path.suffix.lower()), []).append(path)
        result = {}
        for paths in groups.values():
            numbers = sequence_numbers([p.stem for p in paths])
            for path in paths:
                owners = stem_owner(path, pool)
                if len(owners) > 1:
                    result[path] = (AMBIGUOUS, None)
                elif owners:
                    result[path] = (owners[0], path.stem[len(owners[0].stem) :].lstrip("."))
                else:
                    number = numbers.get(path.stem)
                    found = [v for v, n in video_numbers.items() if n == number]
                    result[path] = (
                        found[0] if number is not None and len(found) == 1 else None,
                        None,
                    )
        release.assignments[zone] = (pool, result)
        return release.assignments[zone]

    def _history(self, plan: Plan, client, name: str, route: str, **params) -> dict:
        """File ID or imported path -> original download path from import history."""
        try:
            records = client.get(route, eventType=3, **params)
        except (OSError, ValueError) as exc:
            plan.note(f"{name} history unavailable: {type(exc).__name__}")
            return {}
        result = {}
        for record in records if isinstance(records, list) else []:
            data = {k.casefold(): v for k, v in (record.get("data") or {}).items()}
            dropped = data.get("droppedpath")
            if not dropped:
                continue
            if data.get("fileid"):
                result.setdefault(int(data["fileid"]), Path(dropped))
            if data.get("importedpath"):
                result.setdefault(data["importedpath"], Path(dropped))
        return result

    def _download(self, plan, payload, series_id, series_path, episodes, files):
        items = []
        for imported in payload.get("episodeFiles") or [payload["episodeFile"]]:
            file_id = int(imported["id"])
            if file_id not in files:
                plan.note(f"Obsolete import, episode file {file_id} no longer exists")
                continue
            ids = {e["id"] for e in episodes if e.get("episodeFileId") == file_id}
            if not ids:
                raise ValueError("Sonarr has not associated the imported file with episodes")
            target = confined(Path(files[file_id]["path"]), [series_path], exists=True)
            source = imported.get("sourcePath") or payload.get("sourcePath")
            items.append(Import(file_id, owner_key(series_id, ids), target, source, ids))
        history = lambda: self._history(  # noqa: E731
            plan, self.sonarr, "Sonarr", "history/series", seriesId=series_id
        )
        self._import(plan, items, episodes, history, movie=False)

    def _import(self, plan, items: list["Import"], episodes, load_history, movie: bool):
        """Plan sidecars and fonts for imported library files from their releases.

        Episodes match tracks by stem, sibling numbering or Sonarr numbering; a movie
        release with a single regular video gives every track to that video.
        """
        releases: dict[Path, Release] = {}
        font_targets = set()
        font_faces = None
        history = None
        for item in items:
            file_id, owner, target, ids = item.file_id, item.owner, item.target, item.ids
            if not item.source:
                raise ValueError("No sourcePath; use On Import, or replay with explicit sourcePath")
            source = confined(Path(item.source), self.config.download_roots, exists=True)
            root = self.config.release_root(source)
            if root is None:
                # A single-file torrent: the video alone, nothing to link.
                plan.releases.append(source.name)
                plan.videos.append(target.name)
                if self.state.links(owner):
                    plan.note(f"Old sidecars retained for {owner}; review unmatched files")
                continue
            if root not in releases:
                releases[root] = self._scan(plan, root)
                plan.releases.append(root.name)
                plan.extras += len(releases[root].sidecars) + len(releases[root].fonts)
            release = releases[root]
            plan.videos.append(target.name)
            if source.is_file():
                source_video = source
            else:
                # Directory imports: import history names the original file, otherwise
                # the release's only regular video for this movie or these episodes.
                if history is None:
                    history = load_history()
                dropped = history.get(file_id) or history.get(str(target))
                same = [
                    v
                    for v in release.videos
                    if release.zone(v) is None and (movie or episode_ids(v.name, episodes) == ids)
                ]
                if dropped is not None and dropped in release.videos:
                    source_video = dropped
                else:
                    source_video = same[0] if len(same) == 1 else None
            zone = release.zone(source_video or source / "_")
            pool, assigned = self._assignments(release, zone)
            same_episode = (
                [] if movie else [v for v in pool if episode_ids(v.name, episodes) == ids]
            )
            tracks = []
            skipped = False
            for path in release.sidecars:
                if release.zone(path) != zone:
                    continue
                ext = path.suffix.lower()
                relative = path.relative_to(root).as_posix()
                # Mappings use category-relative paths, unique across releases.
                mapping_key = path.relative_to(
                    next(r for r in self.config.download_roots if path.is_relative_to(r))
                ).as_posix()
                owner_video, extra = assigned[path]
                if mapping_key in self.config.mappings:
                    if set(self.config.mappings[mapping_key]) != ids:
                        continue
                elif owner_video is AMBIGUOUS:
                    plan.note(f"Ambiguous video association: {relative}")
                    skipped = True
                    continue
                elif owner_video is not None:
                    if source_video is not None:
                        if owner_video != source_video:
                            continue
                    elif movie or episode_ids(owner_video.name, episodes) != ids:
                        continue
                elif movie:
                    # A movie release with one regular video: every track is for it,
                    # whatever its name ("Movie.rus.srt" next to "Movie.2019.1080p.mkv").
                    if len(pool) != 1 or source_video not in (None, pool[0]):
                        plan.note(f"Unrecognized sidecar, add a mapping: {relative}")
                        skipped = True
                        continue
                else:
                    sidecar_ids = episode_ids(path.name, episodes)
                    if not sidecar_ids:
                        plan.note(f"Unrecognized sidecar, add a mapping: {relative}")
                        skipped = True
                        continue
                    if sidecar_ids != ids:
                        continue
                    if len(same_episode) > 1:
                        plan.note(f"Ambiguous video association: {relative}")
                        skipped = True
                        continue
                if ext in {".idx", ".sub"}:
                    pair = path.with_suffix(".sub" if ext == ".idx" else ".idx")
                    pair = next(
                        (
                            p
                            for p in release.sidecars
                            if p.parent == path.parent
                            and p.stem == path.stem
                            and p.suffix.lower() == pair.suffix
                        ),
                        None,
                    )
                    if ext == ".idx" and pair is None:
                        plan.note(f"Missing VobSub .sub pair: {relative}")
                        skipped = True
                        continue
                    if ext == ".sub" and pair is None:
                        with path.open("rb") as stream:
                            if stream.read(4) == b"\x00\x00\x01\xba":
                                plan.note(f"Missing VobSub .idx pair: {relative}")
                                skipped = True
                                continue
                track = describe(relative, extra, self.config.rules, self.config.default_language)
                tracks.append((path, relative, track))
            for path, suffix in self._names(tracks):
                dest = target.with_name(target.stem + suffix)
                self._add(plan, path, dest, owner, suffix, "sidecar")
                plan.origins[str(dest)] = path.relative_to(root).as_posix()
            # Retire only after a replacement set is planned. Unmatched files are
            # expected in batches, so preserve old links on upgrades with notices.
            if tracks and not skipped:
                plan.retire_owners.add(owner)
            elif self.state.links(owner):
                plan.note(f"Old sidecars retained for {owner}; review unmatched files")
            if (root, zone, target.parent) not in font_targets:
                font_targets.add((root, zone, target.parent))
                if font_faces is None:
                    font_faces = self._global_faces(plan)
                self._fonts(plan, release, zone, target, owner, font_faces)

    @staticmethod
    def _names(tracks: list) -> list[tuple[Path, str]]:
        """Readable Jellyfin suffixes; a counter only separates identical names.

        Files of one track (an idx/sub pair) share a parent and stem and keep one name.
        """
        by_track = {}
        for path, relative, track in sorted(tracks, key=lambda t: t[1]):
            by_track.setdefault((path.parent, path.stem), (track, []))[1].append(path)
        taken = set()
        result = []
        for track, paths in by_track.values():
            title, counter = track.title, 1
            while True:
                track.title = title if counter == 1 else f"{title or 'Track'} {counter}"
                names = [track.suffix(p.suffix[1:].lower()) for p in paths]
                if not taken & set(names):
                    break
                counter += 1
            taken.update(names)
            result += zip(paths, names)
        return result

    def _fonts(self, plan, release: Release, zone, target: Path, owner: str, font_faces: dict):
        for path in release.fonts:
            if release.zone(path) != zone:
                continue
            relative = path.relative_to(release.root).as_posix()
            info = self._font(plan, path, relative)
            if info is None:
                continue
            digest, faces = info
            # Family + style names distinguish real faces; hash preserves variants.
            family, style = faces[0]
            filename = f"{slug(family)}-{slug(style)}-{digest[:16]}{path.suffix.lower()}"
            destinations = [target.parent / "fonts" / filename]
            if any(face in font_faces and font_faces[face] != digest for face in faces):
                plan.note(f"Global font family/style conflict, local only: {relative}")
            else:
                destinations.append(self.config.font_root / filename)
                for face in faces:
                    font_faces[face] = digest
            for dest in destinations:
                font_owner = "fonts" if dest.parent == self.config.font_root else owner
                self._add(plan, path, dest, font_owner, filename, "font")

    def plan(self, payload: dict) -> Plan:
        plan = Plan()
        event = payload.get("eventType")
        if event == "Test":
            return plan
        if event not in {"Download", "Rename"}:
            plan.note(f"Ignored event: {event}")
            return plan
        if payload.get("_app") == "radarr" or "movie" in payload:
            self._plan_movie(plan, payload, event)
        else:
            self._plan_series(plan, payload, event)
        # Preflight duplicate destinations before any filesystem mutation.
        destinations = {}
        for action in plan.actions:
            previous = destinations.setdefault(action.destination, action.signature)
            if previous != action.signature:
                raise FileExistsError(f"Two sources target {action.destination}")
        return plan

    def _plan_series(self, plan: Plan, payload: dict, event: str):
        if self.sonarr is None:
            raise ValueError("Sonarr event received, but BARNACLARR_SONARR_URL is not set")
        series_id = int(payload["series"]["id"])
        series = self.sonarr.get(f"series/{series_id}")
        series_path = confined(Path(series["path"]), self.config.library_roots, exists=True)
        episodes = self.sonarr.get("episode", seriesId=series_id)
        files = {f["id"]: f for f in self.sonarr.get("episodefile", seriesId=series_id)}
        if event == "Download":
            self._download(plan, payload, series_id, series_path, episodes, files)
            return
        moves = []
        for file_id, video in files.items():
            ids = {e["id"] for e in episodes if e.get("episodeFileId") == file_id}
            if ids:
                target = confined(Path(video["path"]), [series_path], exists=True)
                moves.append((owner_key(series_id, ids), target))
        self._rename(plan, moves)

    def _plan_movie(self, plan: Plan, payload: dict, event: str):
        if self.radarr is None:
            raise ValueError("Radarr event received, but BARNACLARR_RADARR_URL is not set")
        movie_id = int(payload["movie"]["id"])
        movie = self.radarr.get(f"movie/{movie_id}")
        movie_path = confined(Path(movie["path"]), self.config.library_roots, exists=True)
        files = {f["id"]: f for f in self.radarr.get("moviefile", movieId=movie_id)}
        owner = f"movie:{movie_id}"
        if event == "Rename":
            targets = [confined(Path(f["path"]), [movie_path], exists=True) for f in files.values()]
            self._rename(plan, [(owner, target) for target in targets])
            return
        items = []
        for imported in payload.get("movieFiles") or [payload["movieFile"]]:
            file_id = int(imported["id"])
            if file_id not in files:
                plan.note(f"Obsolete import, movie file {file_id} no longer exists")
                continue
            target = confined(Path(files[file_id]["path"]), [movie_path], exists=True)
            source = imported.get("sourcePath") or payload.get("sourcePath")
            items.append(Import(file_id, owner, target, source, {movie_id}))
        history = lambda: self._history(  # noqa: E731
            plan, self.radarr, "Radarr", "history/movie", movieId=movie_id
        )
        self._import(plan, items, [], history, movie=True)

    def _rename(self, plan: Plan, moves: list[tuple[str, Path]]):
        """Relink managed tracks of each owner next to its current video name."""
        for owner, target in moves:
            for row in self.state.links(owner):
                old = confined(Path(row["destination"]), self.config.library_roots)
                if not old.exists() or signature(old) != row["signature"]:
                    plan.note(f"Managed file missing or changed: {old}")
                    continue
                if row["kind"] == "font":
                    self._add(
                        plan, old, target.parent / "fonts" / old.name, owner, row["suffix"], "font"
                    )
                    continue
                if target.name not in plan.videos:
                    plan.videos.append(target.name)
                plan.origins[str(target.with_name(target.stem + row["suffix"]))] = old.name
                self._add(
                    plan,
                    old,
                    target.with_name(target.stem + row["suffix"]),
                    owner,
                    row["suffix"],
                    "sidecar",
                )
                plan.retire_owners.add(owner)

    def apply(self, plan: Plan) -> dict:
        report = plan.report()
        report["dry_run"] = self.config.dry_run
        report.update(linked=[], unchanged=0, removed=[])
        if self.config.dry_run:
            return report
        wanted = {a.destination for a in plan.actions}
        for action in plan.actions:
            source, dest = Path(action.source), Path(action.destination)
            confined(source, self.config.download_roots + self.config.library_roots)
            confined(dest, self.config.library_roots + [self.config.font_root])
            if signature(source) != action.signature:
                raise ValueError(f"Source changed: {source}")
            row = self._managed(dest)
            if action.replaces and row and row["signature"] == action.replaces:
                if not remove_owned(dest, action.replaces):
                    raise FileExistsError(f"Conflict: {dest}")
                self.state.db.execute("DELETE FROM links WHERE destination=?", (str(dest),))
                self.state.db.commit()
            elif row and not dest.exists() and row["owner"] == action.owner:
                # Interrupted replacement: our old link is gone, its journal row is stale.
                if row["signature"] != action.signature:
                    self.state.db.execute("DELETE FROM links WHERE destination=?", (str(dest),))
                    self.state.db.commit()
            if dest.exists():
                if signature(dest) != action.signature:
                    raise FileExistsError(f"Conflict: {dest}")
                # Existing links not in our journal are not claimed or removed.
                self.state.db.execute(
                    "UPDATE links SET status='linked' WHERE destination=? AND signature=?",
                    (str(dest), action.signature),
                )
                self.state.db.commit()
                report["unchanged"] += 1
                continue
            row = self.state.db.execute(
                "SELECT * FROM links WHERE destination=?", (str(dest),)
            ).fetchone()
            if row and row["signature"] != action.signature:
                raise FileExistsError(f"Conflicting ownership journal: {dest}")
            self.state.db.execute(
                "INSERT OR IGNORE INTO links VALUES (?,?,?,?,?,?,?)",
                (
                    str(dest),
                    str(source),
                    action.owner,
                    action.suffix,
                    action.kind,
                    action.signature,
                    "pending",
                ),
            )
            self.state.db.commit()
            link(source, dest, action.signature)
            self.state.db.execute(
                "UPDATE links SET status='linked' WHERE destination=?", (str(dest),)
            )
            self.state.db.commit()
            report["linked"].append(action.destination)
        for owner in plan.retire_owners:
            for row in self.state.links(owner):
                if row["kind"] != "sidecar" or row["destination"] in wanted:
                    continue
                old = confined(Path(row["destination"]), self.config.library_roots)
                try:
                    removed = remove_owned(old, row["signature"])
                except FileNotFoundError:
                    removed = True
                if removed:
                    self.state.db.execute("DELETE FROM links WHERE destination=?", (str(old),))
                    self.state.db.commit()
                    report["removed"].append(str(old))
                else:
                    report["notices"].append(f"Preserved modified destination: {old}")
        return report

    def process(self, payload: dict, label: str = "event") -> dict:
        result = self.apply(self.plan(payload))
        for line in summarize(payload, result, label):
            LOG.info(line)
        LOG.debug("%s report %s", label, json.dumps(result, ensure_ascii=False))
        return result


def _episodes(payload: dict) -> str:
    """S01E01, or S01E01-S01E13 for a batch; empty for events without episodes."""
    numbers = sorted(
        (e["seasonNumber"], e["episodeNumber"])
        for e in payload.get("episodes") or []
        if isinstance(e.get("seasonNumber"), int) and isinstance(e.get("episodeNumber"), int)
    )
    names = [f"S{season:02d}E{episode:02d}" for season, episode in numbers]
    if len(names) > 2:
        return f"{names[0]}-{names[-1]} ({len(names)} episodes)"
    return ", ".join(names)


def event_app(payload: dict) -> str:
    """Which *arr sent the event; queued jobs carry it, older ones are Sonarr's."""
    if payload.get("_app") in {"sonarr", "radarr"}:
        return payload["_app"]
    return "radarr" if isinstance(payload.get("movie"), dict) else "sonarr"


def event_subject(payload: dict) -> str:
    """'Download Black Lagoon S01E03' or 'Download Akira (1988)' from a webhook payload."""
    if isinstance(payload.get("movie"), dict):
        movie = payload["movie"]
        title = movie.get("title") or f"movie {movie.get('id')}"
        if movie.get("year"):
            title += f" ({movie['year']})"
        return " ".join(filter(None, [payload.get("eventType"), title]))
    series = payload.get("series") or {}
    title = series.get("title") or f"series {series.get('id')}"
    return " ".join(filter(None, [payload.get("eventType"), title, _episodes(payload)]))


def _empty_reason(payload: dict, report: dict) -> str:
    if payload.get("eventType") == "Rename":
        return "no managed tracks to move"
    if not report.get("releases"):
        return "nothing to import"
    if not report.get("extras"):
        return "nothing to import, release has only video"
    many = len(payload.get("episodes") or []) > 1
    target = "these episodes" if many else "this episode"
    return f"nothing to import, {report['extras']} extra files in release, none for {target}"


def summarize(payload: dict, report: dict, label: str) -> list[str]:
    """A summary line per job, then where from, where to, and each track.

    Fonts are only counted: a release often carries dozens of them.
    """
    actions = report["actions"]
    if report["dry_run"]:
        done = {a["destination"] for a in actions}
        verb = "would link"
    else:
        done = set(report["linked"])
        verb = "linked"
    sidecars = [a for a in actions if a["kind"] == "sidecar" and a["destination"] in done]
    fonts = {a["source"] for a in actions if a["kind"] == "font" and a["destination"] in done}
    parts = []
    if sidecars or fonts:
        parts.append(f"{verb} {len(sidecars)} tracks, {len(fonts)} fonts")
    if report.get("unchanged"):
        parts.append(f"{report['unchanged']} already in place")
    if report.get("removed"):
        parts.append(f"removed {len(report['removed'])} old tracks")
    if report["notices"]:
        parts.append(f"{len(report['notices'])} notices")
    prefix = f"{label} [dry-run]" if report["dry_run"] else label
    result = ", ".join(parts) or _empty_reason(payload, report)
    lines = [f"{prefix} {event_subject(payload)}: {result}"]
    if report.get("releases"):
        lines.append(f"{label}   release: {', '.join(report['releases'])}")
    videos = report.get("videos") or []
    if sidecars or report.get("removed"):
        shown = videos[0] if len(videos) == 1 else f"{len(videos)} files"
        lines.append(f"{label}   video:   {shown}")
    origins = report.get("origins") or {}
    width = max((len(a["suffix"]) for a in sidecars), default=0)
    for action in sidecars:
        origin = origins.get(action["destination"], Path(action["source"]).name)
        name = action["suffix"] if len(videos) == 1 else Path(action["destination"]).name
        lines.append(f"{label}   + {name:<{width}} <- {origin}")
    lines += [f"{label}   - {Path(d).name}" for d in report.get("removed", [])]
    lines += [f"{label}   ! {n}" for n in report["notices"]]
    return lines
