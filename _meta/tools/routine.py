"""routine: run one of the person's routines headless through Claude Code's command line, so the
routines outlive the session that set them up. The OS scheduler starts it at the routine's times
(`onboard.py <workspace> --os-routines`).

The prompt is the rendered routine file without its frontmatter. Claude runs in the workspace
with its permission profile plus the routine profile this tool writes for each run
(`--settings`, see `routine_settings`); nobody is there to answer a permission prompt, so anything
that would ask is refused and the routine goes on without it. Output and the exit code go to
`~/.spaces/logs/<routine>-<space>.log`.

`probe_tools` asks the command line itself what it would let a run call (its `init` event), since
the tool names a headless run sees are not the ones the app that set it up sees; `reader.py` runs
the read-only runs that open the content a routine did not write.

A routine reads swept mail, chat and transcripts, which anyone can write. The routine profile
keeps such a record from turning into code on this machine: no edit to the machinery, the
instructions or git (`PROTECTED_EDITS`), and only the tools the routines run (`ROUTINE_TOOLS`), by
their exact paths; every other tool script in a space is refused by name.

    python routine.py <workspace>/_meta/state/routines/<routine>.md
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

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
# morning-view, outbox-reconcile, sync), with the two read-only ones every session may start
# with (needs_you.py, as AGENTS.md says) or search with (search.py). Never sign, reply, set-up or
# template sync: those are the person's, or run inside chain.py.
ROUTINE_TOOLS = ("brief.py", "chain.py", "check.py", "classify.py", "distill.py", "landing.py",
                 "librarian.py", "morning_view.py", "needs_you.py", "outbox_reconcile.py",
                 "owner_email.py", "registry.py", "release.py", "request.py", "route.py", "search.py",
                 "status.py", "sweep.py", "sync.py", "tests_run.py", "tracker.py")
# What no routine edits, in any space on this machine: the code that runs (tools, migrations,
# tests and their configuration), the instructions every session loads, git, the rules and the
# routine prompts. A path rule for Edit covers Write and NotebookEdit too; Claude Code reads no
# path rule written for Write.
PROTECTED_EDITS = ("_meta/tools/**", "_meta/migrations/**", "_meta/tests/**", "_meta/templates/**",
                   "_meta/state/routines/**", ".claude/**", ".github/**", ".git/**", ".gitmodules",
                   ".mcp.json", "rules.md", "space.yaml", "CLAUDE.md", "AGENTS.md", "conftest.py",
                   "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
# Rooted at each space: a folder name a subject could also have.
PROTECTED_IN_ROOT = ("identity/**",)
READ_ONLY = ("Bash(gh pr view *)", "Bash(gh pr list *)", "Bash(git status *)", "Bash(git log *)",
             "Bash(git diff *)", "Bash(git show *)")


# What a routine does not inherit from the session that set it up: another provider, another
# endpoint, an API key or token. A routine bills the person's claude.ai sign-in, so the sign-in
# that is checked, and the one that is started, is the one it will find without these.
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_OAUTH_TOKEN")


def clean_env(env: dict) -> dict:
    """A copy of the environment as a routine sees it: without STRIPPED_ENV."""
    return {key: value for key, value in env.items() if key.upper() not in STRIPPED_ENV}   # Windows ignores case


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
    argv = [claude, "-p", *REFUSE, *JSON_OUT]
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


def routine_settings(workspace, body: str, roots=None) -> dict:
    """The profile one routine run adds to the space's own (`claude --settings`). Deny rules win
    over every allow rule in every scope, so the space's own profile cannot widen these."""
    workspace = Path(workspace).resolve()
    roots = [Path(r).resolve() for r in (roots or [workspace])]
    pys = interpreters(body, roots)
    # Each root as it resolves and as the prompt spells it (a rule matches the text as written),
    # only when the spelling is one of the roots.
    named = [m for m in re.findall(r'([^\s`"<>]+)/_meta/tools/[a-z_]+\.py', body)
             if "/" in m and Path(m).is_absolute() and Path(m).resolve() in roots]
    places = list(dict.fromkeys([r.as_posix() for r in roots] + named))
    allow = [f"Bash({py} _meta/tools/{tool} *)" for py in pys for tool in ROUTINE_TOOLS]
    allow += [f"Bash({py} {place}/_meta/tools/{tool} *)"
              for place in places for py in pys for tool in ROUTINE_TOOLS]
    others = sorted({p.name for root in [workspace, *roots] for p in (root / "_meta" / "tools").glob("*.py")}
                    - set(ROUTINE_TOOLS))
    deny = [f"Edit(//**/{pattern})" for pattern in PROTECTED_EDITS]
    deny += [f"Edit(/{rule_path(root)}/{pattern})" for root in roots for pattern in PROTECTED_IN_ROOT]
    deny += [f"Edit(~/{folder}/**)" for folder in (".claude", ".spaces")]
    deny += [f"Bash(*_meta/tools/{tool}*)" for tool in others]
    # `-m` puts the working folder first on the import path, so a planted module would run;
    # `--output` lets a read-only git command write a file.
    deny += ["Bash(python -m *)", "Bash(python3 -m *)", "Bash(git *--output*)"]
    return {"permissions": {"allow": allow + list(READ_ONLY), "deny": deny}}


def write_settings(workspace, prompt: Path, body: str, name: str) -> Path:
    """The routine profile for this run, written again every run, outside every space."""
    path = Path.home() / ".spaces" / "routines" / f"{prompt.stem}-{name}.settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = routine_settings(workspace, body, roots_of(Path(workspace)))
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def main(argv=None, run=subprocess.run) -> int:
    argv = sys.argv[1:] if argv is None else argv
    prompt = Path(argv[0]).resolve()
    workspace = prompt.parents[3]   # <workspace>/_meta/state/routines/<routine>.md
    text = prompt.read_text(encoding="utf-8")
    body = text.split("---", 2)[2].lstrip() if text.startswith("---") else text
    name = spaceyaml.load(workspace).name
    log = Path.home() / ".spaces" / "logs" / f"{prompt.stem}-{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    doctor.use_tools()
    claude = find_claude()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n== {dt.datetime.now().isoformat(timespec='seconds')} {prompt.stem}\n")
        if claude is None:
            fh.write("claude is not installed or not on PATH: install Claude Code, then run onboard "
                     "--os-routines again\nexit 127\n")
            return 127
        settings = write_settings(workspace, prompt, body, name)
        try:
            res = run([claude, "-p", *REFUSE, "--settings", str(settings)], input=body, cwd=str(workspace),
                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        except subprocess.TimeoutExpired:
            fh.write("stopped after an hour\nexit 124\n")
            return 124
        fh.write((res.stdout or "") + (res.stderr or "") + f"\nexit {res.returncode}\n")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
