"""offboard: a subject homed here that another registry holds is archived there too."""
import yaml

import classify
import offboard
import onboard
import registry
import spaceyaml
from conftest import NOW, make_space, mount_brain, write_page
from test_offboard import never


def test_offboard_archives_a_subject_the_brain_holds_and_ships_only_that_note(tmp_path, monkeypatch):
    monkeypatch.setattr(offboard, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "ada"},
        {"id": "beacon", "kind": "projects", "title": "Beacon", "home": "ben"}])
    ada = make_space(tmp_path / "ada", name="ada", subjects=[])
    mount_brain(ada, brain)
    write_page(ada, "_outbound/queue/q-0000a001.md", type="source", title="Still waiting",
               destination="brain", subject="harbor", **{"class": "at_once"},
               stage_after="2026-10-01T00:00:00Z", listed=True)
    report = offboard.run(ada, online=False, offline_roots={"brain": brain})
    assert not report.failed(), [s.row() for s in report.failed()]
    assert registry.Registry(ada).get("harbor").state == "archived"
    assert registry.Registry(ada).get("beacon").state == "active"
    state = yaml.safe_load((ada / "_meta" / "state" / "offboard.yaml").read_text(encoding="utf-8"))
    assert state["archived"] == ["harbor"]
    assert (ada / "_outbound/queue/q-0000a001.md").exists(), "the queue stays the person's to let go"
    assert list((brain / "_inbound" / "candidates").glob("*.md")) == []
    assert classify.land_notes(brain, now=NOW)["applied"] == 1
    held = registry.Registry(brain)
    assert held.get("harbor").state == "archived" and held.get("beacon").state == "active"


def test_only_the_home_archives_a_subject_and_an_archive_is_not_undone_by_a_note(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "ada"}])
    reg = registry.Registry(brain)
    space = spaceyaml.load(brain)
    note = {"id": "p-0000a002", "kind": "registry", "note": "state", "subject": "harbor",
            "from": "ben", "data": {"state": "archived"}}
    assert classify.apply_note(brain, space, reg, note, NOW)[0] == "refused"
    assert classify.apply_note(brain, space, reg, dict(note, **{"from": "ada"}), NOW)[0] == "applied"
    woke = dict(note, **{"from": "ada", "data": {"state": "active"}})
    assert classify.apply_note(brain, space, reg, woke, NOW)[0] == "refused"
    assert reg.get("harbor").state == "archived"
