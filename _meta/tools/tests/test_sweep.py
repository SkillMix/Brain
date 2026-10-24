"""sweep.py: feed records become witness pages, attachments, queue items and signals."""
import datetime as dt
import json

import yaml

import link
import pages
import release
import spaceyaml
import sweep
from conftest import NOW, make_space, mount_brain
from test_link import journal_ws

def walled(home, crew):
    """A settled classification: the gate lets the project reach its own space."""
    return {"class": "walled", "state": "settled", "home": home, "crew": crew, "reason": "its own space",
            "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}


BRAIN_SUBJECTS = [
    {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
     "match": {"label": "Harbor"}, "classification": walled("harbor", ["ada", "ben"])},
    {"id": "beacon", "kind": "projects", "title": "Beacon", "home": "beacon",
     "match": {"label": "Beacon"}, "classification": walled("beacon", ["ada"])},
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


# a mail source its project's crew already reads: it leaves at once, nothing to judge
CREW_MAIL = [{"name": "mail", "adapter": "gmail", "audience": "crew"},
             {"name": "calls", "adapter": "recorder", "audience": "owner"}]


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


def only_queue_item(root):
    """The frontmatter of the one queue item; fails when there are none or several."""
    (fm, _), = queue_items(root)
    return fm


def held_in_doubt(root):
    return sum(fm.get("reason") == "doubt" for fm, _ in held_items(root))


def test_a_record_its_destination_already_reads_queues_at_once_and_writes_signal(tmp_path):
    _, _, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    report = sweep.run(ada, now=NOW)
    assert report.records_seen == 1 and report.witness_written == 1
    assert report.attached == 1 and report.queued == 1 and report.signals == 1
    assert report.held_in_doubt == 0 and report.held_other == 0
    witness = ada / "knowledge" / "sources" / "2026" / "10" / "m-0001.md"
    fm, body = pages.read(witness)
    assert fm["type"] == "source" and fm["status"] == "draft"
    assert fm["subject"] == "harbor" and fm["record"] == "m-0001"
    assert fm["audience"] == "crew" and fm["generated"]["by"] == "sweep"
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


def test_without_router_or_standing_choice_a_widening_record_waits_for_a_yes(tmp_path):
    """Nothing here read what it says: it leaves only when its person says yes."""
    _, _, ada = build(tmp_path)
    write_feed(ada, [record(text="Go-live moves to 3 November.\n" + "More words. " * 900)])
    report = sweep.run(ada, now=NOW)
    assert report.queued == 0 and report.held_in_doubt == 1 and report.signals == 1
    (fm, body), = held_items(ada)
    assert fm["reason"] == "doubt" and "no router" in fm["why"] and "high_stakes" not in fm
    assert len(body) <= sweep.CAP_WITHOUT_ROUTING + 10, "a yes releases it shallow, never in full"


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


def test_a_standing_choice_suggests_the_yes_and_no_word_decides(tmp_path):
    """Without the router a standing choice makes yes the expected answer, never the answer:
    nobody read the record. A word that only looks like a topic decides nothing either way."""
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record("m-0003", text="The transfer pricing team asked for the settlement report.")])
    report = sweep.run(ada, now=NOW)
    assert report.queued == 0 and report.held_in_doubt == 1 and report.held_other == 0
    (fm, _), = held_items(ada)
    assert fm["recommendation"].startswith("yes") and "standing choice" in fm["why"]
    witness_fm, _ = pages.read(ada / "knowledge" / "sources" / "2026" / "10" / "m-0003.md")
    assert "sensitive" not in witness_fm
    _, _, ben = build(tmp_path / "crew", sources=CREW_MAIL)
    write_feed(ben, [record("m-0006", text="Please sign the contract by Friday.")])
    sweep.run(ben, now=NOW)
    (fm, _), = queue_items(ben)
    assert fm["class"] == "at_once" and not fm.get("high_stakes")


def test_second_run_processes_nothing_new(tmp_path):
    _, _, ada = build(tmp_path, sources=CREW_MAIL)
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


def test_calls_never_travel_under_a_standing_choice_and_mixed_calls_are_doubt(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "call"}])
    crew_call = record("c-0001", source="calls", kind="call", subject="Harbor weekly",
                       participants=["Ada Okafor", "Ben Liu"], text="Status of the pier work.")
    mixed_call = record("c-0002", source="calls", kind="call", subject="Harbor and lunch",
                        participants=["Ada Okafor", "Ben Liu", "Chloe Martin"],
                        text="Pier work, then weekend plans.")
    write_feed(ada, [crew_call, mixed_call], source="calls")
    report = sweep.run(ada, now=NOW)
    assert report.queued == 0 and report.held_in_doubt == 2 and report.signals == 2
    held = {fm["evidence"][0]: fm for fm, _ in held_items(ada)}
    assert "no router" in held["c-0001"]["why"]
    assert held["c-0002"]["reason"] == "doubt" and "Chloe Martin" in held["c-0002"]["why"]


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
    assert [fm["evidence"] for fm, _ in held_items(ada)] == [["m-0007"]], "it widens: a person decides"
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
    _, _, ada = build(tmp_path, sources=CREW_MAIL)
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


def test_a_token_link_is_cut_on_the_page_and_raises_no_security_notice(tmp_path):
    """A verification, unsubscribe or ticket link carries a token and no password. The page keeps
    the link without its token, and the person gets no security email for it."""
    import owner_email
    _, _, ada = build(tmp_path, owner_email="ada@lantern.example")
    jwt = "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20
    text = (f"View request: https://help.example.net/portal/7/AB-42?token={jwt}&sda_source=email\n"
            "Turn off notifications: https://help.example.net/portal/7/AB-42/unsubscribe?jwt=[redacted]\n"
            "Meeting ID: 123 456 789 012 345 Passcode: [redacted]")
    write_feed(ada, [record("m-0010", text=text, redacted=True)])
    sweep.run(ada, now=NOW)
    assert owner_email.pending(ada) == []
    (page,) = (ada / "knowledge" / "sources").rglob("*m-0010*.md")
    body = page.read_text(encoding="utf-8")
    assert jwt not in body and "AB-42?token=[link-token]&sda_source=email" in body
    assert "unsubscribe?jwt=[link-token]" in body


def test_a_source_added_later_is_read_in_full_once(tmp_path):
    """Each feed keeps its own watermark: a tracker connected after the mail has run brings its
    older records in, instead of falling behind the mail's watermark."""
    _, _, ada = build(tmp_path)
    write_feed(ada, [record("m-0100", at="2026-10-12T08:00:00Z")])
    sweep.run(ada, now=NOW)
    data = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    data["sources"].append({"name": "tracker", "adapter": "files", "audience": "owner"})
    (ada / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    write_feed(ada, [record("t-0001", at="2026-10-01T08:00:00Z", source="tracker", kind="issue",
                            subject="HAR-1: the export", issue="HAR-1", state="Todo")], source="tracker")
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 1
    assert list((ada / "knowledge" / "sources" / "2026" / "10").glob("*t-0001*.md"))
    assert sweep.run(ada, now=NOW).witness_written == 0


def test_no_release_when_the_home_reads_that_channel_itself(tmp_path):
    """A record from a channel the subject's home sweeps on its own stays a witness page here:
    no queue item, no signal, no second copy at the home."""
    _, harbor, ada = build(tmp_path)
    data = yaml.safe_load((harbor / "space.yaml").read_text(encoding="utf-8"))
    data["sources"] = [{"name": "chat", "adapter": "files", "audience": "crew", "channels": ["C-HARBOR"]}]
    (harbor / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    reg = harbor.parent / "brain" / "_meta" / "subjects.yaml"
    regdata = yaml.safe_load(reg.read_text(encoding="utf-8"))
    for s in regdata["subjects"]:
        if s["id"] == "harbor":
            s["match"] = dict(s.get("match") or {}, channel="C-HARBOR")
    reg.write_text(yaml.safe_dump(regdata, sort_keys=False), encoding="utf-8")
    write_feed(ada, [record("s-0001", source="chat", kind="message", identifiers={"channel": "C-HARBOR"})],
               source="chat")
    data = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    data["sources"].append({"name": "chat", "adapter": "files", "audience": "owner"})
    (ada / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    report = sweep.run(ada, now=NOW)
    assert report.attached == 1 and report.queued == 0 and report.signals == 0


def test_a_record_id_with_characters_files_cannot_hold_still_gets_its_page(tmp_path):
    _, _, ada = build(tmp_path)
    write_feed(ada, [record("SM-224@2026-10-02T15:46:23Z", subject="A/B: test")])
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 1
    (page,) = [p for p in (ada / "knowledge" / "sources").rglob("*.md") if "SM-224" in p.name]
    assert ":" not in page.name and pages.read(page)[0]["record"] == "SM-224@2026-10-02T15:46:23Z"
    assert sweep.file_name("lantern/harbor#310") == "lantern-harbor-310"


def test_a_very_long_record_id_gets_a_short_stable_file_name(tmp_path):
    long_id = "gcal-" + "e9im6r31d5miqobjedkn6t1dcdnmspj2" * 12
    name = sweep.file_name(long_id)
    assert len(name) <= 80 and name == sweep.file_name(long_id) and name.startswith("gcal-e9im6r31")
    assert name != sweep.file_name(long_id + "x")
    _, _, ada = build(tmp_path)
    write_feed(ada, [record(long_id)])
    assert sweep.run(ada, now=NOW).witness_written == 1


# ----- inferred attachment: a client's mail domain, a meeting title -------------------------
def set_match(tmp_path, sid, **match):
    reg = tmp_path / "brain" / "_meta" / "subjects.yaml"
    data = yaml.safe_load(reg.read_text(encoding="utf-8"))
    for s in data["subjects"]:
        if s["id"] == sid:
            s["match"] = dict(s.get("match") or {}, **match)
    reg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def add_people(tmp_path, *people):
    reg = tmp_path / "brain" / "_meta" / "subjects.yaml"
    data = yaml.safe_load(reg.read_text(encoding="utf-8"))
    data["subjects"] += list(people)
    reg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def client_mail(rid="m-0200", **over):
    base = {"from": "Chloe Martin <chloe@harbor.example>", "to": ["ada@lantern.example"],
            "cc": ["ben@lantern.example"], "subject": "Brief after the kickoff",
            "text": "Here is the brief. We expect the first list by Friday.",
            "identifiers": {"thread": "t-200", "labels": ["INBOX"]}}
    base.update(over)
    return record(rid, **base)


def test_a_client_mail_attached_by_domain_waits_for_a_yes_despite_a_standing_choice(tmp_path):
    """The owner's standing choice covered what they label; a mail placed by its domain was
    never chosen, so its person decides."""
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    set_match(tmp_path, "harbor", domain="harbor.example")
    write_feed(ada, [client_mail()])
    report = sweep.run(ada, now=NOW)
    assert report.attached == 1 and report.queued == 0 and report.held_in_doubt == 1
    (fm, body), = held_items(ada)
    assert fm["reason"] == "doubt" and fm["subject"] == "harbor" and "domain or title" in fm["why"]
    assert "first list by Friday" in body
    witness, = list((ada / "knowledge" / "sources").rglob("m-0200.md"))
    assert pages.read(witness)[0]["cc"] == ["ben@lantern.example"]


def test_items_carry_when_who_and_the_thread_for_the_homes_status_page(tmp_path):
    _, _, ada = build(tmp_path)
    set_match(tmp_path, "harbor", domain="harbor.example")
    write_feed(ada, [client_mail()])
    sweep.run(ada, now=NOW)
    (fm, _), = held_items(ada)
    assert fm["at"] == "2026-10-12T08:14:00Z"
    assert fm["from"] == "Chloe Martin <chloe@harbor.example>" and fm["to"] == ["ada@lantern.example"]
    assert fm["identifiers"] == {"thread": "t-200"}, "the thread only: labels and channels stay home"


def test_reattached_pages_wait_for_a_yes_even_under_a_standing_choice(tmp_path):
    """The backfill: pages swept before the domain was known attach now, and their person decides."""
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [client_mail(), record("m-0201", identifiers={"labels": []})])
    first = sweep.run(ada, now=NOW)
    assert first.attached == 0 and queue_items(ada) == []
    set_match(tmp_path, "harbor", domain="harbor.example")
    later = NOW + dt.timedelta(hours=3)
    second = sweep.run(ada, now=later)
    assert second.attached == 1 and second.queued == 0 and second.held_in_doubt == 1
    (fm, body), = held_items(ada)
    assert fm["reason"] == "doubt" and fm["evidence"] == ["m-0200"]
    assert fm["from"] == "Chloe Martin <chloe@harbor.example>" and fm["identifiers"] == {"thread": "t-200"}
    assert "first list by Friday" in body
    assert sweep.run(ada, now=later + dt.timedelta(minutes=5)).held_in_doubt == 0, "attached once"


def test_a_reattached_labelled_record_never_ships_at_once(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                        audience=["Ada Okafor", "Ben Liu"])
    ada = make_space(tmp_path / "ada", name="ada", standing_choices=[{"destination": "harbor", "kind": "mail"}],
                     sources=[{"name": "mail", "adapter": "gmail", "audience": "owner"}])
    mount_brain(ada, brain)
    mount(ada, "harbor", harbor)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (brain / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [
        {"id": "harbor", "kind": "projects", "home": "harbor", "match": {"label": "Harbor"}}]}),
        encoding="utf-8")
    sweep.run(ada, now=NOW + dt.timedelta(minutes=1))
    assert queue_items(ada) == []
    (fm, _), = held_items(ada)
    assert fm["reason"] == "doubt" and "re-attached" in fm["why"]


def _call(rid, subject, participants, text):
    return record(rid, source="calls", kind="call", subject=subject, to=[], participants=participants,
                  identifiers={"thread": rid}, text=text, **{"from": "ada@lantern.example"})


def test_a_crew_name_written_with_accents_is_still_crew(tmp_path):
    _, harbor, ada = build(tmp_path)
    space, dest = spaceyaml.load(ada), spaceyaml.load(harbor)
    assert sweep.outsiders(space, dest, ["Bén Liú", "ADA OKAFOR", "Eve Stone"]) == ["Eve Stone"]


def test_a_client_call_with_the_clients_own_people_names_only_real_outsiders(tmp_path):
    """Participants are addresses: a registered colleague by their email or alias is crew, the
    client's own people (the subject's domain) are its counterpart; anyone else is named."""
    _, _, ada = build(tmp_path, owner_email="ada@lantern.example")
    set_match(tmp_path, "harbor", domain="harbor.example", keywords=["Harbor"])
    add_people(tmp_path, {"id": "ben", "kind": "people", "title": "Ben Liu", "email": "ben@lantern.example",
                          "aliases": ["ben.liu@lantern.example"]})
    kickoff = _call("c-0200", "Kickoff Lantern x Harbor",
                    ["ada@lantern.example", "Ben.Liu@lantern.example", "chloe@harbor.example"],
                    "Scope agreed. Ben sends the plan on Monday.")
    mixed = _call("c-0201", "Harbor and the board",
                  ["ada@lantern.example", "chloe@harbor.example", "eve@elsewhere.example"],
                  "Pier work, then other business.")
    write_feed(ada, [kickoff, mixed], source="calls")
    report = sweep.run(ada, now=NOW)
    assert report.attached == 2 and report.queued == 0 and report.held_in_doubt == 2
    held = {fm["evidence"][0]: (fm, body) for fm, body in held_items(ada)}
    fm, body = held["c-0200"]
    assert "outside" not in fm["why"] and "plan on Monday" in body
    fm, _ = held["c-0201"]
    assert "eve@elsewhere.example" in fm["why"] and "chloe" not in fm["why"]


def test_attachments_lists_what_to_read_on_threads_that_belong_to_one_subject(tmp_path, capsys):
    """The export reads a matching thread's attachments as document records of their own."""
    _, _, ada = build(tmp_path)
    set_match(tmp_path, "harbor", domain="harbor.example")
    write_feed(ada, [client_mail(attachments=["brief.pdf", "plan.xlsx"]),
                     client_mail("m-0202", attachments=["done.pdf"]),
                     record("m-0203", identifiers={"labels": ["Harbor", "Beacon"]}, attachments=["both.pdf"]),
                     record("m-0204", identifiers={"labels": []}, attachments=["none.pdf"]),
                     record("m-0202/done.pdf", kind="document", subject="done.pdf", text="Done.")])
    assert sweep.attachments(ada) == [("m-0200", "harbor", "brief.pdf"), ("m-0200", "harbor", "plan.xlsx")]
    assert sweep.main([str(ada), "--attachments"]) == 0
    out = capsys.readouterr().out
    assert "m-0200\tharbor\tbrief.pdf" in out and "none.pdf" not in out
    assert not (ada / "knowledge" / "sources" / "2026").exists(), "listing writes nothing"


def test_the_export_contract_is_lossless_and_keeps_private_records_out():
    """What the exporting agent reads decides what a project can know: the skill and the
    scheduled routine both ask for every record in full (mail bodies, call transcripts, document
    and attachment text), keep personal and academic records out, park unsure ones privately,
    and hand over to the router."""
    from pathlib import Path

    import pytest
    root = Path(sweep.__file__).resolve().parents[2]
    paths = (root / ".claude" / "skills" / "sweep" / "SKILL.md", root / "_meta" / "templates" / "routines" / "sweep-chain.md")
    if not all(p.exists() for p in paths):
        pytest.skip("this repository does not carry the skills (a project may keep .claude/skills untracked)")
    for text in (p.read_text(encoding="utf-8") for p in paths):
        flat = " ".join(text.split())
        assert "8,000" not in flat and "never the transcript" not in flat, "capture is lossless"
        assert "full transcript" in flat and "full body" in flat and "full text" in flat
        assert "personal" in flat and "academic" in flat and "unsure.jsonl" in flat
        assert "sweep.py <root> --attachments" in flat and "read_via" in flat
        assert "route/SKILL.md" in flat


def test_after_a_yes_the_homes_status_page_shows_the_client_thread_and_the_call(tmp_path):
    """End to end: a client mail and a client call, placed by domain and title, wait for their
    person's yes, land at the project, and its status page lists the call and the thread with
    who moves next."""
    import release
    import sign
    import status
    brain, harbor, ada = build(tmp_path, owner_email="ada@lantern.example")
    mount_brain(harbor, brain)
    set_match(tmp_path, "harbor", domain="harbor.example", keywords=["Harbor"])
    write_feed(ada, [client_mail()])
    write_feed(ada, [_call("c-0400", "Kickoff Lantern x Harbor", ["ada@lantern.example", "chloe@harbor.example"],
                           "Summary: scope agreed.\nAction items: Ada sends the plan by 2026-10-20.")],
               source="calls")
    sweep.run(ada, now=NOW)
    assert release.ship(ada, now=NOW, offline_roots={"harbor": harbor}).shipped == 0, "nothing by itself"
    sign.apply(ada, {fm["id"]: "yes" for fm, _ in held_items(ada)}, by="Ada Okafor", now=NOW)
    later = NOW + dt.timedelta(hours=1)
    assert release.ship(ada, now=later, offline_roots={"harbor": harbor}).shipped == 2
    status.run(harbor, now=later)
    page = (harbor / "live" / "subjects" / "harbor.md").read_text(encoding="utf-8")
    open_part = page.split("## Open", 1)[1].split("## Recently closed", 1)[0]
    assert "### We owe" in open_part and "Brief after the kickoff" in open_part
    assert "Kickoff Lantern x Harbor" in page.split("## Timeline", 1)[1]
    landed = list((harbor / "knowledge" / "sources" / "2026" / "10").glob("*.md"))
    assert len(landed) == 2 and all(pages.read(p)[0]["at"].startswith("2026-10-12") for p in landed)


# ----- judgment routing: the sweep captures, the router releases ---------------------------
def test_with_routing_on_the_sweep_writes_pages_and_releases_nothing(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}],
                      routing={"since": "2026-07-01"})
    write_feed(ada, [record()])
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 1 and report.attached == 1
    assert report.queued == 0 and report.signals == 0 and queue_items(ada) == []
    assert not list((ada / "_outbound" / "signals").glob("*.yaml"))


def test_a_deeper_export_refreshes_the_page_and_keeps_its_id(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record(text="Short preview.")])
    sweep.run(ada, now=NOW)
    page, = list((ada / "knowledge" / "sources").rglob("m-0001.md"))
    fm, body = pages.read(page)
    first_id = fm["id"]
    pages.write(page, dict(fm, routing={"decision": "private"}), body)
    write_feed(ada, [record(text="Short preview. And the whole rest of the message, in full.")])
    (ada / "_meta" / "state" / "sweep-watermark.yaml").unlink()   # the export re-read an old record
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    fm, body = pages.read(page)
    assert fm["id"] == first_id and "whole rest of the message" in body
    assert fm.get("refreshed_at") and "routing" not in fm, "a refreshed page is routed again"
    write_feed(ada, [record(text="Short.")])
    (ada / "_meta" / "state" / "sweep-watermark.yaml").unlink()
    sweep.run(ada, now=NOW + dt.timedelta(hours=2))
    assert "whole rest of the message" in pages.read(page)[1], "a shorter export never shrinks a page"


def test_an_imported_page_is_marked_and_the_original_replaces_it_however_short(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    retold = "An earlier system's retelling of the mail, far longer than the mail itself ever was."
    write_feed(ada, [record(text=retold, witness_page="knowledge/sources/2026/10/old-slug.md")])
    sweep.run(ada, now=NOW)
    page, = list((ada / "knowledge" / "sources").rglob("m-0001.md"))
    assert pages.read(page)[0]["imported_from"] == "knowledge/sources/2026/10/old-slug.md"
    write_feed(ada, [record(text="Go-live moves.", backfill=True)])
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    fm, body = pages.read(page)
    assert "Go-live moves." in body and "retelling" not in body, "the original replaces an import"
    assert "imported_from" not in fm


def test_a_record_whose_text_holds_a_unicode_line_separator_is_read_whole(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    path = ada / "_meta" / "state" / "feed" / "mail.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record(text="Page one\u2028page two."), ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    assert sweep.run(ada, now=NOW).witness_written == 1


def test_a_file_with_no_text_settles_its_attachment_without_a_page(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record(attachments=["image001.png"]),
                     record("m-0001/image001.png", kind="document", subject="image001.png", text="",
                            attachments=[])])
    sweep.run(ada, now=NOW)
    assert not list((ada / "knowledge" / "sources").rglob("image001*.md")), "no page for an empty file"
    assert sweep.attachments(ada) == [], "the feed record alone settles the attachment"


def test_a_document_is_not_listed_for_its_own_file_and_an_object_entry_lists_by_name(tmp_path):
    """A document's `attachments` points at the file the record already holds; an entry written
    as an object (name, type, link) is listed and settled by its name."""
    _, _, ada = build(tmp_path, routing={})
    deck = {"name": "deck.pdf", "mimeType": "application/pdf", "url": ""}
    write_feed(ada, [record(identifiers={"labels": []}, attachments=[deck]),
                     record("d-0001", kind="document", subject="plan.xlsx", text="The plan.",
                            identifiers={"labels": []}, attachments=[{"name": "plan.xlsx", "url": ""}])])
    assert sweep.attachments(ada) == [("m-0001", "", "deck.pdf")]
    write_feed(ada, [record(identifiers={"labels": []}, attachments=[deck]),
                     record("m-0001/deck.pdf", kind="document", subject="deck.pdf", text="The deck.")])
    assert sweep.attachments(ada) == [], "the document record settles the object entry"


def test_a_record_with_windows_line_endings_is_not_refreshed_again_and_again(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record(text="Line one.  \r\nLine two.\r\nLine three.   \n  ", backfill=True)])
    sweep.run(ada, now=NOW)
    page, = list((ada / "knowledge" / "sources").rglob("m-0001.md"))
    fm, body = pages.read(page)
    pages.write(page, dict(fm, routing={"decision": "private"}), body)
    report = sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    assert report.witness_written == 0 and pages.read(page)[0].get("routing"), "same text, no refresh"


def test_a_page_over_the_size_limit_is_split_on_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "PART_BYTES", 60)
    _, _, ada = build(tmp_path, routing={})
    lines = [f"[00:00:{i:02d}] Ada: line {i} of the meeting" for i in range(6)]
    write_feed(ada, [record("c-0900", source="calls", kind="call", subject="Long call", to=[],
                            text="\n".join(lines))], source="calls")
    sweep.run(ada, now=NOW)
    found = sorted((ada / "knowledge" / "sources").rglob("c-0900*.md"))
    assert len(found) >= 3
    texts = "".join(pages.read(p)[1] for p in found)
    assert all(line in texts for line in lines), "nothing lost in the split"
    fms = [pages.read(p)[0] for p in found]
    assert {fm["parts"] for fm in fms} == {len(found)} and sorted(fm["part"] for fm in fms) == list(range(1, len(found) + 1))
    assert all(len(pages.read(p)[1].encode("utf-8")) <= 60 for p in found)


def test_the_workspaces_own_briefing_and_its_reply_never_become_pages(tmp_path):
    _, _, ada = build(tmp_path, owner_email="ada@lantern.example", routing={})
    write_feed(ada, [record("m-brief", subject="Morning briefing: 3 items need you", to=["ada@lantern.example"],
                            text="7. Is this work? Dinner on Friday", **{"from": "ada@lantern.example"}),
                     record("m-reply", subject="Re: Morning briefing: 3 items need you", to=["ada@lantern.example"],
                            text="7 no", **{"from": "Ada Okafor <ada@lantern.example>"})])
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 0
    assert not list((ada / "knowledge" / "sources").rglob("m-*.md"))


def test_with_routing_on_every_unread_attachment_is_listed(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record("m-0300", identifiers={"labels": []}, attachments=["notes.pdf"]),
                     record("m-0301", identifiers={"labels": ["Harbor", "Beacon"]}, attachments=["both.pdf"])])
    assert sweep.attachments(ada) == [("m-0300", "", "notes.pdf"), ("m-0301", "", "both.pdf")]


def test_a_record_the_owner_admitted_gets_its_page_however_old(tmp_path):
    """A record brought in from the unsure list is older than the feed's mark: still swept."""
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record("m-0400", at="2026-10-12T08:30:00Z")])
    sweep.run(ada, now=NOW)
    write_feed(ada, [record("m-0400", at="2026-10-12T08:30:00Z"),
                     record("m-0401", at="2026-10-01T08:00:00Z", admitted=True)])
    assert sweep.run(ada, now=NOW).witness_written == 1
    assert list((ada / "knowledge" / "sources").rglob("m-0401.md"))


# ----- the final review's findings ---------------------------------------------------------
def test_with_routing_on_the_sweep_never_rewrites_a_routed_page(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [client_mail()])
    sweep.run(ada, now=NOW)
    page, = list((ada / "knowledge" / "sources").rglob("m-0200.md"))
    fm, body = pages.read(page)
    pages.write(page, dict(fm, routing={"decision": "private"}), body)
    set_match(tmp_path, "harbor", domain="harbor.example")
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    after = pages.read(page)[0]
    assert after["id"] == fm["id"] and after["routing"] == {"decision": "private"}


def test_parts_of_a_split_page_are_never_re_attached(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "PART_BYTES", 60)
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    text = "\n".join(f"Line {i} of a long message about the pier." for i in range(6))
    write_feed(ada, [record("m-0901", text=text)])
    sweep.run(ada, now=NOW)
    before = sorted(p.name for p in (ada / "knowledge" / "sources").rglob("*.md"))
    queued_before = len(queue_items(ada))
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    assert sorted(p.name for p in (ada / "knowledge" / "sources").rglob("*.md")) == before
    assert len(queue_items(ada)) == queued_before


def test_without_routing_a_call_transcript_never_leaves_by_identifier(tmp_path):
    _, _, ada = build(tmp_path, owner_email="ada@lantern.example")
    set_match(tmp_path, "harbor", domain="harbor.example")
    call = _call("c-0950", "Kickoff", ["ada@lantern.example", "chloe@harbor.example"],
                 "Summary: scope agreed.\nAction items: Ada sends the plan.\n"
                 "[00:01:00] Chloe: our internal numbers are bad.\n" + "More words. " * 900)
    write_feed(ada, [call], source="calls")
    sweep.run(ada, now=NOW)
    (fm, body), = held_items(ada)
    assert "Action items: Ada sends the plan." in body and "[00:01:00]" not in body
    assert len(body) <= sweep.CAP_WITHOUT_ROUTING + 10


def test_a_refresh_takes_the_deeper_exports_people_too(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [client_mail(text="Short.", cc=[])])
    sweep.run(ada, now=NOW)
    write_feed(ada, [client_mail(text="Short. And the rest of the message in full.", cc=["eve@elsewhere.example"])])
    (ada / "_meta" / "state" / "sweep-watermark.yaml").unlink()
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    page, = list((ada / "knowledge" / "sources").rglob("m-0200.md"))
    assert pages.read(page)[0]["cc"] == ["eve@elsewhere.example"]


def test_a_record_found_by_research_keeps_the_subject_it_was_found_for(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record(research="harbor", text="A lead came in through the form.")])
    sweep.run(ada, now=NOW)
    page, = list((ada / "knowledge" / "sources").rglob("m-0001.md"))
    assert pages.read(page)[0].get("research") == "harbor"


def test_an_upcoming_event_does_not_move_the_mark_past_the_run(tmp_path):
    """A calendar export holds events that have not happened yet; the feed's mark stops at the
    run's own time, so an event added later that starts sooner is still swept."""
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record("e-0001", at=(NOW + dt.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))])
    sweep.run(ada, now=NOW)
    write_feed(ada, [record("e-0001", at=(NOW + dt.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")),
                     record("e-0002", at=(NOW + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))])
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    assert list((ada / "knowledge" / "sources").rglob("e-0002.md")), "the sooner event is swept"


def test_a_backfill_re_export_deepens_old_pages_behind_the_mark(tmp_path):
    """A backfill re-reads older records in full: they are behind the feed's mark, yet the sweep
    reads them, so their shallow pages deepen in place."""
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record("m-0500", at="2026-10-01T08:00:00Z", text="Preview."),
                     record("m-0501", at="2026-10-12T08:30:00Z")])
    sweep.run(ada, now=NOW)
    write_feed(ada, [record("m-0500", at="2026-10-01T08:00:00Z", text="Preview. The whole message.", backfill=True),
                     record("m-0501", at="2026-10-12T08:30:00Z")])
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    page, = list((ada / "knowledge" / "sources").rglob("m-0500.md"))
    assert "The whole message." in pages.read(page)[1]


# ----- notes from a codebase session (D8) ---------------------------------------------------------
def test_walled_crew_home_gets_journal_at_once(tmp_path):
    # harbor-voice-agent classified walled, home harbor, crew ["ada"]; routing off; only mail declared
    ws = journal_ws(tmp_path, crew=["ada"])
    link.journal_records(ws, now=NOW)
    sweep.run(ws, now=NOW)
    item = only_queue_item(ws)
    assert item["class"] == "at_once" and item["destination"] == "harbor"
    assert item["path"] == "journal:harbor" and pages.parse_iso(item["stage_after"]) == NOW


def test_other_home_gets_it_after_the_window(tmp_path):
    # the same entry, crew ["ben"]
    ws = journal_ws(tmp_path, crew=["ben"])
    link.journal_records(ws, now=NOW)
    sweep.run(ws, now=NOW)
    item = only_queue_item(ws)
    assert item["class"] == "window" and held_in_doubt(ws) == 0
    assert pages.parse_iso(item["stage_after"]) == NOW + spaceyaml.load(ws).window()


def test_a_paused_journal_path_waits_the_window(tmp_path):
    ws = journal_ws(tmp_path, crew=["ada"])
    release.pause(ws, "journal:harbor", NOW, "pulled back", "x", spaceyaml.load(ws))
    link.journal_records(ws, now=NOW)
    sweep.run(ws, now=NOW)
    assert only_queue_item(ws)["class"] == "window"


def test_a_journal_entry_headed_before_a_swept_one_still_travels(tmp_path):
    # A (10:15) is swept at noon; then an entry headed 10:05 joins the same note (a late write,
    # an evening entry in a UTC heading, or a link made after the entries)
    ws = journal_ws(tmp_path, crew=["ada"])
    noon, later = NOW.replace(hour=12), NOW.replace(hour=13)
    link.journal_records(ws, now=noon)
    sweep.run(ws, now=noon)
    note, = (ws / "journal").rglob("*.md")
    with note.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n## 10:05Z: flows reviewed\n\n- ref: northwind/harbor-voice:docs/flows.md@abd\n"
                     "- subject: harbor-voice-agent\n")
    link.journal_records(ws, now=later)
    sweep.run(ws, now=later)
    assert len(queue_items(ws)) == 2
    sweep.run(ws, now=later)
    assert len(queue_items(ws)) == 2   # a re-read finds the witness page and stops
