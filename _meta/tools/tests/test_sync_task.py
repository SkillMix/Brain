"""The sync task on every system: a hidden Windows task that runs on battery and catches up, a launch
agent on macOS, a persistent systemd user timer on a Linux that has one, else a cron line that finds its
tools; read back by `onboard --check`, removed by offboard."""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import offboard
import onboard
import start
from conftest import make_space


def world(answers: dict, calls: list):
    """A fake outside: `answers` maps the first words of a command to (code, stdout); every
    call is recorded with the contents of any file it names (the task XML, the crontab)."""
    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        files = {a: Path(a).read_bytes() for a in argv if os.path.isabs(a) and Path(a).is_file()}
        calls.append((argv, files))
        for prefix, (code, out) in answers.items():
            if " ".join(argv).startswith(prefix):
                said = "crontab: no crontab for ada" if argv[:2] == ["crontab", "-l"] else "failed"   # what a user with none is told
                return subprocess.CompletedProcess(argv, code, out, "" if code == 0 else said)
        return subprocess.CompletedProcess(argv, 0, "", "")
    return fake


# ----- what each scheduler would hand the program, read back from the text it was given -----------------
def split_windows(line: str) -> list:
    """The words a Windows program is started with from this arguments line (CommandLineToArgvW's rules)."""
    words, word, quoted, started, i = [], [], False, False, 0
    while i < len(line):
        if line[i] == "\\":
            run = len(line[i:]) - len(line[i:].lstrip("\\"))
            i += run
            quote = i < len(line) and line[i] == '"'
            word.append("\\" * (run // 2 if quote else run))
            if quote and run % 2:   # an odd run escapes the quote that follows
                word.append('"')
                i += 1
            started = True
            continue
        if line[i] == '"':
            quoted, started = not quoted, True
        elif line[i] in " \t" and not quoted:
            if started:
                words.append("".join(word))
                word, started = [], False
        else:
            word.append(line[i])
            started = True
        i += 1
    return words + ["".join(word)] if started else words


def split_sh(command: str) -> list:
    """The words sh makes of a command, for what the cron line uses: bare words, 'single' and "double"
    quotes (a backslash before \\ " $ ` is dropped there), a # that starts a comment."""
    words, word, started, i = [], [], False, 0
    while i < len(command):
        ch = command[i]
        if ch == "'":
            end = command.index("'", i + 1)
            word.append(command[i + 1:end])
            i, started = end + 1, True
        elif ch == '"':
            i, started = i + 1, True
            while command[i] != '"':
                i += command[i] == "\\" and command[i + 1] in '\\"$`'
                word.append(command[i])
                i += 1
            i += 1
        elif ch == "\\":
            word.append(command[i + 1])
            i, started = i + 2, True
        elif ch == "#" and not started:
            break
        elif ch.isspace():
            if started:
                words.append("".join(word))
                word, started = [], False
            i += 1
        else:
            word.append(ch)
            i, started = i + 1, True
    return words + ["".join(word)] if started else words


def split_unit(value: str) -> list:
    """The words systemd makes of an ExecStart= or Environment= value: bare words and "double quoted" ones
    (a backslash escapes there), then %% is % and $$ is $."""
    words, word, started, i = [], [], False, 0
    while i < len(value):
        ch = value[i]
        if ch == '"':
            i, started = i + 1, True
            while value[i] != '"':
                i += value[i] == "\\"
                word.append(value[i])
                i += 1
            i += 1
        elif ch.isspace():
            if started:
                words.append("".join(word))
                word, started = [], False
            i += 1
        else:
            word.append(ch)
            i, started = i + 1, True
    words += ["".join(word)] if started else []
    return [w.replace("%%", "%").replace("$$", "$") for w in words]


def unit_setting(unit: str, key: str) -> list:
    """The words of one setting of a unit file (WorkingDirectory= is one word: a path is not split)."""
    value = next(line.split("=", 1)[1] for line in unit.splitlines() if line.startswith(key + "="))
    return [value.replace("%%", "%")] if key == "WorkingDirectory" else split_unit(value)


def unit_command(unit: str) -> list:
    """The program and arguments systemd starts. A program whose path holds a backslash or a quote is
    started as the first argument of `sh -c 'exec "$0" "$@"'`: systemd refuses such a path as a program."""
    words = unit_setting(unit, "ExecStart")
    return words[3:] if words[:3] == ["/bin/sh", "-c", 'exec "$0" "$@"'] else words


def test_the_windows_task_runs_hidden_on_battery_and_catches_up(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({}, calls))
    root = make_space(tmp_path / ("x" * 60) / ("y" * 60) / "ada")   # once too long for /TR
    record = onboard.register_sync(onboard.Report("person"), root, "ada", 15, offline=False, dry_run=False)
    argv, files = next(c for c in calls if c[0][:2] == ["schtasks", "/Create"])
    assert "/XML" in argv and "/TR" not in argv and argv[argv.index("/TN") + 1] == "space-sync-ada"
    xml = files[argv[argv.index("/XML") + 1]].decode("utf-16")
    for want in ("<DisallowStartIfOnBatteries>false", "<StopIfGoingOnBatteries>false",
                 "<StartWhenAvailable>true", "<Interval>PT15M", "conhost.exe</Command>",
                 f'<Arguments>--headless "{sys.executable}" "{root / "_meta" / "tools" / "sync.py"}" '
                 f'--root "{root}" --release-leg</Arguments>'):
        assert want in xml, want
    assert record["check"] == ["schtasks", "/Query", "/TN", "space-sync-ada", "/XML"]
    assert record["root"] == str(root) and "--release-leg" in record["line"]


def test_check_reads_the_battery_and_catch_up_settings_back(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "offline": False,
        "sync_task": {"name": "space-sync-ada", "check": ["schtasks", "/Query", "/TN", "space-sync-ada"]}})
    good = ("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
            "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable>"
            "<Command>C:\\WINDOWS\\System32\\conhost.exe</Command><Arguments>--headless x</Arguments>")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, good)}, calls))
    row = next(s for s in onboard.check(root) if s.name == "sync task")
    assert row.done and calls[0][0][-1] == "/XML"   # an older record is read back as XML too
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, good.replace(
        "<DisallowStartIfOnBatteries>false", "<DisallowStartIfOnBatteries>true"))}, []))
    row = next(s for s in onboard.check(root) if s.name == "sync task")
    assert not row.done and "battery" in row.result


def test_the_cron_line_quotes_its_paths_keeps_the_path_and_logs(tmp_path):
    line = onboard.cron_line(15, "/opt/py 3/bin/python", "/home/ada/my space/_meta/tools/sync.py",
                             "/home/ada/my space", "space-sync-ada", "/opt/homebrew/bin:/usr/bin")
    assert line == ('*/15 * * * * cd "/home/ada/my space" && PATH="/opt/homebrew/bin:/usr/bin" '
                    '"/opt/py 3/bin/python" "/home/ada/my space/_meta/tools/sync.py" --root "/home/ada/my space" '
                    '--release-leg >> "$HOME/.spaces/logs/space-sync-ada.log" 2>&1  # space-sync-ada')
    assert "\\%" in onboard.cron_line(15, "/py", "/s%x/sync.py", "/s%x", "space-sync-x", "/bin")   # cron's % escaped
    odd = onboard.cron_line(15, "/py", '/s/$HOME `id` "q" \\z/sync.py', '/s/$HOME `id` "q" \\z', "space-sync-x", "/bin")
    assert 'cd "/s/\\$HOME \\`id\\` \\"q\\" \\\\z"' in odd   # sh reads nothing in a folder name as its own


def test_a_launch_agent_is_a_plist_with_one_argument_per_word(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    root, py = Path("/Users/ada/Ada & Co/spaces/ada"), "/Users/ada/.spaces/venv/bin/python3"
    args = ["/Users/ada/Ada & Co/spaces/ada/_meta/tools/routine.py", "/Users/ada/p <1>.md"]
    agent = plistlib.loads(onboard.launch_agent("space-sweep-chain-ada", root, py, args, [(7, 0), (13, 30)], None).encode("utf-8"))
    assert agent["Label"] == "com.spaces.space-sweep-chain-ada"
    assert agent["ProgramArguments"] == [py, *args]   # an array: no shell reads it
    assert agent["WorkingDirectory"] == str(root)
    assert agent["StartCalendarInterval"] == [{"Hour": 7, "Minute": 0}, {"Hour": 13, "Minute": 30}] and "StartInterval" not in agent
    log = str(Path.home() / ".spaces" / "logs" / "space-sweep-chain-ada.log")
    assert agent["StandardOutPath"] == log == agent["StandardErrorPath"]
    assert agent["EnvironmentVariables"] == {"PATH": "/opt/homebrew/bin:/usr/bin"}
    every = plistlib.loads(onboard.launch_agent("space-sync-ada", root, py, args, None, 15).encode("utf-8"))
    assert every["StartInterval"] == 900 and "StartCalendarInterval" not in every
    assert "Ada &amp; Co" in onboard.launch_agent("space-sync-ada", root, py, args, None, 15)   # the file is XML


def test_cron_registration_replaces_its_own_line_by_the_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")   # cron is the fallback of a Linux without systemd user timers
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    root = make_space(tmp_path / "ada")
    script = root / "_meta" / "tools" / "sync.py"
    old = f"*/15 * * * * {sys.executable} {script} --root {root} --release-leg"
    other = "0 9 * * * /usr/bin/true  # someone else"
    calls = []
    no_systemd = {"systemctl --user show-environment": (1, "")}
    monkeypatch.setattr(onboard, "run_cmd", world({"crontab -l": (0, f"{other}\n{old}\n"), **no_systemd}, calls))
    record = onboard.register_sync(onboard.Report("person"), root, "ada", 15, offline=False, dry_run=False)
    argv, files = next(c for c in calls if c[0][0] == "crontab" and c[0][1] != "-l")
    written = files[argv[1]].decode("utf-8").splitlines()
    assert written == [other, record["line"]] and record["line"].endswith("# space-sync-ada")
    assert (tmp_path / "home" / ".spaces" / "logs").is_dir()
    # registered already: a second run writes nothing
    calls.clear()
    monkeypatch.setattr(onboard, "run_cmd", world({"crontab -l": (0, f"{other}\n{record['line']}\n"), **no_systemd}, calls))
    onboard.register_sync(onboard.Report("person"), root, "ada", 15, offline=False, dry_run=False)
    assert [c[0] for c in calls if c[0][0] == "crontab" and c[0][1] != "-l"] == []


def test_no_cron_and_no_systemd_on_linux_is_a_named_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(onboard, "run_cmd", world({"crontab": (127, ""), "systemctl --user": (1, "")}, []))
    report = onboard.Report("person")
    onboard.register_sync(report, make_space(tmp_path / "ada"), "ada", 15, offline=False, dry_run=False)
    row = report.steps[-1]
    assert not row.done and row.needs_owner and "cron" in row.result and "onboard again" in row.result


def test_the_sync_task_on_macos_is_a_launch_agent_every_fifteen_minutes(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({}, calls))
    root = make_space(tmp_path / "ada")
    report = onboard.Report("person")
    record = onboard.register_sync(report, root, "ada", 15, offline=False, dry_run=False)
    plist = Path.home() / "Library" / "LaunchAgents" / "com.spaces.space-sync-ada.plist"
    agent = plistlib.loads(plist.read_bytes())
    assert agent["Label"] == "com.spaces.space-sync-ada" and agent["StartInterval"] == 900
    assert agent["ProgramArguments"] == [sys.executable, str(root / "_meta" / "tools" / "sync.py"), "--root", str(root),
                                         "--release-leg"]
    assert agent["WorkingDirectory"] == str(root) and "StartCalendarInterval" not in agent
    assert (Path.home() / ".spaces" / "logs").is_dir()   # launchd opens the log files; it does not make their folder
    assert [c[0] for c in calls if c[0][0] == "launchctl"] == [
        ["launchctl", "bootout", "gui/501/com.spaces.space-sync-ada"],
        ["launchctl", "bootstrap", "gui/501", str(plist)],
        ["launchctl", "print", "gui/501/com.spaces.space-sync-ada"]]
    assert not [c for c in calls if c[0][0] == "crontab" and c[0][1] != "-l"]   # macOS runs no cron line
    assert report.steps[-1].done and record["kind"] == "launchd" and record["label"] == "com.spaces.space-sync-ada"
    assert record["check"] == ["launchctl", "print", "gui/501/com.spaces.space-sync-ada"] and not record["line"]
    assert onboard.read_yaml(Path.home() / ".spaces" / "spaces.yaml")["spaces"][0]["task"] == "space-sync-ada"


def test_offline_on_macos_the_recorded_command_is_the_launch_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    root = make_space(tmp_path / "ada")
    record = onboard.register_sync(onboard.Report("person"), root, "ada", 15, offline=True, dry_run=False)
    plist = Path.home() / "Library" / "LaunchAgents" / "com.spaces.space-sync-ada.plist"
    note = (root / "_meta" / "state" / "sync-task.txt").read_text(encoding="utf-8")
    assert f"launchctl bootstrap gui/501 {plist}" in note and "StartInterval" in note and "crontab" not in note
    assert record["kind"] == "launchd" and not plist.exists()   # recorded, not registered
    assert not (Path.home() / "Library").exists()


def test_the_sync_task_on_linux_is_a_persistent_systemd_timer_every_fifteen_minutes(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({"crontab": (127, ""), "systemctl --user": (0, "")}, calls))
    root = make_space(tmp_path / "ada")
    report = onboard.Report("person")
    record = onboard.register_sync(report, root, "ada", 15, offline=False, dry_run=False)
    units = tmp_path / "home" / ".config" / "systemd" / "user"
    timer = (units / "space-sync-ada.timer").read_text(encoding="utf-8")
    service = (units / "space-sync-ada.service").read_text(encoding="utf-8")
    assert "Persistent=true" in timer and "OnCalendar=*:0/15" in timer
    assert unit_command(service) == [sys.executable, str(root / "_meta" / "tools" / "sync.py"), "--root", str(root),
                                     "--release-leg"]
    assert unit_setting(service, "Environment") == [f"PATH={os.environ.get('PATH', '')}"]
    assert ["systemctl", "--user", "enable", "--now", "space-sync-ada.timer"] in [c[0] for c in calls]
    assert record["check"] == ["systemctl", "--user", "is-active", "space-sync-ada.timer"]
    assert report.steps[-1].done


def test_offboard_removes_the_cron_line_by_its_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "SYSTEM", "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    root = make_space(tmp_path / "ada")
    mine = onboard.cron_line(15, "/py", "/elsewhere/sync.py", "/elsewhere", "space-sync-ada", "/bin")
    old = f"*/15 * * * * /py {root}/_meta/tools/sync.py --root {root} --release-leg"
    other = "0 9 * * * /usr/bin/true"
    calls = []
    state = {"lines": f"{other}\n{mine}\n{old}\n"}

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:2] == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0, state["lines"], "")
        if argv[0] == "crontab":
            state["lines"] = Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(offboard, "run_cmd", fake)
    report = onboard.Report("offboard")
    offboard.remove_sync_task(report, root, {}, "space-sync-ada", False)
    assert state["lines"].splitlines() == [other] and report.steps[-1].done


@pytest.mark.skipif(os.name == "nt", reason="cron runs on macOS and Linux")
def test_the_cron_command_finds_gh_with_cron_s_empty_environment(tmp_path):
    """The line's captured PATH is the one that matters: under `env -i` (cron's bare
    environment) the command still finds a `gh` that only the registration PATH has."""
    stub = tmp_path / "stub bin"
    stub.mkdir()
    (stub / "gh").write_text("#!/bin/sh\necho stub-gh > \"$HOME/gh-ran\"\n", encoding="utf-8")
    (stub / "gh").chmod(0o755)
    probe = tmp_path / "probe.py"
    probe.write_text("import subprocess\nsubprocess.run(['gh', '--version'], check=True)\nprint('probe ok')\n",
                     encoding="utf-8")
    home = tmp_path / "home"
    (home / ".spaces" / "logs").mkdir(parents=True)
    line = onboard.cron_line(15, sys.executable, str(probe), str(tmp_path), "space-sync-probe",
                             f"{stub}:{os.environ.get('PATH', '')}")
    command = line.split(" ", 5)[5].replace("\\%", "%")
    done = subprocess.run(["env", "-i", f"HOME={home}", "sh", "-c", command], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (home / "gh-ran").read_text().strip() == "stub-gh"
    assert "probe ok" in (home / ".spaces" / "logs" / "space-sync-probe.log").read_text()


@pytest.mark.skipif(os.name == "nt", reason="cron runs on macOS and Linux")
@pytest.mark.parametrize("folder", ["José Pérez", "Ada & Co's 100% $HOME `id` \"q\" back\\slash", "(a;b) <c> | {d} *"])
def test_sh_hands_a_folder_name_to_the_cron_command_unchanged(tmp_path, folder):
    """Cron's line, run as cron runs it (`sh -c`, % unescaped): the program starts in the checkout and
    receives each argument as it was given, whatever the folder name holds."""
    root = tmp_path / folder / "spaces" / "ada-okafor-workspace"
    stub = tmp_path / folder / "stub bin" / "py"
    for path in (root, stub.parent):
        path.mkdir(parents=True)
    stub.write_text("#!/bin/sh\npwd\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    stub.chmod(0o755)
    home = tmp_path / "home"
    (home / ".spaces" / "logs").mkdir(parents=True)
    args = [root / "_meta" / "tools" / "routine.py", root / "_meta" / "state" / "routines" / "sweep-chain.md", "--flag"]
    line = onboard.cron_entry("0 7 * * *", root, str(stub), args, "space-sweep-chain-ada", "/usr/bin:/bin")
    command = line.split(None, 5)[5].replace("\\%", "%")   # cron turns \% back into %
    done = subprocess.run(["env", "-i", f"HOME={home}", "sh", "-c", command], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    started_in, *got = (home / ".spaces" / "logs" / "space-sweep-chain-ada.log").read_text(encoding="utf-8").splitlines()
    assert Path(started_in).resolve() == root.resolve() and got == [str(a) for a in args]


def test_a_missing_yaml_under_pep_668_moves_start_into_a_venv(tmp_path, capsys):
    calls = []

    def run(argv, **kw):
        calls.append([str(a) for a in argv])
        refused = argv[1:4] == ["-m", "pip", "install"] and "--user" in argv
        return subprocess.CompletedProcess(argv, 1 if refused else 0, "", "")
    venv = tmp_path / "venv"
    code = start.ensure_yaml(["--offline"], venv=venv, run=run, have=lambda: False, windows=False)   # PEP 668: not Windows
    py = str(start.venv_python(venv))
    assert code == 0
    assert calls[1] == [sys.executable, "-m", "venv", str(venv)]
    assert calls[2] == [py, "-m", "pip", "install", "--quiet", "pyyaml"]
    assert calls[3] == [py, str(Path(start.__file__).resolve()), "--offline"]


def test_a_python_without_venv_names_the_install(tmp_path, capsys):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "")
    assert start.ensure_yaml([], venv=tmp_path / "venv", run=run, have=lambda: False,
                             install=lambda tool: (None, "no network")) == 1
    assert "python3-venv" in capsys.readouterr().out


def test_unattended_text_names_the_interpreter_that_ran_onboarding(tmp_path):
    ws = make_space(tmp_path / "ada")
    python = onboard.python_cmd()
    assert Path(sys.executable).as_posix() in python
    for routine in start.render_routines(ws, "Ada Okafor"):
        text = Path(routine["prompt_file"]).read_text(encoding="utf-8")
        assert f"{python} {ws.as_posix()}/_meta/tools/" in text and "{python}" not in text
    block = onboard.instruction_block(ws)
    assert f"{python} {ws.resolve().as_posix()}/_meta/tools/needs_you.py" in block
