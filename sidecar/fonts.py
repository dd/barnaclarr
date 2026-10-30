"""Read internal font family/style names without modifying font bytes."""

from pathlib import Path
from fontTools.ttLib import TTCollection, TTFont


def identities(path: Path) -> list[tuple[str, str]]:
    """Return all faces in a TTC; prefer typographic family and subfamily."""
    container = (
        TTCollection(path, lazy=True) if path.suffix.lower() == ".ttc" else TTFont(path, lazy=True)
    )
    try:
        fonts = container.fonts if isinstance(container, TTCollection) else [container]
        result = []
        for font in fonts:
            names = font["name"]
            family = names.getDebugName(16) or names.getDebugName(1)
            style = names.getDebugName(17) or names.getDebugName(2) or "Regular"
            if not family:
                raise ValueError("Font has no family name")
            result.append((family, style))
        return result
    finally:
        container.close()
