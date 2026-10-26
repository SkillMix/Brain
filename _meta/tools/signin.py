"""signin: the two sign-ins only the person can give, started together in the first minute while
they are still there: GitHub's device flow (`gh auth login --web`) and the Claude command line's
browser sign-in (`claude auth login --claudeai`). Nothing after them waits on the person.

Each sign-in is started detached, so it keeps going after the tool that started it has exited,
with its output in a log of its own under `~/.spaces/logs/`: a pipe closed when this process
ends would stop gh at its first message after the approval, before it stored the token. The tool
reads that log only until it has what the person needs (gh's one-time code, claude's fallback
link), with a time limit, and leaves the process running.

Whether a sign-in is done is read from the commands themselves: `gh auth status`, and
`claude auth status --json` by its `loggedIn` and `authMethod` (the exit codes differ by system
and decide nothing). Both claude commands run in the environment a routine has
(`routine.clean_env`): a routine bills the person's own sign-in, so an API key or token in
the session's variables must neither count as one nor be inherited by the login. start.py's
`signins` phase calls this; the facts are in docs/design/cli-facts.md.

    python start.py signins [--wait] [--json]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import doctor
import routine

GH_CODE = r"one-time code: (?P<code>[A-Z0-9]{4}-[A-Z0-9]{4})"      # on stderr
GH_LINK = r"(?P<link>https://\S+/login/device)"                      # https://github.com/login/device
# "claude" stands for the path find_claude() returns.
CLAUDE_LOGIN = ["claude", "auth", "login", "--claudeai"]            # opens the browser itself
CLAUDE_STATUS = ["claude", "auth", "status", "--json"]              # read loggedIn, not the exit code
CLAUDE_LINK = r"If the browser didn't open, visit: (?:\x1b\]8;[^;]*;)?(?P<link>https://[^\s\x07\x1b]+)"
# authMethod -> signin.claude_state. Seen live: "claude.ai" and "none" only.
# "oauth_token" is a long-lived subscription token (CLAUDE_CODE_OAUTH_TOKEN, from
# `claude setup-token`, "requires Claude subscription"), so it bills the plan like an account;
# "third_party" bills a cloud provider. The status is read in a routine's environment
# (routine.clean_env), where a token in the session's variables is gone: "oauth_token" then
# comes only from a setup-token the person's own configuration holds.
CLAUDE_STATES = {"claude.ai": "account", "none": "signed-out", "api_key": "api-key",
                 "api_key_helper": "api-key", "oauth_token": "account", "third_party": "api-key"}

# `--web` with the input closed prints the code and the link and does not open a browser; the
# https protocol is what the doctor's own instruction for the person says too.
GITHUB_LOGIN = ["gh", "auth", "login", "--web", "--git-protocol", "https", "--hostname", "github.com"]
DEVICE_LINK = "https://github.com/login/device"
GITHUB_WAIT_S = 30    # gh prints its code within seconds; one that prints nothing is stopped
CLAUDE_WAIT_S = 10    # the link line comes with the browser; without it the browser may still be open
GRACE_S = 2           # after the code, how long to wait for the link line that follows it
POLL_S = 3            # how often `wait` reads the commands
LOG_POLL_S = 0.1
# A device code lasts a quarter of an hour. A flow older than that is over (or its process id
# belongs to another process by now): a re-run starts a new one.
FLOW_MINUTES = 15


class SigninError(RuntimeError):
    """A sign-in that did not start, with what its command printed."""


def _detach(m: doctor.Machine) -> dict:
    """Popen arguments that let the child outlive this process: Windows' detached process, a
    session of its own elsewhere."""
    if m.system == "Windows":
        return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0x8)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def _launch(m: doctor.Machine, spawn, name: str, argv: list, env=None):
    """The command, started detached and with no input, its output (stderr with stdout) in a new
    log under ~/.spaces/logs. Returns (process, log path)."""
    logs = Path(m.home) / ".spaces" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=f"{name}-", suffix=".log", dir=logs)   # private to the person
    os.close(fd)
    with open(path, "wb") as log:
        proc = spawn(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, **_detach(m))
    return proc, Path(path)


def _follow(path: Path, running):
    """The log's complete lines as they arrive; an empty string while there is nothing new, so
    the reader keeps its own clock. Ends once the process has stopped and everything is read."""
    pending = ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        while True:
            alive = running()   # asked first: what it wrote just before it ended is still read
            chunk = fh.readline()
            pending += chunk
            if pending.endswith("\n"):
                yield pending
                pending = ""
            elif not chunk:
                if not alive:
                    return
                yield ""
                time.sleep(LOG_POLL_S)


def _output(proc, log: Path):
    """What the command printed, as text pieces: its own stream when the caller gave it one
    (a stand-in for a process), else the log it writes."""
    stream = getattr(proc, "stderr", None)
    stream = stream if stream is not None else getattr(proc, "stdout", None)
    return stream if stream is not None else _follow(log, lambda: proc.poll() is None)


def _read_until(source, patterns, timeout_s: float) -> str:
    """Read until every pattern matches, the source ends or the time is up; once the first
    pattern has matched, only GRACE_S more for the others."""
    text, end, graced = "", time.monotonic() + timeout_s, False
    for piece in source:
        text += piece
        hits = [re.search(p, text) for p in patterns]
        if all(hits):
            break
        if hits[0] and not graced:
            end, graced = min(end, time.monotonic() + GRACE_S), True
        if time.monotonic() >= end:
            break
    return text


def github_start(m: doctor.Machine, spawn=subprocess.Popen, timeout_s: float = GITHUB_WAIT_S) -> dict:
    """Start GitHub's device flow and read its one-time code and link, which the person types
    at the link. The flow stays running and polls GitHub; `github_signed_in` says when it is
    approved. Raises SigninError when gh gives no code in time (and stops it)."""
    env = {k: v for k, v in os.environ.items() if k.upper() != "GH_DEBUG"}   # its traffic would go to the log
    proc, log = _launch(m, spawn, "gh-login", GITHUB_LOGIN, env=dict(env, GH_PROMPT_DISABLED="1"))
    text = _read_until(_output(proc, log), (GH_CODE, GH_LINK), timeout_s)
    code, link = re.search(GH_CODE, text), re.search(GH_LINK, text)
    if not code:
        proc.kill()
        said = " ".join(text.split())[:200]
        raise SigninError("GitHub did not give a sign-in code" + (f" (it said: {said})" if said else ""))
    return {"code": code["code"], "link": link["link"] if link else DEVICE_LINK, "pid": proc.pid}


def github_signed_in(m: doctor.Machine) -> bool:
    return m.ok("gh", "auth", "status")


def _claude_status(m: doctor.Machine) -> dict | None:
    """`claude auth status --json` as a dict; None when claude cannot be run or answers no JSON.
    The output is read whatever the exit code is."""
    exe = routine.find_claude()
    if exe is None:
        return None
    res = m.run([exe, *CLAUDE_STATUS[1:]], env=routine.clean_env(os.environ))
    if res.returncode == 127:   # the command could not be started at all
        return None
    try:
        data = json.loads(res.stdout or "")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def claude_state(m: doctor.Machine) -> str:
    """`missing` (no claude command), `signed-out`, `account` (a claude.ai account, or a
    long-lived subscription token kept in the person's configuration) or `api-key` (signed in some
    other way: a routine has no account then). Read as a routine sees it: without the session's
    API key, token and provider variables (`routine.clean_env`)."""
    data = _claude_status(m)
    if data is None:
        return "missing"
    if not data.get("loggedIn"):
        return "signed-out"
    return CLAUDE_STATES.get(data.get("authMethod"), "api-key")   # a method nobody mapped is no known account


def claude_identity(m: doctor.Machine) -> dict:
    """Whose account the command line is signed in to: `email` and `org` (the organisation's
    name), each only when the status gives it. The desktop app can be signed in to another one."""
    data = _claude_status(m) or {}
    return {key: data[field] for key, field in (("email", "email"), ("org", "orgName")) if data.get(field)}


def claude_start(m: doctor.Machine, spawn=subprocess.Popen, timeout_s: float = CLAUDE_WAIT_S) -> dict:
    """Start the Claude command line's sign-in. It opens the browser itself; its fallback link is
    read for the person when the browser did not open (None when it printed none in time).
    Raises SigninError when the command ended with a failure instead."""
    argv = [routine.find_claude() or "claude", *CLAUDE_LOGIN[1:]]
    proc, log = _launch(m, spawn, "claude-login", argv, env=routine.clean_env(os.environ))
    text = _read_until(_output(proc, log), (CLAUDE_LINK,), timeout_s)
    link = re.search(CLAUDE_LINK, text)
    if not link and proc.poll() not in (None, 0):
        said = " ".join(text.split())[:200]
        raise SigninError("Claude's sign-in ended at once" + (f" (it said: {said})" if said else ""))
    return {"link": link["link"] if link else None, "pid": proc.pid}


def flow_alive(pid, started, now=None) -> bool:
    """Whether a sign-in an earlier run started is still going: its process runs and is young
    enough to be that process."""
    now = time.time() if now is None else now
    return (isinstance(pid, int) and isinstance(started, (int, float)) and 0 <= now - started < FLOW_MINUTES * 60
            and routine.pid_alive(pid))


def wait(m: doctor.Machine, timeout_s: int = 600, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Read both sign-ins every POLL_S seconds until both are done or `timeout_s` has passed.
    Returns the last reading, `{"github": bool, "claude": state}`. It reads the commands, never
    the journal. A sign-in that has read back is not asked about again."""
    end = clock() + timeout_s
    github, claude = False, "signed-out"
    while True:
        github = github or github_signed_in(m)
        claude = claude if claude == "account" else claude_state(m)
        if (github and claude == "account") or clock() >= end:
            return {"github": github, "claude": claude}
        sleep(POLL_S)
