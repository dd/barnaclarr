"""Hardlinks only, with inode verification and symlink-resistant directory access."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat


def signature(path: Path) -> str:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Not a regular file: {path}")
    return json.dumps([st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns])


@contextmanager
def parent_fd(path: Path, *, create: bool = False):
    """Walk from / using O_NOFOLLOW on every directory, never following symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for component in path.parent.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o775, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def link(source: Path, destination: Path, expected: str):
    """Create a verified hardlink; EXDEV and EACCES propagate without fallback."""
    with parent_fd(source) as src, parent_fd(destination, create=True) as dst:
        st = os.stat(source.name, dir_fd=src, follow_symlinks=False)
        current = json.dumps([st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns])
        if not stat.S_ISREG(st.st_mode) or current != expected:
            raise ValueError("Source changed after planning")
        os.link(
            source.name, destination.name, src_dir_fd=src, dst_dir_fd=dst, follow_symlinks=False
        )
        actual = os.stat(destination.name, dir_fd=dst, follow_symlinks=False)
        if (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns) != (
            st.st_dev,
            st.st_ino,
            st.st_size,
            st.st_mtime_ns,
        ):
            # Do not unlink a possibly concurrently replaced destination.
            raise ValueError("Concurrent source/destination change; manual review required")


def remove_owned(path: Path, expected: str) -> bool:
    """Remove only the recorded inode; preserve edited or replaced files."""
    with parent_fd(path) as fd:
        try:
            st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        actual = json.dumps([st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns])
        if not stat.S_ISREG(st.st_mode) or actual != expected:
            return False
        os.unlink(path.name, dir_fd=fd)
        return True
