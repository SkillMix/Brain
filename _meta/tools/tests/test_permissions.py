"""The permission profiles: the space's own (`.claude/settings.json`) and the one routine.py adds
to every unattended run. A swept record is text anyone can write, so neither profile may let an
agent write a script and then run it, and the routine profile keeps routines off the machinery."""
import json
import re
from pathlib import Path

import routine
import start
from conftest import make_space

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "_meta" / "tools"
PROFILE = REPO / ".claude" / "settings.json"
# Tools a skill names that still ask in an attended session, with the reason.
ASKS = {"template_sync.py": "copies machinery from the path --from names into _meta/tools/"}
# Skills the routine prompts follow, and tools those skills name that a routine never runs.
ROUTINE_SKILLS = ("sweep", "route", "classify", "distill", "check", "status", "release", "request",
                  "tracker", "owner-email", "morning-view", "outbox-reconcile", "sync")
NOT_FOR_ROUTINES = {"sign.py": "signing is the person's", "reply.py": "applies the person's replies"}


def profile() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))["permissions"]


# The sweep skill has a session check the lock with `routine.py --locked`; a routine holds it and never runs it.
NOT_FOR_ROUTINES["routine.py"] = "a session checks the lock; a routine holds it"


def named_tools(*paths) -> set:
    """Every tool script the given files name, as long as it exists in `_meta/tools/`."""
    text = "\n".join(Path(p).read_text(encoding="utf-8") for p in paths)
    return {n for n in re.findall(r"\b([a-z][a-z_]*\.py)\b", text) if (TOOLS / n).is_file()}


def matches(rule: str, command: str) -> bool:
    """Claude Code's Bash rule match: `*` is any text; a trailing ` *` also matches the bare command."""
    body = re.fullmatch(r"Bash\((.*)\)", rule)
    if not body:
        return False
    pattern = body.group(1)
    if pattern.endswith(" *") and pattern.count("*") == 1 and command == pattern[:-2]:
        return True
    return re.fullmatch(".*".join(re.escape(p) for p in pattern.split("*")), command, re.S) is not None


def allowed(rules: dict, command: str) -> bool:
    return (not any(matches(r, command) for r in rules["deny"])
            and any(matches(r, command) for r in rules["allow"]))


def test_the_profile_allows_each_tool_by_name_never_the_folder():
    rules = profile()
    assert not [r for r in rules["allow"] if re.search(r"_meta/tools/\*", r)]
    assert not [r for r in rules["allow"] if "-m " in r]   # `-m` imports from the working folder first
    tools = {m.group(2) for r in rules["allow"] if (m := re.fullmatch(r"Bash\((python3?) _meta/tools/(\S+) \*\)", r))}
    assert tools and all((TOOLS / t).is_file() for t in tools)
    for tool in tools:   # both spellings, so macOS (no bare `python`) gets the same list
        assert f"Bash(python _meta/tools/{tool} *)" in rules["allow"]
        assert f"Bash(python3 _meta/tools/{tool} *)" in rules["allow"]


def test_every_tool_a_skill_or_instruction_names_runs_without_a_prompt():
    files = [*(REPO / ".claude" / "skills").glob("*/SKILL.md"), *(REPO / "_meta" / "templates" / "routines").glob("*.md"),
             REPO / "_meta" / "templates" / "AGENTS.md.tpl", REPO / "_meta" / "templates" / "personal-instruction.md",
             REPO / "CLAUDE.md"]
    rules = profile()
    missing = sorted(t for t in named_tools(*files) - set(ASKS) if not allowed(rules, f"python _meta/tools/{t} ."))
    assert missing == []
    for tool in ASKS:
        assert not allowed(rules, f"python _meta/tools/{tool} .")


def test_a_script_written_into_the_tools_folder_does_not_run_without_a_prompt():
    rules = profile()
    for command in ("python _meta/tools/evil.py", "python3 _meta/tools/evil.py --x", "python _meta/tools/status.py.bak",
                    "python -m pytest", "git difftool -x sh", "git log -1 --output=_meta/tools/status.py"):
        assert not allowed(rules, command), command
    assert allowed(rules, "python _meta/tools/status.py . --json") and allowed(rules, "git log")


def test_no_path_rule_for_a_tool_claude_code_never_checks():
    for scope in profile().values():
        assert not [r for r in scope if re.match(r"(Write|MultiEdit|NotebookEdit)\(", r)]


def test_the_routine_profile_refuses_edits_to_the_machinery_in_every_space(tmp_path):
    other = make_space(tmp_path / "brain")
    data = routine.routine_settings(REPO, "", roots=[REPO, other])["permissions"]
    for pattern in ("_meta/tools/**", ".claude/**", ".github/**", "rules.md", "space.yaml", ".git/**",
                    "_meta/migrations/**", "_meta/state/routines/**", "CLAUDE.md", "AGENTS.md"):
        assert f"Edit(//**/{pattern})" in data["deny"]
    for root in (REPO, other):
        assert f"Edit(/{routine.rule_path(root)}/identity/**)" in data["deny"]
    assert not [r for r in data["deny"] + data["allow"] if re.match(r"(Write|MultiEdit|NotebookEdit)\(", r)]


def test_a_windows_drive_is_matched_the_way_claude_code_spells_it():
    if re.match(r"^[A-Za-z]:", str(REPO.resolve())):
        assert routine.rule_path(REPO).startswith(f"/{str(REPO.resolve())[0].lower()}/")
    else:
        assert routine.rule_path(REPO) == REPO.resolve().as_posix()


def test_every_tool_the_routines_name_is_in_the_routine_profile():
    files = [*(REPO / "_meta" / "templates" / "routines").glob("*.md"),
             *(REPO / ".claude" / "skills" / s / "SKILL.md" for s in ROUTINE_SKILLS)]
    assert sorted(named_tools(*files) - set(routine.ROUTINE_TOOLS) - set(NOT_FOR_ROUTINES)) == []
    assert all((TOOLS / t).is_file() for t in routine.ROUTINE_TOOLS)


def test_a_rendered_routine_prompt_runs_its_tools_and_nothing_else(tmp_path):
    ws = make_space(tmp_path / "ada")
    start.render_routines(ws, "Ada Okafor")
    for prompt in (ws / "_meta" / "state" / "routines").glob("*.md"):
        body = prompt.read_text(encoding="utf-8").split("---", 2)[2]
        rules = routine.routine_settings(ws, body, roots=[ws, REPO])["permissions"]
        merged = {"allow": profile()["allow"] + rules["allow"], "deny": profile()["deny"] + rules["deny"]}
        commands = re.findall(r"`([^`]*/_meta/tools/[a-z_]+\.py[^`]*)`", body)
        commands = [c.replace("<root>", REPO.as_posix()) for c in commands]
        assert commands
        for command in commands:
            assert allowed(merged, command), command
        py = routine.interpreters(body)[0]
        for refused in (f"{py} {ws.as_posix()}/_meta/tools/evil.py", f"{py} {REPO.as_posix()}/_meta/tools/sign.py apply x",
                        "python _meta/tools/sign.py apply verdicts.yaml", "python _meta/tools/onboard.py . --os-routines",
                        f"{py} {REPO.as_posix()}/_meta/tools/routine.py x", "python -m pytest",
                        f"{py} {(ws / 'work').as_posix()}/_meta/tools/chain.py daily ."):
            assert not allowed(merged, refused), refused


def test_a_prompt_cannot_name_its_own_interpreter_or_root(tmp_path):
    ws = make_space(tmp_path / "ada")
    planted = ws / "work" / "python.exe"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("", encoding="utf-8")
    body = f"Run `{planted.as_posix()} {(tmp_path / 'elsewhere').as_posix()}/_meta/tools/chain.py daily .`"
    rules = routine.routine_settings(ws, body, roots=[ws])["permissions"]
    assert not [r for r in rules["allow"] if planted.as_posix() in r or "elsewhere" in r]
