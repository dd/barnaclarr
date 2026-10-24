"""Conservative episode and metadata recognition; uncertain matches are skipped."""

from dataclasses import dataclass
import os
from pathlib import Path
import re

AUDIO = {".mka", ".aac", ".ac3", ".eac3", ".flac", ".opus", ".ogg", ".mp3", ".m4a", ".dts", ".wav"}
SUBS = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".sup", ".smi"}
FONTS = {".ttf", ".otf", ".ttc", ".otc"}
VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}

# Output codes are ISO 639-1; both 639-1 and 639-2 spellings are recognized.
LANGUAGES = {
    "ru": {"ru", "rus", "russian", "рус", "русский", "русская", "русские"},
    "en": {"en", "eng", "english", "англ", "английский", "английская", "английские"},
    "ja": {"ja", "jp", "jpn", "jap", "japanese", "яп", "японский", "японская", "японские"},
    "uk": {"uk", "ua", "ukr", "ukrainian", "укр", "украинский", "украинская", "украинские"},
    "de": {"de", "deu", "ger", "german", "немецкий", "немецкие"},
    "fr": {"fr", "fra", "fre", "french", "французский", "французские"},
    "es": {"es", "spa", "spanish", "испанский", "испанские"},
    "it": {"it", "ita", "italian", "итальянский"},
    "pt": {"pt", "por", "portuguese", "португальский"},
    "zh": {"zh", "zho", "chi", "chinese", "китайский", "китайские"},
    "ko": {"ko", "kor", "korean", "корейский", "корейские"},
}
LANGUAGE_WORDS = {word: code for code, words in LANGUAGES.items() for word in words}
# Container words say what a folder holds, not who made it: "RUS Sound [AniStar]".
CONTAINER_WORDS = {
    "sound", "sounds", "audio", "audios", "dub", "dubs", "dubbing", "voice", "voices",
    "voiceover", "озвучка", "озвучки", "озвучание", "звук", "аудио", "дорожка", "дорожки",
    "sub", "subs", "subtitle", "subtitles", "субтитры", "субтитр", "сабы", "саб",
}  # fmt: skip
SIGNS_WORDS = {"signs", "sign", "inscriptions", "надписи", "надпись"}
FORCED_WORDS = {"forced", "форсированные", "форсированный"}
SDH_WORDS = {"sdh", "cc"}
# Format and completeness labels are shown by Jellyfin itself or carry no meaning.
NOISE_WORDS = {
    "full", "полные", "полный", "main", "default", "and", "и", "songs", "песни",
    "ass", "ssa", "srt", "sup", "vtt", "pgs", "mka", "ac3", "eac3", "dts", "xll", "hd", "ma",
    "flac", "aac", "opus", "truehd", "atmos", "dd", "ddp", "mp3", "pcm", "lpcm", "stereo",
    "surround", "mono", "external", "ext", "track", "tracks",
}  # fmt: skip
# Folders whose content belongs to bonus material, never to regular episodes.
EXTRA_WORDS = {
    "extra", "extras", "bonus", "bonuses", "special", "specials", "sp", "sps", "omake", "omakes",
    "menu", "menus", "bd",
    "dvd", "scan", "scans", "bdcg", "cg", "booklet", "cd", "cds", "ost", "music", "nc",
    "ncop", "nced", "op", "ed", "opening", "ending", "trailer", "trailers", "pv", "pvs", "cm",
    "cms", "preview", "previews", "бонус", "бонусы", "доп", "допы", "спешл", "спешлы", "сканы",
    "меню",
}  # fmt: skip
WORD = re.compile(r"[^\W_]+")
NUMERIC = re.compile(r"\d+(?:ch)?")


def slug(value: str) -> str:
    return re.sub(r"[^\w-]+", "-", value, flags=re.UNICODE).strip("-_")[:70] or "unknown"


def is_extra_dir(name: str) -> bool:
    words = [w.casefold() for w in WORD.findall(name)]
    return bool(words) and all(w in EXTRA_WORDS or w.isdigit() for w in words)


def extra_root(release: Path, path: Path) -> Path | None:
    """The outermost bonus folder containing path, or None for regular content."""
    current = release
    for part in path.relative_to(release).parts[:-1]:
        current = current / part
        if is_extra_dir(part):
            return current
    return None


def episode_ids(name: str, episodes: list[dict]) -> set[int]:
    """Resolve explicit SxxExx or absolute numbers against Sonarr metadata.

    Specials require S00Exx or explicit manual mappings. Absolute numbering
    never guesses a Special from OVA/SP labels or production-order numbers.
    """
    text = Path(name).stem
    explicit = list(re.finditer(r"(?i)(?<!\w)S(\d{1,2})E(\d{1,3})(?!\d)", text))
    if explicit:
        # Ranges/multi-episode names are deliberately rejected until mapped.
        if len(explicit) != 1 or re.search(r"(?i)E\d+(?:[- _]?E\d+|-\d+)", text):
            return set()
        season, number = map(int, explicit[0].groups())
        return {
            e["id"] for e in episodes if (e["seasonNumber"], e["episodeNumber"]) == (season, number)
        }
    if re.search(r"(?i)\b(ova|oad|specials?|sp|ncop|nced|op|ed)\b", text):
        return set()
    clean = re.sub(r"\[[^]]*\]|\([^)]*\)", " ", text)
    matches = re.findall(
        r"(?:^|\s-\s|\b[Ee][Pp](?:isode)?[. ]*)(\d{1,4})(?:v\d+)?(?=$|[ ._])", clean
    )
    if len(matches) != 1:
        return set()
    number = int(matches[0])
    return {
        e["id"]
        for e in episodes
        if e.get("absoluteEpisodeNumber") == number and e["seasonNumber"] != 0
    }


def sequence_numbers(stems: list[str]) -> dict[str, int]:
    """Read the number in the part that differs between sibling names.

    "[Group] ReLIFE 01 [BD]" and "[Group] ReLIFE 02 [BD]" vary only in "01"/"02",
    whatever the naming scheme. Names without a leading number in the varying part,
    and numbers occurring twice, are left out.
    """
    if len(stems) < 2:
        return {}
    prefix = os.path.commonprefix(stems).rstrip("0123456789")
    suffix = os.path.commonprefix([s[::-1] for s in stems])[::-1].lstrip("0123456789")
    shortest = min(len(s) for s in stems)
    suffix = suffix[max(0, len(prefix) + len(suffix) - shortest) :]
    numbers = {}
    for stem in stems:
        found = re.match(r"\s*(\d{1,4})(?!\d)", stem[len(prefix) : len(stem) - len(suffix)])
        if found:
            numbers[stem] = int(found[1])
    counts = {}
    for number in numbers.values():
        counts[number] = counts.get(number, 0) + 1
    return {stem: n for stem, n in numbers.items() if counts[n] == 1}


def stem_owner(sidecar: Path, videos: list[Path]) -> list[Path]:
    """Videos whose full stem names the sidecar: "Video.ass", "Video.AniStar.mka"."""
    stem = sidecar.stem.casefold()
    exact = [v for v in videos if v.stem.casefold() == stem]
    if exact:
        return exact
    prefixed = [v for v in videos if stem.startswith(v.stem.casefold() + ".")]
    longest = max((len(v.stem) for v in prefixed), default=0)
    return [v for v in prefixed if len(v.stem) == longest]


def match(
    sidecar: Path,
    source: Path,
    relative: str,
    episodes: list[dict],
    target_ids: set[int],
    mappings: dict,
) -> bool:
    """Exact source stems can resolve otherwise unnumbered sidecars."""
    if relative in mappings:
        return set(mappings[relative]) == target_ids
    if stem_owner(sidecar, [source]):
        return True
    ids = episode_ids(sidecar.name, episodes)
    return bool(ids) and ids == target_ids


@dataclass
class Track:
    language: str
    title: str
    flags: list[str]

    def suffix(self, ext: str) -> str:
        """Jellyfin layout: video.<title>.<language>.<forced/sdh>.<ext>."""
        return "." + ".".join(
            [*([self.title] if self.title else []), self.language, *self.flags, ext]
        )


def _roles(folded: str) -> list[tuple[str, str]]:
    """What a word means; [] for a word that belongs to a group name.

    Glued words such as "RuSound", "EngSubs" or "SubsRus" are a language plus a
    container word.
    """
    if folded in LANGUAGE_WORDS:
        return [("language", LANGUAGE_WORDS[folded])]
    for kind, words in (
        ("signs", SIGNS_WORDS),
        ("forced", FORCED_WORDS),
        ("sdh", SDH_WORDS),
        ("container", CONTAINER_WORDS),
        ("noise", NOISE_WORDS),
    ):
        if folded in words:
            return [(kind, folded)]
    if NUMERIC.fullmatch(folded):
        return [("noise", folded)]
    for split in range(2, len(folded) - 1):
        head, tail = folded[:split], folded[split:]
        if head in LANGUAGE_WORDS and tail in CONTAINER_WORDS:
            return [("language", LANGUAGE_WORDS[head]), ("container", tail)]
        if head in CONTAINER_WORDS and tail in LANGUAGE_WORDS:
            return [("container", head), ("language", LANGUAGE_WORDS[tail])]
    return []


def _describe(text: str, info: dict) -> list[str]:
    """Classify one folder or name fragment; return what is left as a group name.

    The group keeps its own spelling ("AleX_MytH & Hollow", "Suzaku&SPAWN2");
    only recognized words, brackets and dangling separators are removed.
    """
    languages = set()
    pieces, last = [], 0
    for match in WORD.finditer(text):
        roles = _roles(match[0].casefold())
        for kind, value in roles:
            if kind == "language":
                languages.add(value)
            elif kind in {"signs", "forced", "sdh"}:
                info[kind] = True
        if roles:
            pieces.append(text[last : match.start()] + "\0")
            last = match.end()
    pieces.append(text[last:])
    if len(languages) == 1:
        info["language"] = languages.pop()
    group = re.sub(r"[\[\](){}]", " ", "".join(pieces)).replace(".", " ")
    # Separators next to removed words go too: "Crunchyroll - ENG", "Signs & Songs".
    group = re.sub(r"[\s\-_&+,;:]*\0[\s\-_&+,;:]*", " ", group)
    group = re.sub(r"\s+", " ", group).strip(" -_&+,;:")
    return [group] if WORD.search(group) else []


def describe(
    relative: str,
    extra: str | None,
    rules: list[dict],
    default_language: str = "und",
) -> Track:
    """Derive language, Jellyfin title and flags from the folders around a sidecar.

    Folders give groups and labels ("RUS Subs/SovetRomantica/Надписи"), deeper
    folders win the language. `extra` is the filename part after the source video
    stem ("AniStar", "Reanimedia_full"); without an exact stem match only bracketed
    or trailing dotted language/flag tags of the filename are trusted.
    """
    path = Path(relative)
    info: dict = {}
    groups: list[str] = []
    for part in path.parts[:-1]:
        groups += _describe(part, info)
    if extra is not None:
        groups += _describe(extra, info)
    else:
        tags = re.findall(r"\[([^]]*)\]|\(([^)]*)\)", path.stem)
        tags = [a or b for a, b in tags]
        tags += reversed(list(_trailing_tags(path.stem)))
        for tag in tags:
            words = {w.casefold() for w in WORD.findall(tag)}
            known = LANGUAGE_WORDS.keys() | SIGNS_WORDS | FORCED_WORDS | SDH_WORDS
            if words and words <= known:
                _describe(tag, info)
    language = info.get("language")
    for rule in rules:
        if re.search(rule["pattern"], relative, flags=re.IGNORECASE):
            language = rule.get("language", language)
            if "group" in rule:
                groups = [rule["group"]]
    unique = []
    for group in groups:
        if group.casefold() not in {g.casefold() for g in unique}:
            unique.append(group)
    title = " ".join(unique + (["Signs"] if info.get("signs") else []))
    flags = [f for f in ("forced", "sdh") if info.get(f)]
    return Track(language or default_language, title[:80].strip(), flags)


def _trailing_tags(stem: str):
    """Dotted tags at the end of a filename: "Show - 01.rus.forced" -> forced, rus."""
    parts = stem.split(".")
    known = LANGUAGE_WORDS.keys() | SIGNS_WORDS | FORCED_WORDS | SDH_WORDS
    for part in reversed(parts[1:]):
        if part.casefold() not in known:
            return
        yield part
