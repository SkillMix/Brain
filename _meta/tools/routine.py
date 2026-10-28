"""routine: run one of the person's routines headless through Claude Code's command line, so the
routines outlive the session that set them up. The OS scheduler starts it at the routine's times
(`onboard.py <workspace> --os-routines`).

The prompt is the rendered routine file without its frontmatter. Claude runs in the workspace
with its permission profile plus the routine profile this tool writes for each run
(`--settings`, see `routine_settings`); nobody is there to answer a permission prompt, so anything
that would ask is refused and the routine goes on without it. One computer runs a workspace's
routines, through one scheduler (`HOST`, `gate`): on any other a run leaves one log line and goes,
and while the person has paused them a run writes `paused` and goes, before the lock or the history.
One run at a time per workspace (`take_lock`): a second one writes `stopped` and leaves. Each run
appends its own outcome line, with the tokens it really used, to `_meta/state/runs.jsonl` (`outcome`,
read from what the command line said, never from its exit code), and a summary to
`~/.spaces/logs/<routine>-<space>.log`. A run
that found Claude signed out, or an app that needs a new sign-in, leaves one item on Needs you.

`probe_tools` asks the command line itself what it would let a run call (its `init` event), since
the tool names a headless run sees are not the ones the app that set it up sees; `reader.py` runs
the read-only runs that open the content a routine did not write.

A routine reads swept mail, chat and transcripts, which anyone can write. The routine profile
keeps such a record from turning into code on this machine: no edit to the machinery, the
instructions or git (`PROTECTED_EDITS`), only the built-in tools a routine needs (`ROUTINE_BUILTINS`,
`--tools`), and only the tools the routines run (`ROUTINE_TOOLS`), by their exact paths, behind
`bash_guard.py`, which lets one plain command of those shapes through and nothing the person's own
settings allow besides; every other tool script in a space is refused by name. Of the connectors it
may call only what the grant names (`grant`): the read tools of the apps read as work, a tracker's
writes, and the send tools of the person's own mailbox behind `send_guard.py`, which lets mail go to
the person alone, once the probe proved it in the shell Claude Code will run it with. Every other
connector tool the probe has ever seen is denied. The profile keeps every hook on
(`"disableAllHooks": false`).

    python routine.py <workspace>/_meta/state/routines/<routine>.md
    python routine.py --probe <workspace>    # what a scheduled run sees, and three checks: ~/.spaces/probe.json
    python routine.py --locked <workspace>   # the run that holds the workspace's lock, or nothing
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402
import spaceyaml  # noqa: E402

# claude -p refuses anything that would ask (section 1); acceptEdits still lets a routine write
# in its working folders.
REFUSE = ["--permission-mode", "acceptEdits", "--permission-prompts", "none"]
# One JSON object per line: hook events, then system/init (tools, mcp_servers), the messages,
# rate_limit_event (plan windows, extra usage), and last the result (usage, is_error, result).
# Find init by type/subtype, the result as the last line whose type is "result".
JSON_OUT = ["--output-format", "stream-json", "--verbose"]

LIMIT_PATTERNS = (
    # "You've hit your session limit · resets 11:20pm (America/New_York)",
    # "You've hit your weekly limit · resets Oct 27, 1pm (America/New_York)"
    r"You[\u2019']ve hit your (?:(?P<limit>session|weekly|Opus|Sonnet|Fable|usage) )?limit \u00b7 resets "
    r"(?P<resets>(?:[A-Z][a-z]{2} \d{1,2}, (?:\d{4}, )?)?\d{1,2}(?::\d{2})?(?:[ap]m)?)(?: \((?P<tz>[^)]+)\))?",
    # no reset time: "You've hit your session limit". This one has only the "limit" group: read
    # the reset with m.groupdict().get("resets"), which is None here.
    r"You[\u2019']ve hit your (?:(?P<limit>session|weekly|Opus|Sonnet|Fable|usage) )?limit\b",
)
OVERAGE_PATTERNS = (
    # f$o in the source: "You're now using usage credits", "You're now using your usage
    # allocation", "You're now using extra usage" ...
    r"You[\u2019']re now using (?:usage credits|your usage allocation|extra usage)",
    # ... "Now using your usage allocation", "Now using usage credits", "Now using extra usage"
    r"\bNow using (?:usage credits|your usage allocation|extra usage)",
    r"You[\u2019']re out of usage credits",
    r"Your org is out of usage",
    r"You[\u2019']ve hit your (?:monthly spend limit|(?:channel|org)[\u2019']s monthly (?:spend|usage) limit"
    r"|individual (?:spend|usage) limit|team[\u2019']s shared budget|usage credit limit)",
    r"You[\u2019']ve reached your Fable limit|requires usage credits",
    # Ruling R8: extra usage that ran out, or a seat without any, stops a run as overage too
    r"You[\u2019']re out of extra usage",
    r"Your seat type doesn[\u2019']t include extra usage",
)
AUTH_PATTERNS = (
    # "Not logged in \u00b7 Please run /login" (observed), "Not logged in \u00b7 Run /login"
    r"Not logged in \u00b7 (?:Please run|Run) /login",
    # "OAuth token revoked \u00b7 Please run /login", "Login expired \u00b7 Please run /login",
    # "Login expired \u00b7 Run /login to sign in again, or re-authenticate your Anthropic profile"
    r"(?:OAuth token revoked|Login expired) \u00b7 (?:Please run|Run) /login",
    # "Authentication required \u00b7 Sign in again to continue"
    r"Authentication required \u00b7 Sign in again",
    # "Invalid API key \u00b7 Fix external API key", "Invalid auth token \u00b7 Fix external auth token"
    r"Invalid (?:API key|auth token) \u00b7 Fix external",
    # "Your organization has disabled API key authentication \u00b7 ...",
    # "Your organization has disabled Claude subscription access for Claude Code \u00b7 ..."
    r"Your organization has disabled (?:API key authentication|Claude subscription access)",
    # "Session expired. Please run /login to sign in again.",
    # "Your session has expired. Please run /login to sign in again."
    r"[Ss]ession (?:has )?expired\. Please run /login",
)
# Only the first text was seen live; the rest are UNVERIFIED-live, quoted from the 2.1.303
# binary in section 5.

# PROPOSAL, not observed: the same outcomes in structured form (stream-json), preferred over the
# text when present: the assistant event's "error" and the rate_limit_event. billing_error is
# labelled "usage limit reached - check plan" in the CLI's own source.
ERROR_KINDS = {"authentication_failed": "auth", "oauth_org_not_allowed": "auth",
               "rate_limit": "limit", "billing_error": "limit"}
# rate_limit_info.isUsingOverage is True -> "overage"; status "rejected" -> "limit", with
# rate_limit_info.resetsAt (Unix seconds) as the reset time.

# Connector sign-in (section 7); the init event's mcp_servers status "needs-auth" comes first.
CONNECTOR_PATTERNS = (
    r'MCP server "(?P<server>[^"]+)" needs you to sign in again',
    r'MCP server "(?P<server>[^"]+)" needs to be connected in claude\.ai',
    r'The MCP server "(?P<server>[^"]+)" needs you to sign in; run /mcp to authenticate',
    r'MCP server "(?P<server>[^"]+)" needs (?:authentication|additional permissions)',
)

# The tools the routine prompts and the skills they follow run (`_meta/templates/routines/`,
# sweep, route, classify, distill, check, status, release, request, tracker, owner-email,
# morning-view, outbox-reconcile, sync), with the read-only one every session may search with
# (search.py). Never sign, reply, set-up or template sync: those are the person's, or run inside
# chain.py. Never needs_you.py either: it is the person's list, and `--once-a-day` stamps the day,
# so a routine that ran it would hide the list from the person's first session.
ROUTINE_TOOLS = ("brief.py", "chain.py", "check.py", "classify.py", "distill.py", "landing.py",
                 "librarian.py", "morning_view.py", "outbox_reconcile.py",
                 "owner_email.py", "registry.py", "release.py", "request.py", "route.py", "search.py",
                 "sign_questions.py", "status.py", "sweep.py", "sync.py", "tests_run.py", "tracker.py")
# What no routine edits, in any space on this machine: the code that runs (tools, migrations,
# tests and their configuration), the instructions every session loads, git, the rules, the
# routine prompts and the apps' verdicts the grant is read from. A path rule for Edit covers Write
# and NotebookEdit too; Claude Code reads no path rule written for Write.
PROTECTED_EDITS = ("_meta/tools/**", "_meta/migrations/**", "_meta/tests/**", "_meta/templates/**",
                   "_meta/state/routines/**", "_meta/state/private/apps.yaml", ".claude/**", ".github/**",
                   ".git/**", ".gitmodules", ".mcp.json", "rules.md", "space.yaml", "CLAUDE.md", "AGENTS.md",
                   "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
PROTECTED_EDITS += ("_meta/state/backfill.yaml",)   # the history's pacing and the paid hold: written only through backfill.py
PROTECTED_EDITS += ("_meta/state/routine-host.yaml",)   # where the routines run, and their pause: only a session moves them
PROTECTED_EDITS += ("_meta/state/onboard.yaml",)   # the OS tasks onboard registered: no routine names one to remove
# Rooted at each space: a folder name a subject could also have.
PROTECTED_IN_ROOT = ("identity/**",)
PROTECTED_EDITS += ("_meta/state/routine-limits.jsonl",)   # each run's windows and tokens: written by this process alone
PROTECTED_EDITS += ("CLAUDE.local.md",)   # loaded beside CLAUDE.md at any depth, in a folder outside git too (dry run A)
PROTECTED_EDITS += (".gitattributes",)   # the merge rules the sync script lands every record by
PROTECTED_EDITS += (".gitignore",)   # what the sync script lands at all
PROTECTED_EDITS += ("_meta/plugins/**", ".claude-plugin/**")   # the guard a cloud run carries, and what offers it
# Setup's history reading runs inside the sweep chain (step 0); its own line, away from the list above.
ROUTINE_TOOLS += ("backfill.py",)
READ_ONLY =("Bash(gh pr view *)", "Bash(gh pr list *)", "Bash(git status *)", "Bash(git log *)",
             "Bash(git diff *)", "Bash(git show *)")
# The built-in tools a routine has (`--tools`, Ruling R30): the shell its tools run in, the files, and
# ToolSearch, which loads a connector's tools. Web, PowerShell, agents and schedules are left out; the
# profile denies them by name too (`reader.DENIED`), so one a later version adds under such a name stays out.
# `Task` is there for the second agent that checks route and distill decisions; a subagent runs under
# the same profile and hooks.
ROUTINE_BUILTINS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep", "ToolSearch", "Task")


# What a routine does not inherit from the session that set it up: another provider, another
# endpoint, an API key or token. A routine bills the person's claude.ai sign-in, so the sign-in
# that is checked, and the one that is started, is the one it will find without these.
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_OAUTH_TOKEN")
GIT_BASH = "CLAUDE_CODE_GIT_BASH_PATH"


# The desktop session's own settings for the runs it starts (its effort, entry point, child-session, messaging and SDK
# variables): a setup run, a reader, the probe or a routine run without them is the run cli-facts describes (R45, I-3).
SESSION_ENV = ("CLAUDECODE", "CLAUDE_EFFORT")
SESSION_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")
KEPT_ENV = (GIT_BASH, "CLAUDE_CONFIG_DIR")   # what every caller needs: the Git Bash Claude Code runs hooks in, the config
# the client certificate a company network may require (mTLS): without it no run reaches the API at all (NB-4)
KEPT_ENV += ("CLAUDE_CODE_CLIENT_CERT", "CLAUDE_CODE_CLIENT_KEY", "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE")


def clean_env(env: dict) -> dict:
    """A copy of the environment as a routine sees it: without STRIPPED_ENV (keys, tokens, another provider) and without
    the session's own variables (SESSION_ENV, and every name starting with SESSION_PREFIXES), but with KEPT_ENV.
    Every run setup and the routines start goes through it: readers, the probe, the sign-ins and `routine_env`."""
    def dropped(key: str) -> bool:
        name = key.upper()   # Windows ignores case
        if name in KEPT_ENV:
            return False
        return name in STRIPPED_ENV or name in SESSION_ENV or name.startswith(SESSION_PREFIXES)
    return {key: value for key, value in env.items() if not dropped(key)}


def routine_env(env: dict, shell: str = "", windows: bool | None = None) -> dict:
    """The environment a routine's claude runs in: `clean_env`, and on Windows `CLAUDE_CODE_GIT_BASH_PATH` set to
    `shell`, the Git Bash the probe proved the send guard in (`grant`'s `shell`), so Claude Code runs the hooks
    in that one and no other."""
    env = clean_env(env)
    windows = os.name == "nt" if windows is None else windows
    if windows and shell:
        env = {key: value for key, value in env.items() if key.upper() != GIT_BASH}
        env[GIT_BASH] = shell
    return env


class ProbeError(RuntimeError):
    """The probe run did not report what it sees (no init event, or it ran out of time). Never
    an empty tool list: that would read as "no apps connected"."""


PROBE_PROMPT = "Reply OK."
PROBE_SECONDS = 180


def probe_path() -> Path:
    """Where the probe's result is kept: the tools the command line saw when it last looked."""
    return Path.home() / ".spaces" / "probe.json"


def probe_tools(claude: str, cwd: Path, extra_dirs=(), run=subprocess.run) -> dict:
    """What `claude -p` itself sees, read from the `init` event of a one-turn run started as a
    routine starts (REFUSE, the environment without a key or token): `tools` (connectors
    included, only the connected ones), `mcp_servers` (name and status of each), `cwd`, `model`
    (the probe's own: the cheap one) and `auth` (`apiKeySource`: "none" for a claude.ai sign-in,
    else what took over). `extra_dirs` are the folders a routine adds (`--add-dir`). Raises
    ProbeError when the run reports no init."""
    argv = [claude, "-p", *REFUSE, *JSON_OUT, "--tools", *ROUTINE_BUILTINS]   # what a run has (F-8): its init lists those
    for folder in extra_dirs:
        argv += ["--add-dir", str(folder)]
    argv += ["--model", "haiku", "--max-turns", "1", "--no-session-persistence"]
    try:
        res = run(argv, input=PROBE_PROMPT, cwd=str(cwd), env=clean_env(os.environ), capture_output=True,
                  text=True, encoding="utf-8", errors="replace", timeout=PROBE_SECONDS)
    except subprocess.TimeoutExpired:
        raise ProbeError(f"claude did not report its tools within {PROBE_SECONDS // 60} minutes") from None
    except OSError as e:
        raise ProbeError(f"claude did not start: {e.strerror or e}") from None
    for line in (res.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue   # a hook's plain output, or a line that is not JSON
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return {"tools": list(event.get("tools") or []),
                    "mcp_servers": [{"name": s.get("name", ""), "status": s.get("status", "")}
                                    for s in event.get("mcp_servers") or []],
                    "cwd": event.get("cwd", ""), "model": event.get("model", ""),
                    "auth": event.get("apiKeySource", "")}
    said = " ".join(((res.stdout or "") + (res.stderr or "")).split())[:200]
    raise ProbeError(f"claude printed no init event (exit {res.returncode}): {said}")


def find_claude() -> str | None:
    """Claude Code's command: on PATH, else where the doctor installed it, else where its own
    installer puts it."""
    found = shutil.which("claude")
    if found:
        return found
    for folder in (doctor.tools_home() / "claude", Path.home() / ".local" / "bin", Path.home() / ".claude" / "local"):
        for name in ("claude", "claude.exe"):   # a scheduled task may start without the user's PATH
            if (folder / name).exists():
                return str(folder / name)
    return None


def pid_alive(pid: int) -> bool:
    """Whether a process with this id is running now: a lock whose process is gone is a crashed
    run's, a closed session's. Windows asks for the exit code (a finished process whose handle
    someone still holds is still openable); elsewhere a null signal tells."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        if pid > 0xFFFFFFFF:   # a process id is a 32-bit number there; ctypes would cut this one
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5   # access denied: it runs, as another user's
        try:
            code = wintypes.DWORD()
            asked = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(asked) and code.value == 259   # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def process_started(pid: int) -> str | None:
    """When the process with this id started, as this system says it (Windows: its creation time from
    GetProcessTimes; Linux: field 22 of /proc/<pid>/stat; elsewhere `ps -o lstart=`, in UTC and the C locale so
    every caller reads it alike), or None when no process has the id or the system does not say. A lock keeps it
    beside the pid: the same id with another start time is another process (Ruling R45, I-8)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        if os.name == "nt":
            return _started_windows(pid)
        stat = Path(f"/proc/{pid}/stat")
        if Path("/proc/self/stat").exists():
            fields = stat.read_text(encoding="ascii", errors="replace")
            return fields[fields.rindex(")") + 2:].split()[19] or None   # field 22; the name in brackets may hold spaces
        said = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
                              env={**os.environ, "LC_ALL": "C", "TZ": "UTC0"})
        return " ".join(said.stdout.split()) or None if said.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _started_windows(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes
    if pid > 0xFFFFFFFF:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return str(created) if created else None
    finally:
        kernel32.CloseHandle(handle)


def rule_path(path) -> str:
    """A folder as Claude Code matches it: POSIX form, a Windows drive as `/c/...`."""
    posix = Path(path).resolve().as_posix()
    drive = re.match(r"^([A-Za-z]):/(.*)$", posix)
    return f"/{drive.group(1).lower()}/{drive.group(2)}" if drive else posix


def interpreters(body: str, roots=()) -> list:
    """The interpreter forms a routine may write: this one (quoted when its path has a space, as
    the prompt renders it), `python`, `python3`, and the one the prompt names before a tool path
    when that is a Python installed outside every space (a prompt is text an agent can edit)."""
    here = Path(sys.executable).as_posix()
    found = [f'"{here}"' if " " in here else here, "python", "python3"]
    for token in re.findall(r'("[^"\n]+"|[^\s`"]+) [^\s`]+/_meta/tools/[a-z_]+\.py', body):
        path = Path(token.strip('"'))
        if (path.is_absolute() and path.is_file() and path.name.lower().startswith("python")
                and not any(path.resolve().is_relative_to(Path(r).resolve()) for r in roots)):
            found.append(token)
    return list(dict.fromkeys(found))


def roots_of(workspace: Path) -> list:
    """The spaces this routine walks: what `chain.py spaces` prints, the workspace first."""
    try:
        import chain
        return [Path(r) for r in chain.spaces_on_machine(workspace)]
    except Exception:
        return [Path(workspace)]


def routine_settings(workspace, body: str, roots=None, person: str = "", read_tools=(), write_tools=(),
                     send_tools=(), known_tools=None, profile=None, shell: str | None = None, extra_allow=()) -> dict:
    """The profile one routine run adds to the space's own (`claude --settings`, written to `profile`). Deny
    rules win over every allow rule in every scope, so the space's own profile cannot widen these, and
    `"disableAllHooks": false` keeps its hooks on whatever the person's own settings say.

    Bash runs only the routine tools and the read-only gh and git commands: a PreToolUse hook on Bash
    (`bash_guard.py <profile>`, the workspace's own) lets through one plain command of a shape this profile
    allows, so a Bash allow in the person's own settings never widens a routine. When that hook's command
    cannot be written, Bash is denied outright. The other built-in tools a routine could reach out with are
    denied by name (`ROUTINE_BUILTINS` are the ones it has).

    Of the connectors it allows the grant's tools by their exact names, each checked again here:
    `read_tools` that only read (`reader.is_read_tool`), `write_tools` that create, update or save and
    do nothing else, and `send_tools` whose name says send, only with a `person` the send guard can
    hold them to and a shell to run it with (`shell`, the one the probe proved; else `hook_shell`): a
    PreToolUse hook on each runs the workspace's `send_guard.py <person>` (`send_guard_command`).
    Every other connector tool it knows of (`known_tools`, else every one the probe has seen) is denied,
    so a tool the person once chose "always allow" for in their own settings is refused here too.

    Two more hooks run the same guard in a mode of its own (Ruling R45): on Task (`TASK_MATCHER`) only the
    general-purpose agent starts (`--task`), and on every connector tool (`CONNECTOR_MATCHER`) only the tools this
    profile allows by name go through (`--connectors <profile>`), so a tool that appeared after the last probe is
    refused too. Without a command for them, Task is denied and no connector tool is granted. Every hook carries
    its own `timeout` (`GUARD_TIMEOUT`), well above the time each guard gives itself.

    `extra_allow` adds Bash shapes of the caller's own (`Bash(<pattern>)` on one line; anything else, a rule that
    runs over a line among them, is dropped) right after the tool rules, only while the Bash guard is written: a
    cloud run's read-only `gh api` calls (`cloud.py`)."""
    import reader
    workspace = Path(workspace).resolve()
    tools_dir = workspace / "_meta" / "tools"
    roots = [Path(r).resolve() for r in (roots or [workspace])]
    profile = Path(profile) if profile else settings_path(Path("routine"), "unwritten")
    if shell is None:
        found = hook_shell()
        shell = found[0] if found else ""
    pys = interpreters(body, roots)
    # Each root as it resolves and as the prompt spells it (a rule matches the text as written),
    # only when the spelling is one of the roots.
    named = [m for m in re.findall(r'([^\s`"<>]+)/_meta/tools/[a-z_]+\.py', body)
             if "/" in m and Path(m).is_absolute() and Path(m).resolve() in roots]
    places = list(dict.fromkeys([r.as_posix() for r in roots] + named))
    try:
        bash, task, connectors = (bash_guard_command(profile, script=tools_dir / "bash_guard.py", mode=mode)
                                  for mode in (None, "--task", "--connectors"))
    except ValueError:   # a path the hook's command cannot carry: no Bash, Task or connector at all, rather than unguarded
        bash = task = connectors = None
    allow = [f"Bash({py} _meta/tools/{tool} *)" for py in pys for tool in ROUTINE_TOOLS] if bash else []
    allow += [f"Bash({py} {place}/_meta/tools/{tool} *)"
              for place in places for py in pys for tool in ROUTINE_TOOLS] if bash else []
    allow += [rule for rule in dict.fromkeys(_strings(list(extra_allow or [])))
              if re.fullmatch(r"Bash\(.+\)", rule) and not re.search(r"[\r\n]", rule)] if bash else []
    others = sorted({p.name for root in [workspace, *roots] for p in (root / "_meta" / "tools").glob("*.py")}
                    - set(ROUTINE_TOOLS))
    deny = [f"Edit(//**/{pattern})" for pattern in PROTECTED_EDITS]
    deny += [f"Edit(/{rule_path(root)}/{pattern})" for root in roots for pattern in PROTECTED_IN_ROOT]
    deny += [f"Edit(~/{folder}/**)" for folder in (".claude", ".spaces")]
    deny += [f"Bash(*_meta/tools/{tool}*)" for tool in others]
    # `-m` puts the working folder first on the import path, so a planted module would run;
    # `--output` lets a read-only git command write a file.
    deny += ["Bash(python -m *)", "Bash(python3 -m *)", "Bash(git *--output*)"]
    deny += [t for t in dict.fromkeys([*reader.DENIED, "WebFetch"])
             if t not in ROUTINE_BUILTINS or (t in ("Bash", "Task") and not bash)]
    read_tools, write_tools, send_tools = (_strings(list(x or [])) for x in (read_tools, write_tools, send_tools))
    given = [*read_tools, *write_tools, *send_tools]
    reads = [t for t in dict.fromkeys(read_tools) if reader.is_read_tool(t)]
    writes = [t for t in dict.fromkeys(write_tools) if _writes(t)]
    sends = [t for t in dict.fromkeys(send_tools) if _sends(t)]
    try:   # without a shell that runs the hook (Git Bash on Windows) it would not block: no send at all
        guard = send_guard_command(person, script=tools_dir / "send_guard.py") if sends and shell else None
    except ValueError:
        guard = None
    sends = sends if guard and connectors else []
    granted = list(dict.fromkeys(reads + writes + sends)) if connectors else []
    deny += sorted({t for t in [*reader._known(known_tools), *given]
                    if reader.CONNECTOR_TOOL.fullmatch(t) and t not in granted})
    data = {"disableAllHooks": False,
            "permissions": {"allow": allow + (list(READ_ONLY) if bash else []) + granted, "deny": deny}}
    hook = lambda command: {"type": "command", "command": command, "timeout": GUARD_TIMEOUT}   # noqa: E731
    hooks = [{"matcher": "Bash", "hooks": [hook(bash)]}] if bash else []
    hooks += [{"matcher": tool, "hooks": [hook(guard)]} for tool in sends]
    if bash:   # the agents a run starts and the connector tools it calls (Minor 10, 11)
        hooks += [{"matcher": TASK_MATCHER, "hooks": [hook(task)]}, {"matcher": CONNECTOR_MATCHER, "hooks": [hook(connectors)]}]
    if hooks:
        data["hooks"] = {"PreToolUse": hooks}
    return data


def _connector_part(tool) -> str | None:
    import reader
    found = reader.CONNECTOR_TOOL.fullmatch(tool) if isinstance(tool, str) else None
    return found.group("tool") if found else None


def _writes(tool) -> bool:
    """A tracker's write: its name creates, updates or saves, and says no other act (delete, move,
    assign, ...)."""
    import apps
    import reader
    part = _connector_part(tool)
    if part is None:
        return False
    low, words = part.lower(), reader.name_words(part)
    return (any(w in low for w in apps.WRITE_WORDS)
            and not any(w in low for w in reader.ACT_WORDS if w not in apps.WRITE_WORDS)
            and not any(w in words for w in reader.ACT_VERBS))


def _sends(tool) -> bool:
    import apps
    part = _connector_part(tool)
    return part is not None and apps.SEND_WORD in part.lower()


def settings_path(prompt: Path, name: str) -> Path:
    """Where one routine's profile is written: outside every space."""
    return Path.home() / ".spaces" / "routines" / f"{Path(prompt).stem}-{name}.settings.json"


def write_settings(workspace, prompt: Path, body: str, name: str, got: dict | None = None, roots=None) -> Path:
    """The routine profile for this run, written again every run, outside every space, from the
    grant (`got`, else read now)."""
    path = settings_path(prompt, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    got = got if got is not None else grant(workspace)
    data = routine_settings(workspace, body, roots or roots_of(Path(workspace)), got["person"], got["read"],
                            got["write"], got["send"], profile=path, shell=got["shell"])
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def _prompts(workspace) -> list:
    return sorted((Path(workspace) / "_meta" / "state" / "routines").glob("*.md"))


def split_prompt(text: str) -> tuple:
    """(the frontmatter as a mapping, the prompt without it)."""
    parts = text.split("---", 2) if text.startswith("---") else []
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2].lstrip()


def write_allowlist(workspace) -> list:
    """Every rendered routine's profile, written from the grant as it stands: the build writes them
    between its two probes, and each run writes its own again."""
    workspace = Path(workspace).resolve()
    name = spaceyaml.load(workspace).name
    got, roots = grant(workspace), roots_of(workspace)
    return [write_settings(workspace, prompt, split_prompt(prompt.read_text(encoding="utf-8"))[1], name, got, roots)
            for prompt in _prompts(workspace)]


# ----- the guards' commands -------------------------------------------------------------------
# The hook commands are written as the read guard's is (`reader.guard_command`): each word between
# double quotes with forward slashes, and `|| exit 2`, so a guard that cannot start blocks the call. A
# profile names the workspace's own guards (`routine_settings`), the ones its runs use.
SEND_GUARD = Path(__file__).resolve().with_name("send_guard.py")
BASH_GUARD = Path(__file__).resolve().with_name("bash_guard.py")
STRANGER = "not-you@example.invalid"   # the address the send check proves the guard refuses
HOOK_SECONDS = 10                      # a guard answers within a second; one that hangs has failed
# Each guard ends itself, refusing, after its own LIMIT_SECONDS (10): a hook Claude Code cuts off at its timeout lets
# the call through (dry run A, docs/design/cli-facts.md), so the hook's timeout is well above the guard's own.
GUARD_TIMEOUT = 30
TASK_MATCHER = "Task|Agent"   # the 2.1.303 stream names the Task tool Agent (dry run A); a matcher is a pattern
CONNECTOR_MATCHER = "mcp__.*"   # every connector tool, a tool that came after the last probe included


def guardable(person) -> str | None:
    """The person's one address as the send guard holds a send to it, or None: no address, more than
    one, or one the hook's command cannot carry between double quotes."""
    import reader
    import send_guard
    address = send_guard.address_of(person)
    return address if address and not any(c in address for c in reader.UNQUOTABLE) else None


def send_guard_command(person: str, python: str | None = None, script=None) -> str:
    """`"<python>" -I "<send_guard.py>" "<address>" || exit 2`: only exit code 2 blocks a call, so a guard
    that cannot start (a missing interpreter or script, a crash) ends in a 2 too. ValueError for a
    person that is not one address the command can carry, or a path it cannot quote."""
    import reader
    address = guardable(person)
    if address is None:
        raise ValueError("an address the send guard's command cannot carry")
    return f'{reader._quoted(python or sys.executable)} -I {reader._quoted(script or SEND_GUARD)} "{address}" || exit 2'


def bash_guard_command(profile, python: str | None = None, script=None, mode: str | None = None) -> str:
    """`"<python>" -I "<bash_guard.py>" "<the routine's settings file>" || exit 2`; with `mode` "--task" the hook
    on Task (`... --task || exit 2`), with "--connectors" the one on every connector tool (`... --connectors
    "<the routine's settings file>" || exit 2`). ValueError for a path the command cannot quote."""
    import reader
    head = f"{reader._quoted(python or sys.executable)} -I {reader._quoted(script or BASH_GUARD)}"
    if mode == "--task":
        return f"{head} --task || exit 2"
    words = ["--connectors"] if mode == "--connectors" else []
    return " ".join([head, *words, reader._quoted(profile), "|| exit 2"])


def hook_shell(windows: bool | None = None) -> list | None:
    """The shell Claude Code runs a hook command with: Git Bash on Windows (the one `CLAUDE_CODE_GIT_BASH_PATH`
    names, else the one beside `git`; never a `bash` found on PATH, which can be WSL's and cannot start a
    Windows program by its path), else `sh`. None on a Windows without Git Bash."""
    windows = os.name == "nt" if windows is None else windows
    if not windows:
        return ["/bin/sh", "-c"]
    given = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if given and Path(given).is_file():
        return [given, "-c"]
    git = shutil.which("git")
    if git:
        for up in Path(git).parents[1:3]:   # <Git>/cmd/git.exe, <Git>/mingw64/bin/git.exe
            bash = up / "bin" / "bash.exe"
            if bash.is_file():
                return [str(bash), "-c"]
    return None


def run_hook(command: str, payload: bytes, shell: list | None = None) -> int | None:
    """Run a hook command as Claude Code would, in `shell` (else `hook_shell`), with `payload` on stdin: its exit
    code, or None when there is no shell to run it with or it did not finish."""
    shell = shell or hook_shell()
    if shell is None:
        return None
    try:
        return subprocess.run([*shell, command], input=payload, capture_output=True, timeout=HOOK_SECONDS).returncode
    except (OSError, subprocess.SubprocessError):
        return None


# ----- the grant: what the routines may call ---------------------------------------------------
APPS_FILE = Path("_meta") / "state" / "private" / "apps.yaml"
NO_MAILBOX = "No mailbox of yours is connected, so the routines send no mail."
NO_ADDRESS = "Your morning briefing has no address the routines can hold their mail to, so they send no mail."
MAILBOX_SIGNED_OUT = "Your mailbox in {app} needs a new sign-in, so the routines send no mail until it is connected again."
NO_SHELL = ("This computer has no Git for Windows, which the check on the routines' mail runs in, so the routines "
            "send no mail until it is installed.")
NOT_PROVEN = ("The check that keeps the routines' mail to you alone has not passed on this computer yet, so the "
              "routines send no mail until it does.")


def read_probe() -> dict:
    """What probe.json holds, or {} when there is none or it cannot be read."""
    try:
        data = json.loads(probe_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_probe(data: dict) -> Path:
    """probe.json written whole or not at all: a reader never sees half of it."""
    path = probe_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="probe.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:   # Windows: someone is reading it this instant
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


def _strings(value) -> list:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _connector_tools(tools) -> list:
    import reader
    return [t for t in _strings(tools) if reader.CONNECTOR_TOOL.fullmatch(t)]


def ever_seen(old: dict, now: dict) -> list:
    """Every connector tool the probe or a run has ever listed (probe.json's `seen`): what the profile denies
    unless the grant names it, so a tool that went away and came back is never let through by being forgotten."""
    return list(dict.fromkeys(_strings(old.get("seen")) + _connector_tools(old.get("tools"))
                              + _connector_tools(now.get("tools"))))


def merged(old: dict, seen: dict) -> dict:
    """A routine run's own init joined to probe.json (`refresh_probe`): its tools added to the probe's (the
    run's init leaves out what its profile denies, so it cannot say a tool is gone; only `--probe`, which
    replaces the list, can), each server's status the newest one seen, and `seen` kept whole."""
    tools = list(dict.fromkeys(_strings(old.get("tools")) + _strings(seen.get("tools"))))
    servers: dict = {}
    for server in [*(old.get("mcp_servers") or []), *(seen.get("mcp_servers") or [])]:
        if isinstance(server, dict) and isinstance(server.get("name"), str):
            servers[server["name"]] = {"name": server["name"], "status": str(server.get("status") or "")}
    return {"tools": tools, "mcp_servers": list(servers.values()), "seen": ever_seen(old, seen)}


def _verdicts(workspace) -> list:
    """The apps' verdicts the build kept (`start.keep_findings`); [] when there are none or the file
    cannot be read: a grant from a broken file is empty, never wide."""
    try:
        data = yaml.safe_load((Path(workspace) / APPS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return []
    rows = data.get("apps") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("app"), str)] if isinstance(rows, list) else []


def grant(workspace, probe: dict | None = None, shell: str | None = None) -> dict:
    """What the routines may call, from the apps' verdicts and what the latest probe sees (`probe`, else
    probe.json): {"read", "write", "send", "reconnect", "notes", "person", "shell"}.

    - An app read as work or mixed gives the read tools its verdict names that the probe lists and that
      only read, and, for a tracker, the tools that create, update or save (apps.group decides both).
    - The mail app whose account is the person's own (`owner_email`, `Name <address>` read, without
      regard to case) gives the send tools the probe lists for it, whatever its verdict but
      someone-else's (Ruling R19): the briefing goes from the person's own mailbox to the person.
    - An app read as work or mixed that the probe lists as needing a new sign-in gives nothing and is
      named in `reconnect` (Ruling R17); no other app stands in for it.
    - A send tool needs the shell the send guard was proven in (`shell`, else the probe's `hook_shell`):
      `shell` in the result, which a run pins (`routine_env`). Without a mailbox to send from, without a
      proven shell, or on a Windows without the Git Bash the guard's hook runs in (where Claude Code would
      run it with PowerShell, which does not block), there is no send tool, and one plain line in `notes`
      says so."""
    import apps
    import send_guard
    probe = read_probe() if probe is None else probe
    proven = probe.get("hook_shell") if shell is None else shell
    proven = proven if isinstance(proven, str) and proven and Path(proven).is_file() else ""
    servers = [s for s in probe.get("mcp_servers") or [] if isinstance(s, dict)]
    groups = {g["app"]: g for g in apps.group(_strings(probe.get("tools")), servers)}
    signed_out = set(apps.not_signed_in(servers))
    try:
        person = guardable(spaceyaml.load(workspace).owner_email)
    except Exception:
        person = None
    read, write, send, reconnect, mailbox_out = [], [], [], [], []
    for verdict in _verdicts(workspace):
        app, kind = verdict["app"], verdict.get("verdict")
        group = None if app in signed_out else groups.get(app)
        if kind in apps.READ:
            if app in signed_out:
                reconnect.append(app)
            elif group:
                read += [t for t in _strings(verdict.get("read")) if t in group["read"]]
                write += [t for t in _strings(verdict.get("write")) if t in group["write"]]
        owner = send_guard.address_of(verdict.get("account"))
        if kind != "someone-else" and person and owner and owner.lower() == person.lower():
            if app in signed_out:
                mailbox_out.append(app)
            elif group:
                send += group["send"]
    send = list(dict.fromkeys(send))
    notes = []
    if send and not proven:
        send = []
        notes.append(NO_SHELL if hook_shell() is None else NOT_PROVEN)
    elif not send:
        notes.append(NO_ADDRESS if not person else MAILBOX_SIGNED_OUT.format(app=mailbox_out[0]) if mailbox_out
                     else NO_MAILBOX)
    return {"read": list(dict.fromkeys(read)), "write": list(dict.fromkeys(write)), "send": send,
            "reconnect": list(dict.fromkeys(reconnect)), "notes": notes, "person": person or "", "shell": proven}


def read_grant(workspace) -> tuple:
    """(read tools, write tools, send tools) of the grant."""
    got = grant(workspace)
    return got["read"], got["write"], got["send"]


# ----- what only the person can do: Needs you ----------------------------------------------
ITEMS = Path("_inbound") / "needs-you" / "routine.yaml"
SIGNIN_ID = "routine-claude-signin"
RECONNECT_PREFIX = "routine-reconnect-"


def _signin_item() -> dict:
    import signin
    return {"id": SIGNIN_ID, "kind": "request",
            "text": "Sign in to Claude's command line again: your routines stopped because its sign-in ran out.",
            "recommendation": "sign in once in the browser window the sign-in opens, then say yes",
            "default": "your routines stay stopped until you sign in", "deadline": None, "depends": 1,
            "payload": ITEMS.as_posix(), "fingerprint": "", "check": " ".join(signin.CLAUDE_LOGIN)}


def _reconnect_id(app: str) -> str:
    return RECONNECT_PREFIX + (re.sub(r"[^a-z0-9]+", "-", app.lower()).strip("-") or "app")


def _reconnect_item(app: str) -> dict:
    return {"id": _reconnect_id(app), "kind": "reconnect",
            "text": (f"Reconnect {app}: its sign-in ran out, so your routines leave it out until you sign in to "
                     f"{app} again in Claude's connector settings."),
            "recommendation": "sign in again in your connector settings, then say yes",
            "default": f"{app} stays out of your routines until it is connected again", "deadline": None,
            "depends": 1, "payload": ITEMS.as_posix(), "fingerprint": "",
            "check": f"the next routine run finds {app} connected"}


def write_items(workspace, claude_signin: bool | None = None, reconnect: list | None = None) -> None:
    """The routines' own items, without the model: Claude's sign-in (True asks, False withdraws, None
    leaves it as it is) and one reconnect item per app in `reconnect` (None leaves them)."""
    path = Path(workspace) / ITEMS
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        data = {}
    rows = data.get("items") if isinstance(data, dict) else None
    before = [i for i in rows if isinstance(i, dict)] if isinstance(rows, list) else []
    items = list(before)
    if claude_signin and not any(i.get("id") == SIGNIN_ID for i in items):
        items.insert(0, _signin_item())
    elif claude_signin is False:
        items = [i for i in items if i.get("id") != SIGNIN_ID]
    if reconnect is not None:
        old = {i.get("id"): i for i in items if str(i.get("id", "")).startswith(RECONNECT_PREFIX)}
        items = [i for i in items if i.get("id") not in old] + [old.get(_reconnect_id(a)) or _reconnect_item(a)
                                                                for a in dict.fromkeys(reconnect)]
    if items != before or (not path.exists() and items):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True, width=100),
                        encoding="utf-8", newline="\n")


# ----- one run at a time per workspace ---------------------------------------------------------
LOCK_HOURS = 2   # Ruling R11: a lock without its process's start time, older than this with a live pid, is a reused pid's
TAKEOVER_SECONDS = 10   # a takeover, or a lock being written, takes milliseconds: one older than this was left


def lock_path(workspace) -> Path:
    """The workspace's lock, with the machine, outside every space: {pid, routine, started}."""
    return Path.home() / ".spaces" / "locks" / f"{spaceyaml.load(workspace).name}.lock"


def _lock_data(raw: bytes) -> dict | None:
    """A lock as a run writes it, the time as a moment; None for anything else: a corrupt lock is a dead one."""
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    pid, started = data.get("pid"), data.get("started")
    if isinstance(started, str):
        try:
            started = dt.datetime.fromisoformat(started)
        except ValueError:
            return None
    if not (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and isinstance(started, dt.datetime)):
        return None
    mark = data.get("pid_started")
    return {"pid": pid, "routine": str(data.get("routine") or ""),
            "started": started if started.tzinfo else started.astimezone(), "part": str(data.get("part") or ""),
            "pid_started": str(mark) if isinstance(mark, (str, int)) and not isinstance(mark, bool) and str(mark) else ""}


def _live(data: dict | None) -> bool:
    """Whether a lock is a running run's: its process runs, and it is that process (Ruling R45, I-8): one that
    started when the lock says, however old the lock (a lid closed for hours), and not one that started at another
    time (the pid was given to another process). A lock without a start time, or a system that does not say one,
    keeps the age rule: neither from the future nor older than LOCK_HOURS."""
    if data is None or not pid_alive(data["pid"]):
        return False
    then = data.get("pid_started") or ""
    now = process_started(data["pid"]) if then else None
    if then and now:
        return then == now
    age = dt.datetime.now().astimezone() - data["started"]
    return -dt.timedelta(minutes=5) <= age < dt.timedelta(hours=LOCK_HOURS)


def _link(source: Path, path: Path) -> bool:
    """Give `source`'s file the name `path` only when no file has it: a hard link, or a rename on a
    Windows file system without links (a rename never replaces a file there). False when one is there."""
    try:
        os.link(source, path)
        return True
    except FileExistsError:
        return False
    except OSError:
        if os.name != "nt":
            raise
    try:
        os.rename(source, path)
        return True
    except FileExistsError:
        return False


def _create(path: Path, content: bytes) -> bool:
    """Make the lock with its content in one step, or not at all when there is one."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".new")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        try:
            return _link(Path(tmp), path)
        except OSError:   # a file system without links: an exclusive create, its content right after
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                return False
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            return True
    finally:
        Path(tmp).unlink(missing_ok=True)


def _unique(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}.old")


def _age(path: Path) -> float:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return 0.0


def _remove(path: Path, raw: bytes | None = None) -> bool:
    """Take `path` away in one step that only one run can make: renamed to a name of its own, then removed.
    With `raw`, only while it still holds those bytes (else it is put back where it was). False when it was
    gone already."""
    aside = _unique(path)
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return False
    try:
        if raw is not None and aside.read_bytes() != raw:
            _link(aside, path)   # not the file that was read: it goes back; nothing else takes the name meanwhile
            return False
        return True
    finally:
        aside.unlink(missing_ok=True)


def _take_over(path: Path, raw: bytes) -> None:
    """Move a dead lock out of the way, one run at a time: only the run that made the takeover's own file
    (`<lock>.takeover`, an exclusive create) may move a lock, and only once it has read the lock again and
    found the same dead one, so a run that read it before another run took it over never moves the new
    lock. A takeover file a crashed run left behind (older than TAKEOVER_SECONDS) is taken away first."""
    baton = path.with_name(path.name + ".takeover")
    mine = f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}\n".encode("ascii")
    if not _create(baton, mine):
        try:
            if _age(baton) > TAKEOVER_SECONDS:
                _remove(baton, baton.read_bytes())
        except OSError:
            pass
        return
    try:
        try:
            again = path.read_bytes()
        except FileNotFoundError:
            return
        if again == raw and not _being_written(path, again) and not _live(_lock_data(again)):
            _remove(path, again)
    finally:
        try:
            if baton.read_bytes() == mine:
                baton.unlink()
        except OSError:
            pass


def _being_written(path: Path, raw: bytes) -> bool:
    """An empty lock made a moment ago: on a file system without links the lock is created first and written
    right after (`_create`), so it is a run's, not a dead one."""
    return not raw and _age(path) <= TAKEOVER_SECONDS


def take_lock(workspace, routine_name: str, part: str | None = None) -> bool:
    """The workspace's lock for this run: False while a live run holds it. A dead one is taken over: its
    process is gone, its pid names a process that started at another time, it has no start time to compare and
    is older than LOCK_HOURS, or it cannot be read (`_live`). Runs racing for it never both get
    it (Ruling R30): the lock is made whole in one exclusive step that fails when one is there, and a dead
    one is moved out of the way by one run at a time (`_take_over`) before that step is tried again. The lock keeps
    the process's start time beside its pid (`process_started`), so `_live` tells a reused pid apart. `part` is
    the part of the history this run reads (`next_part`), kept in the lock for as long as the run lasts."""
    path = lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    held = {"pid": os.getpid(), "routine": routine_name,
            "started": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    mark = process_started(os.getpid())
    mine = yaml.safe_dump({**held, **({"pid_started": mark} if mark else {}), **({"part": part} if part else {})},
                          sort_keys=False).encode("utf-8")
    for attempt in range(200):
        if _create(path, mine):
            return True
        try:
            raw = path.read_bytes()
            if _being_written(path, raw) or _live(_lock_data(raw)):
                return False
            _take_over(path, raw)
        except FileNotFoundError:
            continue   # let go of between the two: try again
        except PermissionError:   # Windows: another run has it open this instant
            pass
        time.sleep(0.002 * (attempt % 5))
    return False


def locked(workspace) -> dict | None:
    """The live run that holds the workspace's lock ({pid, routine, started}), or None."""
    try:
        data = _lock_data(lock_path(workspace).read_bytes())
    except OSError:
        return None
    if not _live(data):
        return None
    held = {"pid": data["pid"], "routine": data["routine"], "started": data["started"].isoformat(timespec="seconds")}
    return {**held, "part": data["part"]} if data["part"] else held


def release_lock(workspace, routine_name: str | None = None) -> None:
    """Let go of the lock this process holds (for `routine_name`, when given); someone else's stays."""
    path = lock_path(workspace)
    try:
        data = _lock_data(path.read_bytes())
    except OSError:
        return
    if data and data["pid"] == os.getpid() and routine_name in (None, data["routine"]):
        path.unlink(missing_ok=True)


# ----- what a run came to ---------------------------------------------------------------------
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
MODEL_USAGE_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
RESET = re.compile(r"(?:(?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?:(?P<year>\d{4}), )?)?"
                   r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?(?P<half>[ap]m)?")
RESETS_SAID = re.compile(r"resets (?P<resets>(?:[A-Z][a-z]{2} \d{1,2}, (?:\d{4}, )?)?\d{1,2}(?::\d{2})?(?:[ap]m)?)")
LAST_RESET = 4102444800   # 2100-01-01: a reset time past it is no time


def _local(now) -> dt.datetime:
    """The machine's local clock, without a zone."""
    if now is None:
        return dt.datetime.now()
    return now.astimezone().replace(tzinfo=None) if now.tzinfo else now


def parse_reset(text, now=None) -> str | None:
    """A reset time as the command line prints it (`11:20pm`, `23:20`, `Oct 27, 1pm`, `Jan 2, 2027,
    1:30pm`) as the machine's local time, ISO without a zone; a time of day already past is tomorrow's,
    a date without a year more than a day past is next year's. None for anything that is not a time:
    a reset is never invented. The command line prints the reset in this machine's own time zone."""
    found = RESET.fullmatch(text.strip()) if isinstance(text, str) else None
    if not found:
        return None
    now = _local(now)
    hour, minute, half = int(found["hour"]), int(found["minute"] or 0), found["half"]
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    elif hour > 23:
        return None
    if minute > 59:
        return None
    if found["month"]:
        if found["month"] not in MONTHS:
            return None
        year = int(found["year"]) if found["year"] else now.year
        try:
            at = dt.datetime(year, MONTHS.index(found["month"]) + 1, int(found["day"]), hour, minute)
            if not found["year"] and at < now - dt.timedelta(days=1):
                at = at.replace(year=year + 1)
        except ValueError:
            return None
    else:
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += dt.timedelta(days=1)
    return at.isoformat(timespec="seconds")


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def usage_of(result) -> dict:
    """The tokens a run used, from its result event: summed over every model it used (`modelUsage`),
    else its `usage`; zero for what it does not say."""
    total = dict.fromkeys(USAGE_KEYS, 0)
    if not isinstance(result, dict):
        return total
    models = result.get("modelUsage")
    if isinstance(models, dict) and any(isinstance(m, dict) for m in models.values()):
        for model in models.values():
            if isinstance(model, dict):
                for key, field in zip(USAGE_KEYS, MODEL_USAGE_FIELDS):
                    total[key] += _count(model.get(field))
        return total
    usage = result.get("usage")
    if isinstance(usage, dict):
        for key, field in zip(USAGE_KEYS, USAGE_FIELDS):
            total[key] += _count(usage.get(field))
    return total


def events_of(stdout) -> tuple:
    """(the JSON events a run printed, the lines that are not one)."""
    events, plain = [], []
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict):
            events.append(event)
        else:
            plain.append(line)
    return events, plain


def _first(patterns, text: str):
    return next((m for m in (re.search(p, text) for p in patterns) if m), None)


def _said(found) -> str:
    return " ".join(found.group(0).split())[:200]


def _structured_reset(infos: list) -> str | None:
    for info in infos:
        value = info.get("resetsAt")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < LAST_RESET:
            return dt.datetime.fromtimestamp(value).isoformat(timespec="seconds")
    return None


def _reset_near(text: str, found, now) -> str | None:
    near = RESETS_SAID.search(text, found.start(), found.end() + 120)
    return parse_reset(near["resets"], now) if near else None


def outcome(res, timed_out: bool, now=None) -> dict:
    """What a run came to, from what the command line said, never from its exit code (it differs by
    system): {"outcome", "why", "resets_at", "usage"}. In this order:
    - `overage`: the run started to draw on extra usage (its rate_limit_event), or an extra-usage text;
    - `finished`: the result event says success. Its text is the model's own words, which may repeat
      what a record said, so it is never read for a limit or a sign-in;
    - `limit`: the structured error or rate_limit_event, else a limit text, with the reset time the
      event or the text gives (`resets_at`, local time; None when neither says one);
    - `auth`: the structured sign-in error, else a sign-in text;
    - `timeout` after an hour; `stopped` for anything else (no result at all, an error result, a
      command that did not start).
    The texts are read from the error result, stderr and any line on stdout that is not JSON."""
    zero = dict.fromkeys(USAGE_KEYS, 0)
    if timed_out:
        return {"outcome": "timeout", "why": "stopped after an hour", "resets_at": None, "usage": zero}
    if res is None:
        return {"outcome": "stopped", "why": "Claude's command line did not start", "resets_at": None, "usage": zero}
    now = _local(now)
    events, plain = events_of(res.stdout)
    results = [e for e in events if e.get("type") == "result"]
    result = results[-1] if results else None
    usage = usage_of(result)
    infos = [e["rate_limit_info"] for e in events
             if e.get("type") == "rate_limit_event" and isinstance(e.get("rate_limit_info"), dict)]
    kinds = {ERROR_KINDS[e["error"]] for e in events
             if e.get("type") == "assistant" and isinstance(e.get("error"), str) and e["error"] in ERROR_KINDS}
    finished = result is not None and not result.get("is_error") and result.get("subtype", "success") == "success"
    told = result.get("result") if result is not None and isinstance(result.get("result"), str) else ""
    said = (res.stderr or "") if finished else "\n".join([told, res.stderr or "", *plain])

    def line(kind, why, resets=None) -> dict:
        return {"outcome": kind, "why": why, "resets_at": resets, "usage": usage}
    if any(info.get("isUsingOverage") is True for info in infos):
        return line("overage", "the run started to draw on extra usage",
                    _structured_reset([i for i in infos if i.get("isUsingOverage") is True]))
    found = _first(OVERAGE_PATTERNS, said)
    if found:
        return line("overage", _said(found), _reset_near(said, found, now))
    if finished:
        return line("finished", "")
    limit = _first(LIMIT_PATTERNS, said)
    rejected = [i for i in infos if i.get("status") == "rejected"]
    if "limit" in kinds or rejected or limit:
        resets = _structured_reset(rejected) or (parse_reset(limit.groupdict().get("resets"), now) if limit else None)
        return line("limit", _said(limit) if limit else "the plan's usage limit was reached", resets)
    auth = _first(AUTH_PATTERNS, said)
    if "auth" in kinds or auth:
        return line("auth", _said(auth) if auth else "Claude's sign-in was refused")
    if result is not None:
        return line("stopped", f"claude stopped: {result.get('subtype') or 'error'}")
    return line("stopped", f"claude ended without a result (exit {res.returncode})")


def limits_of(res) -> dict | None:
    """The plan's windows as the run's own newest `rate_limit_event` gave them, the meter of the account the routines
    draw on (cli-facts section 8): {"five_hour": {"utilization", "resets_at"}, "seven_day": {...}, "overage_status",
    "using_overage"}, reset times as UTC text. Structured fields only, each kept only in the shape the command line
    gives it (a number, a moment, one short word, true or false); None when the run printed no event."""
    events, _ = events_of(res.stdout if res is not None else "")
    infos = [e["rate_limit_info"] for e in events
             if e.get("type") == "rate_limit_event" and isinstance(e.get("rate_limit_info"), dict)]
    if not infos:
        return None
    info, limits = infos[-1], {}
    windows = info.get("unifiedWindows") if isinstance(info.get("unifiedWindows"), dict) else {}
    for name in ("five_hour", "seven_day"):
        given = windows.get(name) if isinstance(windows.get(name), dict) else {}
        window = {}
        use, resets = given.get("utilization"), given.get("resetsAt")
        if isinstance(use, (int, float)) and not isinstance(use, bool) and 0 <= use <= 100:
            window["utilization"] = use
        if isinstance(resets, (int, float)) and not isinstance(resets, bool) and 0 < resets < LAST_RESET:
            window["resets_at"] = _utc(dt.datetime.fromtimestamp(resets, dt.timezone.utc))
        if window:
            limits[name] = window
    status = info.get("overageStatus")
    if isinstance(status, str) and re.fullmatch(r"[a-z_]{1,40}", status):
        limits["overage_status"] = status
    if isinstance(info.get("isUsingOverage"), bool):
        limits["using_overage"] = info["isUsingOverage"]
    return limits


def _utc(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Each run's windows and tokens as this process saw them (R40, N-1): no routine may edit the file (`PROTECTED_EDITS`)
# and none of its tools writes it, so the history's pacing reads it, never `runs.jsonl`, where an agent appends lines.
LIMITS_FILE = Path("_meta") / "state" / "routine-limits.jsonl"


def write_outcome(workspace, routine_name: str, line: dict, now=None) -> None:
    """The run's own line in `_meta/state/runs.jsonl`, with the tokens it really used: `cost_tokens` is
    their sum, cache reads and writes included. Its moment, outcome, tokens and windows also go to `LIMITS_FILE`."""
    now = now or dt.datetime.now().astimezone()
    given = line.get("usage") if isinstance(line.get("usage"), dict) else {}
    usage = {key: _count(given.get(key)) for key in USAGE_KEYS}
    row = {"routine": routine_name, "at": _utc(now), "space": spaceyaml.load(workspace).name,
           "cost_tokens": sum(usage.values()), "outcome": line.get("outcome"), "why": line.get("why") or "",
           "resets_at": line.get("resets_at"), "usage": usage, "notes": {"step": "routine"}}
    if isinstance(line.get("limits"), dict):   # a record only: an agent can append a line here too, so nothing trusts it
        row["limits"] = line["limits"]
    path = Path(workspace) / "_meta" / "state" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    meter = {k: row[k] for k in ("routine", "at", "outcome", "cost_tokens")}
    if "limits" in row:
        meter["limits"] = row["limits"]
    with (Path(workspace) / LIMITS_FILE).open("a", encoding="utf-8", newline="\n") as fh:   # the meter backfill paces from
        fh.write(json.dumps(meter, ensure_ascii=False) + "\n")


def _one_line(text, limit: int, tail: bool = False) -> str:
    text = " ".join(str(text or "").split())
    return (text[-limit:] if tail else text[:limit]) if len(text) > limit else text


def log_summary(res) -> str:
    """What the log keeps of a run: the model, the connectors and their states, the rate-limit readings,
    each refused call, the result and what was printed besides the events; never the messages."""
    if res is None:
        return ""
    events, plain = events_of(res.stdout)
    lines = []
    for event in events:
        kind, sub = event.get("type"), event.get("subtype")
        if kind == "system" and sub == "init":
            tools = event.get("tools") if isinstance(event.get("tools"), list) else []
            servers = [s for s in event.get("mcp_servers") or [] if isinstance(s, dict)]
            lines.append(f"model {event.get('model') or 'unknown'}, {len(tools)} tools, connectors: "
                         + (", ".join(f"{s.get('name')} {s.get('status')}" for s in servers) or "none"))
        elif kind == "rate_limit_event" and isinstance(event.get("rate_limit_info"), dict):
            info = event["rate_limit_info"]
            lines.append("rate limit: " + ", ".join(f"{k} {info[k]}" for k in
                                                    ("status", "rateLimitType", "isUsingOverage", "resetsAt") if k in info))
        elif kind == "system" and sub == "permission_denied":
            lines.append(f"refused: {event.get('tool_name')}")
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is not None:
        lines.append(f"result ({result.get('subtype')}, {result.get('num_turns')} turns): "
                     + _one_line(result.get("result"), 500))
    if plain:
        lines.append("printed: " + _one_line(" ".join(plain), 1000, tail=True))
    if (res.stderr or "").strip():
        lines.append("stderr: " + _one_line(res.stderr, 1000, tail=True))
    return "".join(line + "\n" for line in lines)


def init_of(res) -> dict | None:
    """The tools and servers a run's own init event lists, or None."""
    events, _ = events_of(res.stdout if res is not None else "")
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    if init is None:
        return None
    return {"tools": _strings(init.get("tools")),
            "mcp_servers": [{"name": s.get("name", ""), "status": s.get("status", "")}
                            for s in init.get("mcp_servers") or [] if isinstance(s, dict)]}


def refresh_probe(seen: dict) -> None:
    """A run's own init joined to probe.json (`merged`): a connector signed in again, or signed out,
    counts from the next run on. The probe's checks, stamp and proven shell stay as they were."""
    old = read_probe()
    write_probe({**old, **merged(old, seen)})


# ----- the probe: what a scheduled run sees, and three checks --------------------------------
def _check(name: str, ok: bool, why: str) -> dict:
    return {"name": name, "ok": bool(ok), "why": why}


def _profiles(workspace, got: dict, roots, known) -> list:
    """(routine, its profile) for each rendered routine: the file written for it when there is one (None
    when it cannot be read), else the one that would be written now."""
    found = []
    name = spaceyaml.load(workspace).name
    for prompt in _prompts(workspace) or [None]:
        path = settings_path(prompt or Path("routines"), name)
        if prompt is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            found.append((prompt.stem, data if isinstance(data, dict) else None))
            continue
        body = split_prompt(prompt.read_text(encoding="utf-8"))[1] if prompt else ""
        found.append((prompt.stem if prompt else "routines",
                      routine_settings(workspace, body, roots, got["person"], got["read"], got["write"], got["send"],
                                       known_tools=known, profile=path, shell=got["shell"])))
    return found


def _rules(data) -> tuple:
    """(allow, deny, PreToolUse entries) of a profile, empty where it holds something else."""
    perms = data.get("permissions") if isinstance(data, dict) and isinstance(data.get("permissions"), dict) else {}
    hooks = data.get("hooks") if isinstance(data, dict) and isinstance(data.get("hooks"), dict) else {}
    entries = hooks.get("PreToolUse") if isinstance(hooks.get("PreToolUse"), list) else []
    return _strings(perms.get("allow")), _strings(perms.get("deny")), [e for e in entries if isinstance(e, dict)]


def read_checks(workspace, got: dict, profiles: list) -> list:
    """One check per app the routines read: its read tools are there, it needs no new sign-in, and every
    routine's profile allows them. No model reads anything here: the content of an app is copied only
    after the app card has been shown."""
    import apps
    checks, seen = [], set()
    for verdict in _verdicts(workspace):
        app = verdict["app"]
        if verdict.get("verdict") not in apps.READ or app in seen:
            continue
        seen.add(app)
        name = f"reads {app}"
        tools = [t for t in got["read"] if t in _strings(verdict.get("read"))]
        if app in got["reconnect"]:
            checks.append(_check(name, False, f"{app} needs a new sign-in: sign in to {app} again in Claude's "
                                              "connector settings, and the routines read it again"))
        elif not tools:
            checks.append(_check(name, False, f"{app} is not connected for the routines: connect it again in "
                                              "Claude's connector settings"))
        elif any(data is None or not all(t in _rules(data)[0] and t not in _rules(data)[1] for t in tools)
                 for _, data in profiles):
            checks.append(_check(name, False, f"the routines may not use what reads {app}"))
        else:
            checks.append(_check(name, True, f"{app} can be read"))
    return checks


def folder_check(root) -> dict:
    """A space the routines walk: its settings read, a file written, read back and removed in its
    private zone, by this process, which the scheduler started as it starts a routine."""
    root = Path(root)
    try:
        name = spaceyaml.load(root).name
    except Exception:
        return _check(f"folder {root.name}", False, f"the routines cannot read the settings of {root.name}")
    private = root / "_meta" / "state" / "private"
    trial = private / f".probe-{os.getpid()}-{threading.get_ident()}"
    try:
        private.mkdir(parents=True, exist_ok=True)
        content = f"probe {time.time()}\n".encode("utf-8")
        trial.write_bytes(content)
        if trial.read_bytes() != content:
            raise OSError("what was written did not read back")
    except OSError:
        return _check(f"folder {name}", False, f"the routines cannot write in the folder of {name}")
    finally:
        try:
            trial.unlink(missing_ok=True)
        except OSError:
            pass
    return _check(f"folder {name}", True, f"{name} can be read and written")


def _hook_payload(tool: str, address: str) -> bytes:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                       "tool_input": {"to": [address], "subject": "probe", "body": "probe"}}).encode("utf-8")


GUARD_WORDS = "the guard that lets routines send mail only to you"


def _guard_holds(command: str, tool: str, person: str, shell: list) -> str | None:
    """None when the guard's `command`, run in `shell` as Claude Code runs a hook, refuses a stranger and lets
    mail to `person` through; else why not, in plain words."""
    stranger = STRANGER if STRANGER != person.lower() else "also-" + STRANGER
    refused = run_hook(command, _hook_payload(tool, stranger), shell)
    let = run_hook(command, _hook_payload(tool, person), shell)
    if refused is None or let is None:
        return f"{GUARD_WORDS} could not be run on this computer, so the routines may not send mail"
    if refused != 2:
        return f"{GUARD_WORDS} did not refuse a stranger"
    if let != 0:
        return f"{GUARD_WORDS} refused your own address"
    return None


def proven_shell(workspace, got: dict) -> str | None:
    """The shell the send guard holds in (`got`'s `shell`), proved with the command a run's profile carries
    (`send_guard_command` with the workspace's guard, as `routine_settings` writes it each run), or None: no
    send tool to guard, or the guard did not hold. Profiles on disk play no part: each run writes its own."""
    if not got["send"] or not got.get("shell"):
        return None
    try:
        command = send_guard_command(got["person"], script=Path(workspace) / "_meta" / "tools" / "send_guard.py")
    except ValueError:
        return None
    held = _guard_holds(command, got["send"][0], got["person"], [got["shell"], "-c"]) is None
    return got["shell"] if held else None


def send_check(got: dict, profiles: list) -> dict:
    """Mail only to you: every routine profile keeps its hooks on and guards each send tool with the send
    guard, and the guard, run with the profile's own command in the shell Claude Code will run it in (`got`'s
    `shell`, which a run pins), refuses a stranger and lets mail to the person through. Without a mailbox to
    send from it passes and says why."""
    name = "mail only to you"
    if not got["send"]:
        return _check(name, True, " ".join(got["notes"]) or NO_MAILBOX)
    guard = GUARD_WORDS
    commands = []
    for _, data in profiles:
        if data is None:
            return _check(name, False, "a routine's profile cannot be read, so whether it may send mail to "
                                       "anyone else is not known")
        if data.get("disableAllHooks") is not False:
            return _check(name, False, f"a routine's profile lets {guard} be switched off")
        allow, deny, entries = _rules(data)
        for tool in got["send"]:
            if tool not in allow or tool in deny:
                return _check(name, False, "a routine may not send your morning briefing")
            found = [h.get("command") for e in entries if e.get("matcher") == tool for h in e.get("hooks") or []
                     if isinstance(h, dict) and h.get("type") == "command" and isinstance(h.get("command"), str)]
            if not found:
                return _check(name, False, f"a routine may send mail without {guard}")
            commands += [(tool, c) for c in found]
    shell = [got["shell"], "-c"] if got.get("shell") else None
    if shell is None:
        return _check(name, False, f"{guard} could not be run on this computer, so the routines may not send mail")
    for tool, command in dict.fromkeys(commands):
        why = _guard_holds(command, tool, got["person"], shell)
        if why:
            return _check(name, False, why)
    return _check(name, True, "the guard refuses a stranger and lets mail to you through")


PROBE_NAME = "probe"   # the probe holds the workspace's lock under this name


def probe(workspace, run=subprocess.run) -> int:
    """`--probe`, under the workspace's lock (`take_lock`; while a run holds it, `error` says so and `busy` is
    true): what a run the scheduler starts sees (`probe_tools`, as a routine starts: in the workspace, every
    other root added, without a key or token). Its tools and servers replace the ones probe.json held, so an
    app that went away is granted no more (Ruling R30); `seen` keeps every connector tool ever listed, for the
    deny side (`ever_seen`). Then three checks: each app the routines read (`read_checks`), each folder they
    walk (`folder_check`) and mail only to the person (`send_check`, in the shell `hook_shell` finds). That
    shell is written as `hook_shell` once the guard a run carries held in it (`proven_shell`): the one a run
    pins and the grant's send tools need. Written to probe.json with the grant's notes, `ok` and `stamp`; an
    app that needs a new sign-in leaves its item on Needs you. When claude cannot be asked, the tools it knew
    stay and `error` says why. 0 when every check passes."""
    workspace = Path(workspace).resolve()
    if not take_lock(workspace, PROBE_NAME):
        held = locked(workspace) or {}
        write_probe({**read_probe(), "error": f"{BUSY}: {held.get('routine') or 'a routine'}", "busy": True,
                     "checks": [], "notes": [], "ok": False, "stamp": time.time(), "workspace": str(workspace)})
        return 1
    try:
        return _probe(workspace, run)
    finally:
        release_lock(workspace, PROBE_NAME)


def _probe(workspace: Path, run) -> int:
    roots = roots_of(workspace)
    old = read_probe()
    doctor.use_tools()
    claude = find_claude()
    try:
        if claude is None:
            raise ProbeError("Claude Code's command line is not installed on this machine")
        now = probe_tools(claude, workspace, roots[1:], run=run)
    except ProbeError as exc:
        write_probe({**old, "seen": ever_seen(old, {}), "error": str(exc), "busy": False, "checks": [], "notes": [],
                     "ok": False, "stamp": time.time(), "workspace": str(workspace)})
        return 1
    view = {**now, "seen": ever_seen(old, now)}
    candidate = hook_shell()
    got = grant(workspace, view, shell=candidate[0] if candidate else "")
    write_items(workspace, reconnect=got["reconnect"])
    profiles = _profiles(workspace, got, roots, view["seen"] + [t for t in view["tools"] if t not in view["seen"]])
    checks = [*read_checks(workspace, got, profiles), *[folder_check(r) for r in roots], send_check(got, profiles)]
    ok = all(c["ok"] for c in checks)
    write_probe({**view, "checks": checks, "notes": got["notes"], "ok": ok, "stamp": time.time(),
                 "workspace": str(workspace), "hook_shell": proven_shell(workspace, got)})
    return 0 if ok else 1


# ----- one computer, one runner: where the routines run ------------------------------------------
# A workspace's routines run on one computer through one scheduler: its OS tasks, which start this tool (`os`),
# or the desktop app's own scheduled tasks (`harness`). The record sits in the workspace and is synced, so every
# computer reads it; a session writes it (`onboard.py`: the build, `--routines-here`, `--pause`, `--resume`), never
# a routine (PROTECTED_EDITS). One line per key, the claim's four together and the pause a line apart: a merge that
# keeps both sides of two claims leaves a key twice, which reads as no record anyone may run under (`host`); of two
# pauses made before a sync, the later stands. Every space merges the file by union (.gitattributes, sync.MERGE_RULES).
# The third runner is the cloud (`claim_cloud`): two scheduled tasks on claude.ai under CLOUD_MACHINE, which no
# computer's id can take, with their ids, the person's zone, the repositories they attach and the connector grant a
# line each after the claim's four; every computer steps aside for it (`gate`), and a cloud run asks `cloud_gate`.
HOST = Path("_meta/state/routine-host.yaml")
RUNNERS = ("os", "harness", "cloud")
CLAIM_KEYS = ("machine", "name", "runner", "since")
CLOUD_KEYS = ("tasks", "timezone", "roots", "grant")   # the cloud's claim after the four: a cloud record has all
GRANT_KEYS = ("read", "write", "send")
MACHINE = re.compile(r"m-[0-9A-Za-z-]{1,64}")
CLOUD_MACHINE = "m-cloud"   # a computer's id is m- and eight hex digits (`sync.hostname`), never this
CLOUD_NAME = "the cloud"
TASK_ID = re.compile(r"trig_[A-Za-z0-9]{8,64}")   # a scheduled task on claude.ai
ZONE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{0,31}(?:/[A-Za-z0-9_+-]{1,32}){0,2}")   # an IANA zone: Europe/Amsterdam, UTC
# A repository on GitHub, owner/name: neither part dots alone, the rule `ghrest.SEGMENT` reads an origin by.
SLUG = re.compile(r"(?!\.+/)[A-Za-z0-9_.-]+/(?!\.+$)[A-Za-z0-9_.-]+")
CONFLICT = re.compile(r"^(?:<{7}|={7}|>{7})", re.M)   # what a merge git left half done puts in a file
UNCLEAR = "which computer runs the routines is unclear"
HARNESS = "they run as the app's scheduled tasks"
CLOUD = "they run in the cloud"
NO_CLOUD = "nothing says your routines run in the cloud"


def this_machine() -> str:
    """This computer as the record names it: an id made once and kept in `~/.spaces/machine-id` (Minor 6), so a
    computer whose name follows the network still matches its own record. Made from today's value, its part of the
    sync branch names (`sync.hostname`), so every record written before still matches; a file that holds no id is
    made again, and one that cannot be written leaves the id as it is made."""
    import sync
    kept = Path.home() / ".spaces" / "machine-id"
    try:
        said = kept.read_text(encoding="utf-8").strip()
        if MACHINE.fullmatch(said):
            return said
    except (OSError, UnicodeDecodeError):
        pass
    made = sync.hostname()
    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(made + "\n", encoding="utf-8", newline="\n")
    except OSError:
        pass
    return made


def machine_name() -> str:
    """This computer's own name, the one its person knows it by, for the lines that say where the routines run."""
    import socket
    return socket.gethostname().split(".")[0]


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _moment(value) -> dt.datetime | None:
    """A moment as the record holds it (ISO; one written without a zone is this computer's own time); None for
    anything else."""
    if isinstance(value, str):
        text = value.strip()
        try:
            value = dt.datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text)
        except ValueError:
            return None
    if not isinstance(value, dt.datetime):
        return None
    return value if value.tzinfo else value.astimezone()


class _OneRecord(yaml.SafeLoader):
    """YAML that refuses a key twice: what a merge that kept both sides of two records leaves. The pause alone may
    come twice (two computers paused before either synced): the later moment stands, and one that is no moment is
    kept, so the record reads as unclear."""


def _keys_once(loader, node, deep=False):
    seen, pauses = set(), []
    for key, value in node.value:
        name = loader.construct_object(key, deep=deep)
        if not isinstance(name, str) or (name in seen and name != "paused_until"):
            raise yaml.constructor.ConstructorError(None, None, "a key twice", key.start_mark)
        seen.add(name)
        if name == "paused_until":
            pauses.append(loader.construct_object(value, deep=deep))
    mapping = loader.construct_mapping(node, deep=deep)
    if len(pauses) > 1:
        moments = [_moment(p) for p in pauses if p is not None]
        bad = [p for p in pauses if p is not None and _moment(p) is None]
        mapping["paused_until"] = bad[0] if bad else (max(moments).isoformat() if moments else None)
    return mapping


_OneRecord.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _keys_once)


def _cloud_value(key: str, value):
    """One of the cloud's keys as the record keeps it; None for a value not of its shape. `tasks`: the two task ids
    by chain (`sweep`, `daily`); `timezone`: the person's IANA zone; `roots`: the repositories the tasks attach, at
    least one; `grant`: the connector tools by their full names, as `read`, `write` and `send`."""
    import reader
    if key == "tasks":
        ok = isinstance(value, dict) and set(value) == {"sweep", "daily"} and all(
            isinstance(task, str) and TASK_ID.fullmatch(task) for task in value.values())
        return {"sweep": value["sweep"], "daily": value["daily"]} if ok else None
    if key == "timezone":
        return value if isinstance(value, str) and ZONE.fullmatch(value) else None
    if key == "roots":
        ok = isinstance(value, list) and bool(value) and all(isinstance(r, str) and SLUG.fullmatch(r) for r in value)
        return list(dict.fromkeys(value)) if ok else None
    if key == "grant":
        tool = lambda name: isinstance(name, str) and reader.CONNECTOR_TOOL.fullmatch(name)   # noqa: E731
        ok = isinstance(value, dict) and all(isinstance(value.get(part), list) and all(map(tool, value[part]))
                                             for part in GRANT_KEYS)
        return {part: list(dict.fromkeys(value[part])) for part in GRANT_KEYS} if ok else None
    return None


def host(workspace) -> dict | None:
    """Where the routines run: {"machine", "name", "runner", "since", "paused_until"} (the pause an ISO moment, or
    None), with the cloud's keys (`CLOUD_KEYS`: "tasks", "timezone", "roots", "grant") only when they are written; None
    when nothing is recorded. A file that is not one record (a key twice, but for the pause, of which the later
    stands; a merge left half done, a machine or runner that is none, a pause that is no moment, a cloud key of the
    wrong shape, the cloud without all four, or the cloud's machine and runner apart) is {"unclear": True}: nothing
    runs under it."""
    try:
        text = (Path(workspace) / HOST).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return {"unclear": True}
    try:
        data = None if CONFLICT.search(text) else yaml.load(text, Loader=_OneRecord)
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        return {"unclear": True}
    machine, runner, name, paused = data.get("machine"), data.get("runner"), data.get("name"), data.get("paused_until")
    until = None if paused is None else _moment(paused)
    if not (isinstance(machine, str) and MACHINE.fullmatch(machine) and runner in RUNNERS) or (paused is not None and until is None):
        return {"unclear": True}
    cloud = {key: _cloud_value(key, data[key]) for key in CLOUD_KEYS if key in data}
    if (None in cloud.values() or (runner == "cloud") != (machine == CLOUD_MACHINE)
            or (runner == "cloud" and len(cloud) < len(CLOUD_KEYS))):
        return {"unclear": True}
    return {"machine": machine, "name": _one_line(name, 60) if isinstance(name, str) else "", "runner": runner,
            "since": _one_line(data.get("since"), 40), **cloud,
            "paused_until": until.isoformat(timespec="seconds") if until else None}


def _write_host(workspace, record: dict) -> dict:
    """The record written whole, each value on its own line as JSON (which YAML reads as it is): the claim's four, the
    cloud's keys when the record has them, then the pause."""
    lines = ["# Where this workspace's routines run: one computer, one runner. Written by onboard.py, never by a routine."]
    lines += [f"{key}: {json.dumps(record.get(key), ensure_ascii=False)}" for key in CLAIM_KEYS]
    lines += [f"{key}: {json.dumps(record[key], ensure_ascii=False)}" for key in CLOUD_KEYS if key in record]
    lines += ["# The pause, a line apart from the claim: a pause on one computer and a move on another merge into one.",
              f"paused_until: {json.dumps(record.get('paused_until'))}"]
    path = Path(workspace) / HOST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return host(workspace)


def paused_until(record, now) -> dt.datetime | None:
    """When a pause still to come at `now` ends; None when the routines are not paused then."""
    until = _moment((record or {}).get("paused_until"))
    now = now if now.tzinfo else now.astimezone()
    return until if until is not None and until > now else None


def claim_host(workspace, runner: str, now) -> dict:
    """The routines run on this computer through `runner` ("os" or "harness") from `now` on: every other computer's
    runs stop by themselves (`gate`), and the cloud's tasks with them (`cloud_gate`). A pause still to come is kept:
    a move is no resume."""
    if runner == "cloud":
        raise ValueError("the cloud is no computer: claim it with claim_cloud, which records its tasks, zone, "
                         "repositories and grant")
    if runner not in RUNNERS:
        raise ValueError(f"a computer's runner is os or harness, not {runner!r}")
    old = host(workspace) or {}
    keep = old.get("paused_until") if paused_until(old, now) else None
    name = _one_line(machine_name(), 60) or "this computer"
    return _write_host(workspace, {"machine": this_machine(), "name": name, "runner": runner,
                                   "since": (now if now.tzinfo else now.astimezone()).isoformat(timespec="seconds"),
                                   "paused_until": keep})


def claim_cloud(workspace, tasks: dict, timezone: str, roots: list, grant: dict, now) -> dict:
    """The routines run in the cloud from `now` on, as the two scheduled tasks `tasks` names ({"sweep", "daily"}: their
    ids), in the person's `timezone`, attaching the repositories `roots` (owner/name), under the connector `grant`
    ({"read", "write", "send"}: full tool names). Every computer's runs stop by themselves (`gate`). A pause still to
    come is kept: a move is no resume. ValueError, writing nothing, for a value not of its shape (`_cloud_value`)."""
    given = {"tasks": tasks, "timezone": timezone, "roots": roots, "grant": grant}
    cloud = {key: _cloud_value(key, value) for key, value in given.items()}
    wrong = [key for key, value in cloud.items() if value is None]
    if wrong:   # one sentence, for one key or several: onboarding may show it to the person
        listed = wrong[0] if len(wrong) == 1 else f"{', '.join(wrong[:-1])} and {wrong[-1]}"
        raise ValueError(f"The cloud's record needs its {listed} in the shape the record keeps.")
    old = host(workspace) or {}
    keep = old.get("paused_until") if paused_until(old, now) else None
    return _write_host(workspace, {"machine": CLOUD_MACHINE, "name": CLOUD_NAME, "runner": "cloud",
                                   "since": (now if now.tzinfo else now.astimezone()).isoformat(timespec="seconds"),
                                   **cloud, "paused_until": keep})


def set_pause(workspace, until) -> dict | None:
    """The record, every key of it kept, with its pause set to `until` (a moment; None resumes); None when there is no
    record to set it in."""
    record = host(workspace)
    if record is None or record.get("unclear"):
        return None
    when = _moment(until) if until is not None else None
    return _write_host(workspace, {**record, "paused_until": when.isoformat(timespec="seconds") if when else None})


def _held(workspace, now) -> tuple | None:
    """("away", why) when a run must not start on this computer: the routines run in the cloud, another computer is
    the host, the app's own scheduled tasks run them, or the record is not one; ("paused", why) while paused; None to
    go on."""
    record = host(workspace)
    if record is None:
        return None
    if record.get("unclear"):
        return "away", UNCLEAR
    if record["runner"] == "cloud":   # on any computer, paused or not
        return "away", CLOUD
    if record["machine"] != this_machine():
        return "away", f"runs on {record['name'] or 'another computer'}"
    if record["runner"] != "os":
        return "away", HARNESS
    until = paused_until(record, now)
    if until is not None:
        return "paused", f"paused until {until.astimezone():%Y-%m-%d %H:%M}"
    return None


def gate(workspace, now) -> str | None:
    """Why a routine run must not start here: "runs on <name>" (another computer is the host), "paused until <time>",
    they run in the cloud or as the app's own scheduled tasks, or which computer does is unclear. None to go on.
    `main` asks it first."""
    held = _held(workspace, now)
    return held[1] if held else None


def _in_zone(moment: dt.datetime, zone) -> dt.datetime:
    """`moment` in the IANA zone `zone`; as it was kept, at its own offset, where this computer has no data for the
    zone (Windows without the tzdata package)."""
    try:
        from zoneinfo import ZoneInfo
        return moment.astimezone(ZoneInfo(zone))
    except (ImportError, KeyError, ValueError, TypeError, OSError):   # ZoneInfoNotFoundError is a KeyError
        return moment


def cloud_gate(workspace, now) -> str | None:
    """Why a cloud run must not go on (`cloud.py begin` asks it first): nothing says the routines run in the cloud
    (NO_CLOUD), which computer runs them is unclear (UNCLEAR), "they run on <name>" (a computer's runner), or "paused
    until <time>", said in the person's own zone (the record's `timezone`), since the cloud's clock is UTC. None to go
    on."""
    record = host(workspace)
    if record is None:
        return NO_CLOUD
    if record.get("unclear"):
        return UNCLEAR
    if record["runner"] != "cloud":
        return f"they run on {record['name'] or 'another computer'}"
    until = paused_until(record, now)
    if until is not None:
        return f"paused until {_in_zone(until, record.get('timezone')):%Y-%m-%d %H:%M}"
    return None


# ----- one routine run --------------------------------------------------------------------------
NO_CLAUDE = "Claude Code's command line is not installed on this machine"
BUSY = "another run is busy"


def command(claude: str, settings: Path, roots: list, model: str | None = None) -> list:
    """A routine's command line; its prompt goes on stdin. Only the built-in tools a routine needs
    (`ROUTINE_BUILTINS`; the connectors stay, the profile says which it may call). Every root but the
    workspace (the first) is added as a folder of its own, as given: no shell reads it."""
    argv = [claude, "-p", *REFUSE, *JSON_OUT, "--tools", *ROUTINE_BUILTINS, "--settings", str(settings)]
    first = Path(roots[0]).resolve() if roots else None
    for root in dict.fromkeys(roots[1:] if roots else []):
        if Path(root).resolve() != first:
            argv += ["--add-dir", str(root)]
    if model:
        argv += ["--model", model]
    return argv


def wanted(workspace, meta: dict) -> bool:
    """Whether a routine runs now: one whose frontmatter says `only_while: backfill` runs only while a
    backfill is active (`backfill.active`), one with a condition this tool does not know never runs."""
    condition = meta.get("only_while")
    if condition is None:
        return True
    if condition != "backfill":
        return False
    try:
        import backfill
        return bool(backfill.active(Path(workspace)))
    except Exception:
        return False


def _unwanted(meta: dict) -> str:
    """Why `wanted` holds a run back, in plain words."""
    if meta.get("only_while") == "backfill":
        return "no reading of the history is going on"
    return "it runs only while a condition this tool does not know holds"


PART_ROUTINES = ("sweep-chain", "backfill-night")   # the routines whose prompts read a part of the history


def next_part(workspace, routine_name: str, now=None) -> tuple:
    """(the id of the part of the history this run reads, else None; why none, in plain words): what
    `backfill.due` names at the start of a run that reads parts (PART_ROUTINES). A backfill that cannot be
    read is no part, never a stop."""
    if routine_name not in PART_ROUTINES:
        return None, ""
    try:
        import backfill
        now = now or dt.datetime.now().astimezone()
        part = backfill.due(Path(workspace), now)
        return (part["id"], "") if part else (None, backfill.explain(Path(workspace), now)[1])
    except Exception:
        return None, "How far the history has been read could not be checked just now, so this run reads no part."


def note_start(workspace, part: str | None) -> tuple:
    """(the part the run goes on with, the words for the log). The run is about to read `part`: until it checkpoints,
    says `read` or `unfinished`, it has read nothing and the part is not marked done (`backfill.begin`). When that
    cannot be noted the run goes on with no part, so what it came to is never told to the backfill and a part is
    never finished without a begin; what the agent checkpoints is kept and read on from next time."""
    if not part:
        return None, ""
    try:
        import backfill
        backfill.begin(Path(workspace), part)
    except Exception as exc:
        return None, f"the history's part {part} could not be marked as begun, so this run does not finish it: {exc}\n"
    return part, ""


def note_part(workspace, part: str | None, line: dict, now) -> str:
    """What the run that read `part` came to goes to the backfill (`backfill.after_run`); the words for the
    log when that could not be done. The routine's own outcome is already written. A run that stopped on a
    fault of its own is told the same way (`stopped`): it leaves the part as it was, begun and not done."""
    if not part:
        return ""
    try:
        import backfill
        backfill.after_run(Path(workspace), part, line, now=now)
    except Exception as exc:
        return f"the history's part {part} could not be noted: {exc}\n"
    return ""


def _stopped(why: str) -> dict:
    return {"outcome": "stopped", "why": why, "resets_at": None, "usage": dict.fromkeys(USAGE_KEYS, 0)}


def main(argv=None, run=subprocess.run) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--locked"]:
        held = locked(Path(argv[1]))
        if held:
            print(yaml.safe_dump(held, sort_keys=False), end="")
        return 0
    if argv[:1] == ["--probe"]:
        return probe(Path(argv[1]), run=run)
    prompt = Path(argv[0]).resolve()
    workspace = prompt.parents[3]   # <workspace>/_meta/state/routines/<routine>.md
    meta, body = split_prompt(prompt.read_text(encoding="utf-8"))
    name = spaceyaml.load(workspace).name
    log = Path.home() / ".spaces" / "logs" / f"{prompt.stem}-{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = _now()
    held = _held(workspace, now)   # first (F-11): one computer, one runner, and the pause, before the history is read
    if held:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{now.isoformat(timespec='seconds')} {prompt.stem} not run: {held[1]}\n")
        if held[0] == "paused":   # another computer's runs leave no line: that computer's own is the one that counts
            write_outcome(workspace, prompt.stem, {**_stopped(held[1]), "outcome": "paused"}, now)
        return 0
    if not wanted(workspace, meta):   # its condition does not hold: a line says so, and no claude starts
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{now.isoformat(timespec='seconds')} {prompt.stem} not run: nothing to read: "
                     f"{_unwanted(meta)}\n")
        return 0
    part, why = next_part(workspace, prompt.stem)
    if meta.get("only_while") == "backfill" and part is None:   # a run would spend its first tokens to find that out
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n== {dt.datetime.now().isoformat(timespec='seconds')} {prompt.stem}\n"
                     f"nothing to read now: {why}\nexit 0\n")
        return 0
    doctor.use_tools()
    claude = find_claude()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n== {dt.datetime.now().isoformat(timespec='seconds')} {prompt.stem}\n")
        if claude is None:
            write_outcome(workspace, prompt.stem, _stopped(NO_CLAUDE))
            fh.write("claude is not installed or not on PATH: install Claude Code, then run onboard "
                     "--os-routines again\nexit 127\n")
            return 127
        if not take_lock(workspace, prompt.stem, part):
            held = locked(workspace) or {}
            write_outcome(workspace, prompt.stem, _stopped(BUSY))
            fh.write(f"{BUSY}: {held.get('routine', 'a routine')} since {held.get('started', 'a moment ago')}\n"
                     f"outcome stopped: {BUSY}\nexit 0\n")
            return 0
        try:
            return _run(workspace, prompt, body, name, claude, fh, run, part)
        finally:
            release_lock(workspace, prompt.stem)


def _run(workspace: Path, prompt: Path, body: str, name: str, claude: str, fh, run, part: str | None = None) -> int:
    """One run under the lock: the grant's items, the profile, claude (in `routine_env`), then what it came to.
    A fault of its own still ends in an outcome line (`stopped`, why in plain words) and an exit line. The part
    of the history the run reads (`part`) is told it began and what it came to, a fault included."""
    told = False
    try:
        part, words = note_start(workspace, part)
        fh.write(words)
        roots = roots_of(workspace)
        got = grant(workspace)
        write_items(workspace, reconnect=got["reconnect"])
        settings = write_settings(workspace, prompt, body, name, got, roots)
        res, timed_out = None, False
        try:
            res = run(command(claude, settings, roots), input=body, cwd=str(workspace),
                      env=routine_env(os.environ, got["shell"]), capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=3600)
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError as exc:
            fh.write(f"claude did not start: {exc.strerror or exc}\n")
        now = dt.datetime.now().astimezone()
        line = outcome(res, timed_out, now=now)
        line["limits"] = limits_of(res)
        seen = init_of(res)
        if seen is not None:
            refresh_probe(seen)
            got = grant(workspace)
        write_items(workspace, claude_signin={"auth": True, "finished": False}.get(line["outcome"]),
                    reconnect=got["reconnect"])
        write_outcome(workspace, prompt.stem, line, now)
        told = True
        noted = note_part(workspace, part, line, now)   # never raises
        code = (0 if line["outcome"] == "finished" else 124 if timed_out else 127 if res is None
                else res.returncode or 1)
        resets = f" (resets {line['resets_at']})" if line["resets_at"] else ""
        fh.write(log_summary(res) + noted + f"outcome {line['outcome']}" + (f": {line['why']}" if line["why"] else "")
                 + f"{resets}\nexit {code}\n")
        return code
    except Exception as exc:   # noqa: BLE001 - the run's own line says it stopped, whatever stopped it
        why = f"the routine stopped on a fault of its own ({type(exc).__name__})"
        if not told:
            write_outcome(workspace, prompt.stem, _stopped(why))
            note_part(workspace, part, _stopped(why), dt.datetime.now().astimezone())
        fh.write(f"outcome stopped: {why}\nexit 1\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
