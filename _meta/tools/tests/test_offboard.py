"""offboard: archive the space's own subjects, remove the instruction, delete nothing."""
from pathlib import Path

import offboard
import onboard
import registry
import yaml
from conftest import make_space, write_page


def never(*args, **kwargs):
    raise AssertionError(f"run_cmd was called offline: {args}")


def all_files(root: Path) -> set:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_offboard_offline_archives_and_deletes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(offboard, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    ada = make_space(tmp_path / "ada", name="ada", subjects=[
        {"id": "lighthouse", "kind": "projects", "title": "Lighthouse", "home": "ada"},
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor"},
        {"id": "notes", "kind": "topics", "title": "Notes"},
    ])
    instructions = tmp_path / "home" / ".claude" / "CLAUDE.md"
    instructions.parent.mkdir(parents=True)
    instructions.write_text("# Mine\n\nKeep this line.\n", encoding="utf-8")
    onboard.install_instruction(instructions, ada)
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml",
                       {"case": "person", "offline": True, "workspace": str(ada),
                        "user_instructions": str(instructions),
                        "sync_task": {"name": "space-sync-ada", "check": ["schtasks", "/Query"]}})
    write_page(ada, "_inbound/candidates/one.md", title="One")
    (ada / "_outbound" / "queue" / "q-1.md").write_text("---\nclass: window\n---\nx\n", encoding="utf-8")
    before = all_files(tmp_path)
    report = offboard.run(ada, online=False)
    assert not report.failed(), [s.row() for s in report.failed()]
    assert before <= all_files(tmp_path)
    assert (ada / "_inbound" / "candidates" / "one.md").exists()
    assert (ada / "_outbound" / "queue" / "q-1.md").exists()
    reg = registry.Registry(ada)
    assert reg.get("lighthouse").state == "archived"
    assert reg.get("harbor").state == "active" and reg.get("notes").state == "active"
    text = instructions.read_text(encoding="utf-8")
    assert "Keep this line." in text and onboard.MARK_OPEN not in text
    assert not onboard.instruction_present(instructions, ada)
    state = yaml.safe_load((ada / "_meta" / "state" / "offboard.yaml").read_text())
    assert state["counts"] == {"queue": 1, "held": 0, "candidates": 1}
    assert state["archived"] == ["lighthouse"]
    assert any(s.name == "still waiting" and s.done for s in report.steps)


def test_offboard_dry_run_writes_nothing(tmp_path, monkeypatch):
    """--dry-run only prints the plan: the instruction, the allowed directory, the registry
    and the space's state stay as they were."""
    monkeypatch.setattr(offboard, "run_cmd", lambda *a, **k: __import__("subprocess").CompletedProcess(a, 0, "", ""))
    ada = make_space(tmp_path / "ada", name="ada", subjects=[
        {"id": "lighthouse", "kind": "projects", "title": "Lighthouse", "home": "ada"},
    ])
    instructions = tmp_path / "home" / ".claude" / "CLAUDE.md"
    instructions.parent.mkdir(parents=True)
    onboard.install_instruction(instructions, ada)
    settings = instructions.parent / "settings.json"
    onboard.allow_directory(settings, ada)
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml",
                       {"case": "person", "offline": True, "workspace": str(ada),
                        "user_instructions": str(instructions)})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = offboard.run(ada, online=True, dry_run=True)
    assert not report.failed(), [s.row() for s in report.failed()]
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    assert onboard.instruction_present(instructions, ada)
    assert onboard.directory_allowed(settings, ada)


def test_offboard_keeps_another_spaces_instruction_block(tmp_path, monkeypatch):
    """Offboarding a project space on the owner's machine must not wipe the owner's own
    instruction block from the shared user-level file."""
    monkeypatch.setattr(offboard, "run_cmd", lambda *a, **k: __import__("subprocess").CompletedProcess(a, 0, "", ""))
    owner_ws = make_space(tmp_path / "ada")
    project = make_space(tmp_path / "harbor", profile="project", name="harbor")
    instructions = tmp_path / "CLAUDE.md"
    onboard.install_instruction(instructions, owner_ws)
    onboard.write_yaml(project / "_meta" / "state" / "onboard.yaml",
                       {"case": "person", "offline": True, "workspace": str(project),
                        "user_instructions": str(instructions)})
    report = offboard.run(project, online=False)
    assert not report.failed(), [s.row() for s in report.failed()]
    assert onboard.instruction_present(instructions, owner_ws)


def mac(monkeypatch, loaded: set) -> list:
    """launchd as offboard meets it on a Mac: `loaded` agents answer `print` until they are booted out."""
    import subprocess
    monkeypatch.setattr(onboard, "SYSTEM", "Darwin")
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:2] == ["launchctl", "bootout"]:
            loaded.discard(argv[2])
        gone = argv[:2] == ["launchctl", "print"] and argv[2] not in loaded
        return subprocess.CompletedProcess(argv, 113 if gone else 0, "", "")
    monkeypatch.setattr(offboard, "run_cmd", fake)
    monkeypatch.setattr(onboard, "run_cmd", fake)
    return calls


def test_a_probe_record_naming_the_sync_agents_label_leaves_the_sync_agent_alone(tmp_path, monkeypatch):
    """onboard.yaml is a file a merge can change: the probe task goes by the name and label the code derives."""
    ada = make_space(tmp_path / "ada", name="ada")
    agents = Path.home() / "Library" / "LaunchAgents"
    for ident in ("com.spaces.space-sync-ada", "com.spaces.space-probe-ada"):
        onboard.write_text(agents / f"{ident}.plist", "<plist/>")
    loaded = {"gui/501/com.spaces.space-sync-ada", "gui/501/com.spaces.space-probe-ada"}
    calls = mac(monkeypatch, loaded)
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"case": "person", "probe_task": {
        "name": "space-probe-ada", "kind": "launchd", "label": "com.spaces.space-sync-ada"}})
    report = onboard.Report(case="check")
    offboard.remove_probe_task(report, ada)
    assert [c for c in calls if c[:2] == ["launchctl", "bootout"]] == [["launchctl", "bootout", "gui/501/com.spaces.space-probe-ada"]]
    assert (agents / "com.spaces.space-sync-ada.plist").exists() and "gui/501/com.spaces.space-sync-ada" in loaded
    assert not (agents / "com.spaces.space-probe-ada.plist").exists() and report.steps[-1].done


def test_a_probe_record_under_another_runs_prefix_is_not_this_runs_to_remove(tmp_path, monkeypatch):
    """The name comes from the prefix in force (SPACES_TASK_PREFIX): a probe task of another run is left alone."""
    ada = make_space(tmp_path / "ada", name="ada")
    calls = mac(monkeypatch, {"gui/501/com.spaces.space-test-1a2b-probe-ada"})
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"probe_task": {"name": "space-test-1a2b-probe-ada"}})
    report = onboard.Report(case="check")
    offboard.remove_probe_task(report, ada)
    assert calls == [] and report.steps == []
