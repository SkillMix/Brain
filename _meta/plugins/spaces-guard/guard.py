"""spaces-guard: the computer's routine hooks, carried into a cloud run (spec 4.7, layer 1). A cloud session with
several repositories reads none of their hooks or permission rules, so the scheduled task enables this plugin, and
its hooks (`hooks/hooks.json`) run this guard on Bash, on Task, on every connector tool and on every send tool:

    python3 -I guard.py bash|task|connector|send     # the hook's JSON on stdin; exit 0 allows, exit 2 blocks

Outside a cloud run (`CLAUDE_CODE_REMOTE` is not `true`) it lets everything through, so a session on a computer
that enabled the plugin is unchanged. In a cloud run it reads the state `cloud.py begin` writes
(`~/.spaces/routines/cloud.json`: `workspace`, the clone whose guards it runs; `profile`, the routine profile
`begin` wrote with `routine.routine_settings`; `person`, the address the run's mail may go to, the grant's own;
`chain`):
- Before `begin` has written it, the one call that goes through is `begin` itself, one plain shell command (BEGIN:
  `python3 /home/user/<repo>/_meta/tools/cloud.py begin /home/user/<repo> --chain sweep|daily`, then `--proof` or
  not, and after `--proof`, last, `--grant` or not, with the task's grant as one base64url word of at most 8192
  letters, digits, `-` and `_` (`cloud.grant_word`, `cloud.GRANT_MAX`); a repository's name in letters, digits,
  `.`, `_` and `-`, the same name twice: a clone's own cloud.py on that clone). Everything else is refused. Which
  clone is the workspace the guard cannot tell (a task's trigger takes no environment, and nothing else a hook is
  given is proven to name it), so the task's deny rules keep the shell out of every codebase it attaches
  (`cloud.disallowed`), and begin refuses a workspace that is not its own script's clone.
- After, each call goes to the workspace's own guard, as the hooks the computer's profile carries run it: Bash to
  `bash_guard.py <profile>`, Task to `bash_guard.py --task`, a connector tool to `bash_guard.py --connectors
  <profile>`, and a send tool the profile guards to `send_guard.py <person>`. A mode the profile carries no hook
  for is refused, as the computer's profile then denies the tool outright (no shell, no agent, no connector
  tool). A tool the send hook's pattern names (SEND_MATCHER) that the profile does not guard as a send, a read
  tool of a server whose name holds "send", is left to the connector check, which runs on it too.

It keeps no state of its own and writes nothing. It runs as `python3 -I`, so it imports nothing but the standard
library, and it fails closed: a request it cannot read (an event that is not a mapping, or names no tool), in
every mode and before `begin` too, a state or a profile it cannot read, or a workspace guard that is missing,
crashes or takes longer than DELEGATE_SECONDS, is a refusal. It says why on stderr, without
repeating the command or the tool. It bounds its own time as the workspace guards do (LIMIT_SECONDS, then
faulthandler), well inside the hook's timeout. Each hook command ends `|| test "$CLAUDE_CODE_REMOTE" != true ||
exit 2`: in a cloud run a guard that cannot start or crashes blocks the call, and on a computer, where the guard
lets everything through anyway, an interpreter that is not there changes nothing.
"""
from __future__ import annotations

import faulthandler
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

BLOCK = 2
MODES = ("bash", "task", "connector", "send")
LIMIT_SECONDS = 25      # the guard's own time; the hook's timeout (hooks.json) is well above it
DELEGATE_SECONDS = 20   # the most a workspace guard is given; each one ends itself after its own 10 seconds
MAX_RAW = 1024 * 1024   # the most of stdin read: a hook event past it is refused unread
STATE = Path(".spaces") / "routines" / "cloud.json"   # under the home folder; written by `cloud.py begin`
# The matchers the computer's profile writes its hooks under (`routine.TASK_MATCHER`, `routine.CONNECTOR_MATCHER`;
# a send tool's hook is under its own name), and the plugin's send hook: every connector tool whose name holds
# "send" in any case, as the grant reads a send tool (`routine._sends`).
BASH_MATCHER, TASK_MATCHER, CONNECTOR_MATCHER = "Bash", "Task|Agent", "mcp__.*"
SEND_MATCHER = "mcp__.*[Ss][Ee][Nn][Dd].*"
NAME = r"[A-Za-z0-9_-][A-Za-z0-9._-]*"   # a repository's name: no `/`, nothing a shell reads, never `.` or `..`
GRANT = r"[A-Za-z0-9_-]{1,8192}"   # a proof's grant: base64url, no padding, nothing a shell reads (cloud.GRANT_MAX)
BEGIN = re.compile(rf"python3 /home/user/(?P<clone>{NAME})/_meta/tools/cloud\.py begin /home/user/(?P=clone) "
                   rf"--chain (?:sweep|daily)(?: --proof(?: --grant {GRANT})?)?")
WHY = "a cloud routine runs only what the guard profile of its run allows"
BEFORE = "a cloud run starts with cloud.py begin, and nothing runs before it"
NONE = {"bash": "no shell", "task": "no agent", "connector": "no connector tool"}


def remote(env) -> bool:
    """A cloud run: `CLAUDE_CODE_REMOTE` is `true`."""
    return env.get("CLAUDE_CODE_REMOTE") == "true"


def state_path(env) -> Path:
    """Where `cloud.py begin` writes the run's state: the home folder `HOME` names, else this user's."""
    home = env.get("HOME")
    return (Path(home) if home else Path.home()) / STATE


def read_state(path: Path) -> dict:
    """The run's state. ValueError when it names no workspace and no profile."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not (isinstance(data, dict) and all(isinstance(data.get(k), str) and data.get(k) for k in ("workspace", "profile"))):
        raise ValueError("a state without a workspace and a profile")
    return data


def hooked(profile: Path) -> set:
    """The matchers the routine profile carries a PreToolUse hook under. ValueError when it cannot be read."""
    data = json.loads(Path(profile).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("a profile that is not a mapping")
    hooks = data.get("hooks", {})
    entries = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else None
    if not isinstance(entries, list):
        raise ValueError("hooks that cannot be read")
    return {e["matcher"] for e in entries if isinstance(e, dict) and isinstance(e.get("matcher"), str)}


def begins(event) -> bool:
    """The request is `cloud.py begin`, one plain command."""
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return isinstance(command, str) and BEGIN.fullmatch(command.strip(" \t")) is not None


def delegate(script: Path, args: list, raw: bytes, seconds: float = DELEGATE_SECONDS) -> str | None:
    """None when the workspace's guard (`script`, run as the computer's hooks run it: this interpreter, `-I`, the
    hook's JSON on stdin) lets the call through; else the reason it gave, or why it could not answer."""
    if not Path(script).is_file():
        return f"{WHY}: the workspace's guard is not there, so the call is refused"
    try:
        got = subprocess.run([sys.executable, "-I", str(script), *args], input=raw, capture_output=True,
                             timeout=seconds)
    except subprocess.TimeoutExpired:
        return f"{WHY}: the workspace's guard took too long, so the call is refused"
    except OSError:
        return f"{WHY}: the workspace's guard could not start, so the call is refused"
    if got.returncode == 0:
        return None
    said = got.stderr.decode("utf-8", errors="replace").strip().splitlines()
    if got.returncode == BLOCK and said:
        return said[-1]
    return f"{WHY}: the workspace's guard failed, so the call is refused"


def check(mode: str, event, raw: bytes, env) -> str | None:
    """None when the call goes through; else the reason it is refused. OSError or ValueError when the run's state
    or its profile cannot be read (the caller refuses)."""
    if not remote(env):
        return None
    name = event.get("tool_name") if isinstance(event, dict) else None
    if not (isinstance(name, str) and name):   # the hook always names the tool: a request it cannot read
        return f"{WHY}: this request names no tool"
    path = state_path(env)
    if not path.exists():
        return None if mode == "bash" and begins(event) else BEFORE
    state = read_state(path)
    profile, tools = Path(state["profile"]), Path(state["workspace"]) / "_meta" / "tools"
    matchers = hooked(profile)
    if mode == "send":
        if not (name.startswith("mcp__") and name in matchers):
            return None   # not a send the profile guards: the connector check holds it to the grant
        person = state.get("person")
        if not (isinstance(person, str) and person):
            raise ValueError("a state without the person's address")
        return delegate(tools / "send_guard.py", [person], raw)
    matcher, args = {"bash": (BASH_MATCHER, [str(profile)]), "task": (TASK_MATCHER, ["--task"]),
                     "connector": (CONNECTOR_MATCHER, ["--connectors", str(profile)])}[mode]
    if matcher not in matchers:
        return f"{WHY}: its profile gives it {NONE[mode]}"
    return delegate(tools / "bash_guard.py", args, raw)


def main(argv: list, raw: bytes, env=None) -> int:
    env = os.environ if env is None else env
    try:
        if not remote(env):
            return 0
        mode = argv[0] if argv else ""
        if mode not in MODES:
            raise ValueError("no such mode")
        if len(raw) > MAX_RAW:
            raise ValueError("a request larger than any routine's")
        reason = check(mode, json.loads(raw.decode("utf-8")), raw, env)
    except BaseException:   # noqa: BLE001 - whatever went wrong, the call does not go
        reason = f"{WHY}: this request or the run's state could not be read"
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
    """The limit no call outlasts: faulthandler's own thread ends the process with exit 1 after `seconds`, and the
    hook command makes that a refusal in a cloud run."""
    faulthandler.dump_traceback_later(seconds, exit=True, file=sys.stderr)


def run() -> int:
    """The hook: the timer and the hard limit first, then stdin (at most MAX_RAW and one byte), then `main`."""
    arm(WHY)
    bound(LIMIT_SECONDS + 2)
    return main(sys.argv[1:], sys.stdin.buffer.read(MAX_RAW + 1))


if __name__ == "__main__":
    sys.exit(run())
