"""Fail-closed helpers for command-line output files."""

from __future__ import annotations

import os
import secrets
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

TRUSTED_TOP_LEVEL_ALIASES = (
    {
        Path("/etc"): Path("/private/etc"),
        Path("/tmp"): Path("/private/tmp"),
        Path("/var"): Path("/private/var"),
    }
    if sys.platform == "darwin"
    else {}
)


class InputRootError(ValueError):
    """Stable public error for an invalid directory scan root."""

    REASON = "root must be an existing directory without symlink components"

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"{path}: {self.REASON}")


class InputTooLargeError(ValueError):
    """Raised before an input reader can exceed its configured byte budget."""


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _normalize_top_level_alias(path: Path) -> Path:
    """Resolve only a platform-owned first component such as macOS /var."""

    absolute = _absolute_path(path)
    anchor = Path(absolute.anchor)
    relative = absolute.relative_to(anchor)
    if not relative.parts:
        return absolute
    first = anchor / relative.parts[0]
    try:
        first_mode = first.lstat().st_mode
    except FileNotFoundError:
        return absolute
    if not stat.S_ISLNK(first_mode):
        return absolute
    expected = TRUSTED_TOP_LEVEL_ALIASES.get(first)
    if expected is None:
        raise ValueError(f"path contains untrusted top-level symlink: {first}")
    try:
        resolved_first = first.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"path contains unsafe top-level symlink: {first}") from exc
    if resolved_first != expected or not resolved_first.is_dir():
        raise ValueError(f"top-level path alias has unexpected target: {first} -> {resolved_first}")
    return resolved_first.joinpath(*relative.parts[1:])


def _directory_flags() -> int:
    flags = os.O_RDONLY
    for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    return flags


def _supports_dir_fd() -> bool:
    return os.name != "nt" and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd


def _open_directory_fd(path: Path, *, create: bool) -> tuple[Path, int]:
    """Open each directory component relative to the previously verified one."""

    candidate = _normalize_top_level_alias(path)
    anchor = Path(candidate.anchor)
    descriptor = os.open(anchor, _directory_flags())
    current = anchor
    try:
        for component in candidate.relative_to(anchor).parts:
            current = current / component
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise ValueError(f"directory does not exist: {current}") from None
                try:
                    os.mkdir(component, 0o777, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise ValueError(f"directory path contains symlink or non-directory component: {current}") from exc
            os.close(descriptor)
            descriptor = child
        return candidate, descriptor
    except Exception:
        os.close(descriptor)
        raise


def ensure_safe_directory(path: Path, *, create: bool) -> Path:
    """Return an absolute directory path without traversing symlink components."""

    candidate = _normalize_top_level_alias(path)
    if _supports_dir_fd():
        candidate, descriptor = _open_directory_fd(candidate, create=create)
        os.close(descriptor)
        return candidate

    anchor = Path(candidate.anchor)
    current = anchor
    for component in candidate.relative_to(anchor).parts:
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if not create:
                raise ValueError(f"directory does not exist: {current}") from None
            try:
                current.mkdir()
            except FileExistsError:
                pass
            mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"directory path contains symlink component: {current}")
        if not stat.S_ISDIR(mode):
            raise ValueError(f"directory path component is not a directory: {current}")
    return candidate


def _entry_mode(parent_fd: int, name: str) -> int | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return None


def _validate_output_entry(parent_fd: int, candidate: Path) -> None:
    mode = _entry_mode(parent_fd, candidate.name)
    if mode is None:
        return
    if stat.S_ISLNK(mode):
        raise ValueError(f"output path is a symlink: {candidate}")
    if not stat.S_ISREG(mode):
        raise ValueError(f"output path is not a regular file: {candidate}")


def _directory_identity_matches(parent_fd: int, parent: Path) -> bool:
    try:
        opened = os.fstat(parent_fd)
        current = os.stat(parent, follow_symlinks=False)
    except OSError:
        return False
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


def ensure_safe_output_path(path: Path, *, create_parent: bool = True) -> Path:
    """Validate an output path and reject final or ancestor symlinks."""

    candidate = _normalize_top_level_alias(path)
    if _supports_dir_fd():
        parent, descriptor = _open_directory_fd(candidate.parent, create=create_parent)
        try:
            candidate = parent / candidate.name
            _validate_output_entry(descriptor, candidate)
            return candidate
        finally:
            os.close(descriptor)

    parent = ensure_safe_directory(candidate.parent, create=create_parent)
    candidate = parent / candidate.name
    try:
        mode = candidate.lstat().st_mode
    except FileNotFoundError:
        return candidate
    if stat.S_ISLNK(mode):
        raise ValueError(f"output path is a symlink: {candidate}")
    if not stat.S_ISREG(mode):
        raise ValueError(f"output path is not a regular file: {candidate}")
    return candidate


def ensure_safe_input_file(path: Path) -> Path:
    """Validate a regular input file reached without symlink components."""

    candidate = _normalize_top_level_alias(path)
    parent = ensure_safe_directory(candidate.parent, create=False)
    candidate = parent / candidate.name
    try:
        mode = candidate.lstat().st_mode
    except FileNotFoundError:
        raise ValueError(f"input file does not exist: {candidate}") from None
    if stat.S_ISLNK(mode):
        raise ValueError(f"input path is a symlink: {candidate}")
    if not stat.S_ISREG(mode):
        raise ValueError(f"input path is not a regular file: {candidate}")
    return candidate


def read_bytes_no_follow(path: Path, *, max_bytes: int | None = None) -> bytes:
    """Read one stable regular file without following a leaf or ancestor link."""

    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")

    candidate = _normalize_top_level_alias(path)
    if _supports_dir_fd():
        parent, descriptor = _open_directory_fd(candidate.parent, create=False)
        candidate = parent / candidate.name
        try:
            before = os.stat(candidate.name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ValueError(f"input path is not a regular non-symlink file: {candidate}")
            file_descriptor = os.open(
                candidate.name,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                opened = os.fstat(file_descriptor)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError(f"input file changed before read: {candidate}")
                if max_bytes is not None and opened.st_size > max_bytes:
                    raise InputTooLargeError(
                        f"input exceeds byte limit ({max_bytes} bytes): {candidate}"
                    )
                with os.fdopen(file_descriptor, "rb", closefd=True) as stream:
                    file_descriptor = -1
                    data = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
                    after = os.fstat(stream.fileno())
                if (
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                ):
                    raise ValueError(f"input file changed during read: {candidate}")
                if max_bytes is not None and len(data) > max_bytes:
                    raise InputTooLargeError(
                        f"input exceeds byte limit ({max_bytes} bytes): {candidate}"
                    )
                return data
            finally:
                if file_descriptor >= 0:
                    os.close(file_descriptor)
        finally:
            os.close(descriptor)

    # Windows lacks the dir_fd primitives used above. Refuse links, then
    # compare the path metadata before and after the descriptor-backed read.
    candidate = ensure_safe_input_file(candidate)
    before = candidate.lstat()
    with candidate.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"input file changed before read: {candidate}")
        if max_bytes is not None and opened.st_size > max_bytes:
            raise InputTooLargeError(
                f"input exceeds byte limit ({max_bytes} bytes): {candidate}"
            )
        data = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    current = candidate.lstat()
    opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    current_identity = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    if after_identity != opened_identity or current_identity != opened_identity:
        raise ValueError(f"input file changed during read: {candidate}")
    if max_bytes is not None and len(data) > max_bytes:
        raise InputTooLargeError(
            f"input exceeds byte limit ({max_bytes} bytes): {candidate}"
        )
    return data


def ensure_safe_input_directory(path: Path) -> Path:
    """Validate an input directory reached without symlink components."""

    return ensure_safe_directory(path, create=False)


def validate_input_root(path: Path) -> Path:
    """Return a real directory scan root or raise one stable shape error."""

    try:
        return ensure_safe_input_directory(path)
    except (OSError, ValueError):
        raise InputRootError(path) from None


def _open_temporary_at(parent_fd: int, candidate: Path) -> tuple[str, int]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(100):
        name = f".{candidate.name}.{secrets.token_hex(12)}.tmp"
        try:
            return name, os.open(name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
    raise FileExistsError(f"could not allocate a unique temporary output beside {candidate}")


def _apply_output_mode(descriptor: int, mode: int | None) -> None:
    if mode is None:
        return
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(descriptor, mode)


@contextmanager
def atomic_binary_writer(path: Path, *, mode: int | None = None) -> Iterator[BinaryIO]:
    """Yield a binary file and publish it atomically without path traversal."""

    candidate = _normalize_top_level_alias(path)
    if not _supports_dir_fd():
        candidate = ensure_safe_output_path(candidate)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{candidate.name}.", dir=candidate.parent)
        _apply_output_mode(descriptor, mode)
        temporary = Path(temporary_name)
        handle = os.fdopen(descriptor, "w+b")
        try:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            candidate = ensure_safe_output_path(candidate, create_parent=False)
            os.replace(temporary, candidate)
        finally:
            if not handle.closed:
                handle.close()
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return

    parent, parent_fd = _open_directory_fd(candidate.parent, create=True)
    candidate = parent / candidate.name
    temporary_name = ""
    handle: BinaryIO | None = None
    try:
        _validate_output_entry(parent_fd, candidate)
        temporary_name, temporary_fd = _open_temporary_at(parent_fd, candidate)
        _apply_output_mode(temporary_fd, mode)
        handle = os.fdopen(temporary_fd, "w+b")
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        if not _directory_identity_matches(parent_fd, parent):
            raise ValueError(f"output parent directory changed while writing: {parent}")
        _validate_output_entry(parent_fd, candidate)
        os.replace(temporary_name, candidate.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary_name = ""
    finally:
        if handle is not None and not handle.closed:
            handle.close()
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def safe_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> Path:
    """Write text atomically without following final or ancestor symlinks."""

    with atomic_binary_writer(path, mode=mode) as handle:
        handle.write(text.encode(encoding))
    return ensure_safe_output_path(path, create_parent=False)


def safe_create_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> Path:
    """Publish a new text file atomically without replacing an existing path.

    The payload is fully written and synced to a same-directory temporary
    file.  A hard-link publication step then provides no-clobber semantics:
    if another process creates the destination first, publication fails and
    the competing file remains untouched.
    """

    payload = text.encode(encoding)
    candidate = _normalize_top_level_alias(path)
    if _supports_dir_fd():
        parent, parent_fd = _open_directory_fd(candidate.parent, create=True)
        candidate = parent / candidate.name
        temporary_name = ""
        temporary_fd = -1
        try:
            _validate_output_entry(parent_fd, candidate)
            if _entry_mode(parent_fd, candidate.name) is not None:
                raise FileExistsError(f"refusing to replace existing output file: {candidate}")
            temporary_name, temporary_fd = _open_temporary_at(parent_fd, candidate)
            _apply_output_mode(temporary_fd, mode)
            with os.fdopen(temporary_fd, "wb", closefd=True) as stream:
                temporary_fd = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if not _directory_identity_matches(parent_fd, parent):
                raise ValueError(f"output parent directory changed while writing: {parent}")
            try:
                os.link(
                    temporary_name,
                    candidate.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise FileExistsError(
                    f"refusing to replace existing output file: {candidate}"
                ) from None
            os.unlink(temporary_name, dir_fd=parent_fd)
            temporary_name = ""
            try:
                os.fsync(parent_fd)
            except OSError:
                # Some filesystems do not support syncing a directory fd.
                pass
        finally:
            if temporary_fd >= 0:
                os.close(temporary_fd)
            if temporary_name:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)
        return ensure_safe_output_path(candidate, create_parent=False)

    candidate = ensure_safe_output_path(candidate)
    if candidate.exists():
        raise FileExistsError(f"refusing to replace existing output file: {candidate}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{candidate.name}.", suffix=".tmp", dir=candidate.parent
    )
    temporary = Path(temporary_name)
    try:
        _apply_output_mode(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        candidate = ensure_safe_output_path(candidate, create_parent=False)
        if candidate.exists():
            raise FileExistsError(f"refusing to replace existing output file: {candidate}")
        try:
            os.link(temporary, candidate, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError(
                f"refusing to replace existing output file: {candidate}"
            ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return ensure_safe_output_path(candidate, create_parent=False)
