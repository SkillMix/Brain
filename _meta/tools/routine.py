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
writes of issues and comments, and the send tools of the person's own mailbox behind `send_guard.py`,
which lets mail go to the person alone, once the probe proved it in the shell Claude Code will run it with. Every other
connector tool the probe has ever seen is denied. The profile keeps every hook on
(`"disableAllHooks": false`).

A signature is a named person's verdict, so a routine never makes one or acts on one in the person's name:
- No edit or write it makes leaves a page signed (`edit_check`, a PreToolUse hook on every edit): no frontmatter
  with `status: stable` or a signature field, no change to a page that is signed, and no write to a space's own
  `log.md`, where sign.py writes its narrative lines. The same hook reads the deny rules again on the path the edit
  really touches (a link, a junction, a short 8.3 name or another case resolved), and refuses an edit it cannot
  replay as Claude Code will make it. A page whose frontmatter does not parse is no signed page: a routine may
  repair it, as long as what the edit leaves parses.
- No command it runs executes a person's verdict or word (`PERSON_ONLY`, `person_only_check`, a second hook on
  Bash that reads the words as the shell will): no `sign.py`, no `chain.py virtual` or `--verdicts`, no
  `route.py answer` or `--out`, no `sign_questions.py answer`, no `status.py close`; of chain.py only its four
  chains (`sweep`, `daily`, `gate`, `spaces`) are allowed, one rule each. `librarian.py apply` is no person's
  word: it applies only a change the librarian itself makes on the tree as it is now, move by move checked, and a
  change of owner still waits for the owners' yes (`librarian.apply`), so a routine applies what a second agent
  approved.
- The files only a tool writes (`TOOL_WRITTEN`: the queue, the held and the signals, the requests and answers
  waiting to leave, the routing, release, request and classification ledgers, the conflict notes sent, the
  registry, the lessons, the librarian's proposals, the verdict ledgers and the person's verdict store) are denied
  to its edits; its tools keep writing them, and a cloud run lands them (the verdict store aside: a cloud run
  never signs, so the sync never lands a change to it from one).
- Every command it runs sees `SPACES_ROUTINE` (`ROUTINE_MARK`), set in the run's environment and its profile, so a
  tool can tell a routine from the person (`in_routine`, which also reads a cloud run's marker); no command a
  routine may run can unset it.

The commands a routine run starts on its own (`claude`, `gh`, `git`, `ps`) are found by `which`: never in the
current folder or in a space on this machine, where a record could have planted `gh.bat`. On Windows the
import sets `NoDefaultCurrentDirectoryInExePath`, so no program this process starts by a bare name is looked
for in the current folder either.

    python routine.py <workspace>/_meta/state/routines/<routine>.md
    python routine.py --probe <workspace>    # what a scheduled run sees, and its checks: ~/.spaces/probe.json
    python routine.py --locked <workspace>   # the run that holds the workspace's lock, or nothing
    python -I routine.py --edit-guard        # the hook on edits: the event on stdin; exit 0 allows, 2 blocks
    python -I routine.py --person-only       # the second hook on Bash, the same way
"""
from __future__ import annotations

import datetime as dt
import json
import os
import posixpath
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

# Windows looks for a program started by a bare name (`git`, `gh`, `schtasks`) in the current folder first unless
# this is set; set here, it holds for every program this process and its children start (finding 10).
NO_CWD_LOOKUP = "NoDefaultCurrentDirectoryInExePath"
if os.name == "nt":
    os.environ.setdefault(NO_CWD_LOOKUP, "1")

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
PROTECTED_EDITS += ("_meta/plugins/**", ".claude-plugin/**")   # a plugin a space would load, and what offers it
PROTECTED_EDITS += (".git",)   # a mount's .git is a file naming its git folder, whose config git runs; .git/** is a folder
# Setup's history reading runs inside the sweep chain (step 0); its own line, away from the list above.
ROUTINE_TOOLS += ("backfill.py",)
# A cloud run's proof line (`_meta/templates/cloud/`). Of cloud.py a routine runs that verb alone (ROUTINE_VERBS): its
# begin writes a cloud run's marker and its trigger the task a move records, so CLOUD_ONLY are denied however they
# are spelled, whatever comes before the script and between it and the word.
ROUTINE_TOOLS += ("cloud.py",)
# Of chain.py a routine runs its four chains alone, one allow rule each: `virtual` signs on a fixture's behalf and
# `--verdicts` carries a person's answers, so no allow shape reaches them (PERSON_ONLY denies them too).
ROUTINE_VERBS = {"cloud.py": ("proof-line",), "chain.py": ("sweep", "daily", "gate", "spaces")}
CLOUD_ONLY = ("begin", "trigger")
# Of route.py a routine never picks where a pack is written; its `answer` (a routing question answered as the person)
# is PERSON_ONLY's. The option follows a space, so a subject or a path that holds the word passes.
ROUTE_NEVER = ("--out",)
READ_ONLY =("Bash(gh pr view *)", "Bash(gh pr list *)", "Bash(git status *)", "Bash(git log *)",
             "Bash(git diff *)", "Bash(git show *)")
# The repository feed's reads of GitHub (the sweep skill), through REST as a cloud run must: `gh api` and one word,
# its endpoint, so each is a read, never GraphQL. Anything after that word is refused (REST_ONE_WORD), as a cloud
# run's guard refuses it (`cloud.GH_DENY`), however a flag is spelled. The query goes in quotes that open before its
# `?` (`search/issues'?q=...'`), since zsh, a Mac's login shell, refuses a `?` outside quotes that matches no file.
REST_READS = ("Bash(gh api search/issues*)", "Bash(gh api repos/*/pulls/*)")
# GraphQL by its endpoint, never by a word inside a repository's name (`acme/graphql-gateway` is read like any other):
# the endpoint word, a full URL (gh takes any endpoint holding `://` as one; no read is one), an endpoint that starts
# with `/` or `.` (gh cleans `./graphql` and `.//graphql` to the word; every read starts `repos/` or `search/`), a path
# that climbs back out of the word an allow rule starts it with, and a percent escape, which could spell that climb.
GRAPHQL = ("Bash(gh api graphql*)", "Bash(gh api /*)", "Bash(gh api .*)", "Bash(gh api *://*)", "Bash(gh api */../*)",
           "Bash(gh api *%*)")
REST_ONE_WORD = ("Bash(gh api * *)", "Bash(gh api *\t*)", *GRAPHQL)
# Of sync.py the shell never runs `--merge-latest`, the driver git runs itself mid-merge, outside every hook: given
# any file it writes it, past the edit rules. sync.py is allowed only in exact commands, the landing (SYNC_LEGS on a
# computer, `--cloud <chain>` in the cloud), so no word the shell expands (`{--m,X}erge-latest`, `--me[r]ge-latest`)
# gets through. These denies are the text-only backstop: the flag abbreviated (argparse takes `--mer`) or with a
# quote the shell drops (`--'merge'-latest`); no routine passes sync.py a quoted word.
SYNC_NEVER = ("--mer", "'", '"')
# A computer's landing (the sweep chain's release leg, the sync skill's land now), each root as itself.
SYNC_LEGS = ("", " --release-leg")
CLOUD_CHAINS = ("sweep",)   # the chain a cloud run is (`cloud.CHAINS`): the daily stays on the computer
# The built-in tools a routine has (`--tools`, Ruling R30): the shell its tools run in, the files, and
# ToolSearch, which loads a connector's tools. Web, PowerShell, agents and schedules are left out; the
# profile denies them by name too (`reader.DENIED`), so one a later version adds under such a name stays out.
# `Task` is there for the second agent that checks route and distill decisions; a subagent runs under
# the same profile and hooks.
ROUTINE_BUILTINS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep", "ToolSearch", "Task")

# What only a tool writes, and no routine by hand: what leaves and when (the queue, the held, the signals and the
# ledgers release and route check them against), the gate's and the registry's control state (classifications,
# registry notes, the registry, the access list, the template's origin), the lessons every brief reads, the
# librarian's proposals and the verdict ledgers. Python writes are no Edit calls, so the tools keep writing them,
# in a cloud run too; unlike PROTECTED_EDITS they are landed by every sync (`sync.protected_paths` never reads this
# list), so only the routine's own edits are refused. Findings 1, 4 and 24.
TOOL_WRITTEN = ("_outbound/queue/**", "_outbound/held/**", "_outbound/signals/**", "_meta/state/routing.jsonl",
                "_meta/state/released.jsonl", "_meta/state/queued.jsonl", "_meta/state/classify.jsonl",
                "_meta/state/registry-notes.jsonl", "_meta/subjects.yaml", "_meta/lessons.md",
                "_meta/access/resources.yaml", "_meta/state/template-origin.yaml", "_meta/state/librarian/**",
                "_meta/state/check-verdicts.jsonl", "_meta/state/sign-questions.jsonl")
# The corridor and the answers: requests and answers waiting to leave and the logs release.ship_requests reads as proof
# that request.py asked or answered (finding 38), the conflict notes check.py already sent, and the person's verdict
# store, which sign.py writes and every tool acting on an answer reads (finding 7). Landed by every sync, as above.
TOOL_WRITTEN += ("_outbound/requests/**", "_outbound/answers/**", "_meta/state/requests.jsonl",
                 "_meta/state/requests-asked.jsonl", "_meta/state/conflict-notes.jsonl", "_meta/state/verdicts.jsonl")
# What the agent's own upkeep reads back as fact: the health rows the tools report and retry from (classify's waits,
# the mount keys'), the brain's key requests and its record of the deploy keys it attached (`onboard`), which a hand
# edit could otherwise forge or hide. Landed by every sync, as above.
TOOL_WRITTEN += ("_meta/state/health/**", "_meta/state/private/key-requests/**", "_meta/state/mount-keys-attached.yaml")
# Rooted at each space: its log, where sign.py writes a narrative line for every verdict and note and the fingerprint
# lines another space's landing reads, and every other tool its one line. The answers themselves are read from
# `_meta/state/verdicts.jsonl` (`status.verdicts`), never from this log.
TOOL_WRITTEN_IN_ROOT = ("log.md",)
# What Windows runs by its name, or Python at its start (a `.pth` in a site folder) (finding 10). A `.pth` or a
# `.lnk` is never written anywhere (EXECUTABLE_EVERYWHERE): Python reads a `.pth` from any folder it adds, and a
# shortcut opens whatever it points at. The others are refused where a program is looked for or the machinery
# lives (`executable_place`): a space's root (a routine's working folder, which Windows searched first for a bare
# name), anything under `_meta/`, `.claude/` or `.github/`, and every folder on PATH. A chart's `.js` or a script in
# a desk folder or an attached codebase is no program anything here starts by its name: `which` never takes one from
# a space, and on Windows no program is looked for in the current folder (NO_CWD_LOOKUP). Denied by rule
# (`executable_rules`) and refused again by the edit guard, which ignores case (`edit_check`).
EXECUTABLE_SUFFIXES = (".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                       ".msc", ".scr", ".pif", ".lnk", ".dll", ".cpl", ".hta", ".msi", ".reg", ".pth")
EXECUTABLE_EVERYWHERE = (".pth", ".lnk")
MACHINERY_FOLDERS = ("_meta", ".claude", ".github")   # the folders, at any depth, where no program is written
# The commands that execute a person's verdict or word, which a routine never runs (finding 5): of each tool, the
# verbs (a word alone) and the options (a word that starts with one, as argparse takes an abbreviation; an
# abbreviation of three letters or more counts too). `None` refuses the tool whole. The routines' text deny rules
# say the same (`PERSON_ONLY_TEXT`), and `person_only_check` reads the words as the shell will, quotes taken off.
# librarian.py is none of them: its `apply` acts only on a change the librarian derives itself from the tree as it
# is now (`librarian._stored`), every move checked (`check_moves`, `_check_registry`) and a change of owner turned
# into the owners' question, so the reviewer's name is no authority to keep from a routine. Its `answer` is an
# owner's yes, which only sign.py calls, and which refuses itself inside a routine (`librarian.answer`).
PERSON_ONLY = {"sign.py": None,
               "chain.py": {"verbs": ("virtual",), "options": ("--verdicts",)},
               "route.py": {"verbs": ("answer",), "options": ()},
               "sign_questions.py": {"verbs": ("answer",), "options": ()},
               "status.py": {"verbs": ("close",), "options": ()},
               # withdrawing what a person signed or released is their word too (the release skill)
               "landing.py": {"verbs": ("retract",), "options": ()},
               "release.py": {"verbs": ("retract", "pull-back"), "options": ()}}
# The text-only backstop, for Claude Code's own rule match and the Bash guard, and all a cloud task has of this
# (`cloud.disallowed`: no hook runs there). Each verb follows a space, as ATTENDED_ASK_OPT_IN's do, and a verb is
# followed by one too or ends the command (`person_only_rules`), so a root or a subject whose path or id holds the
# word (`/home/ada/virtual/ws`, `--subject answer-desk`) never blocks a chain; an option is a word that starts with
# it (`--verd`, as argparse takes an abbreviation); a question's answer is the verb with its id after it, so a
# question's own words still pass. A spliced spelling (`an''swer`, a tab for the space) is the hook's to refuse on
# the computer (`person_only_check`) and the cloud's own rules' (`cloud.PERSON_ONLY_SPLICE`).
PERSON_ONLY_TEXT = ("chain.py* virtual ", "chain.py* --verd", "route.py* answer ", "sign_questions.py* answer q-",
                    "status.py* close ", "landing.py* retract ", "release.py* retract ", "release.py* pull-back ")
# An attended session runs these commands without a prompt: `submit` in the chat is the person's word for a
# signature, a question's answer or a close, and a click after it would only repeat it. A person who wants that click
# anyway adds these `ask` rules to their own `.claude/settings.local.json` (SECURITY.md shows them). Each verb follows a
# space, so a root whose path holds the word (`/home/ada/virtual/ws`) never meets them, nor does any command a routine
# prompt names. A quoted or globbed spelling can slip past a text rule, so they are a speed bump, not a boundary.
# `librarian.py apply` is not among them: the daily chain runs it after a second agent's review, and a routine
# loads the person's settings.local.json too, where an ask rule would refuse it (no one is there to answer).
ATTENDED_ASK_OPT_IN = tuple(f"Bash(*_meta/tools/{text}*)"
                            for text in ("sign.py* apply", "sign.py* --verd", "chain.py* virtual", "chain.py* --verd",
                                         "route.py* answer", "sign_questions.py* answer q-", "status.py* close ",
                                         "landing.py* retract ", "release.py* retract ", "release.py* pull-back "))


def person_only_rules() -> list:
    """The deny rules PERSON_ONLY_TEXT gives: `Bash(*_meta/tools/<text>*)` for each, and for a verb that a space
    follows (`chain.py* virtual `) also the command that ends with the verb (`Bash(*_meta/tools/chain.py* virtual)`)."""
    rules = [f"Bash(*_meta/tools/{text}*)" for text in PERSON_ONLY_TEXT]
    rules += [f"Bash(*_meta/tools/{text.rstrip(' ')})" for text in PERSON_ONLY_TEXT if text.endswith(" ")]
    return rules


def executable_rules(roots=(), path_folders=()) -> list:
    """The deny rules for the programs a routine never writes (EXECUTABLE_SUFFIXES): a `.pth` or `.lnk` at any depth;
    every other suffix right in each of `roots` (each as `rule_path` spells it, a cloud clone as its absolute POSIX
    path), anywhere under a MACHINERY_FOLDERS folder, and right in each of `path_folders` (the folders on PATH that
    lie inside a root: every other one is outside the folders a routine may write in at all)."""
    rules = [f"Edit(//**/*{suffix})" for suffix in EXECUTABLE_EVERYWHERE]
    others = [s for s in EXECUTABLE_SUFFIXES if s not in EXECUTABLE_EVERYWHERE]
    for folder in dict.fromkeys([*roots, *path_folders]):
        rules += [f"Edit(/{folder}/*{suffix})" for suffix in others]
    rules += [f"Edit(//**/{name}/**/*{suffix})" for name in MACHINERY_FOLDERS for suffix in others]
    return rules


def _path_folders() -> list:
    """The folders on PATH, each resolved (an empty or relative entry is the current folder, as Windows reads it)."""
    out = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        try:
            out.append(Path(os.path.abspath(entry or ".")).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return list(dict.fromkeys(out))


def executable_place(path: Path) -> str | None:
    """Why a program written at `path` (resolved, or as written) is one a resolver or the machinery could start, or
    None: it sits right in a space's root (a folder that holds a space.yaml), under a MACHINERY_FOLDERS folder, or
    right in a folder on PATH."""
    parent = path.parent
    try:
        if (parent / "space.yaml").is_file():
            return "a space's own folder"
    except OSError:
        return "a folder that cannot be read"
    if any(part.casefold() in MACHINERY_FOLDERS for part in path.parts[:-1]):
        return "the machinery's folders"
    try:
        real = parent.resolve()
    except (OSError, RuntimeError):
        return "a folder that cannot be resolved"
    if real in _path_folders():
        return "a folder on PATH"
    return None
# Every command a routine runs sees this set and not empty (the run's environment names the routine, the profile's
# `env` says 1): a tool tells a routine from the person by it, and no command shape a routine may run unsets it.
ROUTINE_MARK = "SPACES_ROUTINE"


def in_routine() -> str | None:
    """Why the process runs inside a routine, or None: ROUTINE_MARK is set (a computer's run), or a cloud run's
    marker is there (`cloud.state_path`, written by `cloud.py begin` outside every repository, never on a
    computer). A tool's command that executes a person's verdict or word refuses itself on it (PERSON_ONLY): the
    profile's hook and rules are the first line, this is the one no spelling of the command gets past. The marker
    is looked for in the home folder `HOME` names, and on Linux or macOS also in the account's own home and in the
    cloud's (`cloud.HOME`), so a command that sets `HOME` to another folder does not hide it."""
    if os.environ.get(ROUTINE_MARK):
        return f"a routine runs this command ({ROUTINE_MARK} is set)"
    for places in (_marker_places_set, _marker_places_account):
        try:
            if any(place.exists() for place in places()):
                return "a cloud run runs this command (its marker is there)"
        except Exception:   # noqa: BLE001 - a marker that cannot be looked for is no marker
            pass
    return None


def _marker_places_set() -> list:
    """Where `cloud.py begin` writes a cloud run's marker: under the home folder `HOME` names."""
    import cloud
    return [cloud.state_path()]


def _marker_places_account() -> list:
    """Where else a cloud run's marker can be when `HOME` was changed for one command: the account's own home and
    the cloud's. Nowhere else on Windows, where no cloud run happens."""
    if os.name == "nt":
        return []
    import cloud
    import pwd
    return [Path(pwd.getpwuid(os.getuid()).pw_dir) / cloud.STATE, Path(str(cloud.HOME)) / cloud.STATE]


# What a routine does not inherit from the session that set it up: another provider, another
# endpoint, an API key or token. A routine bills the person's claude.ai sign-in, so the sign-in
# that is checked, and the one that is started, is the one it will find without these.
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_OAUTH_TOKEN")
STRIPPED_ENV += ("SPACES_LOCAL_CLONES",)   # the tests' switch for cloning a local path: a routine clones GitHub only
GIT_BASH = "CLAUDE_CODE_GIT_BASH_PATH"


# The desktop session's own settings for the runs it starts (its effort, entry point, child-session, messaging and SDK
# variables): a setup run, a reader, the probe or a routine run without them is the run cli-facts describes (R45, I-3).
SESSION_ENV = ("CLAUDECODE", "CLAUDE_EFFORT")
SESSION_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")
KEPT_ENV = (GIT_BASH, "CLAUDE_CONFIG_DIR")   # what every caller needs: the Git Bash Claude Code runs hooks in, the config
# the client certificate a company network may require (mTLS): without it no run reaches the API at all (NB-4)
KEPT_ENV += ("CLAUDE_CODE_CLIENT_CERT", "CLAUDE_CODE_CLIENT_KEY", "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE")
# Set in every run, over the caller's value (a desktop session's is `true`): unless it is `false`, `claude -p` connects
# the connected apps in the background and its init lists none, so the probe saw no app and a routine was granted none.
FORCED_ENV = {"MCP_CONNECTION_NONBLOCKING": "false"}


def clean_env(env: dict) -> dict:
    """A copy of the environment as a routine sees it: without STRIPPED_ENV (keys, tokens, another provider) and without
    the session's own variables (SESSION_ENV, and every name starting with SESSION_PREFIXES), but with KEPT_ENV, and
    with FORCED_ENV set. Every run setup and the routines start goes through it: readers, the probe, the sign-ins and
    `routine_env`."""
    def dropped(key: str) -> bool:
        name = key.upper()   # Windows ignores case
        if name in KEPT_ENV:
            return False
        return name in STRIPPED_ENV or name in SESSION_ENV or name.startswith(SESSION_PREFIXES) or name in FORCED_ENV
    return {**{key: value for key, value in env.items() if not dropped(key)}, **FORCED_ENV}


def routine_env(env: dict, shell: str = "", windows: bool | None = None) -> dict:
    """The environment a routine's claude runs in: `clean_env`, and on Windows `CLAUDE_CODE_GIT_BASH_PATH` set to
    `shell`, the Git Bash the probe proved the send guard in (`grant`'s `shell`), so Claude Code runs the hooks
    in that one and no other."""
    env = clean_env(env)
    env["PYTHONUTF8"] = "1"   # a record's title in any script prints, never raises on a cp1252 pipe
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
# A connector still connecting when the command line starts is `pending` in its init and brings no tools yet: the
# probe looks again, PENDING_WAIT seconds apart, at most PENDING_LOOKS times, while the next look, as long as the
# last one took, ends within PENDING_BUDGET seconds of the first: the looks and the waits together, inside the
# build's wait for the probe (start.PROBE_WAIT) with time left for the rest of the probe.
PENDING_LOOKS, PENDING_WAIT, PENDING_BUDGET = 3, 20, 150
_sleep, _clock = time.sleep, time.monotonic   # the tests stand in for them


def probe_path() -> Path:
    """Where the probe's result is kept: the tools the command line saw when it last looked."""
    return Path.home() / ".spaces" / "probe.json"


def _pending(seen: dict) -> int:
    return sum(1 for s in seen["mcp_servers"] if s.get("status") == "pending")


def probe_tools(claude: str, cwd: Path, extra_dirs=(), run=subprocess.run) -> dict:
    """What `claude -p` itself sees, read from the `init` event of a one-turn run started as a
    routine starts (REFUSE, the environment without a key or token): `tools` (connectors
    included, only the connected ones), `mcp_servers` (name and status of each), `cwd`, `model`
    (the probe's own: the cheap one) and `auth` (`apiKeySource`: "none" for a claude.ai sign-in,
    else what took over). `extra_dirs` are the folders a routine adds (`--add-dir`). While a
    connector is still `pending` it looks again (PENDING_LOOKS), and keeps the look with the fewest
    pending: an app that was still connecting is not taken for one that is not connected. Raises
    ProbeError when the first run reports no init; a later look that fails keeps the best one so far."""
    began, best = _clock(), None
    for look in range(PENDING_LOOKS):
        started = _clock()
        try:
            seen = _probe_once(claude, cwd, extra_dirs, run)
        except ProbeError:
            if best is None:
                raise
            break
        if best is None or _pending(seen) < _pending(best):
            best = seen
        took = _clock() - started
        if not _pending(best) or look == PENDING_LOOKS - 1 or _clock() - began + PENDING_WAIT + took > PENDING_BUDGET:
            break
        _sleep(PENDING_WAIT)
    return best


def _probe_once(claude: str, cwd: Path, extra_dirs, run) -> dict:
    """One look of `probe_tools`."""
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


def _refused_folders(roots=()) -> tuple:
    """(the folder no program is taken from, the folders no program below is taken from), each as it resolves
    (`which`): the current folder itself, as Windows' own lookup would use it, and every space a record can put a
    file in, below it too: the space around the current folder, the one this tool sits in, and `roots`."""
    here, trees = [], [Path(__file__).resolve().parents[2], *[Path(r) for r in roots]]
    try:
        cwd = Path.cwd()
        here.append(cwd)
        import pages
        outer = pages.space_root_of(cwd)
        if outer is not None:
            trees.append(outer)
    except (OSError, ValueError):
        pass

    def real(folders) -> list:
        out = []
        for folder in folders:
            try:
                out.append(folder.resolve())
            except (OSError, RuntimeError):
                continue
        return list(dict.fromkeys(out))
    return real(here), real(trees)


def _outside(path: Path, refused: tuple) -> bool:
    """Whether `path` lies neither in the refused folder nor below a refused tree (`_refused_folders`)."""
    here, trees = refused
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        return False
    return real.parent not in here and not any(real == t or real.is_relative_to(t) for t in trees)


def which(name: str, roots=()) -> str | None:
    """A program on PATH, as an absolute path, never one in the current folder or a space (`_refused_folders`):
    Windows (Python 3.11 and older always, 3.12 and newer unless NO_CWD_LOOKUP is set) puts the current folder first,
    and an empty or relative PATH entry does the same anywhere. What `shutil.which` finds is kept when it is absolute
    and outside them; otherwise PATH is walked again, its empty and relative entries left out, with PATHEXT on
    Windows. None when `shutil.which` finds nothing: then no folder on PATH has it either."""
    found = shutil.which(name)
    if not found:
        return None
    refused = _refused_folders(roots)
    if os.path.isabs(found) and _outside(Path(found), refused):
        return os.path.abspath(found)
    if os.name == "nt":
        exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if e]
        names = [name] if any(name.lower().endswith(e.lower()) for e in exts) else [name + e for e in exts]
    else:
        names = [name]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        for candidate in (Path(entry) / n for n in names):
            if candidate.is_file() and os.access(candidate, os.X_OK) and _outside(candidate, refused):
                return os.path.abspath(candidate)
    return None


def find_claude() -> str | None:
    """Claude Code's command: on PATH (`which`: never in the current folder or a space), else where the doctor
    installed it, else where its own installer puts it."""
    found = which("claude")
    if found:
        return found
    refused = _refused_folders()
    for folder in (doctor.tools_home() / "claude", Path.home() / ".local" / "bin", Path.home() / ".claude" / "local"):
        for name in ("claude", "claude.exe"):   # a scheduled task may start without the user's PATH
            if (folder / name).exists() and _outside(folder / name, refused):
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
        ps = which("ps") or "/bin/ps"   # never one in the current folder (an empty PATH entry is that folder)
        said = subprocess.run([ps, "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
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
                     send_tools=(), known_tools=None, profile=None, shell: str | None = None, extra_allow=(),
                     cloud_chain: str | None = None) -> dict:
    """The profile one routine run adds to the space's own (`claude --settings`, written to `profile`). Deny
    rules win over every allow rule in every scope, so the space's own profile cannot widen these, and
    `"disableAllHooks": false` keeps its hooks on whatever the person's own settings say.

    Bash runs only the routine tools (cloud.py for its proof line alone, `ROUTINE_VERBS`; its begin and trigger
    are denied in any spelling, CLOUD_ONLY; sync.py only as the landing, in exact commands, never as git's merge
    driver, SYNC_NEVER) and the read-only gh and git commands, GitHub's REST reads among them as one word each
    (REST_READS, REST_ONE_WORD, GraphQL refused by its endpoint, GRAPHQL): a PreToolUse hook on Bash
    (`bash_guard.py <profile>`, the workspace's own) lets through one plain command of a shape this profile
    allows, so a Bash allow in the person's own settings never widens a routine. When that hook's command
    cannot be written, Bash is denied outright. The other built-in tools a routine could reach out with are
    denied by name (`ROUTINE_BUILTINS` are the ones it has).

    Of the connectors it allows the grant's tools by their exact names, each checked again here:
    `read_tools` that only read (`reader.is_read_tool`), `write_tools` that create, update or save an issue, a
    ticket, a task or a comment, or move or edit an issue, and do nothing else (`_writes`), and `send_tools` whose
    name says send, only with a `person` the send guard can
    hold them to and a shell to run it with (`shell`, the one the probe proved; else `hook_shell`): a
    PreToolUse hook on each runs the workspace's `send_guard.py <person>` (`send_guard_command`).
    Every other connector tool it knows of (`known_tools`, else every one the probe has seen) is denied,
    so a tool the person once chose "always allow" for in their own settings is refused here too.

    Two more hooks run the same guard in a mode of its own (Ruling R45): on Task (`TASK_MATCHER`) only the
    general-purpose agent starts (`--task`), and on every connector tool (`CONNECTOR_MATCHER`) only the tools this
    profile allows by name go through (`--connectors <profile>`), so a tool that appeared after the last probe is
    refused too. Without a command for them, Task is denied and no connector tool is granted. Every hook carries
    its own `timeout` (`GUARD_TIMEOUT`), well above the time each guard gives itself.

    A signature stays the person's (finding 5): a second hook on Bash (`routine.py --person-only`) refuses every
    command that executes a person's verdict or word (`PERSON_ONLY`), with text deny rules beside it
    (`PERSON_ONLY_TEXT`); a hook on every edit (`EDIT_MATCHER`, `routine.py --edit-guard`) refuses an edit that
    would leave a page signed, touch a signed page or write a space's own `log.md` (`edit_check`). Without a command
    for these hooks, Edit and Write are denied with Bash. The files only a tool writes (`TOOL_WRITTEN`, and `log.md`
    at each root, `TOOL_WRITTEN_IN_ROOT`) are denied to edits as PROTECTED_EDITS are, and so are the programs where
    a resolver or the machinery would find one (`executable_rules`). The profile's `env` sets ROUTINE_MARK for every
    command the run starts (and, on Windows, NO_CWD_LOOKUP).

    `extra_allow` adds Bash shapes of the caller's own (`Bash(<pattern>)` on one line; anything else, a rule that
    runs over a line among them, is dropped) right after the tool rules, only while the Bash guard is written: a
    cloud run's read-only `gh api` calls (`cloud.py`).

    Without `cloud_chain` sync.py runs only as a computer's landing: `<root>/_meta/tools/sync.py --root <root>`,
    alone or with `--release-leg` (SYNC_LEGS), each root as itself, and `_meta/tools/sync.py --root .` (or the
    workspace) the same way from the workspace. `cloud_chain` is a cloud run's chain (`cloud.py begin`,
    CLOUD_CHAINS): sync.py then runs only as that run's landing, `<root>/_meta/tools/sync.py --root <root> --cloud
    <chain>`, each root as itself, never the computer's landing (no `--cloud`, which pushes a machine's branch) or
    another chain's. ValueError for a chain that is none."""
    import reader
    if cloud_chain is not None and cloud_chain not in CLOUD_CHAINS:
        raise ValueError(f"no cloud chain {cloud_chain!r}: {', '.join(CLOUD_CHAINS)}")
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
        edits, person_only = (routine_guard_command(mode, script=tools_dir / "routine.py")
                              for mode in ("--edit-guard", "--person-only"))
    except ValueError:   # a path the hook's command cannot carry: no Bash, Task, connector or edit at all, rather than unguarded
        bash = task = connectors = edits = person_only = None
    shapes = [shape for tool in ROUTINE_TOOLS if tool != "sync.py"
              for shape in ([f"{tool} {verb}" for verb in ROUTINE_VERBS[tool]] if tool in ROUTINE_VERBS else [tool])]
    allow = [f"Bash({py} _meta/tools/{shape} *)" for py in pys for shape in shapes] if bash else []
    allow += [f"Bash({py} {place}/_meta/tools/{shape} *)"
              for place in places for py in pys for shape in shapes] if bash else []
    legs = [f" --cloud {cloud_chain}"] if cloud_chain else list(SYNC_LEGS)
    allow += [f"Bash({py} {place}/_meta/tools/sync.py --root {place}{leg})"
              for place in places for py in pys for leg in legs] if bash else []
    allow += [f"Bash({py} _meta/tools/sync.py --root {root}{leg})" for root in (".", workspace.as_posix())
              for py in pys for leg in legs] if bash and not cloud_chain else []
    allow += [rule for rule in dict.fromkeys(_strings(list(extra_allow or [])))
              if re.fullmatch(r"Bash\(.+\)", rule) and not re.search(r"[\r\n]", rule)] if bash else []
    others = sorted({p.name for root in [workspace, *roots] for p in (root / "_meta" / "tools").glob("*.py")}
                    - set(ROUTINE_TOOLS))
    deny = [f"Edit(//**/{pattern})" for pattern in (*PROTECTED_EDITS, *TOOL_WRITTEN)]
    deny += [f"Edit(/{rule_path(root)}/{pattern})" for root in roots for pattern in (*PROTECTED_IN_ROOT,
                                                                                    *TOOL_WRITTEN_IN_ROOT)]
    inside = [rule_path(f) for f in _path_folders() if any(f == r or f.is_relative_to(r) for r in roots)]
    deny += executable_rules([rule_path(root) for root in roots], inside)
    deny += [f"Edit(~/{folder}/**)" for folder in (".claude", ".spaces")]
    deny += [f"Bash(*_meta/tools/{tool}*)" for tool in others]
    deny += [f"Bash(*_meta/tools/cloud.py*{verb}*)" for verb in CLOUD_ONLY]
    deny += [f"Bash(*_meta/tools/route.py* {text}*)" for text in ROUTE_NEVER]
    deny += [f"Bash(*_meta/tools/sync.py*{text}*)" for text in SYNC_NEVER]
    deny += person_only_rules()
    # `-m` puts the working folder first on the import path, so a planted module would run;
    # `--output` lets a read-only git command write a file.
    deny += ["Bash(python -m *)", "Bash(python3 -m *)", "Bash(git *--output*)", *REST_ONE_WORD]
    deny += [t for t in dict.fromkeys([*reader.DENIED, "WebFetch"])
             if t not in ROUTINE_BUILTINS or (t in ("Bash", "Task") and not bash)
             or (t in ("Edit", "Write") and not edits)]
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
    shell_reads = list(READ_ONLY + REST_READS) if bash else []
    data = {"disableAllHooks": False,
            "permissions": {"allow": list(dict.fromkeys(allow + shell_reads + granted)), "deny": deny},
            "env": {ROUTINE_MARK: "1", **({NO_CWD_LOOKUP: "1"} if os.name == "nt" else {})}}
    hook = lambda command: {"type": "command", "command": command, "timeout": GUARD_TIMEOUT}   # noqa: E731
    hooks = [{"matcher": "Bash", "hooks": [hook(bash), hook(person_only)]}] if bash else []
    hooks += [{"matcher": tool, "hooks": [hook(guard)]} for tool in sends]
    if bash:   # the agents a run starts and the connector tools it calls (Minor 10, 11), and every edit it makes
        hooks += [{"matcher": TASK_MATCHER, "hooks": [hook(task)]}, {"matcher": CONNECTOR_MATCHER, "hooks": [hook(connectors)]},
                  {"matcher": EDIT_MATCHER, "hooks": [hook(edits)]}]
    if hooks:
        data["hooks"] = {"PreToolUse": hooks}
    return data


def _connector_part(tool) -> str | None:
    import reader
    found = reader.CONNECTOR_TOOL.fullmatch(tool) if isinstance(tool, str) else None
    return found.group("tool") if found else None


def _writes(tool) -> bool:
    """A tracker's write: its name creates, updates or saves an issue, a ticket, a task or a comment and says
    nothing else (`apps.tracker_write`: no document, customer, project or diff), and no other act (delete, move,
    assign, ...). Checked here again, so a verdict or grant written before the rule was narrowed grants no more."""
    import apps
    import reader
    part = _connector_part(tool)
    if part is None:
        return False
    low, words = part.lower(), reader.name_words(part)
    return (apps.tracker_write(part)   # an issue's own verbs (a transition, an edit) only for an issue, it checks
            and not any(w in low for w in reader.ACT_WORDS if w not in (*apps.WRITE_WORDS, *apps.ADD_VERBS))
            and not any(w in words for w in reader.ACT_VERBS if w not in (*apps.ISSUE_VERBS, *apps.ADD_VERBS)))


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


def routine_guard_command(mode: str, python: str | None = None, script=None) -> str:
    """`"<python>" -I "<routine.py>" <mode> || exit 2`, `mode` "--edit-guard" or "--person-only": the hooks this
    tool answers itself (`guard_main`). ValueError for a path the command cannot quote or a mode that is none."""
    import reader
    if mode not in GUARD_MODES:
        raise ValueError(f"no guard mode {mode!r}")
    return f"{reader._quoted(python or sys.executable)} -I {reader._quoted(script or Path(__file__).resolve())} {mode} || exit 2"


# ----- the guards this tool answers: no signature, no verdict, from a routine ----------------------------------
EDIT_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"
GUARD_MODES = ("--edit-guard", "--person-only")
GUARD_SECONDS = 10   # as the Bash guard: the hook's timeout (GUARD_TIMEOUT) is three times that
SIGNATURE_KEYS = ("signed_by", "signed_at", "signed_in", "fingerprint")
EDIT_WHY = ("routines never sign: no edit may leave a page `status: stable` or carrying a signature field, change "
            "a signed page or write a space's own log.md")
# Each refusal of the edit guard in its own plain words, so a routine knows what to do instead (finding: one message
# for every refusal left a routine unable to tell a broken page from a signed one).
WOULD_SIGN = ("routines never sign: what this edit leaves has a frontmatter with `status: stable` or a signature "
              "field (signed_by, signed_at, signed_in, fingerprint)")
SIGNED_PAGE = "routines never sign: this page is signed, and a signed page changes only through the person's sign.py"
SIGNATURE_LINE_WHY = ("routines never sign: this edit brings a `status: stable` or signature-field line where it can "
                      "land in the page's frontmatter")
NO_PARSE = ("what this edit leaves has a frontmatter that does not parse as YAML (an unquoted colon in a value, a "
            "bracket left open): quote the value or write the whole frontmatter again, so it parses")
NOT_IN_FILE = ("the old string is not in the file as Claude Code would match it: read the file again and copy the "
               "text exactly")
PERSON_WHY = "routines never execute a person's verdict or word: that command is the person's"
FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
OPENER = re.compile(r"\A﻿?---[ \t]*\r?\n")   # a frontmatter begun: until a line closes it, all of it may be one
# A key starts only at a line's start or in a flow mapping (after `{` or `,`), after spaces and any `- `, `? `, tag or
# anchor; never after a word, so a title "Re: device fingerprint: results" or a summary "Build status: stable" is a
# value, not a key (finding: such a witness page was refused as signed).
_KEY_START = r"""(?:^|[{,])[ \t]*(?:(?:[-?]|[&!]\S*)[ \t]+)*["']?"""
SIGNATURE_LINE = re.compile(rf"""(?im){_KEY_START}(?:signed_by|signed_at|signed_in|fingerprint)["']?\s*:""")
STABLE_LINE = re.compile(rf"""(?im){_KEY_START}status["']?\s*:\s*["']?stable\b""")
# The same, as a line of its own: what no new string an edit brings may hold when it can land in the leading
# frontmatter (`_edited`'s `head`), whatever the guard makes of the file around it. A body line far below the
# closing `---` ("status: stable release by Friday") is no frontmatter, and passes.
SIGNATURE_KEY_LINE = re.compile(r"""(?im)^[ \t]*["']?(?:signed_by|signed_at|signed_in|fingerprint)["']?[ \t]*:""")
STABLE_KEY_LINE = re.compile(r"""(?im)^[ \t]*["']?status["']?[ \t]*:[ \t]*["']?stable\b""")
# Claude Code matches an old string's straight quotes to a file's curly ones and back (its normalizeQuotes: the four
# curly quotes as straight ones), one character for one, and then writes the new string's quotes curly to match the
# file (its preserveQuoteStyle). The guard replays both (`_match`, `_curled`).
QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
CURLED = str.maketrans({"'": "’", '"': "”"})
MAX_READINGS = 64   # the ways a MultiEdit can be read once its quotes are normalised: past this it is refused
MAX_EDITED = 32 * 1024 * 1024   # a file larger than any page: its frontmatter is still read
UNQUOTED_EXPANDS = set("*?[]{}~")   # outside quotes the shell would turn these into other words
GLOB_CHARS = set("*?[]")   # what makes a word a pattern of file names
GUARD_MAX_RAW = 64 * 1024 * 1024   # the most of stdin read: a Write of a large file is a large event


def _frontmatter_state(text: str) -> str | None:
    """"signed" when `text` starts with a frontmatter that a reader would take as signed: `status: stable` or any
    signature field (SIGNATURE_KEYS); "unparsed" when a frontmatter block does not parse as YAML and none reads as
    signed; else None. Two blocks are read: the one this guard finds (FRONTMATTER, which also takes a BOM and closes
    only on a line that is `---` alone) and the one the readers find (`pages.FM_RE`, which closes on the first line
    that starts with `---`), since a `---x` line makes the two differ and a duplicate key after it would hide the
    reader's own block. Each is read as text (a quoted key, SIGNATURE_LINE and STABLE_LINE) and, when it parses, as
    YAML (an escape such as `"st\\x61ble"`, a tag, an alias)."""
    import pages
    text = text or ""
    blocks = [found.group(1) for found in (FRONTMATTER.match(text), pages.FM_RE.match(text)) if found]
    unparsed = False
    for block in dict.fromkeys(blocks):
        if SIGNATURE_LINE.search(block) or STABLE_LINE.search(block):
            return "signed"
        try:
            import yaml as _yaml
            data = _yaml.safe_load(block)
        except Exception:   # noqa: BLE001 - a block that does not parse: the text checks above are all it gives
            unparsed = True
            continue
        if isinstance(data, dict) and (str(data.get("status") or "").strip().casefold() == "stable"
                                       or any(k in data for k in SIGNATURE_KEYS)):
            return "signed"
    return "unparsed" if unparsed else None


def _signed_frontmatter(text: str, unparsed_signed: bool = True) -> bool:
    """Whether `text` starts with a frontmatter a reader would take as signed (`_frontmatter_state`). With
    `unparsed_signed` (the rule for what an edit leaves) a block that does not parse counts as signed, since the
    guard cannot tell what a reader of a shorter block makes of it; without it (the rule for the file as it is now) a
    block that does not parse is decided by the text checks alone. A reader cannot take a page as signed from a
    frontmatter it cannot parse (`pages.split` raises), so such a page is not provably signed, and a routine may
    repair it into a valid unsigned one."""
    state = _frontmatter_state(text)
    return state == "signed" or (unparsed_signed and state == "unparsed")


def _head_end(text: str) -> int:
    """Where the part of `text` that is, or can become, its leading frontmatter ends: the end of the longer of the two
    blocks `_frontmatter_state` reads; the whole text when a frontmatter is begun and no line closes it yet (any
    edit below may close it); 0 when it begins none."""
    import pages
    ends = [found.end() for found in (FRONTMATTER.match(text), pages.FM_RE.match(text)) if found]
    if ends:
        return max(ends)
    return len(text) if OPENER.match(text) else 0


def _current(path: Path, limit: int) -> str | None:
    """The text of `path`, at most `limit` bytes of it, as UTF-8 with what does not decode replaced; "" when there is
    no such file; None when it cannot be read (a folder, no access)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        return None


class Unreadable(ValueError):
    """An edit whose result this guard cannot tell: it refuses it."""


def _match(text: str, old: str) -> tuple:
    """(the old string as `text` holds it, whether its quotes had to be normalised to find it), as Claude Code finds
    it: the string itself when it is there, else the same length of `text` where the two read alike with the curly
    quotes as straight ones (QUOTES). (None, False) when it is not there either way."""
    if old in text:
        return old, False
    at = text.translate(QUOTES).find(old.translate(QUOTES))
    return (text[at:at + len(old)], True) if at >= 0 else (None, False)


def _edited(current: str, tool_input: dict, tool: str) -> tuple:
    """(the texts the edit can leave, the new strings that can land in the leading frontmatter), as Claude Code makes
    the edit. A Write leaves its content, and what of it is the leading frontmatter (`_head_end`) is what lands
    there. An Edit's (or MultiEdit's) replacements are applied in order; a file of CRLF lines is matched as LF ones,
    and an old string whose quotes differ from the file's is found with them normalised (`_match`), after which the
    edit can leave the new string as given or with its quotes curled to match the file (both are read, at most
    MAX_READINGS ways). A new string lands in the frontmatter when its edit is at the start of the text (no old
    string, or one found at offset 0) or its old string starts inside the part that is or can become the leading
    frontmatter (`_head_end`); a new string the edit puts below the closing `---` cannot move a key into it. An old
    string that is not in the text either way raises Unreadable."""
    if tool == "Write":
        content = str(tool_input.get("content") or "")
        return [content], [content[:_head_end(content)]]
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
    if not isinstance(edits, list):
        raise Unreadable("no edits to read")
    readings, head = [current], []
    for edit in edits:
        if not isinstance(edit, dict):
            raise Unreadable("an edit that is no mapping")
        old, new = str(edit.get("old_string") or ""), str(edit.get("new_string") or "")
        after = []
        for text in readings:
            this_old, this_new = old, new
            if this_old and this_old not in text and "\r\n" in text:   # Claude Code matches CRLF lines as LF ones
                text, this_old, this_new = (s.replace("\r\n", "\n") for s in (text, this_old, this_new))
            if not this_old:
                after.append(this_new + text)
                head.append(this_new)
                continue
            found, normalised = _match(text, this_old)
            if found is None:
                raise Unreadable(NOT_IN_FILE)
            news = [this_new, this_new.translate(CURLED)] if normalised else [this_new]
            end, at = _head_end(text), text.find(found)
            starts = [m.start() for m in re.finditer(re.escape(found), text)] if edit.get("replace_all") else [at]
            for each in dict.fromkeys(news):
                after.append(text.replace(found, each) if edit.get("replace_all") else text.replace(found, each, 1))
                if any(s == 0 or s < end for s in starts):
                    head.append(each)
        readings = list(dict.fromkeys(after))
        if len(readings) > MAX_READINGS:
            raise Unreadable("an edit that matches only with its quotes normalised, in too many places to read")
    return readings, head


def _glob_rule(pattern: str, anywhere: bool) -> re.Pattern:
    """A deny rule's path pattern as a regex over a casefolded posix path: `**` any text, `*` any text within one
    name; `anywhere` for a rule written `//**/<pattern>` (at any depth), else from the start."""
    body = "".join(".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part.casefold())
                   for part in re.split(r"(\*\*|\*)", pattern))
    return re.compile(("(?:^|/)" if anywhere else "^") + body + r"\Z", re.S)


def _denied_path(path: Path) -> str | None:
    """The deny rule an edit of `path` falls under, read on the path as written and on the path the file system
    resolves (a link, a junction, a short 8.3 name such as `_OUTBO~1`, another case), or None. Claude Code matches
    its deny rules on the path as written only; this reads them again on what the edit would really touch: the
    protected and tool-written paths at any depth (PROTECTED_EDITS, TOOL_WRITTEN), the per-root ones under every
    folder above that holds a space.yaml (PROTECTED_IN_ROOT, TOOL_WRITTEN_IN_ROOT: a space's own `log.md`, not a desk
    folder's), and `~/.claude`, `~/.spaces`."""
    seen = [Path(os.path.abspath(path))]
    try:
        seen.append(path.resolve())
    except (OSError, RuntimeError):
        return "the path cannot be resolved"
    home = []
    try:
        home = [Path.home().resolve() / folder for folder in (".claude", ".spaces")]
    except (OSError, RuntimeError, KeyError):
        pass
    anywhere = [(p, _glob_rule(p, True)) for p in (*PROTECTED_EDITS, *TOOL_WRITTEN)]
    rooted = [(p, _glob_rule(p, False)) for p in (*PROTECTED_IN_ROOT, *TOOL_WRITTEN_IN_ROOT)]
    for each in seen:
        posix = each.as_posix().casefold()
        hit = next((p for p, rule in anywhere if rule.search(posix)), None)
        if hit:
            return hit
        for folder in home:
            if each == folder or each.is_relative_to(folder):
                return f"~/{folder.name}/**"
        for parent in each.parents:
            try:
                if not (parent / "space.yaml").is_file():
                    continue
            except OSError:
                continue
            rel = each.relative_to(parent).as_posix().casefold()
            hit = next((p for p, rule in rooted if rule.search(rel)), None)
            if hit:
                return hit
    return None


def edit_check(event) -> str | None:
    """None when an Edit, Write, MultiEdit or NotebookEdit a routine makes leaves no page signed; else why it is
    refused, in its own plain words. Refused: a target whose name is no plain file name (`pages.name_problem` on each
    part: a stream after a colon, a trailing dot or space, a device name, which Windows writes as another file); a
    program (EXECUTABLE_SUFFIXES, in any case): a `.pth` or `.lnk` anywhere, any other where a resolver or the
    machinery would find it (`executable_place`); each of these on the path as written and on the file it resolves
    to (a link named `notes.md` that points at a log); a target a deny rule names once its link, junction, short 8.3
    name or case is resolved (`_denied_path`: a space's own `log.md` among them, where sign.py writes its lines; a
    desk folder's log.md is a desk note, since an answer counts only from the verdict store); a notebook edit of
    anything but a `.ipynb`; a target that exists and is signed (`_signed_frontmatter` without `unparsed_signed`: a
    frontmatter that does not parse is no signed page, and the edit may repair it) or cannot be read; an edit whose
    old string is not in the file as Claude Code matches it (`_edited`); an edit that can leave a frontmatter a
    reader would take as signed, or one that does not parse; and a new string with a signature line where it can
    land in the leading frontmatter."""
    import pages
    tool = event.get("tool_name") if isinstance(event, dict) else None
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return f"{EDIT_WHY}: this request names no edit"
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(target, str) or not target.strip():
        return f"{EDIT_WHY}: this request names no file"
    path = Path(target)
    if not path.is_absolute():
        path = Path.cwd() / path
    parts = path.parts[1:] if path.anchor else path.parts
    if any(pages.name_problem(part) for part in parts):
        return "that file name is not a plain one (a stream after a colon, a trailing dot or space, a device name)"
    try:
        places = [Path(os.path.abspath(path)), path.resolve()]
    except (OSError, RuntimeError):
        return "the path cannot be resolved, so what the edit touches cannot be told"
    for place in places:
        suffix = place.suffix.casefold()
        if suffix in EXECUTABLE_EVERYWHERE:
            return f"no routine writes a {suffix} file anywhere: Python or Windows would run or follow it"
        where = executable_place(place) if suffix in EXECUTABLE_SUFFIXES else None
        if where:
            return (f"no routine writes a program ({suffix}) in {where}, where it could be started by its name; "
                    "under work/ or in an attached codebase it may")
    denied = _denied_path(path)
    if denied:
        return (f"routines do not edit {denied}: the machinery, the instructions, or a file only a tool writes "
                "(its tool writes it, never a routine's edit)")
    if tool == "NotebookEdit" and not all(p.name.casefold().endswith(".ipynb") for p in places):
        return "a notebook edit reaches a .ipynb notebook only"
    current = _current(path, MAX_EDITED)
    if current is None:
        return "the file there cannot be read, so whether it is signed cannot be told"
    if _signed_frontmatter(current, unparsed_signed=False):
        return SIGNED_PAGE
    if tool == "NotebookEdit":   # no page: its target was checked above
        return None
    if tool not in ("Edit", "Write", "MultiEdit"):
        return f"{EDIT_WHY}: an edit of a kind this guard does not know"
    try:
        readings, head = _edited(current[:MAX_EDITED], tool_input, tool)
    except Unreadable as exc:
        return f"{exc}, so what the edit leaves cannot be told"
    states = {_frontmatter_state(text) for text in readings}
    if "signed" in states:
        return WOULD_SIGN
    if any(SIGNATURE_KEY_LINE.search(new) or STABLE_KEY_LINE.search(new) or _frontmatter_state(new) == "signed"
           for new in head):
        return SIGNATURE_LINE_WHY
    if "unparsed" in states:
        return NO_PARSE
    return None


def shell_words(command: str) -> list | None:
    """The words a plain command (`bash_guard.plain`) is to the shell: (word, expands), quotes taken off, `expands`
    the characters outside quotes that would make the shell turn the word into others (a glob, a brace, a tilde at
    the start of the word or after a `=` or `:`, where the shell reads one), empty when there are none. None when a
    quote is left open or a character the Bash guard refuses is there anyway."""
    words, word, quote, expands, started = [], [], None, "", False
    for ch in command:
        if quote:
            if ch == quote:
                quote = None
            elif ch in "\n\r\0" or (quote == '"' and ch in "$`\\"):
                return None
            else:
                word.append(ch)
            continue
        if ch in "'\"":
            quote, started = ch, True
        elif ch in " \t":
            if started:
                words.append(("".join(word), expands))
            word, expands, started = [], "", False
        elif ch in ";&|<>()$`\\!\n\r\0":
            return None
        else:
            if ch in UNQUOTED_EXPANDS and (ch != "~" or not word or word[-1] in "=:"):
                expands += ch
            word.append(ch)
            started = True
    if quote:
        return None
    if started:
        words.append(("".join(word), expands))
    return words


def _tool_name(word: str) -> str | None:
    """The tool script a command word names, casefolded, when it is one under `_meta/tools/` however the path is
    spelled (`//`, `./`, `x/..`, backslashes, a trailing dot or space Windows drops), else None."""
    path = posixpath.normpath(word.replace("\\", "/")).casefold()
    if "_meta/tools/" not in path:
        return None
    return path.rsplit("/", 1)[-1].rstrip(". ")


def person_only_check(event) -> str | None:
    """None when a routine's Bash command executes no person's verdict or word (PERSON_ONLY); else why it is
    refused (PERSON_WHY). The command is read as the shell reads it (`shell_words`), so a verb spelled with quotes
    inside it (`ap''ply`) is the verb, and the tool is found however its path is spelled (`_tool_name`); a path under
    `_meta/tools/` the shell would expand (`rout?.py`), or a tool named by a short 8.3 name (`SIGN_Q~1.PY`), is
    refused, since it could name any tool (a folder the shell would expand, `_meta/too?s/route.py`, is left to the
    Bash guard, which runs a tool only by its path written out). For a tool
    PERSON_ONLY names, refused: the tool whole when it says `None`; any word that is one of its verbs; any word that
    starts an option of its (`--verd`, as argparse takes an abbreviation of three characters or more, `=` and what
    follows left off); a bare `--`, which moves the verb past where a text rule or a position looks for it; and, at
    any position, a word the shell would expand into a verb or an option: one with a brace or a tilde, or a glob
    with no `/` (a glob that holds one only ever names paths that hold one, as `_meta/state/x/*.yaml`). A word the
    shell would expand where the verb stands (the two words after the script) or that starts with `-` is refused
    too. A command this cannot read is refused."""
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return f"{PERSON_WHY}: this request names no command"
    words = shell_words(command.strip(" \t"))
    if words is None:
        return f"{PERSON_WHY}: this command could not be read"
    for at, (word, spread) in enumerate(words):
        name = _tool_name(word)
        if name is not None and (spread or "~" in name):
            return f"{PERSON_WHY}: a tool's path the shell would expand, or a short 8.3 name, could name any tool"
        if name not in PERSON_ONLY:
            continue
        rule = PERSON_ONLY[name]
        if rule is None:
            return PERSON_WHY
        rest = words[at + 1:]
        for position, (arg, expands) in enumerate(rest):
            low = arg.casefold()
            if low in rule["verbs"]:
                return PERSON_WHY
            option = low.split("=", 1)[0]
            if len(option) >= 3 and option.startswith("--") and any(o.startswith(option) for o in rule["options"]):
                return PERSON_WHY
            if arg == "--":
                return f"{PERSON_WHY}: a bare `--` moves the verb where the guard would not look for it"
            if expands and ("{" in expands or "~" in expands or (set(expands) & GLOB_CHARS and "/" not in arg)):
                return f"{PERSON_WHY}: a word the shell would expand could become a verb or an option"
            if expands and (position < 2 or arg.startswith("-")):
                return f"{PERSON_WHY}: a word the shell would expand where a verb stands"
    return None


def guard_main(mode: str, raw: bytes) -> int:
    """One guard hook (`--edit-guard`, `--person-only`): the event on stdin, exit 0 to let it through, 2 to refuse,
    with why on stderr. Anything it cannot read, and any fault of its own, is a refusal."""
    why = EDIT_WHY if mode == "--edit-guard" else PERSON_WHY
    try:
        if len(raw) > GUARD_MAX_RAW:
            raise ValueError("a request larger than any routine's")
        event = json.loads(raw.decode("utf-8"))
        reason = edit_check(event) if mode == "--edit-guard" else person_only_check(event)
    except BaseException:   # noqa: BLE001 - whatever went wrong, the call does not go
        reason = f"{why}: this request could not be read"
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


def _guard_run(mode: str) -> int:
    """The hook: its own time limit first (as the Bash guard's: after GUARD_SECONDS it refuses and ends, and two
    seconds later faulthandler ends it even inside one long call), then stdin, then `guard_main`."""
    import faulthandler

    def expire() -> None:
        try:
            print("the check took too long, so the call is refused", file=sys.stderr, flush=True)
        finally:
            os._exit(2)
    timer = threading.Timer(GUARD_SECONDS, expire)
    timer.daemon = True
    timer.start()
    faulthandler.dump_traceback_later(GUARD_SECONDS + 2, exit=True, file=sys.stderr)
    return guard_main(mode, sys.stdin.buffer.read(GUARD_MAX_RAW + 1))


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
    git = which("git")   # never a git.exe in the current folder or a space
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
      only read, and, for a tracker, the tools that create, update or save an issue or a comment, or move or edit
      an issue, and nothing else (apps.group decides both, `apps.tracker_write`).
      An app no name knows, that setup's sorting step gave a kind (the verdict's `kinds`), gives its
      read tools only, whatever kind it was sorted as.
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
    verdicts = _verdicts(workspace)
    sorted_kinds = {v["app"]: v["kinds"] for v in verdicts if isinstance(v.get("kinds"), list)}
    groups = {g["app"]: g for g in apps.group(_strings(probe.get("tools")), servers, sorted_kinds=sorted_kinds)}
    signed_out = set(apps.not_signed_in(servers))
    try:
        person = guardable(spaceyaml.load(workspace).owner_email)
    except Exception:
        person = None
    read, write, send, reconnect, mailbox_out = [], [], [], [], []
    for verdict in verdicts:
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
    pid, started = data.get("pid"), _moment(data.get("started"))   # one this computer cannot place is no lock
    if not (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and isinstance(started, dt.datetime)):
        return None
    mark = data.get("pid_started")
    return {"pid": pid, "routine": str(data.get("routine") or ""),
            "started": started, "part": str(data.get("part") or ""),
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


# ----- the probe: what a scheduled run sees, and its checks ----------------------------------
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


GUARDS_CHECK = "the guards on a routine's edits and commands"
# What each guard mode is given once, as Claude Code gives a hook its event: one call it must let through and one it
# must refuse. Nothing is written: the guard only reads.
GUARD_SAMPLES = {
    "--edit-guard": ({"tool_name": "Write", "tool_input": {"content": "probe\n"}},
                     {"tool_name": "Write", "tool_input": {"content": "---\nstatus: stable\n---\nprobe\n"}}),
    "--person-only": ({"tool_name": "Bash", "tool_input": {"command": "python _meta/tools/status.py ."}},
                      {"tool_name": "Bash", "tool_input": {"command": "python _meta/tools/sign.py . apply"}}),
}


def _guard_said(command: str, event: dict, shell: list) -> tuple:
    """(exit code or None, the last line the guard wrote to stderr) of a hook command run as Claude Code runs it."""
    payload = json.dumps({"hook_event_name": "PreToolUse", **event}).encode("utf-8")
    try:
        res = subprocess.run([*shell, command], input=payload, capture_output=True, timeout=HOOK_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    said = [line.strip() for line in res.stderr.decode("utf-8", errors="replace").splitlines() if line.strip()]
    return res.returncode, (said[-1] if said else "")


def guards_check(workspace, profiles: list, shell: list | None) -> dict:
    """The edit guard and the person-only guard (`--edit-guard`, `--person-only`) run in the exact shell and Python
    each routine's profile names: every distinct command of theirs is given a harmless call it must let through and
    one it must refuse (GUARD_SAMPLES). A guard that refuses everything, as one does whose Python cannot import what
    it needs under `-I` (PyYAML found only in the user's own site-packages), would refuse every edit and command of
    every routine; the check fails in plain words, naming that Python, so the setup agent can install PyYAML into
    it. A profile without the guards fails too: its routines could edit or run without them."""
    probe_file = Path(workspace) / "_meta" / "state" / "private" / ".probe-guard.md"
    commands = []
    for _, data in profiles:
        if data is None:
            return _check(GUARDS_CHECK, False, "a routine's profile cannot be read, so whether its guards run is not known")
        found = {mode: [h.get("command") for e in _rules(data)[2] for h in e.get("hooks") or []
                        if isinstance(h, dict) and isinstance(h.get("command"), str) and f" {mode} " in h["command"]]
                 for mode in GUARD_MODES}
        if not all(found.values()):
            return _check(GUARDS_CHECK, False, "a routine's profile carries no guard on its edits or its commands")
        commands += [(mode, command) for mode, listed in found.items() for command in listed]
    if not commands:
        return _check(GUARDS_CHECK, True, "no routine is rendered yet")
    if shell is None:
        return _check(GUARDS_CHECK, False, "this computer has no Git for Windows, which a routine's guards run in")
    for mode, command in dict.fromkeys(commands):
        allow, refuse = (json.loads(json.dumps(e)) for e in GUARD_SAMPLES[mode])
        if mode == "--edit-guard":
            for event in (allow, refuse):
                event["tool_input"]["file_path"] = str(probe_file)
        let, why = _guard_said(command, allow, shell)
        refused, _ = _guard_said(command, refuse, shell)
        python = next(iter(re.findall(r'"([^"]+)"', command)), command.split(" ", 1)[0])
        if let != 0:
            said = f" (it said: {why[:160]})" if why else ""
            return _check(GUARDS_CHECK, False,
                          f"the guard {mode} refused a harmless call{said}, so every routine would be refused each "
                          f"edit or command: the Python it runs, {python}, must import PyYAML with -I. Run "
                          f"`python _meta/tools/start.py` again: it builds ~/.spaces/venv with PyYAML and renders the "
                          f"routines with that Python. Only if start.py finds PyYAML already there, install PyYAML "
                          f"into {python} itself, not into your own user folder")
        if refused != 2:
            return _check(GUARDS_CHECK, False, f"the guard {mode} let through a call it must refuse, so the routines "
                                               "may not run until it is fixed")
    return _check(GUARDS_CHECK, True, "the guards let a routine's own work through and refuse a signature")


def tracker_notes(view: dict, got: dict) -> list:
    """One plain line per tracker app the routines may write to: the tools granted (by their names in the app), and
    the tools the app offers whose names look like a write the tracker rule refused (`apps.tracker_write`), so a
    refused `update_issue_state` shows here and an agent can add its shape instead of the tracker step failing
    unseen."""
    import apps
    notes = []
    for app, granted, refused in apps.tracker_tools(_strings(view.get("tools")), view.get("mcp_servers"), got["write"]):
        line = f"{app}: the tracker step writes with {', '.join(granted) or 'nothing'}"
        if refused:
            line += f"; refused, as more than an issue or a comment: {', '.join(refused)}"
        notes.append(line + ".")
    return notes


GITHUB_CHECK = "work reaches GitHub"


def github_check(run=subprocess.run) -> dict:
    """That a run the scheduler starts can use the person's GitHub sign-in (`gh auth status`), as the sync
    task, started the same way, needs to: where the desktop app's sign-in sits where the scheduler cannot
    see it, the probe says so instead of the sync failing unseen. `gh` is the one `which` finds, never a `gh.bat` in
    the current folder or a space: when the only one PATH has is there, the check fails without starting anything.
    When PATH has none at all the bare name is tried, which then finds none either (on Windows the current folder is
    left out, NO_CWD_LOOKUP), and the check says it did not start."""
    gh = which("gh") or (None if shutil.which("gh") else "gh")
    if gh is None:
        return _check(GITHUB_CHECK, False, "GitHub's command line was found only inside a space or the current folder, "
                                           "where a file can be planted, so it was not run and your work would not "
                                           "reach GitHub")
    try:
        res = run([gh, "auth", "status", "--hostname", "github.com"], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return _check(GITHUB_CHECK, False, "GitHub's command line did not start as the scheduler runs it, so your "
                                           "work would not reach GitHub")
    if res.returncode == 0:
        return _check(GITHUB_CHECK, True, "the scheduler's runs use your GitHub sign-in")
    said = next((line.strip() for line in ((res.stderr or "") + (res.stdout or "")).splitlines() if line.strip()), "")
    return _check(GITHUB_CHECK, False, "the scheduler's runs cannot use your GitHub sign-in, so your work would not "
                                       f"reach GitHub: {said[:160]}")


PROBE_NAME = "probe"   # the probe holds the workspace's lock under this name


def probe(workspace, run=subprocess.run) -> int:
    """`--probe`, under the workspace's lock (`take_lock`; while a run holds it, `error` says so and `busy` is
    true): what a run the scheduler starts sees (`probe_tools`, as a routine starts: in the workspace, every
    other root added, without a key or token). Its tools and servers replace the ones probe.json held, so an
    app that went away is granted no more (Ruling R30); `seen` keeps every connector tool ever listed, for the
    deny side (`ever_seen`). Then the checks: each app the routines read (`read_checks`), each folder they
    walk (`folder_check`), mail only to the person (`send_check`, in the shell `hook_shell` finds), the edit and
    person-only guards in that shell and the Python the profiles name (`guards_check`), and GitHub. That
    shell is written as `hook_shell` once the guard a run carries held in it (`proven_shell`): the one a run
    pins and the grant's send tools need. Written to probe.json with the grant's notes and a line per tracker
    (`tracker_notes`: what it may write with, and what was refused), `ok` and `stamp`; an
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
    reach = github_check(run)
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
    checks = [*read_checks(workspace, got, profiles), *[folder_check(r) for r in roots], send_check(got, profiles),
              guards_check(workspace, profiles, candidate), reach]
    ok = all(c["ok"] for c in checks)
    write_probe({**view, "checks": checks, "notes": [*got["notes"], *tracker_notes(view, got)], "ok": ok,
                 "stamp": time.time(), "workspace": str(workspace), "hook_shell": proven_shell(workspace, got)})
    return 0 if ok else 1


# ----- one computer, one runner: where the routines run ------------------------------------------
# A workspace's routines run on one computer through one scheduler: its OS tasks, which start this tool (`os`),
# or the desktop app's own scheduled tasks (`harness`). The record sits in the workspace and is synced, so every
# computer reads it; a session writes it (`onboard.py`: the build, `--routines-here`, `--pause`, `--resume`), never
# a routine (PROTECTED_EDITS). One line per key, the claim's four together and the pause a line apart: a merge that
# keeps both sides of two claims leaves a key twice, which reads as no record anyone may run under (`host`); of two
# pauses made before a sync, the later stands. Every space merges the file by union (.gitattributes, sync.MERGE_RULES).
# The third runner is the cloud (`claim_cloud`): the sweep's scheduled task on claude.ai under CLOUD_MACHINE, which
# no computer's id can take, with its id, the person's zone, the repositories it attaches and the connector grant a
# line each after the claim's four, and then the computer the move ran on, which keeps its daily chain and the
# computer's leg (`sync.Run.keeps_leg`); every other routine steps aside for it (`gate`), and a cloud run asks
# `cloud_gate`.
HOST = Path("_meta/state/routine-host.yaml")
RUNNERS = ("os", "harness", "cloud")
CLAIM_KEYS = ("machine", "name", "runner", "since")
CLOUD_KEYS = ("tasks", "timezone", "roots", "grant")   # the cloud's claim after the four: a cloud record has all
LEG_KEYS = ("computer",)   # after them: a record the move wrote before it named a computer lacks it
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
    made again, the cloud's machine among them (no computer is the cloud), and one that cannot be written leaves the
    id as it is made."""
    import sync
    kept = Path.home() / ".spaces" / "machine-id"
    try:
        said = kept.read_text(encoding="utf-8").strip()
        if MACHINE.fullmatch(said) and said != CLOUD_MACHINE:
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
    anything else, and for a moment without a zone this computer's clock cannot place (Windows: 9999-12-31T00:00:00),
    so a record that holds one reads as unclear."""
    if isinstance(value, str):
        text = value.strip()
        try:
            value = dt.datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text)
        except ValueError:
            return None
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo:
        return value
    try:
        return value.astimezone()
    except (OSError, OverflowError):   # past what this computer's own time can say
        return None


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
    """One of the cloud's keys as the record keeps it; None for a value not of its shape. `tasks`: the sweep's task
    id (`sweep`; a record from before the daily stayed on the computer also names `daily`, which is let go);
    `timezone`: the person's IANA zone; `roots`: the repositories the tasks attach, at
    least one; `grant`: the connector tools by their full names, as `read`, `write` and `send`; `computer`: the id
    of the computer the move ran on (`this_machine`), never the cloud's."""
    import reader
    if key == "computer":
        return value if isinstance(value, str) and MACHINE.fullmatch(value) and value != CLOUD_MACHINE else None
    if key == "tasks":
        ok = isinstance(value, dict) and {"sweep"} <= set(value) <= {"sweep", "daily"} and all(
            isinstance(task, str) and TASK_ID.fullmatch(task) for task in value.values())
        return {"sweep": value["sweep"]} if ok else None
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
    None), with the cloud's keys (`CLOUD_KEYS`: "tasks", "timezone", "roots", "grant") and "computer", the computer
    that keeps the leg (`LEG_KEYS`), only when they are written; None when nothing is recorded. A file that is not
    one record (a key twice, but for the pause, of which the later stands; a merge left half done, a machine or
    runner that is none, a pause that is no moment, a cloud key of the wrong shape, a computer that is no computer's
    id, the cloud without all four of its keys, or the cloud's machine and runner apart) is {"unclear": True}:
    nothing runs under it."""
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
    cloud = {key: _cloud_value(key, data[key]) for key in (*CLOUD_KEYS, *LEG_KEYS) if key in data}
    if (None in cloud.values() or (runner == "cloud") != (machine == CLOUD_MACHINE)
            or (runner == "cloud" and not set(CLOUD_KEYS) <= set(cloud))):
        return {"unclear": True}
    return {"machine": machine, "name": _one_line(name, 60) if isinstance(name, str) else "", "runner": runner,
            "since": _one_line(data.get("since"), 40), **cloud,
            "paused_until": until.isoformat(timespec="seconds") if until else None}


def _write_host(workspace, record: dict) -> dict:
    """The record written whole, each value on its own line as JSON (which YAML reads as it is): the claim's four, the
    cloud's keys and the computer that keeps the leg when the record has them, then the pause."""
    lines = ["# Where this workspace's routines run: one computer, one runner. Written by onboard.py, never by a routine."]
    lines += [f"{key}: {json.dumps(record.get(key), ensure_ascii=False)}" for key in CLAIM_KEYS]
    lines += [f"{key}: {json.dumps(record[key], ensure_ascii=False)}" for key in (*CLOUD_KEYS, *LEG_KEYS)
              if key in record]
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
    """The sweep runs in the cloud from `now` on, as the scheduled task `tasks` names ({"sweep": its id}), in the
    person's `timezone`, attaching the repositories `roots` (owner/name), under the connector `grant` ({"read",
    "write", "send"}: full tool names, read alone). Every computer's sweep stops by itself (`gate`). The record names
    this computer, the one the move ran on, by its id (`computer`): it keeps running the daily chain and the
    computer's leg (`sync.Run.keeps_leg`), as it ran the chains before. A pause still to come is kept: a move is no
    resume. ValueError, writing nothing, for a value not of its shape (`_cloud_value`)."""
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
                                   **cloud, "computer": this_machine(), "paused_until": keep})


def set_pause(workspace, until) -> dict | None:
    """The record, every key of it kept, with its pause set to `until` (a moment; None resumes); None when there is no
    record to set it in. ValueError for an `until` that is no moment this computer can place (`_moment`), so that
    one is never written as a resume."""
    record = host(workspace)
    if record is None or record.get("unclear"):
        return None
    when = _moment(until) if until is not None else None
    if until is not None and when is None:
        raise ValueError(f"{until!r} is no moment this computer can place")
    return _write_host(workspace, {**record, "paused_until": when.isoformat(timespec="seconds") if when else None})


def _held(workspace, now, routine: str | None = None) -> tuple | None:
    """("away", why) when a run must not start on this computer: the sweep runs in the cloud (the sweep, `routine`,
    steps aside everywhere then, and every other routine but on the computer the move ran on, which keeps its daily),
    another computer is the host, the
    app's own scheduled tasks run them, or the record is not one; ("paused", why) while paused; None to go on. A
    pause past what this computer's clock can put in its own time (Windows: past the year 3000) still holds, said as
    the record keeps it."""
    record = host(workspace)
    if record is None:
        return None
    if record.get("unclear"):
        return "away", UNCLEAR
    if record["runner"] == "cloud":   # the sweep everywhere; the rest on any computer but the one the move ran on
        if routine == "sweep-chain" or record.get("computer") not in (None, this_machine()):   # none named: each, as the leg
            return "away", CLOUD
    elif record["machine"] != this_machine():
        return "away", f"runs on {record['name'] or 'another computer'}"
    elif record["runner"] != "os":
        return "away", HARNESS
    until = paused_until(record, now)
    if until is not None:
        try:
            until = until.astimezone()
        except (OSError, OverflowError, ValueError):   # too far ahead for this clock: as the record keeps it
            pass
        return "paused", f"paused until {until:%Y-%m-%d %H:%M}"
    return None


def gate(workspace, now, routine: str | None = None) -> str | None:
    """Why a routine run must not start here: "runs on <name>" (another computer is the host), "paused until <time>",
    they run in the cloud or as the app's own scheduled tasks, or which computer does is unclear. None to go on.
    `main` asks it first."""
    held = _held(workspace, now, routine)
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
    if argv[:1] and argv[0] in GUARD_MODES:   # a hook: before anything reads a workspace
        return _guard_run(argv[0])
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
    held = _held(workspace, now, prompt.stem)   # first (F-11): one computer, one runner, and the pause, before the history is read
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
                      env={**routine_env(os.environ, got["shell"]), ROUTINE_MARK: prompt.stem}, capture_output=True,
                      text=True, encoding="utf-8", errors="replace", timeout=3600)
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
