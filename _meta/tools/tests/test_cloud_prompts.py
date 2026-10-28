"""The cloud chains' prompts (`_meta/templates/cloud/`), the gate every computer prompt starts with, and the skills
that know the routines may run in the cloud (spec 4.2, 4.4, 4.5a, 4.6 and 4.7). A cloud prompt is read by an agent
under the guard `cloud.py begin` writes, so every command it gives must be one that guard lets through, spelled as
the guard reads it: one plain command, absolute paths, and `gh api` with one word."""
import json
import re
import shlex
import shutil
from pathlib import Path

import pytest

import bash_guard
import cloud
import onboard
import routine
import start
import sweep
from conftest import make_space

TEMPLATE = Path(__file__).resolve().parents[3]
TOOLS = TEMPLATE / "_meta" / "tools"
PROMPTS = {chain: TEMPLATE / "_meta" / "templates" / "cloud" / f"{chain}-chain.md" for chain in cloud.CHAINS}
COMPUTER = TEMPLATE / "_meta" / "templates" / "routines"
SKILLS = TEMPLATE / ".claude" / "skills"
TRY = 'python3 -c "print(1)"'   # the proof's one command outside the allow list, which the guard must refuse
# What each placeholder of a prompt's commands stands for in a run of Ada's (the clone folder names in /home/user).
SAMPLE = {"<workspace>": "ada-space", "<root>": "ada-space", "<repo>": "ada/ada-site", "<number>": "5",
          "<since>": "2026-10-01T08:00:00Z", "<source>": "mail", "<record id>": "gmail-1a2b", "<name>": "report.pdf",
          "<file>": "/home/user/ada-space/_meta/state/private/notices/n1.json", "<message id>": "m1",
          "<address>": "ada@example.test", "<id>": "t1", "<key>": "ADA-1", "<tool>": "status"}
# The task's grant a cloud run's profile is written from (`cloud.write_profile`): read tools, a tracker write, a send.
GRANT = {"read": ["mcp__mail__get_message", "mcp__mail__search_threads", "mcp__tracker__list_issues"],
         "write": ["mcp__tracker__create_issue"], "send": ["mcp__mail__send_message"]}
# An attachment's name that holds a single quote, a double quote and a `$`, and how the prompt and the skill write it.
AWKWARD = 'Ada\'s "final" $5.pdf'
AWKWARD_WRITTEN = "'Ada'\"'\"'s \"final\" $5.pdf'"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def spans(body: str) -> list:
    return re.findall(r"`([^`]+)`", body)


def step(body: str, needle: str) -> str:
    """The numbered step of `body` that holds `needle`, on one line."""
    return " ".join(next(p for p in re.split(r"\n(?=\d+\. )", body) if needle in p).split())


def written(name: str) -> str:
    """An attachment's name as the cloud sweep and the sweep skill say to write it in a command: as it is when it
    holds only letters, digits, `.`, `-` and `_`; else between single quotes, each single quote in it as '"'"'."""
    if re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return name
    return "'" + name.replace("'", "'\"'\"'") + "'"


def commands_in(body: str) -> list:
    """Every command the text gives: the code spans that start with a program a run calls."""
    return [s for s in spans(body) if re.match(r"(python3?|gh|git|cd|sh|bash) ", s)]


def gh_calls(body: str) -> list:
    return [c for c in commands_in(body) if c.startswith("gh ")]


def fill(command: str, home: str = "/home/user") -> str:
    """The command as a run of Ada's writes it: every placeholder filled, /home/user as `home`."""
    for slot, value in SAMPLE.items():
        command = command.replace(slot, value)
    assert not re.search(r"<[a-z][a-z ]*>", command), command
    return command.replace("/home/user/", f"{home}/")


def event(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}


def passes(profile: Path, command: str) -> bool:
    return bash_guard.check(profile, event(command)) is None


def write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def cloud_rules(tmp_path: Path) -> Path:
    """The `gh` rules a cloud run's profile holds, alone: its REST reads (the routine's own, which `begin` writes
    through `routine.routine_settings`, and the cloud's), and its refusal of anything after the endpoint word."""
    allow = list(dict.fromkeys([*routine.REST_READS, *cloud.EXTRA_ALLOW]))
    return write(tmp_path / "gh.json", {"permissions": {"allow": allow, "deny": list(cloud.GH_DENY)}})


def unquoted(command: str) -> str:
    """The characters of `command` outside its quotes: what a shell reads as a pattern, as zsh, a Mac's login shell,
    refuses a pattern that matches no file."""
    out, quote = [], None
    for ch in command:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        else:
            out.append(ch)
    return "".join(out)


def clone(home: Path, name: str) -> Path:
    """A space cloned in the cloud's home: its tool scripts there, as every clone has them, and the cloud prompts
    the run's profile is written from."""
    root = make_space(home / name, name=name)
    (root / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    for tool in TOOLS.glob("*.py"):
        (root / "_meta" / "tools" / tool.name).write_text("", encoding="utf-8")
    shutil.copytree(TEMPLATE / "_meta" / "templates" / "cloud", root / "_meta" / "templates" / "cloud",
                    dirs_exist_ok=True)
    return root.resolve()


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """The cloud's /home/user, as test_cloud.py's: an empty folder `SPACES_CLOUD_HOME` names. The home folder, where
    begin writes the guard profile and the state the guard plugin reads, is conftest's."""
    home = (tmp_path / "home-user").resolve()
    home.mkdir()
    monkeypatch.setenv("SPACES_CLOUD_HOME", str(home))
    return home


def profiles(tmp_path: Path, home: Path, chain: str = "sweep") -> dict:
    """The two profiles a routine's commands meet, over Ada's workspace cloned in the cloud's home: a computer
    routine's (`routine.routine_settings`), and a cloud run's of `chain` as its begin writes it
    (`cloud.write_profile`, from the task's grant). {"root": the workspace, name: (path, data)}."""
    ws = clone(home, "ada-space")
    computer = tmp_path / "computer.json"
    write(computer, routine.routine_settings(ws, "", [ws], profile=computer))
    run = cloud.write_profile(ws, [ws], chain, GRANT, "ada@example.test")
    return {"root": ws.as_posix(),
            **{name: (path, json.loads(path.read_text(encoding="utf-8"))) for name, path in
               (("computer", computer), ("cloud", run))}}


# ----- the cloud prompts ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("chain", cloud.CHAINS)
def test_every_cloud_prompt_command_is_one_absolute_call(chain):
    commands = commands_in(text(PROMPTS[chain]))
    assert any("/_meta/tools/sync.py" in c for c in commands)
    for command in commands:
        if command == TRY:
            continue
        assert command.startswith(("python3 /home/user/", "gh api ")), command
        assert not re.search(r"&&|\|\||;|\|", command), command
        if command.startswith("python3 "):
            assert re.fullmatch(r"python3 /home/user/<(root|workspace)>/_meta/tools/([a-z_]+|<tool>)\.py( \S.*)?",
                                command), command
        assert "\n" not in command, command


@pytest.mark.parametrize("chain", cloud.CHAINS)
def test_every_cloud_prompt_command_passes_the_guard_its_begin_writes(tmp_path, machine, chain):
    profile = profiles(tmp_path, machine, chain)["cloud"][0]
    assert profile == cloud.profile_path(chain)   # begin's own file, which the guard plugin reads
    commands = commands_in(text(PROMPTS[chain]))
    for command in commands:
        filled = fill(command, machine.as_posix())
        assert passes(profile, filled) == (command != TRY), filled
    assert TRY in commands   # the proof tries the one command the guard must refuse


@pytest.mark.parametrize("chain", cloud.CHAINS)
def test_the_cloud_prompts_never_call_graphql_or_git(chain):
    body = text(PROMPTS[chain])
    for command in commands_in(body):
        assert not command.startswith(("gh pr", "gh repo", "git ", "cd ")) and "graphql" not in command, command
    assert "--jq" not in body and "--paginate" not in body


def test_every_gh_api_call_of_the_prompts_and_the_sweep_skill_is_one_endpoint_word(tmp_path):
    rules = cloud_rules(tmp_path)
    calls = [c for path in [*PROMPTS.values(), SKILLS / "sweep" / "SKILL.md"] for c in gh_calls(text(path))]
    sweep = gh_calls(text(PROMPTS["sweep"]))
    assert any("search/issues'?q=" in c for c in sweep) and any(c.endswith("/files'?per_page=100'") for c in sweep)
    assert any("search/issues'?q=" in c for c in gh_calls(text(SKILLS / "sweep" / "SKILL.md")))
    for call in calls:
        filled = fill(call)
        assert len(shlex.split(filled)) == 3 and filled.startswith("gh api "), filled   # gh, api, the endpoint
        assert passes(rules, filled), filled


def test_no_command_of_the_prompts_or_the_sweep_skill_leaves_a_pattern_to_the_shell():
    """A `?`, `*` or `[` outside quotes is a pattern: zsh, the login shell of a Mac and so of its routines' Bash,
    refuses one that matches no file ('no matches found') and runs nothing. Each goes inside quotes."""
    commands = [c for path in PROMPTS.values() for c in commands_in(text(path)) if c != TRY]
    commands += gh_calls(text(SKILLS / "sweep" / "SKILL.md"))
    assert any("?" in c for c in commands)
    for command in commands:
        assert not set("?*[") & set(unquoted(fill(command))), command


def test_the_computers_routines_read_the_repository_feed_as_the_skill_says(tmp_path):
    """The sweep skill reads pull requests through REST alone, on a computer as in the cloud: a computer's routine
    lets through its REST reads, one word each, and still nothing else of `gh api`."""
    ws = make_space(tmp_path / "ada")
    profile = write(tmp_path / "p.json", routine.routine_settings(ws, "", [ws], profile=tmp_path / "p.json"))
    for call in gh_calls(text(SKILLS / "sweep" / "SKILL.md")):
        assert passes(profile, fill(call)), call
    for refused in ("gh api repos/x", "gh api user", "gh api graphql -f query=x",
                    "gh api repos/ada/ada-site/pulls/5/merge -X PUT", "gh api repos/ada/ada-site/pulls/5\t-XPUT",
                    "gh api search/issues?q=x --paginate", "gh api search/issues'?q=x' -XPOST",
                    "gh api search/issues'?q=x'\t--method=POST", "gh api search/graphql"):
        assert not passes(profile, refused), refused


@pytest.mark.parametrize("chain", cloud.CHAINS)
def test_both_cloud_prompts_begin_with_the_command_their_task_gives(chain):
    body = text(PROMPTS[chain])
    first = re.search(r"^0\. \*\*Begin\.\*\*(.*?)(?=^\d+\. |\n\n)", body, re.M | re.S)
    assert first, "step 0 is the begin"
    said = " ".join(first.group(1).split())
    assert "exactly as written" in said and "one command" in said and "stop" in said and "write nothing" in said
    assert "cloud.py begin" not in body and "--grant" not in body   # the task's prompt spells it, never the template


@pytest.mark.parametrize("chain", cloud.CHAINS)
def test_both_cloud_prompts_land_every_root_the_workspace_last(chain):
    body = " ".join(text(PROMPTS[chain]).split())
    land = f"python3 /home/user/<root>/_meta/tools/sync.py --root /home/user/<root> --cloud {chain}"
    assert land in commands_in(text(PROMPTS[chain])) and "the workspace last" in body
    assert "--release-leg" not in body


@pytest.mark.parametrize("chain", cloud.CHAINS)
def test_both_cloud_prompts_have_a_proof_that_tries_each_refusal(chain):
    body = text(PROMPTS[chain])
    assert body.count("\n## Proof\n") == 1
    proof = body.split("\n## Proof\n", 1)[1]
    commands = commands_in(proof)
    assert commands[0] == TRY and "/home/user/<workspace>/rules.md" in proof and "trash_message" in proof
    # Edit refuses a file it has not read with its own error, which is no refusal of the profile's: read it first.
    edit = " ".join(next(p for p in re.split(r"\n(?=\d+\. )", proof) if "rules.md" in p).split())
    assert edit.index("`Read`") < edit.index("rules.md") < edit.index("`Edit`"), edit
    assert "only a permission refusal counts" in edit, edit
    line = next(c for c in commands if "cloud.py proof-line" in c)
    assert line == (f"python3 /home/user/<workspace>/_meta/tools/cloud.py proof-line --chain {chain} --guard yes "
                    f"--edit yes --tool yes" + (" --send yes" if chain == "daily" else ""))
    assert commands[-1] == f"python3 /home/user/<workspace>/_meta/tools/sync.py --root /home/user/<workspace> --cloud {chain}"
    assert ("nobody@example.test" in proof) == (chain == "daily")


@pytest.mark.parametrize("chain", cloud.CHAINS)
def test_both_cloud_prompts_end_a_run_at_a_tool_or_a_refusal_they_do_not_name(chain):
    body = " ".join(text(PROMPTS[chain]).split())
    assert "**Tripwire.**" in body
    trip = body.split("**Tripwire.**", 1)[1]
    assert "does not name" in trip and "does not expect" in trip and "land nothing" in trip
    for tool in ("Bash", "Read", "Edit", "Write", "ToolSearch", "Task", "Agent"):   # the built-in tools the run has
        assert f"`{tool}`" in body, tool   # `Agent` too: the second agent's tool, as some versions name `Task`


def test_the_cloud_sweep_names_the_attachment_commands_and_the_rows_they_read():
    for path in (PROMPTS["sweep"], SKILLS / "sweep" / "SKILL.md"):
        body = text(path)
        assert "--attachment-unread <record id> <name>" in body and "--attachment-read <record id> <name>" in body
        assert "--attachments" in body and r"<record id>\t\t<name>" in body and "tab" in body
        # One rule for a name, the same in both: single quotes, each single quote in it closed, given between
        # double quotes and opened again; a line break, which the guard refuses even between quotes, is not marked.
        said = step(body, "--attachment-unread <record id> <name>")
        assert "letters, digits, `.`, `-` and `_`" in said and "single quotes" in said, path.name
        assert "`'\"'\"'`" in said, path.name
        assert "line break" in said and "left unmarked" in said and "run line" in said, path.name
        assert "double quotes" not in said and "cannot be written" not in said, path.name
        # sweep.py takes a value that starts with `--` for a flag and refuses it: that name is not marked either.
        assert "starts with `--`" in said and said.count("left unmarked") == 2, path.name
    assert sweep._flag(["--attachment-read", "gmail-1a2b", "--draft.pdf"], "--attachment-read", 2) == []


def test_an_attachment_name_with_quotes_and_a_dollar_reaches_sweep_py_whole(tmp_path, machine):
    """A name the sender chose can hold anything: written as the rule says, its attachment command passes the guard
    of a computer's routine and of a cloud run, and the shell hands sweep.py the exact name."""
    assert written(AWKWARD) == AWKWARD_WRITTEN and written("report-1_v2.pdf") == "report-1_v2.pdf"
    made = profiles(tmp_path, machine)
    for path in (PROMPTS["sweep"], SKILLS / "sweep" / "SKILL.md"):
        assert f"`{AWKWARD_WRITTEN}`" in text(path), path.name   # the example each gives is this name, so written
        for flag in ("--attachment-unread", "--attachment-read"):
            command = next(c for c in commands_in(text(path)) if f"{flag} <record id> <name>" in c)
            filled = fill(command.replace("<name>", written(AWKWARD)), machine.as_posix())
            assert shlex.split(filled)[-3:] == [flag, "gmail-1a2b", AWKWARD], filled
            for name in ("computer", "cloud"):
                assert passes(made[name][0], filled), (path.name, name, filled)


def test_a_kept_codebase_file_is_read_from_its_clone_as_it_is_now():
    """The cloud runs no git, so a kept codebase file's text is the file as a clone on the repository's default
    branch holds it: in the cloud its clone in /home/user (a placeholder of its own, `<codebase>`, not an attachment's
    `<name>`); on a computer a checkout, only while it is on that branch and the file has no change there. Any other
    file is left out and named in the run line, never guessed."""
    for path in (PROMPTS["sweep"], SKILLS / "sweep" / "SKILL.md"):
        said = step(text(path), "<codebase>")
        assert "`/home/user/<codebase>/<path>`" in said and "/home/user/<name>/" not in said, path.name
        assert "on `main`" not in said, path.name
        rule = said[said.index("default branch"):]   # the rule, from where it names the branch
        assert "`/home/user/<codebase>/<path>`" in rule and "left out" in rule and "run line" in rule, path.name
    skill = step(text(SKILLS / "sweep" / "SKILL.md"), "<codebase>")
    assert re.search(r"on a computer[^.]*checkout[^.]*on that branch[^.]*no change", skill), skill


def test_the_sweep_skill_says_how_a_cloud_sweep_differs():
    """Where the skill says when it runs, a session learns what a cloud sweep does its own way: every feed but the
    repository's through a connector, and the steps that say the rest (GitHub and a codebase's files in step 1, the
    attachments in step 2), pointed to rather than said twice."""
    when = text(SKILLS / "sweep" / "SKILL.md").split("\n## When\n", 1)[1].split("\n## ", 1)[0]
    said = " ".join(next(b for b in when.split("\n- ") if b.startswith("In the cloud")).split())
    for part in ("every feed but the repository's comes through a connector", "step 1", "step 2"):
        assert part in said, part


def test_the_cloud_prompts_sit_outside_the_computers_tasks_and_are_protected(tmp_path):
    ws = make_space(tmp_path / "ada")
    names = [r["name"] for r in start.render_routines(ws, "Ada Okafor")]
    assert names and not any("cloud" in name for name in names)
    assert sorted(p.name for p in (ws / "_meta" / "state" / "routines").glob("*.md")) == \
        sorted(p.name for p in COMPUTER.glob("*.md"))
    assert "_meta/templates/**" in routine.PROTECTED_EDITS


# ----- the computer's prompts: the gate first ---------------------------------------------------------------------
def test_every_computer_prompt_starts_with_the_gate(tmp_path):
    ws = make_space(tmp_path / "ada")
    rendered = start.render_routines(ws, "Ada Okafor")
    assert len(rendered) == len(list(COMPUTER.glob("*.md"))) >= 3
    gate = f"{onboard.python_cmd()} {ws.as_posix()}/_meta/tools/chain.py gate {ws.as_posix()}"
    for task in rendered:
        body = routine.split_prompt(text(Path(task["prompt_file"])))[1]
        assert [s for s in spans(body) if re.search(r"/_meta/tools/[a-z_]+\.py", s)][0] == gate, task["name"]
        said = next(" ".join(p.split()) for p in body.split("\n\n") if "chain.py gate" in p)
        assert "exits 3" in said and "stop" in said and "write nothing" in said, task["name"]


# ----- cloud.py: a routine's proof line, never its begin or trigger ----------------------------------------------
REFUSED = ("{py} {root}/_meta/tools/cloud.py begin {root} --chain sweep",
           "{py} {root}/_meta/tools/cloud.py begin {root} --chain daily --proof",
           "{py} {root}/_meta/tools/cloud.py trigger {root} --chain sweep",
           "{py} {root}/_meta/tools/cloud.py  begin {root} --chain sweep",          # two spaces
           "{py} {root}/_meta/tools/cloud.py\tbegin {root} --chain sweep",         # a tab
           "{py} {root}/_meta/tools/cloud.py 'begin' {root} --chain sweep",        # quoted
           '{py} {root}/_meta/tools/cloud.py "trigger" {root} --chain sweep',
           "{py} {root}/_meta/tools/cloud.py b'e'gin {root} --chain sweep",        # split by quotes
           "{py} {root}/_meta/tools/cloud.py tri''gger {root} --chain sweep",
           "{py} {root}/_meta/tools/cloud.py {{begin,}} {root} --chain sweep",     # out of a brace
           "{py} {root}/_meta/tools/cloud.py beg?n {root} --chain sweep",          # a pattern the shell expands
           "{py} {root}/_meta/tools/cloud.py b* {root} --chain sweep",
           "{py} {root}/_meta/tools/cloud.py proof-line; {py} {root}/_meta/tools/cloud.py begin {root} --chain sweep",
           "python3 _meta/tools/cloud.py begin . --chain sweep", "python _meta/tools/cloud.py trigger . --chain daily")


def test_a_routine_may_call_cloud_py_for_its_proof_line_alone(tmp_path, machine):
    assert "cloud.py" in routine.ROUTINE_TOOLS
    made = profiles(tmp_path, machine)
    root = made["root"]
    for name in ("computer", "cloud"):
        path, data = made[name]
        for verb in ("begin", "trigger"):
            assert f"Bash(*_meta/tools/cloud.py*{verb}*)" in data["permissions"]["deny"], (name, verb)
        for py in ("python3", "python"):
            line = f"{py} {root}/_meta/tools/cloud.py proof-line --chain daily --guard yes --edit yes --tool yes --send yes"
            assert passes(path, line), (name, line)
            assert passes(path, f"{py} _meta/tools/cloud.py proof-line --chain sweep --guard yes --edit no --tool yes")
            for refused in REFUSED:
                command = refused.format(py=py, root=root)
                assert not passes(path, command), (name, command)


# ----- the skills that know the routines may run in the cloud ----------------------------------------------------
@pytest.mark.parametrize("skill", ["sweep", "distill", "owner-email"])
def test_a_session_on_a_computer_leaves_the_clouds_work_to_the_cloud(skill):
    body = " ".join(text(SKILLS / skill / "SKILL.md").split())
    assert '`runner: "cloud"`' in body and "_meta/state/routine-host.yaml" in body, skill
    if skill == "owner-email":
        assert "queued itself" in body


def test_the_sweep_skill_reads_pull_requests_through_rest_only():
    body = text(SKILLS / "sweep" / "SKILL.md")
    for command in commands_in(body):
        assert not command.startswith(("gh pr", "gh repo")) and "graphql" not in command, command
    for call in gh_calls(body):
        assert not re.search(r"--jq|--paginate|--json| -[A-Za-z]", call), call


def test_the_onboard_skill_renders_both_tasks_in_the_persons_zone():
    body = text(SKILLS / "onboard" / "SKILL.md")
    renders = re.findall(r"cloud\.py trigger[^`]*", body)
    assert len(renders) >= 2
    for render in renders:
        assert "--timezone <zone>" in " ".join(render.split()), render
    flat = " ".join(body.split())
    assert "Get-TimeZone" in flat and "IANA" in flat and "routine-host.yaml" in flat
    assert re.search(r"send guard[^.]*proven only when[^.]*daily[^.]*proof line[^.]*send", flat), \
        "the send guard counts as proven only by a daily proof line that shows its send refused"
