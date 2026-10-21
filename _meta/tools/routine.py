"""routine: run one of the person's routines headless through Claude Code's command line, so the
routines outlive the session that set them up. The OS scheduler starts it at the routine's times
(`onboard.py <workspace> --os-routines`).

The prompt is the rendered routine file without its frontmatter. Claude runs in the workspace
with its permission profile plus the routine profile this tool writes for each run
(`--settings`, see `routine_settings`); nobody is there to answer a permission prompt, so anything
that would ask is refused and the routine goes on without it. Output and the exit code go to
`~/.spaces/logs/<routine>-<space>.log`.

A routine reads swept mail, chat and transcripts, which anyone can write. The routine profile
keeps such a record from turning into code on this machine: no edit to the machinery, the
instructions or git (`PROTECTED_EDITS`), and only the tools the routines run (`ROUTINE_TOOLS`), by
their exact paths; every other tool script in a space is refused by name.

    python routine.py <workspace>/_meta/state/routines/<routine>.md
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402
import spaceyaml  # noqa: E402

CLAUDE = ["-p", "--permission-mode", "acceptEdits", "--permission-prompts", "none"]

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


def find_claude() -> str | None:
    """Claude Code's command: on PATH, else where its own installer puts it."""
    found = shutil.which("claude")
    if found:
        return found
    for folder in (Path.home() / ".local" / "bin", Path.home() / ".claude" / "local"):
        for name in ("claude", "claude.exe"):   # a scheduled task may start without the user's PATH
            if (folder / name).exists():
                return str(folder / name)
    return None


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
            res = run([claude, *CLAUDE, "--settings", str(settings)], input=body, cwd=str(workspace),
                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        except subprocess.TimeoutExpired:
            fh.write("stopped after an hour\nexit 124\n")
            return 124
        fh.write((res.stdout or "") + (res.stderr or "") + f"\nexit {res.returncode}\n")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
