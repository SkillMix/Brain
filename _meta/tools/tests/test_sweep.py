"""sweep.py: feed records become witness pages, attachments, queue items and signals."""
import datetime as dt
import json

import yaml

import pages
import spaceyaml
import sweep
from conftest import NOW, make_space, mount_brain

BRAIN_SUBJECTS = [
    {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
     "match": {"label": "Harbor"}},
    {"id": "beacon", "kind": "projects", "title": "Beacon", "home": "beacon",
     "match": {"label": "Beacon"}},
    {"id": "pricing", "kind": "topics", "title": "Pricing", "match": {"label": "Pricing"}},
]


def record(rid="m-0001", **over):
    base = {"id": rid, "source": "mail", "kind": "mail", "at": "2026-10-12T08:14:00Z",
            "from": "Chloe Martin", "to": ["Ada Okafor"], "subject": "Re: Harbor go-live",
            "text": "Go-live moves to 3 November. Please confirm.",
            "identifiers": {"thread": "t-1", "labels": ["Harbor"]}, "attachments": []}
    base.update(over)
    return base


def write_feed(root, records, source="mail"):
    path = root / "_meta" / "state" / "feed" / f"{source}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8", newline="\n")


def mount(space_root, name, target):
    data = yaml.safe_load((space_root / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] = [m for m in (data.get("mounts") or []) if m.get("name") != name]
    data["mounts"].append({"name": name, "path": str(target)})
    (space_root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def build(tmp_path, **ada_extra):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=BRAIN_SUBJECTS)
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                        audience=["Ada Okafor", "Ben Liu"])
    ada_extra.setdefault("sources", [{"name": "mail", "adapter": "gmail", "audience": "owner"},
                                     {"name": "calls", "adapter": "recorder", "audience": "owner"}])
    ada = make_space(tmp_path / "ada", name="ada", **ada_extra)
    mount_brain(ada, brain)
    mount(ada, "harbor", harbor)
    return brain, harbor, ada


def queue_items(root):
    return [pages.read(p) for p in sorted((root / "_outbound" / "queue").glob("*.md"))]


def held_items(root):
    return [pages.read(p) for p in sorted((root / "_outbound" / "held").glob("*.md"))]


def test_standing_choice_queues_at_once_and_writes_signal(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    report = sweep.run(ada, now=NOW)
    assert report.records_seen == 1 and report.witness_written == 1
    assert report.attached == 1 and report.queued == 1 and report.signals == 1
    assert report.held_in_doubt == 0 and report.held_other == 0
    witness = ada / "knowledge" / "sources" / "2026" / "10" / "m-0001.md"
    fm, body = pages.read(witness)
    assert fm["type"] == "source" and fm["status"] == "draft"
    assert fm["subject"] == "harbor" and fm["record"] == "m-0001"
    assert fm["audience"] == "owner" and fm["generated"]["by"] == "sweep"
    assert pages.ID_RE.match(fm["id"]) and "sensitive" not in fm
    assert "3 November" in body
    (item_fm, item_body), = queue_items(ada)
    assert item_fm["class"] == "at_once" and item_fm["destination"] == "harbor"
    assert item_fm["subject"] == "harbor" and item_fm["evidence"] == ["m-0001"]
    assert item_fm["listed"] is False and item_fm["from_space"] == "ada"
    assert item_fm["kind"] == "mail" and item_fm["status"] == "draft"
    assert pages.parse_iso(item_fm["stage_after"]) == NOW
    assert "3 November" in item_body
    signal_path, = sorted((ada / "_outbound" / "signals").glob("*.yaml"))
    signal = yaml.safe_load(signal_path.read_text(encoding="utf-8"))
    assert set(signal) == {"id", "subject", "destination", "at", "note"}
    assert signal["subject"] == "harbor" and signal["destination"] == "harbor"
    assert signal["note"] == "open item moved" and "November" not in signal_path.read_text()
    assert yaml.safe_load((ada / "_meta" / "state" / "sweep-watermark.yaml").read_text())["at"] \
        == "2026-10-12T08:14:00Z"
    runs = [json.loads(l) for l in (ada / "_meta" / "state" / "runs.jsonl").read_text().splitlines()]
    assert runs[-1]["routine"] == "sweep" and runs[-1]["space"] == "ada"
    assert runs[-1]["cost_tokens"] == len(json.dumps(record())) // 4
    assert runs[-1]["at"] == pages.iso(NOW)


def test_without_standing_choice_queues_window_of_30_minutes_at_scale_48(tmp_path):
    _, _, ada = build(tmp_path, mode="fixture", time_scale=48)
    write_feed(ada, [record()])
    report = sweep.run(ada, now=NOW)
    assert report.queued == 1 and report.signals == 1
    (fm, _), = queue_items(ada)
    assert fm["class"] == "window"
    assert pages.parse_iso(fm["stage_after"]) == NOW + dt.timedelta(minutes=30)
    assert "high_stakes" not in fm


def test_two_matches_are_held_in_doubt_and_counted(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record("m-0002", identifiers={"labels": ["Harbor", "Beacon"]})])
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 1 and report.attached == 0
    assert report.queued == 0 and report.signals == 0 and report.held_in_doubt == 1
    assert queue_items(ada) == []
    (fm, _), = held_items(ada)
    assert fm["reason"] == "doubt" and fm["class"] == "doubt"
    assert sorted(fm["candidates"]) == ["beacon", "harbor"]
    assert fm["why"]
    witness_fm, _ = pages.read(ada / "knowledge" / "sources" / "2026" / "10" / "m-0002.md")
    assert "subject" not in witness_fm


def test_left_out_term_for_the_destination_is_held(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record("m-0003", text="Our day rate 950 for the extra week.")])
    report = sweep.run(ada, now=NOW)
    assert report.attached == 1 and report.queued == 0 and report.held_other == 1
    assert report.signals == 1
    (fm, _), = held_items(ada)
    assert fm["reason"] == "left_out" and fm["destination"] == "harbor"
    assert "day rate" in fm["why"]


def test_never_leaves_term_is_held_and_marks_the_witness_sensitive(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record("m-0004", text="The settlement terms are attached.")])
    report = sweep.run(ada, now=NOW)
    assert report.held_other == 1 and report.queued == 0
    assert report.signals == 0, "nothing about a never-leaves record leaves, not even a signal"
    (fm, _), = held_items(ada)
    assert fm["reason"] == "never_leaves"
    witness_fm, _ = pages.read(ada / "knowledge" / "sources" / "2026" / "10" / "m-0004.md")
    assert witness_fm["sensitive"] is True


def test_second_run_processes_nothing_new(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record(), record("m-0005", at="2026-10-12T08:20:00Z")])
    first = sweep.run(ada, now=NOW)
    assert first.records_seen == 2 and first.witness_written == 2
    second = sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    assert second.records_seen == 0 and second.witness_written == 0 and second.queued == 0
    assert second.watermark == "2026-10-12T08:20:00Z"
    assert len(queue_items(ada)) == 2
    assert len(list((ada / "_outbound" / "signals").glob("*.yaml"))) == 2
    runs = (ada / "_meta" / "state" / "runs.jsonl").read_text().splitlines()
    assert len(runs) == 2


def test_high_stakes_term_waits_the_window_even_under_a_standing_choice(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record("m-0006", text="Please sign the contract by Friday.")])
    sweep.run(ada, now=NOW)
    (fm, _), = queue_items(ada)
    assert fm["class"] == "window" and fm["high_stakes"] is True
    assert pages.parse_iso(fm["stage_after"]) == NOW + dt.timedelta(hours=24)


def test_calls_never_travel_under_a_standing_choice_and_mixed_calls_are_doubt(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "call"}])
    crew_call = record("c-0001", source="calls", kind="call", subject="Harbor weekly",
                       participants=["Ada Okafor", "Ben Liu"], text="Status of the pier work.")
    mixed_call = record("c-0002", source="calls", kind="call", subject="Harbor and lunch",
                        participants=["Ada Okafor", "Ben Liu", "Chloe Martin"],
                        text="Pier work, then weekend plans.")
    write_feed(ada, [crew_call, mixed_call], source="calls")
    report = sweep.run(ada, now=NOW)
    assert report.queued == 1 and report.held_in_doubt == 1 and report.signals == 2
    (fm, _), = queue_items(ada)
    assert fm["class"] == "window" and fm["evidence"] == ["c-0001"]
    (held, _), = held_items(ada)
    assert held["reason"] == "doubt" and held["evidence"] == ["c-0002"]
    assert "Chloe Martin" in held["why"]


def test_audience_everyone_leaves_at_once_and_audience_falls_back_to_the_source(tmp_path):
    _, _, ada = build(tmp_path, sources=[{"name": "mail", "adapter": "gmail", "audience": "owner"},
                                         {"name": "tracker", "adapter": "issues", "audience": "crew"},
                                         {"name": "wiki", "adapter": "pages", "audience": "everyone"}])
    write_feed(ada, [record("w-0001", source="wiki", kind="document", subject="Harbor runbook",
                            text="Restart the beacon service after deploys.")], source="wiki")
    write_feed(ada, [record("i-0001", source="tracker", kind="message", subject="Harbor #12",
                            text="Pier sensor flaky.")], source="tracker")
    write_feed(ada, [record("m-0007", audience="owner")])
    sweep.run(ada, now=NOW)
    by_record = {fm["evidence"][0]: fm for fm, _ in queue_items(ada)}
    assert by_record["w-0001"]["class"] == "at_once"
    assert by_record["i-0001"]["class"] == "at_once", "crew audience bound for the home project"
    assert by_record["m-0007"]["class"] == "window"
    witness_fm, _ = pages.read(ada / "knowledge" / "sources" / "2026" / "10" / "w-0001.md")
    assert witness_fm["audience"] == "everyone"
    assert "beacon" in witness_fm["mentions"]


def test_record_without_a_home_elsewhere_is_witnessed_only(tmp_path):
    _, _, ada = build(tmp_path)
    write_feed(ada, [record("m-0008", identifiers={"labels": ["Pricing"]}),
                     record("m-0009", identifiers={"labels": []})])
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 2 and report.attached == 1
    assert report.queued == 0 and report.signals == 0 and report.held_in_doubt == 0
    fm, _ = pages.read(ada / "knowledge" / "sources" / "2026" / "10" / "m-0008.md")
    assert fm["subject"] == "pricing"


def test_dry_run_writes_nothing(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    report = sweep.run(ada, now=NOW, dry_run=True)
    assert report.queued == 1 and report.plan
    assert not (ada / "knowledge" / "sources" / "2026").exists()
    assert queue_items(ada) == []
    assert not (ada / "_meta" / "state" / "sweep-watermark.yaml").exists()
    assert not (ada / "_meta" / "state" / "runs.jsonl").exists()


def test_no_stable_status_anywhere_and_main_runs(tmp_path, monkeypatch, capsys):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    monkeypatch.setenv("SPACE_NOW", pages.iso(NOW))
    assert sweep.main([str(ada)]) == 0
    for path in list(ada.rglob("*.md")):
        fm, _ = pages.read(path)
        assert fm.get("status") != "stable"
    assert sweep.main([str(ada), "--dry-run"]) == 0
    assert "nothing" in capsys.readouterr().out.lower()
    assert spaceyaml.load(ada).errors() == []


def test_reattach_when_the_registry_catches_up(tmp_path):
    """A record swept before its subject existed is placed once the registry knows it."""
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[])
    ada = make_space(tmp_path / "ada", name="ada", mode="fixture", time_scale=48)
    mount_brain(ada, brain)
    write_feed(ada, [record()])
    first = sweep.run(ada, now=NOW)
    assert first.witness_written == 1 and first.attached == 0 and queue_items(ada) == []
    # the brain registers the project; the next sweep re-attaches the orphan witness page
    (brain / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [
        {"id": "harbor", "kind": "projects", "home": "harbor", "match": {"label": "Harbor"}}]}),
        encoding="utf-8")
    second = sweep.run(ada, now=NOW + dt.timedelta(minutes=1))
    assert second.attached == 1 and second.witness_written == 1
    page, = [p for p in (ada / "knowledge" / "sources").rglob("*.md") if p.name != "index.md"]
    assert pages.read(page)[0].get("subject") == "harbor"
    assert len(queue_items(ada)) + len(list((ada / "_outbound" / "held").glob("*.md"))) == 1


def test_reattach_does_not_rehold_a_doubt_record(tmp_path):
    """A record held in doubt (several matches) is settled: later sweeps leave it alone."""
    brain, harbor, ada = build(tmp_path)
    write_feed(ada, [record(identifiers={"thread": "t-2", "labels": ["Harbor", "Beacon"]})])
    first = sweep.run(ada, now=NOW)
    assert first.held_in_doubt == 1
    second = sweep.run(ada, now=NOW + dt.timedelta(minutes=10))
    third = sweep.run(ada, now=NOW + dt.timedelta(minutes=20))
    assert second.held_in_doubt == 0 and third.held_in_doubt == 0
    assert len(list((ada / "_outbound" / "held").glob("*.md"))) == 1



def test_a_redacted_credential_raises_one_security_notice_for_the_person(tmp_path):
    """A record exported with a key or password redacted (or one the scan still finds) queues a
    plain security notice to the person; the value is never in it, and it is queued once."""
    import owner_email
    _, _, ada = build(tmp_path, owner_email="ada@lantern.example")
    write_feed(ada, [record("m-0009", text="The login is [redacted], use it for the portal.", redacted=True)])
    sweep.run(ada, now=NOW)
    (notice,) = owner_email.pending(ada)
    data = json.loads(notice.read_text(encoding="utf-8"))
    assert data["kind"] == "security" and data["payload"]["to"] == ["ada@lantern.example"]
    assert "[redacted]" not in json.dumps(data["notice"])
    write_feed(ada, [record("m-0009", text="The login is [redacted], use it for the portal.", redacted=True)])
    sweep.run(ada, now=NOW)
    assert len(owner_email.pending(ada)) == 1
