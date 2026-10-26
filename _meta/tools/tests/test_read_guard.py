"""The files reader's guard (Ruling R23): a hook on the Read tool that refuses every path that does not
resolve inside the reader's own folder, whatever the person's own settings allow. Tested as a function,
as a process (exit codes) and as the command line the settings file carries, quoting included."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import read_guard
import reader


def event(path) -> dict:
    """What the hook receives on stdin for a Read call."""
    return {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": str(path)}}


def a_folder(tmp_path, name="Zoë Müller & co") -> Path:
    """The reader's folder, under a name with a space, an accent and an ampersand, holding one copy;
    a secret file lies beside it."""
    folder = tmp_path / name / "files"
    folder.mkdir(parents=True)
    (folder / "01-brand.pdf").write_bytes(b"%PDF")
    (folder.parent / "secret.txt").write_text("not for the reader", encoding="utf-8")
    return folder


def link_dir(link: Path, target: Path) -> None:
    """A folder link: a symbolic link, else (Windows, no right to make one) a junction, which needs none."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError, AttributeError):
        pytest.skip("this system gives no right to make links")


def test_a_file_inside_the_folder_is_allowed_by_its_full_path_or_by_its_name(tmp_path):
    folder = a_folder(tmp_path)
    assert read_guard.check(folder, event(folder / "01-brand.pdf")) is None
    assert read_guard.check(folder, event("01-brand.pdf")) is None   # a relative path is the folder's own


def test_every_other_path_is_refused_an_escape_a_missing_file_and_a_request_with_no_path(tmp_path):
    folder = a_folder(tmp_path)
    outside = folder.parent / "secret.txt"
    for path in (outside, folder / ".." / "secret.txt", "../secret.txt", str(outside).upper() + "x",
                 folder / "02-missing.pdf", folder / "nested" / ".." / ".." / "secret.txt", tmp_path, "", "   "):
        assert read_guard.check(folder, event(path)), path
    for odd in ({}, {"tool_input": None}, {"tool_input": {}}, {"tool_input": {"file_path": None}},
                {"tool_input": {"file_path": 7}}, {"tool_input": {"file_path": ["a"]}}, [], "text", None,
                {"tool_input": {"file_path": "a\0b"}}):
        assert read_guard.check(folder, odd), odd
    assert read_guard.check(tmp_path / "gone", event(outside))   # a folder that is not there guards nothing: refused


NETWORK_PATHS = ("\\\\10.255.255.1\\share\\x.txt", "//10.255.255.1/share/x.txt", "/\\10.255.255.1\\share\\x.txt",
                 "\\/10.255.255.1/share/x.txt", "\\\\?\\UNC\\10.255.255.1\\share\\x.txt", "\\\\?\\C:\\Windows\\win.ini",
                 "\\\\.\\pipe\\anything", "//./pipe/anything", "//?/C:/Windows/win.ini", "\\\\localhost\\c$\\x.txt")


def test_a_network_or_device_path_is_refused_by_its_spelling_before_any_file_is_touched(tmp_path):
    """`resolve` on a path of another machine opens it over the network: it takes seconds, shows the
    machine's name to the host (a Windows sign-in leak), and a hook that is slow enough fails open."""
    folder = a_folder(tmp_path)
    touched, refused, exits = [], {}, {}

    def never(*a, **k):
        touched.append(a)
        raise AssertionError("a file system call was made")
    with pytest.MonkeyPatch.context() as patch:   # undone before anything is asserted: pytest itself needs the file system
        for name in ("resolve", "exists", "stat", "lstat", "is_file", "samefile"):
            patch.setattr(Path, name, never)
        patch.setattr(os, "stat", never)
        patch.setattr(os.path, "realpath", never)
        for path in NETWORK_PATHS:
            refused[path] = read_guard.check(folder, event(path))
            exits[path] = read_guard.main([str(folder)], json.dumps(event(path)).encode("utf-8"))
    assert touched == []
    assert all(refused.values()) and set(exits.values()) == {2}
    assert read_guard.check(folder, event(folder / "01-brand.pdf")) is None   # a plain local path is still read


def test_a_network_path_exits_two_quickly_as_a_process(tmp_path):
    import time
    folder = a_folder(tmp_path)
    started = time.perf_counter()
    for path in NETWORK_PATHS[:3]:
        got = subprocess.run([sys.executable, "-I", read_guard.__file__, str(folder)], input=json.dumps(event(path)).encode("utf-8"),
                             capture_output=True)
        assert got.returncode == 2 and b"its own folder" in got.stderr
    assert time.perf_counter() - started < 10   # a host that does not answer took 21 seconds


def test_a_link_inside_the_folder_to_a_place_outside_it_is_refused(tmp_path):
    folder = a_folder(tmp_path)
    link_dir(folder / "out", folder.parent)
    assert (folder / "out" / "secret.txt").exists()   # the link works: only the guard stops the read
    assert read_guard.check(folder, event(folder / "out" / "secret.txt"))


def test_a_file_link_out_of_the_folder_is_refused_where_links_can_be_made(tmp_path):
    folder = a_folder(tmp_path)
    try:
        os.symlink(folder.parent / "secret.txt", folder / "02-link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this system gives no right to make links")
    assert read_guard.check(folder, event(folder / "02-link.txt"))


def test_the_process_exits_two_to_block_and_zero_to_allow_and_fails_closed(tmp_path, capsys):
    folder = a_folder(tmp_path)
    as_bytes = lambda payload: json.dumps(payload).encode("utf-8")   # noqa: E731
    assert read_guard.main([str(folder)], as_bytes(event(folder / "01-brand.pdf"))) == 0
    assert read_guard.main([str(folder)], as_bytes(event(folder.parent / "secret.txt"))) == 2
    err = capsys.readouterr().err
    assert "its own folder" in err and "secret" not in err   # tells the model why, repeats no path
    for argv, raw in (([str(folder)], b"not json"), ([str(folder)], b""), ([str(folder)], b"\xff\xfe\x00"), ([], as_bytes(event("x"))),
                      ([str(folder)], b"[]")):
        assert read_guard.main(argv, raw) == 2, (argv, raw)   # anything it cannot read is refused


def test_the_hook_command_runs_as_a_process_through_a_shell_in_a_folder_with_spaces_and_accents(tmp_path):
    folder = a_folder(tmp_path)
    command = reader.guard_command(folder)
    allowed = subprocess.run(command, shell=True, input=json.dumps(event(folder / "01-brand.pdf")).encode("utf-8"),
                             capture_output=True)
    blocked = subprocess.run(command, shell=True, input=json.dumps(event(folder.parent / "secret.txt")).encode("utf-8"),
                             capture_output=True)
    assert (allowed.returncode, blocked.returncode) == (0, 2), (allowed.stderr, blocked.stderr)
    assert b"its own folder" in blocked.stderr


def test_the_command_form_is_python_isolated_the_script_and_the_folder_each_in_double_quotes_with_forward_slashes(tmp_path):
    folder = a_folder(tmp_path)
    command = reader.guard_command(folder)
    python, script = Path(sys.executable).as_posix(), Path(reader.GUARD).as_posix()
    assert command == f'"{python}" -I "{script}" "{folder.resolve().as_posix()}" || exit 2'
    assert "\\" not in command and " " in command and "ë" in command   # spaces and accents stay inside the quotes
    assert shlex.split(command) == [python, "-I", script, folder.resolve().as_posix(), "||", "exit", "2"]   # one word each, as a shell reads it
    assert Path(reader.GUARD).name == "read_guard.py" and Path(reader.GUARD).is_file()


def test_a_guard_that_cannot_start_blocks_instead_of_failing_open(tmp_path):
    """Only exit code 2 blocks a call; a missing interpreter or script is exit 127 or 1. `|| exit 2` in the
    command (read by cmd.exe and by sh alike) turns any failure of the guard into the block."""
    folder = a_folder(tmp_path)
    payload = json.dumps(event(folder / "01-brand.pdf")).encode("utf-8")
    for python in (None, str(tmp_path / "no-such-python")):
        command = reader.guard_command(folder, python=python)
        assert command.endswith(" || exit 2")
        got = subprocess.run(command, shell=True, input=payload, capture_output=True).returncode
        assert got == (0 if python is None else 2), (python, got)   # the file is inside: allowed, unless the guard is not there
    gone = reader.guard_command(folder).replace("read_guard.py", "read_guard_gone.py")
    assert subprocess.run(gone, shell=True, input=payload, capture_output=True).returncode == 2   # the script is not there


def test_a_name_the_command_cannot_quote_is_refused_not_guessed_at(tmp_path):
    for bad in ('say "hi"', "cash$HOME", "back`tick`", "50%off", "bang!", "line\nbreak"):
        with pytest.raises(ValueError):
            reader.guard_command(tmp_path / bad)
        with pytest.raises(ValueError):
            reader.guard_command(tmp_path, python=f"/bin/{bad}")


def test_the_files_readers_hook_has_its_own_time_limit_well_above_the_guards(tmp_path):
    """C-1 and dry run A: a hook Claude Code cuts off lets the call through; the hook says how long it may take,
    and the guard ends itself (exit 2) well before that."""
    import routine
    folder = a_folder(tmp_path)
    data = json.loads(reader._files_settings(folder.parent, folder, []).read_text(encoding="utf-8"))
    assert [h["timeout"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]] == [routine.GUARD_TIMEOUT]
    assert read_guard.LIMIT_SECONDS == 10 and routine.GUARD_TIMEOUT >= 3 * read_guard.LIMIT_SECONDS


def test_the_guard_gives_up_with_a_refusal_once_its_own_time_is_up(monkeypatch, capsys):
    import threading
    exits, done = [], threading.Event()
    monkeypatch.setattr(read_guard.os, "_exit", lambda code: (exits.append(code), done.set()))
    monkeypatch.setattr(read_guard, "LIMIT_SECONDS", 0.05)
    read_guard.arm(read_guard.WHY)
    assert done.wait(10) and exits == [2] and "its own folder" in capsys.readouterr().err


def test_a_guard_process_that_hangs_exits_two_by_itself(tmp_path):
    """A path whose resolving hangs (a sleeping disk, a scan of the file): the guard refuses at its own limit."""
    folder = a_folder(tmp_path)
    tools = Path(read_guard.__file__).parent
    code = ("import sys, threading; sys.path.insert(0, sys.argv[1]); import read_guard as g; g.LIMIT_SECONDS = 0.2; "
            "g.check = lambda folder, event: threading.Event().wait(); sys.argv = ['read_guard.py', sys.argv[2]]; sys.exit(g.run())")
    got = subprocess.run([sys.executable, "-I", "-c", code, str(tools), str(folder)],
                         input=json.dumps(event(folder / "01-brand.pdf")).encode("utf-8"), capture_output=True, timeout=30)
    assert got.returncode == 2 and b"its own folder" in got.stderr and b"took too long" in got.stderr


def test_a_guard_held_inside_one_long_call_is_ended_all_the_same(tmp_path):
    """NB-1: the timer cannot run while one call holds the interpreter; faulthandler ends the process with exit 1,
    which the hook command's `|| exit 2` makes a block."""
    folder = a_folder(tmp_path)
    tools = Path(read_guard.__file__).parent
    code = ("import re, sys; sys.path.insert(0, sys.argv[1]); import read_guard as g; g.LIMIT_SECONDS = 0.2; "
            "g.check = lambda folder, event: re.match('(a+)+$', 'a' * 40 + '!'); sys.argv = ['read_guard.py', sys.argv[2]]; "
            "sys.exit(g.run())")
    got = subprocess.run([sys.executable, "-I", "-c", code, str(tools), str(folder)],
                         input=json.dumps(event(folder / "01-brand.pdf")).encode("utf-8"), capture_output=True, timeout=60)
    assert got.returncode == 1
    assert reader.guard_command(folder).endswith(" || exit 2")


def test_the_guard_needs_nothing_but_the_standard_library():
    """It runs as `python -I`: no import path but the standard library's, so no module of the tools."""
    text = Path(read_guard.__file__).read_text(encoding="utf-8")
    imports = {line.split()[1].split(".")[0] for line in text.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "faulthandler", "json", "os", "sys", "pathlib", "threading"}
