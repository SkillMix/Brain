"""bash_guard: the routines' Bash guard (Ruling R30). A PreToolUse hook on the Bash tool in each routine's
profile (`routine.routine_settings`): it lets a command through only when it is one plain command of a shape
the profile's own allow list names (`Bash(<python> [<root>/]_meta/tools/<tool>.py *)`, the read-only gh and git
commands) and none its deny list names, and refuses (exit 2) everything else. The person's own settings may
allow more (`Bash(wget *)`, a bare `Bash`); a routine reads records anyone can write, so this hook is the check
that does not depend on them.

    python -I bash_guard.py <the routine's settings file>     # the hook's JSON on stdin; exit 0 allows, exit 2 blocks

One plain command: nothing after `;`, `&`, `&&`, `||` or `|`, no redirection, no subshell, no command or
variable substitution, no backslash, no line break. Text between single quotes is literal; between double
quotes `$`, a backtick and a backslash are refused too. A rule `Bash(<words> *)` lets through `<words>` alone
or followed by a space and anything plain; a `*` elsewhere stands for any text.

It runs as `python -I`, so it can import nothing but the standard library, and it fails closed: a request it
cannot read, a settings file it cannot read, or any fault of its own is a refusal. It says why on stderr,
without repeating the command.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BLOCK = 2
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


def main(argv: list, raw: bytes) -> int:
    try:
        reason = check(argv[0], json.loads(raw.decode("utf-8")))
    except BaseException:   # noqa: BLE001 - whatever went wrong, the command does not run
        reason = f"{WHY}: this request could not be read"
    if reason:
        print(reason, file=sys.stderr)
        return BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin.buffer.read()))
