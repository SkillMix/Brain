"""The transcript adapter: the capture rate always, witness pages only with the switch on."""
import datetime as dt
import json

import yaml

import pages
import status
import transcripts
import validate
from conftest import NOW, make_space


def session(folder, sid, start, minutes=40, cwd="C:/work/harbor-repo", asked="Draft the Harbor plan"):
    folder = folder / "project-a"
    folder.mkdir(parents=True, exist_ok=True)
    events = [{"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "message": {"role": "user", "content": asked}},
              {"type": "assistant", "sessionId": sid, "cwd": cwd,
               "timestamp": (start + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "message": {"role": "assistant", "content": [{"type": "text", "text": "The plan is drafted."}]}}]
    (folder / f"{sid}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def setup(tmp_path, switch="off"):
    folder = tmp_path / "transcripts"
    root = make_space(tmp_path / "ada", transcripts={"path": str(folder), "switch": switch})
    session(folder, "s-marker", NOW - dt.timedelta(hours=10))
    session(folder, "s-heading", NOW - dt.timedelta(hours=6))
    session(folder, "s-missed", NOW - dt.timedelta(hours=3))
    day = root / "journal" / "2026" / "10" / "2026-10-12.md"
    day.parent.mkdir(parents=True, exist_ok=True)
    day.write_text("# 2026-10-12\n\n## 23:15Z — plan\n\n- drafted <!-- session: s-marker -->\n\n"
                   "## 03:20Z — another\n\n- a fact\n", encoding="utf-8")
    return root


def test_scan_records_sessions_and_the_capture_rate_without_content(tmp_path):
    root = setup(tmp_path)
    report = transcripts.scan(root, now=NOW)
    assert report == {"seen": 3, "captured": 2, "witnessed": 0, "switch": "off"}
    data = yaml.safe_load((root / "_meta/state/sessions.yaml").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["sessions"]}
    assert by_id["s-marker"]["captured"] and by_id["s-heading"]["captured"] and not by_id["s-missed"]["captured"]
    assert "Harbor plan" not in (root / "_meta/state/sessions.yaml").read_text(encoding="utf-8")
    assert not list((root / "knowledge" / "sources").rglob("session-*.md"))
    status.run(root, now=NOW)
    assert "capture rate 0.67 (3 sessions)" in (root / "live/health.md").read_text(encoding="utf-8")


def test_a_missing_transcripts_folder_leaves_the_sessions_file_as_it_was(tmp_path):
    """A computer without the session files (the cloud, a second laptop) has nothing to count:
    the scan says the folder is missing and keeps the last scan's sessions and capture rate."""
    import shutil
    root = setup(tmp_path)
    transcripts.scan(root, now=NOW)
    sessions = (root / "_meta/state/sessions.yaml").read_text(encoding="utf-8")
    runs = (root / "_meta/state/runs.jsonl").read_text(encoding="utf-8")
    shutil.rmtree(tmp_path / "transcripts")
    report = transcripts.scan(root, now=NOW + dt.timedelta(hours=1))
    assert report == {"seen": 0, "captured": 0, "witnessed": 0, "switch": "off", "path": "missing"}
    assert (root / "_meta/state/sessions.yaml").read_text(encoding="utf-8") == sessions
    assert (root / "_meta/state/runs.jsonl").read_text(encoding="utf-8") == runs, "no run line either"
    status.run(root, now=NOW + dt.timedelta(hours=1))
    assert "capture rate 0.67 (3 sessions)" in (root / "live/health.md").read_text(encoding="utf-8")


def test_switch_on_turns_missed_sessions_into_witness_pages_here_only(tmp_path):
    root = setup(tmp_path, switch="on")
    report = transcripts.scan(root, now=NOW)
    assert report["witnessed"] == 1
    (page,) = list((root / "knowledge" / "sources").rglob("session-*.md"))
    fm, body = pages.read(page)
    assert fm["type"] == "source" and fm["kind"] == "session" and "subject" not in fm
    assert "Draft the Harbor plan" in body and fm["status"] == "draft"
    assert transcripts.scan(root, now=NOW)["witnessed"] == 0, "once per session"
    assert [e for e in validate.validate(root) if not e.startswith("warning:")] == []


def test_transcripts_setting_is_validated(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", transcripts={"path": "x", "switch": "on"})
    assert any("transcripts" in e for e in validate.validate(root))
    root = make_space(tmp_path / "ada", transcripts={"path": "x", "switch": "maybe"})
    assert any("transcripts" in e for e in validate.validate(root))


def test_a_low_capture_rate_proposes_the_switch(tmp_path):
    folder = tmp_path / "transcripts"
    root = make_space(tmp_path / "ada", transcripts={"path": str(folder), "switch": "off"})
    for n in range(10):
        session(folder, f"s-{n}", NOW - dt.timedelta(days=n, hours=2))
    transcripts.scan(root, now=NOW)
    status.run(root, now=NOW)
    items = yaml.safe_load((root / "_inbound/needs-you/librarian.yaml").read_text(encoding="utf-8"))["items"]
    item = next(i for i in items if i["id"] == "transcripts-on")
    assert "0.00" in item["text"]
    status.record_verdict(root, "transcripts-on", "Ada Okafor", verb="yes", at="2026-10-12T10:00:00Z")
    status.run(root, now=NOW + dt.timedelta(hours=1))
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    assert data["transcripts"]["switch"] == "on"


def test_old_files_and_excluded_folders_are_not_counted(tmp_path):
    import os
    folder = tmp_path / "transcripts"
    root = make_space(tmp_path / "ada", transcripts={"path": str(folder), "switch": "off",
                                                     "exclude": ["thesis"]})
    session(folder, "s-work", NOW - dt.timedelta(hours=5))
    session(folder, "s-thesis", NOW - dt.timedelta(hours=4), cwd="C:/school/thesis")
    session(folder, "s-old", NOW - dt.timedelta(days=60))
    session(folder, "s-routine", NOW - dt.timedelta(hours=3), asked='<scheduled-task name="local-sync">run')
    old = folder / "project-a" / "s-old.jsonl"
    stamp = (NOW - dt.timedelta(days=60)).timestamp()
    os.utime(old, (stamp, stamp))
    nested = folder / "project-a" / "s-work" / "subagents"
    nested.mkdir(parents=True)
    (nested / "agent-1.jsonl").write_text((folder / "project-a" / "s-work.jsonl").read_text(encoding="utf-8"),
                                          encoding="utf-8")
    assert transcripts.scan(root, now=NOW)["seen"] == 1


def test_typed_exclude_words_keep_a_session_out_of_pages_and_the_rate(tmp_path):
    folder = tmp_path / "transcripts"
    root = make_space(tmp_path / "ada", transcripts={"path": str(folder), "switch": "on",
                                                     "exclude_words": ["homework", "plan my day"]})
    session(folder, "s-work", NOW - dt.timedelta(hours=5), asked="Draft the Harbor plan")
    session(folder, "s-school", NOW - dt.timedelta(hours=4), asked="Check my Homework 3 answers")
    session(folder, "s-day", NOW - dt.timedelta(hours=3), asked="Help me plan my day")
    session(folder, "s-near", NOW - dt.timedelta(hours=2), asked="Plan my days off the homeworkshop")
    report = transcripts.scan(root, now=NOW)
    made = {p.stem for p in (root / "knowledge" / "sources").rglob("session-*.md")}
    assert made == {"session-s-work", "session-s-near"}
    assert report["seen"] == 2


def test_exclude_words_must_be_a_list(tmp_path):
    root = make_space(tmp_path / "ada", transcripts={"path": "x", "switch": "on", "exclude_words": "homework"})
    assert any("exclude_words" in e for e in validate.validate(root))
