"""bash_guard: the routines' Bash guard (Ruling R30). A PreToolUse hook on the Bash tool in each routine's
profile (`routine.routine_settings`): it lets a command through only when it is one plain command of a shape
the profile's own allow list names (`Bash(<python> [<root>/]_meta/tools/<tool>.py *)`, the read-only gh and git
commands) and none its deny list names, and refuses (exit 2) everything else. The person's own settings may
allow more (`Bash(wget *)`, a bare `Bash`); a routine reads records anyone can write, so this hook is the check
that does not depend on them.

    python -I bash_guard.py <the routine's settings file>     # the hook's JSON on stdin; exit 0 allows, exit 2 blocks
    python -I bash_guard.py --task                            # the hook on Task (Agent): the general-purpose agent only
    python -I bash_guard.py --connectors <settings file>      # the hook on mcp__.*: the connector tools the grant names

One plain command: nothing after `;`, `&`, `&&`, `||` or `|`, no redirection, no subshell, no command or
variable substitution, no backslash, no line break. Text between single quotes is literal; between double
quotes `$`, a backtick and a backslash are refused too. A rule `Bash(<words> *)` lets through `<words>` alone
or followed by a space and anything plain; a `*` elsewhere stands for any text.

Two more modes guard what else a routine reaches (Ruling R45). `--task`: a routine's second agent is the
general-purpose one, given a description and a prompt (a model and a background run may be named); any other
agent, a place or a permission mode of its own is refused (Minor 10). `--connectors`: a connector tool goes
through only when the routine's own settings file allows it by its exact name and does not deny it, so a tool
that appeared after the last probe, which no deny rule names, is refused too (Minor 11).

It runs as `python -I`, so it can import nothing but the standard library, and it fails closed: a request it
cannot read, a settings file it cannot read, or any fault of its own is a refusal. It bounds its own time: a
hook Claude Code cuts off at its timeout lets the call through (dry run A), so after LIMIT_SECONDS the guard
ends itself with exit 2, well inside the hook's timeout (`routine.GUARD_TIMEOUT`), and two seconds later
faulthandler ends it even inside one long call (exit 1, which the hook command's `|| exit 2` turns into a
refusal). It says why on stderr, without repeating the command, the agent or the tool.
"""
from __future__ import annotations

import faulthandler
import json
import os
import re
import sys
import threading
from pathlib import Path

BLOCK = 2
LIMIT_SECONDS = 10   # the guard's own time; the hook's timeout (routine.GUARD_TIMEOUT) is three times that
MAX_RAW = 1024 * 1024   # the most of stdin read: a hook event past it is refused unread
TASK_WHY = ("routines start only the general-purpose agent, given a description and a prompt: no other agent, "
            "place or permission mode")
CONNECTOR_WHY = "routines call only the connector tools they were granted"
TASK_KEYS = {"description", "prompt", "subagent_type", "model", "run_in_background"}
GRANTED = re.compile(r"mcp__[A-Za-z0-9_-]+")   # one connector tool by its exact name: no pattern, no rule
WHY = ("routines run only their own tools (python _meta/tools/<tool>.py ...) and the read-only gh and git "
       "commands, one plain command at a time: no cd, pipes or redirects, and forward slashes in paths")
OUTSIDE = set(";&|<>()$`\\!\n\r\0")    # outside quotes: what joins, redirects, substitutes or escapes
IN_DOUBLE = set("$`\\\n\r\0")          # between double quotes: what still substitutes or escapes
RULE = re.compile(r"Bash\((?P<pattern>.+)\)", re.DOTALL)


def plain(command: str) -> bool:
    """One command and nothing else: no character outside quotes that joins, redirects, substitutes or
    escapes, every quote closed."""
    quote = None
    for ch in command:
        if quote == "'":
            if ch == "'":
                quote = None
            elif ch in "\n\r\0":
                return False
        elif quote == '"':
            if ch == '"':
                quote = None
            elif ch in IN_DOUBLE:
                return False
        elif ch in "'\"":
            quote = ch
        elif ch in OUTSIDE:
            return False
    return quote is None


def _glob(pattern: str) -> re.Pattern:
    return re.compile("".join(".*" if part == "*" else re.escape(part) for part in re.split(r"(\*)", pattern)),
                      re.DOTALL)


def _patterns(rules, allow: bool) -> list:
    found = []
    for rule in rules:
        match = RULE.fullmatch(rule) if isinstance(rule, str) else None
        if match is None:
            continue
        pattern = match.group("pattern")
        if allow and pattern.startswith("*"):   # an allow rule that starts with any text is no shape
            continue
        found.append(_glob(pattern))
        if allow and pattern.endswith(" *"):   # `<words> *` lets `<words>` through alone too
            found.append(_glob(pattern[:-2]))
    return found


def rules(profile) -> tuple:
    """(allow patterns, deny patterns) of the Bash rules in the routine's settings file. ValueError when the
    file holds no permissions to read."""
    data = json.loads(Path(profile).read_text(encoding="utf-8"))
    perms = data.get("permissions") if isinstance(data, dict) else None
    if not (isinstance(perms, dict) and isinstance(perms.get("allow"), list) and isinstance(perms.get("deny", []), list)):
        raise ValueError("no permissions to read")
    return _patterns(perms["allow"], True), _patterns(perms.get("deny", []), False)


def check(profile, event) -> str | None:
    """None when the request is one plain command the profile's Bash rules allow and do not deny; else the
    reason it is refused."""
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return f"{WHY}: this request names no command"
    command = command.strip(" \t")
    if not command or not plain(command):
        return WHY
    try:
        allow, deny = rules(profile)
    except (OSError, ValueError):
        return f"{WHY}: the routine's settings could not be read"
    if any(p.fullmatch(command) for p in deny) or not any(p.fullmatch(command) for p in allow):
        return WHY
    return None


def task_check(event) -> str | None:
    """None when a Task (Agent) call starts the general-purpose agent (named so, or by no type at all) with a
    description and a prompt, and nothing but a model or a background run besides; else the reason it is refused."""
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("prompt"), str):
        return TASK_WHY
    if set(tool_input) - TASK_KEYS or tool_input.get("subagent_type", "general-purpose") != "general-purpose":
        return TASK_WHY
    return None


def granted(profile) -> tuple:
    """(the connector tools the routine's settings file allows by their exact names, the ones it denies)."""
    data = json.loads(Path(profile).read_text(encoding="utf-8"))
    perms = data.get("permissions") if isinstance(data, dict) else None
    if not (isinstance(perms, dict) and isinstance(perms.get("allow"), list) and isinstance(perms.get("deny", []), list)):
        raise ValueError("no permissions to read")
    exact = lambda rules: {r for r in rules if isinstance(r, str) and GRANTED.fullmatch(r)}   # noqa: E731
    return exact(perms["allow"]), exact(perms.get("deny", []))


def connector_check(profile, event) -> str | None:
    """None when the connector tool called is one the routine's settings file allows by its exact name and does
    not deny; else the reason it is refused."""
    name = event.get("tool_name") if isinstance(event, dict) else None
    if not (isinstance(name, str) and GRANTED.fullmatch(name)):
        return CONNECTOR_WHY
    try:
        allow, deny = granted(profile)
    except (OSError, ValueError):
        return f"{CONNECTOR_WHY}: the routine's settings could not be read"
    return None if name in allow and name not in deny else CONNECTOR_WHY


def _mode(argv: list) -> tuple:
    """(the reason a refusal gives, the check for this mode's request)."""
    if argv[:1] == ["--task"]:
        return TASK_WHY, task_check
    if argv[:1] == ["--connectors"]:
        return CONNECTOR_WHY, lambda event: connector_check(argv[1], event)
    return WHY, lambda event: check(argv[0], event)


def main(argv: list, raw: bytes) -> int:
    said = WHY
    try:
        said, checked = _mode(argv)
        if len(raw) > MAX_RAW:
            raise ValueError("a request larger than any routine's")
        reason = checked(json.loads(raw.decode("utf-8")))
    except BaseException:   # noqa: BLE001 - whatever went wrong, the call does not go
        reason = f"{said}: this request could not be read"
    if reason:
        print(reason, file=sys.stderr)
        return BLOCK
    return 0


def arm(said: str) -> threading.Timer:
    """The guard's own time limit: after LIMIT_SECONDS a daemon timer says why and ends the process with exit 2,
    whatever it is doing."""
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
    makes that a refusal (`routine.bash_guard_command`)."""
    faulthandler.dump_traceback_later(seconds, exit=True, file=sys.stderr)


def run() -> int:
    """The hook: the timer and the hard limit first, then stdin (at most MAX_RAW and one byte), then `main`."""
    argv = sys.argv[1:]
    arm({"--task": TASK_WHY, "--connectors": CONNECTOR_WHY}.get(argv[0] if argv else "", WHY))
    bound(LIMIT_SECONDS + 2)
    return main(argv, sys.stdin.buffer.read(MAX_RAW + 1))


if __name__ == "__main__":
    sys.exit(run())
