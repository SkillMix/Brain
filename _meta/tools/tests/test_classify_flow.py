"""The classification gate at every exit: the router, the sweep, distill and the ship hold an
unclassified client, project or deal, and put it back once its owner's agent has classified it."""
import datetime as dt
import json

import yaml

import classify
import distill
import pages
import registry
import release
import route
import sweep
from conftest import NOW, make_space, mount_brain

PEOPLE = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "workspace": "ada",
           "email": "ada@lantern.example"},
          {"id": "ben", "kind": "people", "title": "Ben Liu", "email": "ben@lantern.example"}]
WALLED = {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ada", "ben"],
          "reason": "its own space", "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}
COMPANY = {"class": "company", "state": "settled", "home": "lantern", "reason": "ordinary client work",
           "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}
CALL = "\n".join([
    "Summary: the pilot scope.",
    "[00:01:00] Chloe: The first list should hold forty leads.",
    "[00:02:00] Ada: We send it by Friday 2026-10-23.",
])
PARTS = [{"text": "[00:01:00] Chloe: The first list should hold forty leads."}]


def subjects(harbor=None, acme=None):
    h = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "owner": "Ada Okafor",
         "match": {"domain": "harbor.example", "label": "Harbor"}}
    a = {"id": "acme", "kind": "clients", "title": "Acme Foods", "owner": "Ada Okafor", "home": "lantern",
         "match": {"domain": "acme.example"}}
    if harbor:
        h["classification"] = harbor
    if acme:
        a["classification"] = acme
    return PEOPLE + [h, a]


def world(tmp_path, harbor=None, acme=None, routing=True):
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", owner="Ada Okafor",
                       subjects=subjects(harbor, acme))
    project = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor",
                         audience=["Ada Okafor", "Ben Liu"])
    extra = ({"routing": {}, "sources": [{"name": "calls", "adapter": "files", "audience": "owner"}]} if routing
             else {"sources": [{"name": "mail", "adapter": "gmail", "audience": "crew"}]})
    ada = make_space(tmp_path / "ada", name="ada", owner_email="ada@lantern.example", **extra)
    mount_brain(ada, brain)
    mount_brain(project, brain)
    data = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] += [{"name": "harbor", "path": str(project)}]
    (ada / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return brain, project, ada


def classify_in_brain(brain, sid, value):
    path = brain / "_meta" / "subjects.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for entry in data["subjects"]:
        if entry["id"] == sid:
            entry["classification"] = value
            if value.get("home"):
                entry["home"] = value["home"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def captured(root, rid="c-1", sender="ada@lantern.example", participants=None, identifiers=None):
    rec = {"id": rid, "source": "calls", "kind": "call", "at": "2026-10-12T08:00:00Z", "from": sender,
           "to": [], "subject": "Kickoff", "text": CALL,
           "participants": participants or ["ada@lantern.example", "chloe@harbor.example"],
           "identifiers": dict({"thread": rid}, **(identifiers or {}))}
    feed = root / "_meta" / "state" / "feed" / "calls.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    with feed.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    (root / "_meta" / "state" / "sweep-watermark.yaml").unlink(missing_ok=True)
    sweep.run(root, now=NOW)
    return rec


def decision(space, subject, tier="at_once", rid="c-1"):
    return {"record": rid, "decision": "route", "confidence": "high", "why": "the kickoff",
            "checked_by": "Checker",
            "routes": [{"space": space, "subject": subject, "tier": tier, "why": "pilot work",
                        "content": "parts", "parts": PARTS}]}


def folder(root, name):
    return [pages.read(p) for p in sorted((root / "_outbound" / name).glob("*.md"))]


def signals(root):
    return sorted((root / "_outbound" / "signals").glob("*.yaml"))


def queue_item(root, subject, destination, cls="at_once", **extra):
    fm = {"id": pages.new_id(), "type": "source", "title": "A note", "status": "draft",
          "generated": pages.generated("sweep", NOW), "class": cls, "destination": destination,
          "subject": subject, "stage_after": pages.iso(NOW - dt.timedelta(hours=1)), "listed": False,
          "evidence": ["m-1"], "from_space": "ada", "kind": "mail"}
    fm.update(extra)
    return pages.write(root / "_outbound" / "queue" / f"{fm['id']}.md", fm, "Go-live moves to 3 November.\n")


# ----- the router -----------------------------------------------------------------------------------
def test_route_holds_an_unclassified_projection_without_a_signal(tmp_path):
    _, _, ada = world(tmp_path)
    captured(ada)
    out = route.record(ada, decision("harbor", "harbor"), now=NOW)
    assert out["queued"] == [] and len(out["held"]) == 1
    (fm, body), = folder(ada, "held")
    assert fm["reason"] == "unclassified" and fm["subject"] == "harbor" and "forty leads" in body
    assert signals(ada) == []
    row = json.loads((ada / "_meta" / "state" / "routing.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["routes"][0]["outcome"] == "held"


def test_route_first_release_waits_the_window(tmp_path):
    _, _, ada = world(tmp_path, acme=COMPANY)
    captured(ada, participants=["ada@lantern.example", "chloe@acme.example"])
    out = route.record(ada, decision("lantern", "acme"), now=NOW)
    (fm, _), = folder(ada, "queue")
    assert out["queued"] == [fm["id"]]
    assert fm["class"] == "window" and fm["first_release"] == "acme"
    assert pages.parse_iso(fm["stage_after"]) > NOW
    assert "the first release since acme was classified company" in fm["why"]


def test_no_subject_backfill_before_classification(tmp_path):
    brain, _, ada = world(tmp_path)
    captured(ada, participants=["ada@lantern.example", "chloe@harbor.example"])
    pack = route.pending(ada, subject="harbor", now=NOW)
    assert pack["records"] == [] and "harbor" in pack["waits"]
    classify_in_brain(brain, "harbor", WALLED)
    pack = route.pending(ada, subject="harbor", now=NOW)
    assert [r["record"] for r in pack["records"]] == ["c-1"] and not pack.get("waits")


def test_retract_withdraws_an_unclassified_projection(tmp_path):
    _, _, ada = world(tmp_path)
    captured(ada)
    route.record(ada, decision("harbor", "harbor"), now=NOW)
    out = route.retract(ada, "c-1", by="Ada Okafor", now=NOW)
    (fm, _), = folder(ada, "held")
    assert out["withdrawn"] == [fm["id"]] and fm["reason"] == "withdrawn"


# ----- the sweep without the router --------------------------------------------------------------
def test_sweep_holds_an_unclassified_subject(tmp_path):
    _, _, ada = world(tmp_path, routing=False)
    feed = ada / "_meta" / "state" / "feed" / "mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"id": "m-0001", "source": "mail", "kind": "mail", "at": "2026-10-12T08:14:00Z",
                                "from": "Ben Liu", "to": ["Ada Okafor"], "subject": "Re: Harbor go-live",
                                "text": "Go-live moves to 3 November.",
                                "identifiers": {"thread": "t-1", "labels": ["Harbor"]},
                                "attachments": []}) + "\n", encoding="utf-8")
    report = sweep.run(ada, now=NOW)
    assert report.queued == 0 and report.signals == 0
    (fm, _), = folder(ada, "held")
    assert fm["reason"] == "unclassified" and fm["class"] == "at_once"
    assert signals(ada) == [] and folder(ada, "queue") == []


# ----- distill ----------------------------------------------------------------------------------------
def witness(root, rid, subject, text):
    at = NOW - dt.timedelta(days=1)
    fm = {"id": pages.new_id(), "type": "source", "title": text.strip()[:40], "status": "draft",
          "generated": pages.generated("sweep", at), "record": rid, "source": "mail", "kind": "mail",
          "at": pages.iso(at), "from": "Ben Liu", "audience": "owner", "subject": subject}
    return pages.write(root / f"knowledge/sources/{at:%Y}/{at:%m}/{rid}.md", fm, text)


def test_distill_writes_no_candidate_for_a_held_home(tmp_path):
    brain, _, ada = world(tmp_path)
    witness(ada, "rec-d", "harbor", "Decision: Harbor ships in two phases\n\nAgreed on the call.\n")
    report = distill.run(ada, now=NOW)
    assert not list((ada / "_inbound" / "candidates").glob("*.md"))
    assert report.unclassified == 2   # the subject candidate and the decision candidate
    runs = [json.loads(l) for l in (ada / "_meta" / "state" / "runs.jsonl").read_text().splitlines()]
    assert runs[-1]["notes"]["unclassified"] == 2


# ----- the ship: the backstop and the way back ------------------------------------------------------
def test_ship_holds_an_item_queued_before_the_gate(tmp_path):
    _, project, ada = world(tmp_path)
    queue_item(ada, "harbor", "harbor")
    report = release.ship(ada, now=NOW, offline_roots={"harbor": project})
    assert report.shipped == 0 and report.held == 1
    (fm, _), = folder(ada, "held")
    assert fm["reason"] == "unclassified"
    assert not list((project / "_inbound" / "candidates").glob("*.md"))


def test_ship_puts_it_back_after_classification(tmp_path):
    brain, project, ada = world(tmp_path)
    queue_item(ada, "harbor", "harbor")
    release.ship(ada, now=NOW, offline_roots={"harbor": project})
    classify_in_brain(brain, "harbor", WALLED)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": project})
    assert report.shipped == 1 and folder(ada, "held") == []
    assert len(list((project / "_inbound" / "candidates").glob("*.md"))) == 1


def test_a_first_release_at_once_waits_the_window_at_ship(tmp_path):
    brain, _, ada = world(tmp_path, acme=COMPANY)
    queue_item(ada, "acme", "lantern")
    report = release.ship(ada, now=NOW, offline_roots={"lantern": brain})
    assert report.shipped == 0
    (fm, _), = folder(ada, "queue")
    assert fm["class"] == "window" and fm["first_release"] == "acme"


def test_a_signal_of_a_held_subject_waits(tmp_path):
    brain, project, ada = world(tmp_path)
    path = ada / "_outbound" / "signals" / "s-1.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(sweep.signal_for("harbor", "harbor", NOW)), encoding="utf-8")
    release.ship(ada, now=NOW, offline_roots={"harbor": project})
    assert path.exists() and not list((project / "_inbound" / "signals").glob("*.yaml"))


def test_a_walled_decision_reroutes_held_items(tmp_path):
    brain, _, ada = world(tmp_path)
    captured(ada)
    route.record(ada, decision("lantern", "acme"), now=NOW)                    # a router item
    queue_item(ada, "acme", "lantern")                                         # an identifier item
    release.ship(ada, now=NOW, offline_roots={"lantern": brain})
    assert len(folder(ada, "held")) == 2
    classify_in_brain(brain, "acme", dict(WALLED, home="tidewater"))
    space = release.spaceyaml.load(ada)
    out = classify.requeue(ada, space, registry.Registry(ada, space), NOW)
    assert out["rerouted"] == 2
    held = {fm.get("routed_by", "identifier"): fm for fm, _ in folder(ada, "held")}
    assert held["router"]["reason"] == "withdrawn"
    assert [r["record"] for r in route.pending(ada, now=NOW)["records"]] == ["c-1"]   # offered again
    (item, _), = folder(ada, "queue")
    assert item["destination"] == "tidewater" and item["rerouted_from"] == "lantern"
    assert item["class"] == "window"


# ----- the final review's fixes: fail closed, follow the class, every subject a record is about ------
def test_an_unreadable_brain_lets_nothing_go(tmp_path):
    brain, project, ada = world(tmp_path)
    queue_item(ada, "harbor", "harbor")
    release.ship(ada, now=NOW, offline_roots={"harbor": project})          # held: harbor is not classified
    classify_in_brain(brain, "harbor", WALLED)
    queue_item(ada, "harbor", "harbor")
    empty = tmp_path / "empty"
    empty.mkdir()
    mount_brain(ada, empty)                                                  # the brain cannot be read
    report = release.ship(ada, now=NOW, offline_roots={"harbor": project})
    assert report.shipped == 0 and report.requeued == 0
    assert len(folder(ada, "held")) == 2 and folder(ada, "queue") == []
    assert not list((project / "_inbound" / "candidates").glob("*.md"))


def test_a_registry_that_cannot_be_read_lets_nothing_go(tmp_path):
    brain, project, ada = world(tmp_path)
    classify_in_brain(brain, "harbor", WALLED)
    queue_item(ada, "harbor", "harbor")
    signal = ada / "_outbound" / "signals" / "s-1.yaml"
    signal.parent.mkdir(parents=True, exist_ok=True)
    signal.write_text(yaml.safe_dump(sweep.signal_for("harbor", "harbor", NOW)), encoding="utf-8")
    (ada / "_meta" / "subjects.yaml").write_text("subjects: [\n", encoding="utf-8")
    report = release.ship(ada, now=NOW, offline_roots={"harbor": project})
    assert report.shipped == 0 and len(folder(ada, "queue")) == 1 and signal.exists()
    assert not list((project / "_inbound" / "candidates").glob("*.md"))
    assert not list((project / "_inbound" / "signals").glob("*.yaml"))


def test_a_failed_sample_goes_back_to_the_releaser_whatever_the_class(tmp_path):
    _, project, ada = world(tmp_path)                                         # harbor is not classified
    path = project / "_outbound" / "signals" / "p-0000cc02.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"id": "p-0000cc02", "destination": "ada", "at": pages.iso(NOW),
                                    "kind": "sample_failed", "item": "p-00000001", "subject": "harbor"}),
                    encoding="utf-8")
    release.ship(project, now=NOW, offline_roots={"ada": ada})
    assert not path.exists()
    assert [p.name for p in (ada / "_inbound" / "signals").glob("*.yaml")] == ["p-0000cc02.yaml"]


def whole(space, subject):
    out = decision(space, subject)
    out["routes"][0].update(content="whole", parts=[])
    return out


def test_a_whole_record_also_about_an_unclassified_client_is_held(tmp_path):
    brain, _, ada = world(tmp_path, acme=COMPANY)
    captured(ada)                                   # the call carries harbor's mail domain: it is about harbor too
    assert route.record(ada, decision("lantern", "acme", rid="c-1"), now=NOW)["queued"]   # parts: the router's call
    captured(ada, rid="c-2", participants=["ada@lantern.example"], identifiers={"labels": ["Harbor"]})
    out = route.record(ada, whole("lantern", "acme") | {"record": "c-2"}, now=NOW)
    assert out["queued"] == [] and len(out["held"]) == 1
    (fm, _), = folder(ada, "held")
    assert fm["reason"] == "unclassified" and fm["also_about"] == ["harbor"] and "harbor" in fm["why"]
    classify_in_brain(brain, "harbor", dict(COMPANY))
    release.ship(ada, now=NOW, offline_roots={"lantern": brain})
    item, = [fm for fm, _ in folder(ada, "queue") if fm.get("also_about")]
    assert item["first_release"] == "acme" and item["also_about"] == ["harbor"]


def test_a_part_of_a_walled_client_goes_to_the_walled_home(tmp_path):
    brain, project, ada = world(tmp_path)
    classify_in_brain(brain, "acme", WALLED)                                  # acme is walled: home harbor
    path = brain / "_meta" / "subjects.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["subjects"].append({"id": "acme-pilot", "kind": "projects", "title": "Acme pilot", "part_of": "acme",
                             "home": "lantern"})   # as onboarding registers a project without a space
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    captured(ada, participants=["ada@lantern.example", "chloe@acme.example"])
    out = route.record(ada, decision("harbor", "acme-pilot"), now=NOW)
    assert len(out["queued"]) == 1
    report = release.ship(ada, now=NOW, offline_roots={"harbor": project})
    assert report.shipped == 1 and report.rerouted == 0


def test_requeue_clears_the_listing_and_tags_a_window_first_release(tmp_path):
    brain, _, ada = world(tmp_path)
    queue_item(ada, "acme", "lantern", cls="window", listed=True)
    release.ship(ada, now=NOW, offline_roots={"lantern": brain})              # held: acme is not classified
    classify_in_brain(brain, "acme", COMPANY)
    space = release.spaceyaml.load(ada)
    classify.requeue(ada, space, registry.Registry(ada, space), NOW)
    (fm, _), = folder(ada, "queue")
    assert fm.get("listed") is not True and fm["first_release"] == "acme"


# ----- Task 6: Needs you, the morning view and sign ---------------------------------------------------
import morning_view  # noqa: E402
import needs_you  # noqa: E402
import sign  # noqa: E402
from conftest import write_page  # noqa: E402


def held_page(root, pid, reason, subject="harbor"):
    return write_page(root, f"_outbound/held/{pid}.md", id=pid, type="source", subject=subject,
                      destination="harbor", reason=reason, why=f"{subject} waits", evidence=[f"m-{pid}"])


def first_release_items(root, count=2):
    stage = pages.iso(NOW + dt.timedelta(hours=24))
    return [pages.read(queue_item(root, "acme", "lantern", cls="window", stage_after=stage,
                                  first_release="acme"))[0]["id"] for _ in range(count)]


def test_doubt_line_counts_unclassified_but_does_not_offer_them(tmp_path):
    _, _, ada = world(tmp_path)
    held_page(ada, "p-00000d01", "doubt")
    held_page(ada, "p-00000c01", "unclassified")
    held_page(ada, "p-00000c02", "unclassified")
    line = next(i for i in needs_you.collect(ada, NOW, online=False) if i.id == "doubt")
    assert line.text == "held in doubt: 1, 2 wait for a classification (Harbor)"
    assert needs_you.doubt_count(ada) == 3
    assert [i.id for i in needs_you.doubt_items(ada)] == ["p-00000d01"]
    report = sign.apply(ada, {"p-00000c01": "yes"}, by="Ada Okafor", now=NOW)
    assert report.skipped == ["p-00000c01"]
    assert pages.read(ada / "_outbound" / "held" / "p-00000c01.md")[0]["reason"] == "unclassified"


def test_first_release_is_one_line(tmp_path):
    _, _, ada = world(tmp_path, acme=COMPANY)
    ids = first_release_items(ada)
    items = needs_you.collect(ada, NOW, online=False)
    line, = [i for i in items if i.id == "first-acme"]
    assert line.kind == "leaving" and line.payload == "_outbound/queue/"
    assert line.text == ("Acme Foods: the first 2 items since it was classified company leave for lantern "
                         "at 2026-10-13T09:00:00Z")
    assert not [i for i in items if i.id in ids]


def test_the_morning_view_lists_every_first_release_item(tmp_path):
    _, _, ada = world(tmp_path, acme=COMPANY)
    ids = first_release_items(ada)
    space = release.spaceyaml.load(ada)
    stamped = morning_view._mark_listed(ada, space, needs_you.collect(ada, NOW, online=False), NOW)
    assert sorted(stamped) == sorted(ids)
    assert all(fm["listed"] is True for fm, _ in folder(ada, "queue"))


def test_no_on_the_first_release_line_pulls_them_all_back(tmp_path):
    _, _, ada = world(tmp_path, acme=COMPANY)
    ids = first_release_items(ada)
    report = sign.apply(ada, {"first-acme": "no"}, by="Ada Okafor", now=NOW)
    assert sorted(report.discarded) == sorted(ids) and folder(ada, "queue") == []
    assert [fm["reason"] for fm, _ in folder(ada, "held")] == ["pulled", "pulled"]


def test_yes_on_the_first_release_line_lets_them_go(tmp_path):
    brain, _, ada = world(tmp_path, acme=COMPANY)
    first_release_items(ada)
    sign.apply(ada, {"first-acme": "yes"}, by="Ada Okafor", now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"lantern": brain})
    assert report.shipped == 2 and folder(ada, "queue") == []


# ----- Task 7: onboarding and mint ---------------------------------------------------------------------
from pathlib import Path  # noqa: E402

import mint  # noqa: E402
import onboard  # noqa: E402
import spaceyaml  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[3]


def never(*args, **kwargs):
    raise AssertionError(f"run_cmd was called offline: {args}")


def small_company(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    root = tmp_path / "co"
    onboard.copy_template(TEMPLATE, root)
    answers = {"company": "Lantern Works", "owner": "Ada Okafor", "owner_slug": "ada",
               "owner_email": "ada@lantern.example",
               "people": [{"name": "Ben Liu", "slug": "ben", "email": "ben@lantern.example"}],
               "projects": [{"id": "harbor", "space": True, "crew": ["ada", "ben"]}, {"id": "dock"}],
               "classification_default": "closed"}
    report = onboard.run(root, answers, offline=True, home=tmp_path, with_projects=True,
                         user_instructions=str(tmp_path / "CLAUDE.md"))
    assert not report.failed(), [s.row() for s in report.failed()]
    return root


def test_onboarding_asks_the_classification_default_once():
    asked = [q for q in onboard.questions() if q["key"] == "classification_default"]
    assert len(asked) == 1
    q = asked[0]
    assert q["applies_to"] == ["company"] and q["kind"] == "choice"
    assert q["choices"] == ["open", "closed"] and q["default"] == "open"
    assert onboard.interview({}, interactive=False, case="company")["classification_default"] == "open"
    assert "classification_default" not in onboard.interview({}, interactive=False, case="person")


def test_a_project_with_its_own_space_is_walled_at_onboarding(tmp_path, monkeypatch):
    root = small_company(tmp_path, monkeypatch)
    reg = registry.Registry(root)
    c = reg.get("harbor").extra["classification"]
    assert c["class"] == "walled" and c["state"] == "settled" and c["home"] == "harbor"
    assert c["crew"] == ["ada", "ben"] and c["decided_by"] == "Ada Okafor"
    assert c["reason"] == "given its own space at onboarding"
    assert c["checked_with"] == [] and c["not_yet_checked"] == []
    assert "classification" not in reg.get("dock").extra   # its home is the brain: its owner classifies it
    assert spaceyaml.load(root).data["classification_default"] == "closed"
    assert reg.check() == []


def test_people_with_a_workspace_carry_it(tmp_path, monkeypatch):
    root = small_company(tmp_path, monkeypatch)
    reg = registry.Registry(root)
    assert reg.get("ada").extra.get("workspace") == "ada"
    assert "workspace" not in reg.get("ben").extra   # no workspace was made for Ben yet


def test_a_person_onboarding_later_announces_their_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", owner="Ada Okafor",
                       subjects=[{"id": "ben", "kind": "people", "title": "Ben Liu"}])
    ws = tmp_path / "ben-workspace"
    onboard.copy_template(TEMPLATE, ws)
    report = onboard.run(ws, {"name": "Ben Liu", "slug": "ben", "email": "ben@lantern.example"}, offline=True,
                         brain=str(brain), home=tmp_path, user_instructions=str(tmp_path / "CLAUDE.md"))
    assert not report.failed(), [s.row() for s in report.failed()]
    sent = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in signals(ws)]
    assert [(s["note"], s["subject"], s["from"]) for s in sent] == [("workspace", "ben", "ben")]


def test_mint_needs_no_home_item_after_a_walled_classification(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "lighthouse", "kind": "projects", "title": "Lighthouse", "home": "lighthouse",
         "classification": dict(WALLED, home="lighthouse", crew=["ada"])}])
    ada = make_space(tmp_path / "ada", name="ada")
    mount_brain(ada, brain)
    seed = ada / "work" / "projects" / "lighthouse" / "kickoff"
    seed.mkdir(parents=True)
    (seed / "brief.md").write_text("# Brief\n\nA refit.\n", encoding="utf-8")
    mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse", crew=["Ada Okafor"], online=False)
    ids = [i["id"] for i in yaml.safe_load((ada / "_inbound" / "needs-you" / "mint.yaml").read_text())["items"]]
    assert "mint-lighthouse-team" in ids and "mint-lighthouse-home" not in ids


def test_a_client_waiting_only_for_its_classification_is_named_on_needs_you(tmp_path):
    _, _, ada = world(tmp_path)
    held_page(ada, "p-00000c03", "unclassified")
    line = next(i for i in needs_you.collect(ada, NOW, online=False) if i.id == "doubt")
    assert line.text == "1 wait for a classification (Harbor)"
