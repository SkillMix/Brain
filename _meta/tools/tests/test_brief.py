"""brief.py: the pack an agent reads before it writes a subject's brief (understand-first spec,
sections 4.2 and 4.6)."""
import json

import brief
import pages
from conftest import make_space, mount_brain, write_page

TIDEWATER = {"id": "tidewater", "kind": "clients", "title": "Tidewater", "aliases": ["Tidewater Ports"]}
HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "part_of": "tidewater",
          "aliases": ["Pier Work"]}
BEACON = {"id": "beacon", "kind": "projects", "title": "Beacon"}


def witness(root, rid, body="Body.\n", title="A record", source="mail", **fm):
    rel = f"knowledge/sources/2026/10/{rid.replace('#', '-')}.md"
    return write_page(root, rel, body, type="source", title=title, record=rid, source=source,
                      kind="mail", at="2026-10-10T09:00:00Z", **fm)


def journal(root, day, text):
    path = root / "journal" / day[:4] / day[5:7] / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ada(tmp_path, **extra):
    root = make_space(tmp_path / "ada", subjects=[TIDEWATER, HARBOR, BEACON], **extra)
    witness(root, "r-route", title="Kickoff", routing={"decision": "route", "routes": ["ada/harbor"]})
    witness(root, "r-elsewhere", title="Kickoff elsewhere", routing={"decision": "route", "routes": ["beacon/harbor"]})
    witness(root, "r-subject", title="Contract draft", subject="tidewater")
    witness(root, "r-mention", title="Weekly", mentions=["harbor"])
    witness(root, "r-alias", "Notes on the pier work schedule.\n", title="Schedule")
    witness(root, "r-client-alias", title="Call with Tidewater Ports")
    witness(root, "r-other", "Beacon only. Harborview offsite.\n", title="Beacon plan", subject="beacon")
    witness(root, "r-archive", "Tidewater, as the old system had it.\n", title="Old notes", source="archive")
    journal(root, "2026-10-11", "# 2026-10-11\n\n## 09:00 Harbor sync\nTalked it through.\n\n"
                                "## 10:00 Paperwork\nSent the forms.\nsubject: tidewater\n\n"
                                "## 11:00 Lunch\nNothing to note.\n")
    return root


def records(pack):
    return {e["record"] for e in pack["evidence"]}


def test_evidence_by_route_mention_and_name_for_the_subject_and_its_parts(tmp_path):
    root = ada(tmp_path)
    pack = brief.pack(root, "tidewater")
    assert records(pack) == {"r-route", "r-subject", "r-mention", "r-alias", "r-client-alias",
                             "journal-20261011T0900", "journal-20261011T1000"}
    entry = next(e for e in pack["evidence"] if e["record"] == "r-alias")
    assert entry["path"] == "knowledge/sources/2026/10/r-alias.md"
    assert (entry["at"], entry["source"], entry["kind"], entry["title"]) == \
        ("2026-10-10T09:00:00Z", "mail", "mail", "Schedule")
    assert entry["characters"] == len("Notes on the pier work schedule.\n")
    assert pack["subject"]["entry"]["id"] == "tidewater"
    assert [p["id"] for p in pack["subject"]["parts"]] == ["harbor"]
    assert pack["subject"]["umbrella"] is None

    narrow = brief.pack(root, "harbor")
    assert records(narrow) == {"r-route", "r-mention", "r-alias", "journal-20261011T0900"}, \
        "the umbrella's own records are not the part's; whole words only"
    assert narrow["subject"]["umbrella"]["id"] == "tidewater"


def test_a_record_research_found_for_the_subject_is_evidence(tmp_path):
    root = ada(tmp_path)
    witness(root, "r-found", "A lead asked for a call.\n", title="New lead", research="harbor")
    pack = brief.pack(root, "harbor")
    entry = next(e for e in pack["evidence"] if e["record"] == "r-found")
    assert entry["why"] == ["researched harbor"]


def test_a_split_record_comes_whole(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR, TIDEWATER])
    witness(root, "r-long", "Harbor scope, part one.\n", title="Long thread", part=1, parts=2)
    witness(root, "r-long#2", "The rest of it.\n", title="Long thread", part=2, parts=2)
    witness(root, "r-short", "Nothing here.\n", title="Unrelated")
    assert records(brief.pack(root, "harbor")) == {"r-long", "r-long#2"}


def test_archive_derived_pages_stay_out_until_the_switch_is_on(tmp_path):
    root = ada(tmp_path)
    pack = brief.pack(root, "tidewater")
    assert "r-archive" not in records(pack)
    left = [x for x in pack["excluded"] if x["path"].endswith("r-archive.md")]
    assert left and "archive" in left[0]["why"]
    assert pack["archives"] is False

    on = ada(tmp_path / "on", routing={"archives": True})
    pack = brief.pack(on, "tidewater")
    assert "r-archive" in records(pack)
    assert not [x for x in pack["excluded"] if x["path"].endswith("r-archive.md")]


def test_rebuild_leaves_the_open_candidate_out(tmp_path):
    root = ada(tmp_path)
    write_page(root, "_inbound/candidates/tidewater.md", "# Tidewater\n", type="entity", title="Tidewater",
               subject="tidewater", kind="clients", evidence=["r-subject"])
    pack = brief.pack(root, "tidewater")
    assert pack["candidate"]["path"] == "_inbound/candidates/tidewater.md"
    assert not [x for x in pack["excluded"] if "candidates" in x["path"]]

    rebuilt = brief.pack(root, "tidewater", rebuild=True)
    assert rebuilt["candidate"] is None
    left = [x for x in rebuilt["excluded"] if x["path"] == "_inbound/candidates/tidewater.md"]
    assert left and "rebuil" in left[0]["why"]


def test_lessons_for_the_subject_its_parts_and_all(tmp_path):
    root = ada(tmp_path)
    (root / "_meta" / "lessons.md").write_text(
        "# Lessons\n\n"
        "- 2026-10-14 distill tidewater: never put prices on the page (from: \"no prices\")\n"
        "- 2026-10-14 all: research before asking (from: \"that's your job\")\n"
        "- 2026-10-14 route beacon: beacon mail is internal (from: no)\n"
        "- 2026-10-15 distill [harbor]: the pier work is phase two (from: edit)\n"
        "- 2026-10-15 route all: an event and its call are one episode (from: edit)\n", encoding="utf-8")
    lessons = brief.pack(root, "tidewater")["lessons"]
    assert [line.split(":")[0] for line in lessons] == [
        "- 2026-10-14 distill tidewater", "- 2026-10-14 all", "- 2026-10-15 distill [harbor]",
        "- 2026-10-15 route all"]
    beacon = brief.pack(root, "beacon")["lessons"]
    assert [line.split(":")[0] for line in beacon] == [
        "- 2026-10-14 all", "- 2026-10-14 route beacon", "- 2026-10-15 route all"]


def test_signed_pages_and_status_here_and_at_the_home(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "lighthouse", "kind": "projects", "title": "Lighthouse", "home": "brain"}])
    write_page(brain, "knowledge/projects/lighthouse/lighthouse.md", "# Lighthouse\n", type="entity",
               title="Lighthouse", status="stable", signed_at="2026-10-01T09:00:00Z")
    write_page(brain, "live/subjects/lighthouse.md", "# Lighthouse: status\n", type="snapshot",
               title="Lighthouse: status")
    root = ada(tmp_path)
    mount_brain(root, brain)
    write_page(root, "knowledge/clients/tidewater/tidewater.md", "# Tidewater\n", type="entity",
               title="Tidewater", status="stable", signed_at="2026-10-02T09:00:00Z")
    write_page(root, "knowledge/clients/tidewater/notes.md", "# Notes\n", type="concept", title="Notes")
    write_page(root, "live/subjects/tidewater.md", "# Tidewater: status\n", type="snapshot",
               title="Tidewater: status")

    pack = brief.pack(root, "tidewater")
    assert [(p["space"], p["path"]) for p in pack["pages"]] == \
        [("ada", "knowledge/clients/tidewater/tidewater.md")], "signed pages only"
    assert [(s["space"], s["path"]) for s in pack["status"]] == [("ada", "live/subjects/tidewater.md")]

    home = brief.pack(root, "lighthouse")
    assert [p["space"] for p in home["pages"]] == ["brain"]
    assert home["pages"][0]["path"].endswith("knowledge/projects/lighthouse/lighthouse.md")
    assert [s["space"] for s in home["status"]] == ["brain"]


def test_a_brain_named_otherwise_than_its_mount_is_the_home(tmp_path):
    # a subject is homed by the brain's name; the brain is mounted as `brain`
    brain = make_space(tmp_path / "brain", profile="brain", name="northwind", subjects=[
        {"id": "lighthouse", "kind": "projects", "title": "Lighthouse", "home": "northwind"}])
    write_page(brain, "knowledge/projects/lighthouse/lighthouse.md", "# Lighthouse\n", type="entity",
               title="Lighthouse", status="stable", signed_at="2026-10-01T09:00:00Z")
    write_page(brain, "live/subjects/lighthouse.md", "# Lighthouse: status\n", type="snapshot",
               title="Lighthouse: status")
    root = ada(tmp_path)
    mount_brain(root, brain)

    home = brief.pack(root, "lighthouse")
    assert [p["space"] for p in home["pages"]] == ["northwind"]
    assert home["pages"][0]["path"].endswith("knowledge/projects/lighthouse/lighthouse.md")
    assert [s["space"] for s in home["status"]] == ["northwind"]


def test_the_cli_writes_the_pack_and_prints_the_brief_path(tmp_path, capsys):
    root = ada(tmp_path)
    assert brief.main([str(root), "pack", "tidewater"]) == 0
    out = root / "_meta" / "state" / "private" / "briefs" / "tidewater.pack.json"
    pack = json.loads(out.read_text(encoding="utf-8"))
    assert pack["brief"] == {"path": "_meta/state/private/briefs/tidewater.md", "exists": False}

    pages.write(root / "_meta/state/private/briefs/tidewater.md",
                {"subject": "tidewater", "built_at": "2026-10-11T09:00:00Z",
                 "updated_at": "2026-10-11T09:00:00Z", "records_read": 3}, "# Tidewater\n")
    elsewhere = root / "_meta" / "state" / "private" / "briefs" / "packs" / "t.json"   # --out stays in the briefs folder
    assert brief.main([str(root), "pack", "tidewater", "--out", str(elsewhere)]) == 0
    again = json.loads(elsewhere.read_text(encoding="utf-8"))
    assert again["brief"]["exists"] is True and again["brief"]["records_read"] == 3
    outside = tmp_path / "packs" / "t.json"   # finding 12: anywhere else is refused, and nothing is written
    assert brief.main([str(root), "pack", "tidewater", "--out", str(outside)]) == 2 and not outside.exists()

    capsys.readouterr()
    assert brief.main([str(root), "path", "tidewater"]) == 0
    assert capsys.readouterr().out.strip().replace("\\", "/").endswith("_meta/state/private/briefs/tidewater.md")


def test_an_unknown_subject_exits_2(tmp_path, capsys):
    root = ada(tmp_path)
    assert brief.main([str(root), "pack", "nowhere"]) == 2
    assert "nowhere" in capsys.readouterr().out
    assert brief.main([str(root), "path", "nowhere"]) == 2
    assert not (root / "_meta" / "state" / "private" / "briefs" / "nowhere.pack.json").exists()
