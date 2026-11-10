"""The cloud sweep's prompt (`_meta/templates/cloud/sweep-chain.md`), the gate every computer prompt starts with, and
the skills that know the sweep may run in the cloud (spec 4.2, 4.4, 4.5a, 4.6 and 4.7;
docs/design/2026-10-28-cloud-sweep-design.md). No guard runs in a cloud run: the task's own deny list
(`cloud.disallowed`) holds it, so every command the prompt gives must be one plain command, with absolute paths and
`gh api` with one word, that the deny list lets through. The proof's one try made with a command (a request past the
network limit) passes the deny list too: the environment must stop it. The proof tries no change made with a command:
the platform refuses the landing of a run that just had one refused, so a proof that tried it could not land."""
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
PROMPT = TEMPLATE / "_meta" / "templates" / "cloud" / "sweep-chain.md"
COMPUTER = TEMPLATE / "_meta" / "templates" / "routines"
SKILLS = TEMPLATE / ".claude" / "skills"
# What each placeholder of a prompt's commands stands for in a run of Ada's (the clone folder names in /home/user).
SAMPLE = {"<workspace>": "ada-space", "<root>": "ada-space", "<repo>": "ada/ada-site", "<number>": "5",
          "<since>": "2026-10-01T08:00:00Z", "<source>": "mail", "<record id>": "gmail-1a2b", "<name>": "report.pdf",
          "<file>": "/home/user/ada-space/_meta/state/private/notices/n1.json", "<message id>": "m1",
          "<address>": "ada@example.test", "<id>": "t1", "<key>": "ADA-1", "<tool>": "status",
          "<handle>": "att-0123456789ab"}
# The read tools the sweep's task is given (`cloud.task_grant`), and a tool the computer has seen that it is not.
READS = ["mcp__mail__get_message", "mcp__mail__search_threads", "mcp__tracker__list_issues"]
NOT_GIVEN = ["mcp__mail__trash_message", "mcp__mail__send_message", "mcp__tracker__create_issue"]
# The proof's one try made with a command, deliberately not one of the prompt's tool calls: a request to a host
# outside the environment's network limit, which the environment must keep from answering.
NETWORK_TRY = "curl -sS -m 10 https://example.com"
TRIES = (NETWORK_TRY,)
PROOF_LINE = ("python3 /home/user/<workspace>/_meta/tools/cloud.py proof-line --chain sweep --edit yes --tool yes "
              "--network yes")
LAND = "python3 /home/user/<workspace>/_meta/tools/sync.py --root /home/user/<workspace> --cloud sweep"
# An attachment's name that holds a single quote, a double quote and a `$`: it never reaches a command, its handle does.
# Commands the sweep's task must refuse by its deny list alone: a tool of the space that writes beyond the sweep, a
# GraphQL call, a pull request listing and a git write. The control that makes a deny list's silence mean something.
MUST_REFUSE = ("python3 /home/user/<workspace>/_meta/tools/sign.py /home/user/<workspace>", "gh pr list",
               "gh api graphql -f q=1", "git push")
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
    return [s for s in spans(body) if re.match(r"(python3?|gh|git|cd|sh|bash|sed|curl) ", s)]


def proof_of(body: str) -> str:
    """The prompt's Proof section, which a proof run does alone."""
    assert body.count("\n## Proof\n") == 1
    return body.split("\n## Proof\n", 1)[1]


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


def gh_rules(tmp_path: Path) -> Path:
    """The `gh` rules alone: a routine's REST reads, and the task's refusal of anything after the endpoint word."""
    return write(tmp_path / "gh.json", {"permissions": {"allow": list(routine.REST_READS),
                                                         "deny": list(cloud.GH_DENY)}})


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
    """A space cloned in the cloud's home: its tool scripts there, as every clone has them, and the cloud prompt."""
    root = make_space(home / name, name=name)
    (root / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    for tool in TOOLS.glob("*.py"):
        (root / "_meta" / "tools" / tool.name).write_text("", encoding="utf-8")
    shutil.copytree(TEMPLATE / "_meta" / "templates" / "cloud", root / "_meta" / "templates" / "cloud",
                    dirs_exist_ok=True)
    return root.resolve()


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """The cloud's /home/user, as test_cloud.py's: an empty folder `SPACES_CLOUD_HOME` names."""
    home = (tmp_path / "home-user").resolve()
    home.mkdir()
    monkeypatch.setenv("SPACES_CLOUD_HOME", str(home))
    return home


def computer_profile(tmp_path: Path, home: Path) -> tuple:
    """A computer routine's profile (`routine.routine_settings`) over Ada's workspace cloned in the cloud's home:
    (the workspace, as POSIX text; the profile's path; its data)."""
    ws = clone(home, "ada-space")
    path = tmp_path / "computer.json"
    data = routine.routine_settings(ws, "", [ws], profile=path)
    write(path, data)
    return ws.as_posix(), path, data


def task_deny(home: Path) -> list:
    """The sweep task's deny list (`cloud.disallowed`), as `trigger` renders it for Ada."""
    ws = clone(home, "ada-space")
    return cloud.disallowed(ws, ["ada/ada-space"], READS, READS + NOT_GIVEN)


def refuses(deny: list):
    """Whether a deny list refuses a command, read as the platform reads a Bash rule: by its text alone."""
    patterns = bash_guard._patterns(deny, False)
    return lambda command: any(p.fullmatch(command) for p in patterns)


def task_refuses(home: Path):
    """Whether the sweep task's deny list refuses a command."""
    return refuses(task_deny(home))


# ----- the cloud prompt -----------------------------------------------------------------------------------------
def test_every_cloud_prompt_command_is_one_absolute_call():
    """Every command a run gives is one call of a space's tool or one `gh api` read, with absolute paths, on one line
    and never two joined: the deny list reads a command by its text alone. The proof's try made with a command
    (`TRIES`) is the one exception, and only in the Proof section: it is what the environment must keep from
    answering, so a sweep run never gives it; it is still one command on one line, with nothing for the shell to join
    or redirect. No command of the prompt changes a file with the shell (`python3 -c`)."""
    body = text(PROMPT)
    commands = commands_in(body)
    assert any("/_meta/tools/sync.py" in c for c in commands)
    sweep_run = body.split("\n## Proof\n", 1)[0]
    for command in commands:
        assert not re.search(r"&&|\|\||;|\|", command), command
        assert "\n" not in command, command
        if command in TRIES:
            assert command in proof_of(body) and command not in sweep_run, command
            assert not set(";&|<>`$\n") & set(unquoted(fill(command))), command
            continue
        assert command.startswith(("python3 /home/user/", "gh api ")), command
        if command.startswith("python3 "):
            assert re.fullmatch(r"python3 /home/user/<(root|workspace)>/_meta/tools/([a-z_]+|<tool>)\.py( \S.*)?",
                                command), command
    assert all(t in commands for t in TRIES)
    assert "python3 -c" not in body and not any("rules.md" in c for c in commands)


def test_no_cloud_prompt_command_is_refused_by_the_tasks_deny_list(machine):
    """Every command the prompt gives goes through the task's deny list, the proof's request among them: it must
    meet the environment's network limit, not a deny rule."""
    refused = task_refuses(machine)
    for command in MUST_REFUSE:
        assert refused(fill(command)), command
    commands = commands_in(text(PROMPT))
    assert all(t in commands for t in TRIES)
    for command in commands:
        assert not refused(fill(command)), command


def test_the_proofs_commands_pass_the_deny_list_and_its_edit_and_tool_do_not(machine):
    """A deny refusal of the proof's request, its proof line or its landing would prove nothing about the network
    limit (and would leave no line to read): none is refused by the task's deny list. Its first two tries are the
    ones the task itself refuses: the `Edit` of rules.md is a deny rule, and the tool it was not given is denied by
    name."""
    proof = proof_of(text(PROMPT))
    assert commands_in(proof) == [NETWORK_TRY, PROOF_LINE, LAND]
    deny = task_deny(machine)
    refused = refuses(deny)
    # The control: the same reading of the same deny list refuses what the task must refuse, so a pass below means
    # the deny list was read and let the proof's commands through, not that it was read as empty.
    for command in MUST_REFUSE:
        assert refused(fill(command)), command
    for command in commands_in(proof):
        assert not refused(fill(command)), command
    assert "Edit(//**/rules.md)" in deny and "mcp__mail__trash_message" in deny
    assert not any(t in deny for t in READS)


def test_the_cloud_prompt_never_calls_graphql_or_git():
    body = text(PROMPT)
    for command in commands_in(body):
        assert not command.startswith(("gh pr", "gh repo", "git ", "cd ")) and "graphql" not in command, command
    assert "--jq" not in body and "--paginate" not in body


def test_the_cloud_sweep_only_reads_and_leaves_the_tracker_and_the_notices_to_the_computer():
    body = text(PROMPT)
    flat = " ".join(body.split())
    for gone in ("tracker.py", "owner_email.py", "morning_view.py", "tracker/SKILL.md", "owner-email/SKILL.md",
                 "send_message", "send tool", "**Tracker**", "**Owner notices.**"):
        assert gone not in body, gone
    assert not any(re.search(r"tracker|owner_email", c) for c in commands_in(body))
    assert "nothing that writes, sends, shares or deletes" in flat and "Send nothing, to anyone" in flat
    assert "daily chain on the person's computer" in flat


def test_every_gh_api_call_of_the_prompt_and_the_sweep_skill_is_one_endpoint_word(tmp_path):
    rules = gh_rules(tmp_path)
    calls = [c for path in (PROMPT, SKILLS / "sweep" / "SKILL.md") for c in gh_calls(text(path))]
    prompt = gh_calls(text(PROMPT))
    assert any("search/issues'?q=" in c for c in prompt) and any(c.endswith("/files'?per_page=100'") for c in prompt)
    assert any("search/issues'?q=" in c for c in gh_calls(text(SKILLS / "sweep" / "SKILL.md")))
    for call in calls:
        filled = fill(call)
        assert len(shlex.split(filled)) == 3 and filled.startswith("gh api "), filled   # gh, api, the endpoint
        assert passes(rules, filled), filled


def test_no_command_of_the_prompt_or_the_sweep_skill_leaves_a_pattern_to_the_shell():
    """A `?`, `*` or `[` outside quotes is a pattern: zsh, the login shell of a Mac and so of its routines' Bash,
    refuses one that matches no file ('no matches found') and runs nothing. Each goes inside quotes."""
    commands = list(commands_in(text(PROMPT))) + gh_calls(text(SKILLS / "sweep" / "SKILL.md"))
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


def test_the_cloud_prompt_begins_with_the_command_its_task_gives():
    body = text(PROMPT)
    first = re.search(r"^0\. \*\*Begin\.\*\*(.*?)(?=^\d+\. |\n\n)", body, re.M | re.S)
    assert first, "step 0 is the begin"
    said = " ".join(first.group(1).split())
    assert "exactly as written" in said and "one command" in said and "stop" in said and "write nothing" in said
    assert "cloud.py begin" not in body and "--grant" not in body   # the task's prompt spells it, never the template


def test_the_cloud_prompt_lands_every_root_the_workspace_last():
    body = " ".join(text(PROMPT).split())
    land = "python3 /home/user/<root>/_meta/tools/sync.py --root /home/user/<root> --cloud sweep"
    assert land in commands_in(text(PROMPT)) and "the workspace last" in body
    assert "--release-leg" not in body


def test_the_cloud_prompts_proof_tries_the_three_things_the_task_must_keep_it_from_doing():
    """An edit, a tool and a request; no change of rules.md made with a command, which the platform answers by
    refusing the run's landing, so a proof that tried it could never land."""
    proof = proof_of(text(PROMPT))
    commands = commands_in(proof)
    assert "python3 -c" not in proof and "--guard" not in proof and "--send" not in proof
    assert "--shell" not in proof and "left_out" not in proof
    assert "three things" in proof and "four things" not in proof
    # 1. Edit refuses a file it has not read with its own error, which is no refusal of the deny list: read it first.
    edit = step(proof, "`Edit`")
    assert edit.index("`Read`") < edit.index("rules.md") < edit.index("`Edit`"), edit
    assert "only a permission refusal counts" in edit, edit
    # 2. A tool the task was not given, which does not exist in the run.
    assert "trash_message" in step(proof, "not given") and "does not have at all counts as refused" in proof
    # 3. A host outside the environment's network limit, which the task cannot set itself: no page back is refused.
    network = step(proof, NETWORK_TRY)
    assert "network limit" in network and "no page came back" in network, network
    # 4. The proof line says each of the three, one flag each, as one line; 5. the landing.
    said = step(proof, "proof-line")
    for flag, what in (("--edit", "the edit"), ("--tool", "the tool"), ("--network", "the request")):
        assert f"`{flag}` {what}" in said, flag
    assert "as one line" in said and "`yes` for each try that was refused" in said, said
    assert commands == [NETWORK_TRY, PROOF_LINE, LAND]
    assert proof.index("`Edit`") < proof.index("trash_message") < proof.index(NETWORK_TRY) < \
        proof.index(PROOF_LINE) < proof.index(LAND)
    # The line as the prompt gives it is one cloud.py takes, with every try's flag (it names no workspace: begin's).
    args = cloud.parse(shlex.split(fill(PROOF_LINE))[2:])
    assert (args.command, args.chain, args.workspace) == ("proof-line", "sweep", None)
    assert (args.edit, args.tool, args.network) == ("yes", "yes", "yes") and not hasattr(args, "shell")


def test_the_proof_line_takes_each_of_its_three_flags_and_no_shell(capsys):
    """cloud.py refuses a proof line that leaves a try's flag out, so a line never says a try was refused by saying
    nothing; and it has no `--shell`, the try the proof no longer makes."""
    line = ["proof-line", "--chain", "sweep", "--edit", "yes", "--tool", "yes", "--network", "yes"]
    assert cloud.parse(line).network == "yes"
    for flag in ("--edit", "--tool", "--network"):
        at = line.index(flag)
        with pytest.raises(SystemExit):
            cloud.parse(line[:at] + line[at + 2:])
    with pytest.raises(SystemExit):
        cloud.parse(line + ["--shell", "no"])
    with pytest.raises(SystemExit):
        cloud.parse(line[:-1] + ["maybe"])
    capsys.readouterr()


def test_the_cloud_prompt_ends_a_run_at_a_tool_or_a_refusal_it_does_not_name():
    body = " ".join(text(PROMPT).split())
    assert "**Tripwire.**" in body
    trip = body.split("**Tripwire.**", 1)[1]
    assert "does not name" in trip and "does not expect" in trip and "land nothing" in trip
    for tool in ("Bash", "Read", "Edit", "Write", "ToolSearch", "Task", "Agent"):   # the built-in tools the run has
        assert f"`{tool}`" in body, tool   # `Agent` too: the second agent's tool, as some versions name `Task`


def test_the_cloud_sweep_names_the_attachment_commands_and_the_rows_they_read():
    for path in (PROMPT, SKILLS / "sweep" / "SKILL.md"):
        body = text(path)
        assert "--attachment-unread <handle>" in body and "--attachment-read <handle>" in body
        assert "--attachments" in body and r"<record id>\t\t<name>\t<handle>" in body and "tab" in body
        # a name a sender chose never goes into a command: the handle is the only value either takes
        assert "<record id> <name>" not in body and "single quotes" not in step(body, "--attachment-unread"), path.name
        assert "never reaches the shell" in step(body, "--attachment-unread <handle>"), path.name
    assert sweep._flag(["--attachment-read", "--draft.pdf"], "--attachment-read", 1) == []
    assert sweep.main(["--attachment-read", "gmail-1a2b", "draft.pdf"]) == 2   # a record id is no handle


def test_an_attachment_handle_reaches_sweep_py_whole(tmp_path, machine):
    """A name the sender chose can hold anything, and none of it reaches the shell: the command carries the
    handle alone, which passes a computer's routine guard and the cloud task's deny list as it is."""
    handle = sweep.attachment_handle("gmail-1a2b", AWKWARD)
    assert sweep.HANDLE_RE.match(handle)
    _, profile, _ = computer_profile(tmp_path, machine)
    refused = task_refuses(tmp_path / "cloud-home")
    for path in (PROMPT, SKILLS / "sweep" / "SKILL.md"):
        for flag in ("--attachment-unread", "--attachment-read"):
            command = next(c for c in commands_in(text(path)) if f"{flag} <handle>" in c)
            command = command.replace("<handle>", handle)
            filled = fill(command, machine.as_posix())
            assert shlex.split(filled)[-2:] == [flag, handle], filled
            assert passes(profile, filled), (path.name, filled)
            assert not refused(fill(command)), (path.name, command)


def test_a_kept_codebase_file_is_read_from_its_clone_as_it_is_now():
    """The cloud runs no git, so a kept codebase file's text is the file as a clone on the repository's default
    branch holds it: in the cloud its clone in /home/user (a placeholder of its own, `<codebase>`, not an attachment's
    `<name>`); on a computer a checkout, only while it is on that branch and the file has no change there. Any other
    file is left out and named in the run line, never guessed."""
    for path in (PROMPT, SKILLS / "sweep" / "SKILL.md"):
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


def test_the_cloud_prompt_sits_outside_the_computers_tasks_and_is_protected(tmp_path):
    ws = make_space(tmp_path / "ada")
    names = [r["name"] for r in start.render_routines(ws, "Ada Okafor")]
    assert names and not any("cloud" in name for name in names)
    assert sorted(p.name for p in (ws / "_meta" / "state" / "routines").glob("*.md")) == \
        sorted(p.name for p in COMPUTER.glob("*.md"))
    assert "_meta/templates/**" in routine.PROTECTED_EDITS
    assert sorted(p.name for p in PROMPT.parent.glob("*.md")) == ["sweep-chain.md"]   # the cloud daily is gone


# ----- the computer's prompts: the gate first ---------------------------------------------------------------------
def test_every_computer_prompt_starts_with_the_gate(tmp_path):
    """The sweep and the daily name themselves to the gate: under a cloud record the sweep is held on every computer
    (`--chain sweep`), while the computer the move ran on keeps running the daily (`--chain daily`). Any other
    routine names no chain and goes by the record's computer."""
    ws = make_space(tmp_path / "ada")
    rendered = start.render_routines(ws, "Ada Okafor")
    assert len(rendered) == len(list(COMPUTER.glob("*.md"))) >= 3
    assert {"sweep-chain", "daily-chain"} <= {task["name"] for task in rendered}
    gate = f"{onboard.python_cmd()} {ws.as_posix()}/_meta/tools/chain.py gate {ws.as_posix()}"
    names = {"sweep-chain": " --chain sweep", "daily-chain": " --chain daily"}
    for task in rendered:
        body = routine.split_prompt(text(Path(task["prompt_file"])))[1]
        expected = gate + names.get(task["name"], "")
        assert [s for s in spans(body) if re.search(r"/_meta/tools/[a-z_]+\.py", s)][0] == expected, task["name"]
        said = next(" ".join(p.split()) for p in body.split("\n\n") if "chain.py gate" in p)
        assert "exits 3" in said and "stop" in said and "write nothing" in said, task["name"]


# ----- cloud.py: a routine's proof line, never its begin or trigger ----------------------------------------------
REFUSED = ("{py} {root}/_meta/tools/cloud.py begin {root} --chain sweep",
           "{py} {root}/_meta/tools/cloud.py begin {root} --chain sweep --proof",
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
           "python3 _meta/tools/cloud.py begin . --chain sweep", "python _meta/tools/cloud.py trigger . --chain sweep")


def test_a_computer_routine_may_call_cloud_py_for_its_proof_line_alone(tmp_path, machine):
    assert "cloud.py" in routine.ROUTINE_TOOLS
    root, path, data = computer_profile(tmp_path, machine)
    for verb in ("begin", "trigger"):
        assert f"Bash(*_meta/tools/cloud.py*{verb}*)" in data["permissions"]["deny"], verb
    for py in ("python3", "python"):
        line = f"{py} {root}/_meta/tools/cloud.py proof-line --chain sweep --edit yes --tool yes --network yes"
        assert passes(path, line), line
        assert passes(path, f"{py} _meta/tools/cloud.py proof-line --chain sweep --edit no --tool yes --network no")
        for refused in REFUSED:
            command = refused.format(py=py, root=root)
            assert not passes(path, command), command


# ----- the skills that know the sweep may run in the cloud -------------------------------------------------------
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


def test_the_onboard_skill_renders_the_sweeps_task_in_the_persons_zone_and_environment():
    body = text(SKILLS / "onboard" / "SKILL.md")
    renders = re.findall(r"cloud\.py trigger[^`]*", body)
    assert len(renders) >= 2
    for render in renders:
        flat = " ".join(render.split())
        assert "--chain sweep" in flat and "--timezone <zone>" in flat and "--environment <env>" in flat, render
    flat = " ".join(body.split())
    assert "Get-TimeZone" in flat and "IANA" in flat and "routine-host.yaml" in flat
    assert "environment_id" in flat and "--chain daily" not in flat
    assert re.search(r"proof line with `edit`, `tool` or `network` false, or a run log that shows any of them going "
                     r"through", flat), \
        "a refusal missing is a proof line's edit, tool or network false, or a run log showing one going through"
    assert ("it tries an edit of `rules.md`, an app tool it was not given and a request to a host outside the network "
            "limit") in flat
    assert "--shell" not in flat and "a command's change" not in flat
    # The network limit is the person's to set on claude.ai, and the proof checks it; a run with it open stops.
    assert "Its network must be limited" in flat and "the proof checks it" in flat
    assert "Network false: limit the environment's network on claude.ai, then prove a new task" in flat
    # The daily stays on the computer, so the move needs this computer to run the routines.
    assert "The move also needs this computer to run the routines" in flat


def test_the_onboard_skill_says_how_a_rendered_body_becomes_the_one_sent():
    """`cloud.py trigger` prints the body with the move's `roots` and `grant` beside it, and connections named only:
    the skill says to drop the two and give each connection its connector's id and address, and that the body made
    without `--proof`, the one the sweep runs with, is completed the same way."""
    flat = " ".join(text(SKILLS / "onboard" / "SKILL.md").split())
    said = re.search(r"cloud\.py trigger[^`]*--proof[^`]*` prints the task's body(.*?)Create the task", flat)
    assert said, "the proof's render step says what to change before the body is sent"
    for word in ("drop `roots` and `grant`", "`mcp_connections`", "`connector_uuid`", "`url`", "`name`",
                 "`permitted_tools`", "with `--proof` or without, is sent completed the same way"):
        assert word in said.group(1), word


def test_the_briefing_is_recorded_with_every_recipient_after_one_to():
    """`record-send --to` keeps every recipient however it is given; the daily prompt and the morning-view skill say
    to put them all after the one `--to`."""
    for path in (COMPUTER / "daily-chain.md", SKILLS / "morning-view" / "SKILL.md"):
        flat = " ".join(text(path).split())
        assert re.search(r"record-send --id <message id> --to <address>", flat), path
        assert "every recipient" in flat and "after the one `--to`" in flat, path
