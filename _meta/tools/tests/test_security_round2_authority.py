"""Authority, second round: what the first round's guards still let through, and what they broke.

- No ask rule meets a routine's own commands (review 3): Claude Code checks ask before allow in every scope, and a
  routine loads the space's `.claude/settings.json` and the person's `settings.local.json` too, so an ask rule that
  matched `chain.py daily <root> --person "<name>"` refused the daily chain every day. That holds for the space's
  profile, which asks nothing for a person's verbs, and for the opt-in rules a person may add
  (`routine.ATTENDED_ASK_OPT_IN`).
- The opt-in rules catch `sign_questions.py answer` and `status.py close` too (review 9).
- A routine's `--person-only` guard finds the verb wherever it sits: after `--` or `--land`, as a glob or a brace at
  any position, through a path spelled `//`, `./` or with a short 8.3 name (review 8).
- Each tool behind PERSON_ONLY refuses itself inside a routine or a cloud run, whatever command reached it
  (review 7): route.py's answer, librarian.py's answer, sign_questions.py's answer, chain.py's virtual
  clock and its verdicts. A cloud run's marker is found even when a command sets `HOME` to another folder.
- `ids.table` checks a large index in bulk: one walk for links, never a resolve per entry (review 5).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
import yaml

import chain
import cloud
import ids
import librarian
import pages
import route
import routine
import sign_questions
import start
from conftest import make_space

REPO = Path(__file__).resolve().parents[3]
BS = chr(92)   # a backslash, spelled so no editor or shell between here and Python takes one away
NOW = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.timezone.utc)


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def rule_matches(rule: str, command: str) -> bool:
    """Claude Code's Bash rule match: `*` is any text; a trailing ` *` also matches the bare command."""
    body = re.fullmatch(r"Bash\((.*)\)", rule)
    if not body:
        return False
    pattern = body.group(1)
    if pattern.endswith(" *") and pattern.count("*") == 1 and command == pattern[:-2]:
        return True
    return re.fullmatch(".*".join(re.escape(p) for p in pattern.split("*")), command, re.S) is not None


def perms() -> dict:
    return json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]


def asks(command: str) -> bool:
    """An ask rule of the space's profile, or one of the opt-in rules a person may add, meets the command."""
    return any(rule_matches(r, command) for r in (*perms().get("ask", []), *routine.ATTENDED_ASK_OPT_IN))


def allowed(command: str) -> bool:
    p = perms()
    return any(rule_matches(r, command) for r in p["allow"]) and not any(rule_matches(r, command) for r in p["deny"])


# ----- review 3: no ask rule meets a routine's own command --------------------------------------------------------
def rendered_commands(ws: Path, root: str) -> list:
    commands = []
    for prompt in (ws / "_meta" / "state" / "routines").glob("*.md"):
        commands += re.findall(r"`([^`]*/_meta/tools/[a-z_]+\.py[^`]*)`", prompt.read_text(encoding="utf-8"))
    return [re.sub(r"<[^<>]*>", "x", c.replace("<root>", root)) for c in commands]


def test_no_ask_rule_meets_a_command_a_rendered_routine_prompt_names(tmp_path):
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    commands = rendered_commands(ws, ws.as_posix())
    assert any("chain.py daily" in c and '"Ada Okafor"' in c for c in commands)   # the command review 3 refused
    for command in commands:
        assert not asks(command), command


def test_no_ask_rule_meets_a_routines_quoted_root(tmp_path):
    """A root that holds a space is quoted, in the rendered prompts and in what a routine runs from a skill."""
    ws = make_space(tmp_path / "Ada Okafor" / "ada")
    start.render_routines(ws, "Ada Okafor")
    for command in rendered_commands(ws, f'"{ws.as_posix()}"'):
        assert not asks(command), command
    py, root = '"C:/Users/Ada Okafor/.spaces/venv/Scripts/python.exe"', '"C:/Users/Ada Okafor/spaces/ada"'
    for command in (f"{py} {root}/_meta/tools/chain.py daily {root} --person \"Ada Okafor\"",
                    f"{py} {root}/_meta/tools/chain.py sweep {root}",
                    f"{py} {root}/_meta/tools/route.py {root} record {root}/_meta/state/private/decisions/d.yaml",
                    f"{py} {root}/_meta/tools/route.py {root} pending --subject harbor",
                    f"{py} {root}/_meta/tools/librarian.py {root} list",
                    f"{py} {root}/_meta/tools/sign_questions.py {root} ask harbor --page p-1 "
                    "--text \"what is the answer? q-1\" --why w --recommendation r --by distill"):
        assert not asks(command), command


def test_no_opt_in_rule_meets_a_root_whose_path_holds_a_verb(tmp_path):
    """A space under `/home/ada/virtual/` or `.../apply/` runs its daily chain all the same: each opt-in rule wants a
    space before the verb, and a path holds none."""
    ws = make_space(tmp_path / "virtual" / "apply" / "answer" / "close" / "ada")
    start.render_routines(ws, "Ada Okafor")
    commands = rendered_commands(ws, ws.as_posix())
    assert any("chain.py daily" in c for c in commands)
    for command in commands:
        assert not asks(command), command


# ----- review 9: the opt-in rules catch every person's verb ------------------------------------------------------
@pytest.mark.parametrize("command", [
    "python _meta/tools/sign_questions.py . answer q-1a2b3c4d --text yes --by Ada",
    "python3 _meta/tools/sign_questions.py answer q-1a2b3c4d --text yes --by Ada",
    "python _meta/tools/status.py . close harbor p-1a2b3c4d --as done --by Ada",
    "python _meta/tools/sign.py . apply --by Ada --verdicts v.yaml",
])
def test_the_opt_in_rules_ask_before_each_persons_verb(command):
    assert allowed(command) and asks(command), command


@pytest.mark.parametrize("command", [
    "python _meta/tools/sign_questions.py . ask harbor --page p-1 --text \"what is the answer?\" --why w "
    "--recommendation r --by distill",
    "python _meta/tools/sign_questions.py . list harbor",
    "python _meta/tools/status.py .",
    "python _meta/tools/route.py . pending",
    "python _meta/tools/librarian.py . list",
    "python _meta/tools/chain.py spaces .",
])
def test_the_attended_profile_still_runs_the_rest_without_asking(command):
    assert allowed(command) and not asks(command), command


# ----- review 8: the routine's guard finds the verb wherever it sits -----------------------------------------------
@pytest.mark.parametrize("command", [
    "python _meta/tools/route.py . -- answe? m-1 --yes --by Ada",           # `--` pushes the glob past position 1
    "python _meta/tools/route.py . -- answer m-1 --yes --by Ada",
    "python _meta/tools/route.py . --x answe? m-1 {-,}-yes",                   # a glob and a brace past `--x`
    "python _meta/tools/chain.py sweep . {virtual,x}",
    "python _meta/tools/sign_questions.py . {answer,} q-1 --text yes --by Ada",
    "python _meta/tools/sign_questions.py .  answer  q-1 --text yes --by Ada",   # two spaces
    "python _meta/tools/status.py . x clos? harbor p-1",
    "python _meta/tools/chain.py sweep . virtua?",
    "python _meta/tools/route.py . pending ~",                                # a tilde expands at the start of a word
    "python _meta/tools//route.py . answer m-1 --yes --by Ada",                # a path spelled another way
    "python _meta//tools/route.py . answer m-1 --yes --by Ada",
    "python _meta/tools/./route.py . answer m-1 --yes --by Ada",
    "python _meta/tools/x/../route.py . answer m-1 --yes --by Ada",
    "python _meta/tools/route.py. . answer m-1 --yes --by Ada",               # Windows drops the trailing dot
    "python _meta/tools/rout?.py . answer m-1 --yes --by Ada",                # a glob in the tool's own name
    "python _meta/tools/SIGN_Q~1.PY . answer q-1 --text yes --by Ada",        # a short 8.3 name
])
def test_a_routine_never_runs_a_persons_verb_however_it_is_spelled(command):
    assert routine.person_only_check(bash(command)), command


@pytest.mark.parametrize("command", [
    "python _meta/tools/route.py . record _meta/state/private/decisions/*.yaml",   # a glob that holds a `/`
    "python C:/Users/ADAOKA~1/ws/_meta/tools/route.py C:/Users/ADAOKA~1/ws pending",  # a short name in the root
    "python _meta/tools/chain.py daily . --person \"Ada Okafor\"",
    "python _meta/tools/sign_questions.py . ask harbor --page p-1 --text \"{x} ~ answer?\" --why w "
    "--recommendation r --by distill",
    "python _meta/tools/search.py . lines \"answer\"",
])
def test_the_commands_a_routine_runs_still_pass_the_guard(command):
    assert routine.person_only_check(bash(command)) is None, command


def test_the_words_the_shell_expands_are_told_apart():
    words = dict(routine.shell_words("a ~ x~y --k=~ 'b*' c* {d,e} f[g]"))
    assert words["~"] == "~" and words["x~y"] == "" and words["--k=~"] == "~"
    assert words["b*"] == "" and words["c*"] == "*" and set(words["{d,e}"]) == {"{", "}"}
    assert set(words["f[g]"]) == {"[", "]"}


# ----- review 7: each person-only tool refuses itself in a routine and in a cloud run -----------------------------
@pytest.fixture(params=["mark", "cloud"])
def in_a_routine(request, monkeypatch):
    if request.param == "mark":
        monkeypatch.setenv(routine.ROUTINE_MARK, "sweep-chain")
    else:
        monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
        cloud.state_path().parent.mkdir(parents=True, exist_ok=True)
        cloud.state_path().write_text("{}", encoding="utf-8")   # a cloud run's marker, under the test's home
    assert routine.in_routine()
    return request.param


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_route_answer_refuses_inside_a_routine(tmp_path, in_a_routine):
    root = make_space(tmp_path / "ada")
    before = snapshot(root)
    for yes in (True, False):
        with pytest.raises(route.RouteError, match="routine|cloud run"):
            route.answer(root, "m-ada-001", yes=yes, by="Ada Okafor")
    assert route.main([str(root), "answer", "m-ada-001", "--yes", "--by", "Ada Okafor"]) == 1
    assert snapshot(root) == before


def test_route_answer_still_runs_for_the_person(tmp_path, monkeypatch):
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    root = make_space(tmp_path / "ada")
    with pytest.raises(route.RouteError, match="no routing question waits"):   # past the refusal, to the question
        route.answer(root, "m-ada-001", yes=True, by="Ada Okafor")


def test_librarian_answer_refuses_inside_a_routine_and_apply_reaches_its_own_checks(tmp_path, in_a_routine):
    """An owner's yes is the person's; `apply` is the tool's own check of a change it derives itself (round 3:
    test_security_round3_routine applies a genuine proposal and refuses a stale or forged one in a routine)."""
    root = make_space(tmp_path / "ada")
    before = snapshot(root)
    with pytest.raises(ValueError, match="routine|cloud run"):
        librarian.answer(root, "lib-1a2b3c4d", by="Ada Okafor")
    with pytest.raises(ValueError) as caught:   # past the routine, to the proposal that is not there
        librarian.apply(root, "lib-1a2b3c4d", "Chloe")
    assert "routine" not in str(caught.value) and "cloud run" not in str(caught.value)
    assert snapshot(root) == before


def test_librarian_apply_still_runs_for_the_person(tmp_path, monkeypatch):
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    root = make_space(tmp_path / "ada")
    with pytest.raises(Exception) as caught:   # past the refusal, to the proposal that is not there
        librarian.apply(root, "lib-1a2b3c4d", "Chloe")
    assert "routine" not in str(caught.value) and "cloud run" not in str(caught.value)


def test_a_sign_question_answer_refuses_inside_a_routine(tmp_path, monkeypatch):
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    root = make_space(tmp_path / "ada")
    now = NOW
    q = sign_questions.ask(root, "harbor", "p-1a2b3c4d", "When does Harbor go live?", "w", "r", [], "distill", now)
    for marker in ("mark", "cloud"):
        if marker == "mark":
            monkeypatch.setenv(routine.ROUTINE_MARK, "sweep-chain")
        else:
            monkeypatch.delenv(routine.ROUTINE_MARK)
            cloud.state_path().parent.mkdir(parents=True, exist_ok=True)
            cloud.state_path().write_text("{}", encoding="utf-8")
        with pytest.raises(sign_questions.QuestionError, match="routine|cloud run"):
            sign_questions.answer(root, q.id, "Ada Okafor", now, text="1 December")
        assert sign_questions.main([str(root), "answer", q.id, "--text", "1 December", "--by", "Ada Okafor"]) == 1
        assert sign_questions.load(root)[q.id].status == "open"
    cloud.state_path().unlink()
    assert sign_questions.answer(root, q.id, "Ada Okafor", now, text="1 December").status == "answered"


def test_the_cloud_marker_is_found_where_home_no_longer_points(tmp_path, monkeypatch):
    """`HOME=/tmp python3 .../route.py ...` moves `Path.home()`, and with it `cloud.state_path()`: the marker is also
    looked for in the account's own home and in the cloud's, so a command that sets HOME does not hide it."""
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    marker = tmp_path / "account" / cloud.STATE
    monkeypatch.setattr(routine, "_marker_places_account", lambda: [marker])
    assert not cloud.state_path().exists() and routine.in_routine() is None
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    assert "cloud run" in routine.in_routine()
    root = make_space(tmp_path / "ada")
    with pytest.raises(route.RouteError, match="cloud run"):
        route.answer(root, "m-ada-001", yes=True, by="Ada Okafor")


@pytest.mark.skipif(os.name == "nt", reason="the account's home and the cloud's are looked for on Linux and macOS")
def test_the_cloud_home_is_one_of_the_places_looked_in(tmp_path, monkeypatch):
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    monkeypatch.setattr(cloud, "HOME", tmp_path / "cloud-home")
    assert routine.in_routine() is None
    (tmp_path / "cloud-home" / cloud.STATE).parent.mkdir(parents=True)
    (tmp_path / "cloud-home" / cloud.STATE).write_text("{}", encoding="utf-8")
    assert "cloud run" in routine.in_routine()


def test_a_place_that_cannot_be_looked_in_leaves_the_others(tmp_path, monkeypatch):
    monkeypatch.delenv(routine.ROUTINE_MARK, raising=False)
    monkeypatch.setattr(routine, "_marker_places_account", lambda: (_ for _ in ()).throw(KeyError("no account")))
    assert routine.in_routine() is None
    cloud.state_path().parent.mkdir(parents=True, exist_ok=True)
    cloud.state_path().write_text("{}", encoding="utf-8")
    assert "cloud run" in routine.in_routine()


def test_the_virtual_clock_refuses_inside_a_routine(tmp_path, in_a_routine, capsys):
    root = make_space(tmp_path / "lantern", mode="fixture")
    assert chain.main(["virtual", "--days", "1", "--roots", str(root)]) == 2
    assert re.search("routine|cloud run", capsys.readouterr().err)
    with pytest.raises(ValueError, match="routine|cloud run"):
        chain.virtual([root], 1, verdicts={"lantern": {"x-1": "yes"}}, verdicts_after_day=1)


# ----- review 5: a large index is checked in bulk ------------------------------------------------------------------
def write_index(root: Path, entries: dict) -> None:
    path = root / ids.IDS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"ids": entries}, sort_keys=True), encoding="utf-8")


def many_pages(root: Path, n: int) -> dict:
    entries = {}
    for i in range(n):
        pid = f"p-{i:08x}"
        rel = f"knowledge/sources/2026/11/m-{i:05d}.md"
        entries[pid] = rel
    folder = root / "knowledge" / "sources" / "2026" / "11"
    folder.mkdir(parents=True, exist_ok=True)
    for pid, rel in list(entries.items())[:50]:   # the check reads no page: a few on disk are enough
        (root / rel).write_text(f"---\nid: {pid}\n---\nbody\n", encoding="utf-8")
    return entries


def test_a_large_index_is_used_without_a_resolve_per_entry(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    entries = many_pages(root, 3000)
    write_index(root, entries)
    calls = []
    real = pages.confine
    monkeypatch.setattr(pages, "confine", lambda *a, **k: calls.append(a) or real(*a, **k))
    ids._CHECKED.clear()
    started = time.monotonic()
    assert ids.table(root) == entries
    assert calls == [] and time.monotonic() - started < 10


def test_a_link_anywhere_on_an_entrys_path_makes_the_table_scan(tmp_path):
    root = make_space(tmp_path / "ada")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("---\nid: p-0000dead\n---\nnot this space's\n", encoding="utf-8")
    (root / "knowledge" / "projects").mkdir(parents=True, exist_ok=True)
    link = root / "knowledge" / "projects" / "harbor"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True,
                              text=True) if os.name == "nt" else None
        if made is None or made.returncode != 0:   # a junction needs no privilege on Windows
            pytest.skip("this system makes no link without a privilege")
    write_index(root, {"p-0000dead": "knowledge/projects/harbor/secret.md"})
    ids._CHECKED.clear()
    assert ids.table(root) == {}   # the scan reads no page through the link
    assert ids.entry_problem(root, "p-0000dead", "knowledge/projects/HARBOR/secret.md")
    assert not ids.usable(root, {"p-0000dead": "knowledge/projects/HARBOR/secret.md"})   # case ignored


@pytest.mark.parametrize("rel", ["../outside/a.md", "knowledge/../a.md", "/etc/a.md", "C:/a.md", "work/a.md",
                                 "knowledge" + BS + "a.md", "knowledge/.git/a.md", "knowledge/a.md.", "knowledge/index.md"])
def test_the_bulk_check_refuses_what_the_full_check_refuses(tmp_path, rel):
    root = make_space(tmp_path / "ada")
    assert ids.entry_problem(root, "p-1a2b3c4d", rel)
    assert not ids.usable(root, {"p-1a2b3c4d": rel})


def test_a_path_that_could_be_a_short_name_gets_the_full_check(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    calls = []
    monkeypatch.setattr(ids, "entry_problem", lambda r, k, v: calls.append(v) or None)
    assert ids.usable(root, {"p-1a2b3c4d": "knowledge/HARBOR~1/a.md", "p-1a2b3c4e": "knowledge/b.md"})
    assert calls == ["knowledge/HARBOR~1/a.md"]
