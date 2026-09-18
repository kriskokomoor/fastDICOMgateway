"""A pragmatic, line-based parser for `strace -f -tt -yy` output.

This is NOT a general strace-grammar parser. It handles the common
single-line syscall-completed form that `strace -f -tt -yy` produces for
the small set of syscalls M2 traces (%file family + write). It does not
attempt to reconstruct syscalls split across `<unfinished ...>` /
`<... resumed>` lines under heavy thread/process interleaving -- those
lines are counted and skipped, not silently dropped without a trace (see
ParseSummary.unparsed_lines). This is a documented limitation (see
docs/M2_PERSISTENCE_BOUNDARY_VALIDATION.md "Limitations"): the bounded
post-run filesystem scan in m2.py is the independent backstop for exactly
what this parser might miss.

Expected line shape (one example per traced syscall):

    12345 10:00:00.123456 openat(AT_FDCWD, "/path", O_RDONLY) = 3</path>
    12345 10:00:00.234567 openat(AT_FDCWD, "/tmp/x", O_WRONLY|O_CREAT, 0644) = 5</tmp/x>
    12345 10:00:00.345678 write(5</tmp/x>, "hello", 5) = 5
    12345 10:00:00.456789 write(4<socket:[98765]>, "HTTP/1.1 200...", 1234) = 1234
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_LINE_RE = re.compile(
    r"^(?P<pid>\d+)\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"(?P<syscall>[a-zA-Z_][a-zA-Z0-9_]*)"
    r"\((?P<args>.*)\)\s*=\s*"
    r"(?P<retval>-?\d+|0x[0-9a-fA-F]+|\?)"
    r"(?P<annotation>.*)$"
)

# First double-quoted string in an argument list -- used to pull the path
# out of open/openat/creat/unlink/rename argument lists (open/creat/unlink
# take it as the first arg; openat/renameat take it as the second, after a
# dirfd -- searching for the first quoted string handles both).
_FIRST_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')

# fd argument annotated by -yy, e.g. `5</tmp/x>` or `4<socket:[123]>`.
_FD_ANNOTATION_RE = re.compile(r"^\s*(?P<fd>\d+)<(?P<target>[^>]*)>")

# Trailing return-value annotation from -yy on a successful open, e.g.
# `= 5</tmp/x>`.
_RETVAL_ANNOTATION_RE = re.compile(r"^<(?P<target>[^>]*)>")

WRITE_CAPABLE_FLAGS = ("O_CREAT", "O_WRONLY", "O_RDWR", "O_TRUNC", "O_APPEND")

_OPEN_SYSCALLS = {"open", "openat", "creat"}
_DELETE_SYSCALLS = {"unlink", "unlinkat"}
_RENAME_SYSCALLS = {"rename", "renameat", "renameat2"}


@dataclass(frozen=True)
class Event:
    pid: int
    timestamp: str  # "HH:MM:SS.ffffff", same-day wall clock
    syscall: str
    args: str
    retval: str
    path: str | None  # resolved path, when determinable
    write_capable: bool  # True if an open-family call requested write access
    fd_target: str | None  # for write(): the -yy annotation of the fd ("socket:[...]", "/path", ...)


@dataclass
class ParseSummary:
    events: list[Event] = field(default_factory=list)
    total_lines: int = 0
    parsed_lines: int = 0
    unparsed_lines: int = 0
    unparsed_samples: list[str] = field(default_factory=list)


def _classify_fd_target(target: str) -> str:
    if target.startswith("socket:"):
        return "socket"
    if target.startswith("pipe:"):
        return "pipe"
    if target.startswith("anon_inode:"):
        return "anon_inode"
    if target in ("", "???"):
        return "unknown"
    return "path"


def parse_strace_text(text: str) -> ParseSummary:
    summary = ParseSummary()
    for line in text.splitlines():
        summary.total_lines += 1
        if not line or line.startswith("+++") or line.startswith("---"):
            continue
        if "<unfinished ...>" in line or "resumed>" in line:
            summary.unparsed_lines += 1
            if len(summary.unparsed_samples) < 10:
                summary.unparsed_samples.append(line)
            continue

        match = _LINE_RE.match(line)
        if not match:
            summary.unparsed_lines += 1
            if len(summary.unparsed_samples) < 10:
                summary.unparsed_samples.append(line)
            continue

        pid = int(match.group("pid"))
        timestamp = match.group("time")
        syscall = match.group("syscall")
        args = match.group("args")
        retval = match.group("retval")
        annotation = match.group("annotation").strip()

        path = None
        write_capable = False
        fd_target = None

        if syscall in _OPEN_SYSCALLS or syscall in _DELETE_SYSCALLS or syscall in _RENAME_SYSCALLS:
            string_match = _FIRST_STRING_RE.search(args)
            if string_match:
                path = string_match.group(1)
            if syscall in _OPEN_SYSCALLS:
                write_capable = any(flag in args for flag in WRITE_CAPABLE_FLAGS)
                # Prefer the resolved path from the -yy return-value
                # annotation when the open succeeded (handles relative
                # paths resolved against a dirfd).
                retval_match = _RETVAL_ANNOTATION_RE.match(annotation)
                if retval_match:
                    path = retval_match.group("target")

        elif syscall == "write":
            fd_match = _FD_ANNOTATION_RE.match(args)
            if fd_match:
                fd_target = fd_match.group("target")

        summary.parsed_lines += 1
        summary.events.append(
            Event(
                pid=pid,
                timestamp=timestamp,
                syscall=syscall,
                args=args,
                retval=retval,
                path=path,
                write_capable=write_capable,
                fd_target=fd_target,
            )
        )
    return summary


def parse_strace_file(path: str) -> ParseSummary:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parse_strace_text(handle.read())


def write_capable_opens(summary: ParseSummary) -> list[Event]:
    """Every open-family call that requested create/write/truncate/append
    access, regardless of whether it later succeeded -- the request itself
    is the signal M2 cares about."""
    return [e for e in summary.events if e.syscall in _OPEN_SYSCALLS and e.write_capable]


def mutating_events(summary: ParseSummary) -> list[Event]:
    """Every event that asked the kernel to create, write, rename, or
    delete something on a filesystem: write-capable opens plus all
    rename/unlink calls (which are inherently mutating regardless of
    flags). This is the superset `unexpected_filesystem_writes` accounting
    is built from."""
    return [
        e
        for e in summary.events
        if (e.syscall in _OPEN_SYSCALLS and e.write_capable)
        or e.syscall in _DELETE_SYSCALLS
        or e.syscall in _RENAME_SYSCALLS
    ]


def file_writes(summary: ParseSummary) -> list[Event]:
    """write() calls whose fd was -yy-annotated as a real filesystem path
    (as opposed to a socket, pipe, or unresolved fd)."""
    return [
        e
        for e in summary.events
        if e.syscall == "write" and e.fd_target is not None and _classify_fd_target(e.fd_target) == "path"
    ]


def classify_fd_target(target: str) -> str:
    return _classify_fd_target(target)


def in_window(event: Event, start: str, end: str) -> bool:
    """Same-day HH:MM:SS.ffffff string comparison -- valid because these
    are lexicographically ordered fixed-width timestamps and a single M2
    run never spans midnight."""
    return start <= event.timestamp <= end
