#!/usr/bin/env python3
"""Run one maintenance subprocess with a bounded lifetime and diagnostics."""

from __future__ import annotations

import argparse
import codecs
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence, TextIO

TAIL_LIMIT = 4000


@dataclass(frozen=True)
class CapturedRun:
    """Bounded subprocess result used by independently installed Skills."""

    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    stdout_limit_exceeded: bool = False
    stderr_limit_exceeded: bool = False


def _wait_for_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _posix_group_exists(process_group: int) -> bool:
    """Return whether a POSIX process group still has a live member."""

    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_posix_group_exit(process_group: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _posix_group_exists(process_group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def _terminate(process: subprocess.Popen[bytes], *, grace_period: float = 5.0) -> None:
    """Terminate the complete process tree owned by *process*.

    A direct ``Popen.kill`` is insufficient for validation commands because a
    child can inherit the parent's stdout/stderr pipes and keep the parent
    ``communicate`` call blocked.  POSIX uses the process group created by
    ``start_new_session``.  Windows uses ``taskkill /T`` as the reliable tree
    operation and falls back to the direct process handle if the utility is
    unavailable.
    """

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=max(1.0, grace_period),
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError):
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        _wait_for_exit(process, grace_period)
        if _wait_for_posix_group_exit(process.pid, grace_period):
            return

    if os.name == "nt" and _wait_for_exit(process, grace_period):
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=max(1.0, grace_period),
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    # A hostile or detached process may still refuse both tree operations.
    # Do not turn that edge case into an unbounded wait in the caller.
    if not _wait_for_exit(process, grace_period):
        try:
            process.kill()
        except OSError:
            pass
        _wait_for_exit(process, grace_period)
    if os.name != "nt":
        _wait_for_posix_group_exit(process.pid, grace_period)


def _cleanup_descendants_after_success(
    process: subprocess.Popen[bytes], *, grace_period: float
) -> None:
    """Do not let a successful wrapper orphan descendants in its process group."""

    if os.name == "nt":
        # ``taskkill /T`` cannot reliably discover descendants after their
        # parent handle has exited. Timeout paths remain tree-safe on Windows;
        # successful maintenance commands are required not to daemonize.
        return
    if _posix_group_exists(process.pid):
        _terminate(process, grace_period=grace_period)


def _read_tail(stream: BinaryIO) -> str:
    """Read only the diagnostic tail of a potentially large captured stream."""

    stream.flush()
    stream.seek(0, os.SEEK_END)
    byte_count = stream.tell()
    stream.seek(max(0, byte_count - TAIL_LIMIT * 4))
    return stream.read().decode("utf-8", errors="replace")[-TAIL_LIMIT:]


def _emit_stream(stream: BinaryIO, target: TextIO) -> None:
    """Replay captured output without loading the complete stream into memory."""

    stream.flush()
    stream.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while chunk := stream.read(64 * 1024):
        target.write(decoder.decode(chunk))
    target.write(decoder.decode(b"", final=True))


def _read_bounded(stream: BinaryIO, limit: int) -> tuple[bytes, bool]:
    if limit < 0:
        raise ValueError("capture limits must be non-negative")
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return stream.read(min(size, limit + 1)), size > limit


def _spool_exceeds(stream: BinaryIO, limit: int) -> bool:
    if limit < 0:
        raise ValueError("capture limits must be non-negative")
    return os.fstat(stream.fileno()).st_size > limit


def run_capture(
    command: Sequence[str],
    timeout: float,
    label: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    grace_period: float = 5.0,
    max_stdout_bytes: int = 16 * 1024 * 1024,
    max_stderr_bytes: int = 4 * 1024 * 1024,
) -> CapturedRun:
    """Capture a bounded command while terminating its complete process tree."""

    if not command:
        raise ValueError("a command is required")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite number greater than zero")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with tempfile.TemporaryFile(mode="w+b") as stdout_spool, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_spool:
        try:
            process = subprocess.Popen(
                list(command),
                stdout=stdout_spool,
                stderr=stderr_spool,
                cwd=Path(cwd).resolve() if cwd is not None else None,
                env=dict(env) if env is not None else None,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
        except OSError as exc:
            return CapturedRun(127, b"", str(exc).encode("utf-8", errors="replace"))
        timed_out = False
        output_limit_exceeded = False
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if _spool_exceeds(stdout_spool, max_stdout_bytes) or _spool_exceeds(
                stderr_spool,
                max_stderr_bytes,
            ):
                output_limit_exceeded = True
                _terminate(process, grace_period=grace_period)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate(process, grace_period=grace_period)
                break
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
        if not timed_out and not output_limit_exceeded:
            _cleanup_descendants_after_success(process, grace_period=grace_period)
        stdout, stdout_too_large = _read_bounded(stdout_spool, max_stdout_bytes)
        stderr, stderr_too_large = _read_bounded(stderr_spool, max_stderr_bytes)
        stdout_too_large = stdout_too_large or _spool_exceeds(stdout_spool, max_stdout_bytes)
        stderr_too_large = stderr_too_large or _spool_exceeds(stderr_spool, max_stderr_bytes)
        limit_exceeded = output_limit_exceeded or stdout_too_large or stderr_too_large
        if timed_out:
            returncode = 124
        elif limit_exceeded:
            returncode = 125
        else:
            returncode = process.returncode
        return CapturedRun(
            returncode,
            stdout,
            stderr,
            timed_out=timed_out,
            stdout_limit_exceeded=stdout_too_large,
            stderr_limit_exceeded=stderr_too_large,
        )


def run(
    command: Sequence[str],
    timeout: float,
    label: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    grace_period: float = 5.0,
) -> int:
    if not command:
        raise ValueError("a command is required")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite number greater than zero")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with tempfile.TemporaryFile(mode="w+b") as stdout_spool, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_spool:
        try:
            process = subprocess.Popen(
                list(command),
                stdout=stdout_spool,
                stderr=stderr_spool,
                cwd=Path(cwd).resolve() if cwd is not None else None,
                env=dict(env) if env is not None else None,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
        except OSError as exc:
            print(f"FAILED step={label}: {exc}", file=sys.stderr)
            return 127
        started = time.monotonic()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(process, grace_period=grace_period)
            elapsed = time.monotonic() - started
            print(
                f"TIMEOUT step={label} timeout_seconds={timeout:g} "
                f"elapsed_seconds={elapsed:.2f}",
                file=sys.stderr,
            )
            print(f"COMMAND {' '.join(command)}", file=sys.stderr)
            stdout_tail = _read_tail(stdout_spool)
            stderr_tail = _read_tail(stderr_spool)
            if stdout_tail:
                print(f"STDOUT_TAIL\n{stdout_tail}", file=sys.stderr)
            if stderr_tail:
                print(f"STDERR_TAIL\n{stderr_tail}", file=sys.stderr)
            return 124
        _cleanup_descendants_after_success(process, grace_period=grace_period)
        _emit_stream(stdout_spool, sys.stdout)
        _emit_stream(stderr_spool, sys.stderr)
        return process.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--label", default="subprocess")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite number greater than zero")
    try:
        return run(command, args.timeout, args.label)
    except OSError as exc:
        print(f"FAILED step={args.label}: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
