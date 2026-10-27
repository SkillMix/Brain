"""read_guard: the files reader's guard (Ruling R23). A PreToolUse hook on the Read tool, written into the
settings file of the files reader's own run (`reader.read_files`): it refuses (exit 2) every file that does
not resolve to a place inside the reader's folder. The person's own settings may allow Read everywhere or
add folders, and a bare Read allow reads any file on the machine; this hook is the check that does not
depend on them.

    python -I read_guard.py <folder>        # the hook's JSON on stdin; exit 0 allows, exit 2 blocks

It runs as `python -I`, so it can import nothing but the standard library, and it fails closed: a request
it cannot read, a path it cannot resolve (one that is not there included), a folder that is not there, a
link that leads out, or any fault of its own is a refusal. A path of another machine or a device (two
separators first) is refused as spelt, without a file system call: resolving it would reach out over
the network. It bounds its own time (Ruling R45): a hook Claude Code cuts off at its timeout lets the call
through (dry run A), so after LIMIT_SECONDS the guard ends itself with exit 2, well inside the hook's timeout
(`routine.GUARD_TIMEOUT`), and two seconds later faulthandler ends it even inside one long call (exit 1, which
the hook command's `|| exit 2` turns into a refusal). It says why on stderr, without repeating the path.
"""
from __future__ import annotations

import faulthandler
import json
import os
import sys
import threading
from pathlib import Path

BLOCK = 2
LIMIT_SECONDS = 10   # the guard's own time; the hook's timeout (routine.GUARD_TIMEOUT) is three times that
MAX_RAW = 1024 * 1024   # the most of stdin read: a hook event past it is refused unread
WHY = "the reader may only read the files in its own folder"


def check(folder, event) -> str | None:
    """None when the request is a Read of a file inside `folder`; else the reason it is refused. The
    path is resolved first (links and `..` followed), against the folder when it is relative."""
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str) or not path.strip():
        return f"{WHY}: this request names no file"
    if path.strip().replace("/", "\\").startswith("\\\\"):
        # Another machine's path or a device, whatever the slashes (\\host\share, //host/share, \\?\..., \\.\...):
        # refused as spelt. Resolving it would open it over the network, which takes seconds, tells that host
        # this machine's name and, for a hook slow enough to be cut off, lets the call through.
        return f"{WHY}: this file is not in it"
    try:
        root = Path(folder).resolve(strict=True)
        target = (root / path).resolve(strict=True)   # an absolute path replaces the folder in the join
        inside = target.is_relative_to(root)
    except (OSError, ValueError, RuntimeError):   # not there, a null byte, a loop of links
        return f"{WHY}: this file is not in it"
    return None if inside else f"{WHY}: this file is not in it"


def main(argv: list, raw: bytes) -> int:
    try:
        if len(raw) > MAX_RAW:
            raise ValueError("a request larger than any Read")
        reason = check(argv[0], json.loads(raw.decode("utf-8")))
    except BaseException:   # noqa: BLE001 - whatever went wrong, the call does not go through
        reason = f"{WHY}: this request could not be read"
    if reason:
        print(reason, file=sys.stderr)
        return BLOCK
    return 0


def arm(said: str) -> threading.Timer:
    """The guard's own time limit: after LIMIT_SECONDS a daemon timer says why and ends the process with exit 2,
    whatever it is doing (resolving a path on a disk that sleeps, say)."""
    def expire() -> None:
        try:
            print(f"{said}: the check took too long, so the call is refused", file=sys.stderr, flush=True)
        finally:
            os._exit(BLOCK)
    timer = threading.Timer(LIMIT_SECONDS, expire)
    timer.daemon = True
    timer.start()
    return timer


def bound(seconds: float) -> None:
    """The limit no call outlasts (NB-1): the timer cannot run while one long call holds the interpreter, so
    faulthandler's own thread ends the process with exit 1 after `seconds`, and the hook command's `|| exit 2`
    makes that a refusal (`reader.guard_command`)."""
    faulthandler.dump_traceback_later(seconds, exit=True, file=sys.stderr)


def run() -> int:
    """The hook: the timer and the hard limit first, then stdin (at most MAX_RAW and one byte), then `main`."""
    arm(WHY)
    bound(LIMIT_SECONDS + 2)
    return main(sys.argv[1:], sys.stdin.buffer.read(MAX_RAW + 1))


if __name__ == "__main__":
    sys.exit(run())
