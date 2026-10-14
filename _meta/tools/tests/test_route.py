"""route.py: an agent's routing decision, gated, logged and turned into projections."""
import datetime as dt
import json

import pytest
import yaml

import pages
import release
import route
import sweep
from conftest import NOW, make_space, mount_brain

SUBJECTS = [
    {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
     "match": {"domain": "harbor.example"}},
    {"id": "beacon", "kind": "projects", "title": "Beacon", "home": "beacon"},
    {"id": "ben", "kind": "people", "title": "Ben Liu", "email": "ben@lantern.example"},
    {"id": "acme", "kind": "clients", "title": "Acme Foods"},
]
CALL = "\n".join([
    "Summary: the pilot scope and the board dinner.",
    "[00:01:00] Chloe: The first list should hold forty leads.",
    "[00:02:00] Ada: We send it by Friday 2026-10-23.",
    "[00:03:00] Ben: For Beacon, the venue moves to the harbour hall.",
])


def space_set(tmp_path, **ada):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=SUBJECTS)
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor",
                        audience=["Ada Okafor", "Ben Liu"])
    beacon = make_space(tmp_path / "beacon", profile="project", name="beacon", owner="Ada Okafor",
                        audience=["Ada Okafor"])
    ada.setdefault("routing", {})
    ada.setdefault("sources", [{"name": "calls", "adapter": "files", "audience": "owner"}])
    root = make_space(tmp_path / "ada", name="ada", owner_email="ada@lantern.example", **ada)
    mount_brain(root, brain)
    mount_brain(harbor, brain)
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] += [{"name": "harbor", "path": str(harbor)}, {"name": "beacon", "path": str(beacon)}]
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root, harbor, beacon


def captured(root, rid="c-1", text=CALL, participants=None, title="Kickoff", kind="call",
             sender="ada@lantern.example"):
    rec = {"id": rid, "source": "calls", "kind": kind, "at": "2026-10-12T08:00:00Z",
           "from": sender, "to": [], "subject": title, "text": text,
           "participants": participants if participants is not None else
           ["ada@lantern.example", "ben@lantern.example", "chloe@harbor.example"],
           "identifiers": {"thread": rid}}
    feed = root / "_meta" / "state" / "feed" / "calls.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    with feed.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    (root / "_meta" / "state" / "sweep-watermark.yaml").unlink(missing_ok=True)
    sweep.run(root, now=NOW)
    return rec


def decision(**over):
    base = {"record": "c-1", "decision": "route", "confidence": "high", "why": "the client's kickoff",
            "routes": [{"space": "harbor", "subject": "harbor", "tier": "at_once", "why": "pilot work",
                        "content": "parts",
                        "parts": [{"text": "[00:01:00] Chloe: The first list should hold forty leads."},
                                  {"text": "[00:02:00] Ada: We send it by Friday 2026-10-23."}],
                        "informs": [{"kind": "open_item", "text": "Send the first list", "owner": "Ada Okafor",
                                     "due": "2026-10-23", "direction": "we owe"}]}]}
    base.update(over)
    return base


def route_with(**over):
    return [dict(decision()["routes"][0], **over)]


def queued(root):
    return [pages.read(p) for p in sorted((root / "_outbound" / "queue").glob("*.md"))]


# ----- pending and record -------------------------------------------------------------------
def test_pending_lists_unrouted_pages_with_their_context(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    pack = route.pending(root, now=NOW)
    rec, = pack["records"]
    assert rec["record"] == "c-1" and "forty leads" in rec["text"] and rec["suggested"] == ["harbor"]
    assert {s["name"] for s in pack["spaces"]} >= {"harbor", "beacon"}
    assert any(s["id"] == "harbor" and s["home"] == "harbor" for s in pack["subjects"])


def test_pending_for_a_subject_reaches_back_past_the_horizon(tmp_path):
    root, _, _ = space_set(tmp_path, routing={"since": "2026-10-13"})
    captured(root)
    assert route.pending(root, now=NOW)["records"] == []
    assert [r["record"] for r in route.pending(root, subject="harbor", now=NOW)["records"]] == ["c-1"]
    assert route.pending(root, subject="beacon", now=NOW)["records"] != [], "named in the text"


def test_record_queues_one_projection_per_route_with_verbatim_parts(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    out = route.record(root, decision(), now=NOW)
    (fm, body), = queued(root)
    assert out["queued"] == [fm["id"]] and fm["class"] == "at_once" and fm["destination"] == "harbor"
    assert "forty leads" in body and "Beacon" not in body and "board dinner" not in body
    assert fm["informs"][0]["due"] == "2026-10-23" and fm["why"] == "pilot work"
    page = route.find_page(root, "c-1")
    stamp = pages.read(page)[0]["routing"]
    assert stamp["decision"] == "route" and stamp["routes"] == ["harbor/harbor"]
    log = [json.loads(line) for line in (root / route.LOG).read_text(encoding="utf-8").splitlines()]
    assert log[-1]["record"] == "c-1" and log[-1]["routes"][0]["outcome"] == "queued"
    assert route.pending(root, now=NOW)["records"] == []


def test_a_part_may_differ_only_in_whitespace(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    spaced = decision(routes=route_with(parts=[{"text": "[00:01:00]  Chloe:\nThe first list   should hold forty leads."}]))
    assert route.record(root, spaced, now=NOW)["queued"]
    captured(root, rid="c-2")
    paraphrase = decision(record="c-2", routes=route_with(
        parts=[{"text": "[00:01:00] Chloe: The first list should hold 40 leads."}]))
    with pytest.raises(route.RouteError, match="verbatim"):
        route.record(root, paraphrase, now=NOW)


def test_whole_needs_everyone_inside_the_audience(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    whole = decision(routes=route_with(content="whole", parts=[]))
    assert route.record(root, whole, now=NOW)["queued"]
    (fm, body), = queued(root)
    assert "board dinner" in body, "whole is the full record"
    captured(root, rid="c-3", participants=["ada@lantern.example", "eve@elsewhere.example"])
    with pytest.raises(route.RouteError, match="outside"):
        route.record(root, dict(whole, record="c-3"), now=NOW)


def test_gates(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root, text=CALL + "\n[00:04:00] Ada: The settlement terms are separate.")
    route.record(root, decision(), now=NOW)
    (fm, _), = queued(root)
    assert fm["class"] == "window" and "never-leaves" in fm["why"]
    captured(root, rid="c-4", text="[00:01:00] Chloe: Our day rate is 950.\n[00:02:00] Ada: Fine.")
    out = route.record(root, decision(record="c-4", routes=route_with(
        parts=[{"text": "[00:01:00] Chloe: Our day rate is 950."}])), now=NOW)
    assert out["held"][0][1] == "left_out"
    captured(root, rid="c-5", text="[00:01:00] Chloe: Please sign the contract.\n")
    route.record(root, decision(record="c-5", routes=route_with(
        parts=[{"text": "[00:01:00] Chloe: Please sign the contract."}])), now=NOW)
    fm = [f for f, _ in queued(root) if f["evidence"] == ["c-5"]][0]
    assert fm["class"] == "window" and fm["high_stakes"] is True
    captured(root, rid="c-6")
    route.record(root, decision(record="c-6", confidence="medium"), now=NOW)
    assert [f for f, _ in queued(root) if f["evidence"] == ["c-6"]][0]["class"] == "window"
    captured(root, rid="c-9", text="[00:01:00] Ada: The settlement is agreed.\n")
    out = route.record(root, decision(record="c-9", routes=route_with(
        parts=[{"text": "[00:01:00] Ada: The settlement is agreed."}])), now=NOW)
    assert out["held"][0][1] == "never_leaves"


def test_low_confidence_becomes_a_question_and_nothing_leaves(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    out = route.record(root, decision(confidence="low"), now=NOW)
    assert queued(root) == [] and out["question"]
    fm, _ = pages.read(root / out["question"])
    assert fm["type"] == "review" and fm["record"] == "c-1" and "harbor" in fm["recommendation"]
    assert route.pending(root, now=NOW)["records"] == [], "a question waits; it is not pending again"


def test_review_mode_holds_every_projection_for_the_window(tmp_path):
    root, _, _ = space_set(tmp_path, routing={"review_until": "2026-10-28"})
    captured(root)
    route.record(root, decision(), now=NOW)
    (fm, _), = queued(root)
    assert fm["class"] == "window" and "review" in fm["why"]
    assert pages.parse_iso(fm["stage_after"]) == NOW + dt.timedelta(hours=24)


def test_private_and_own_space_routes_release_nothing(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(decision="private", routes=[]), now=NOW)
    assert queued(root) == []
    assert pages.read(route.find_page(root, "c-1"))[0]["routing"]["decision"] == "private"
    (root / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [
        {"id": "pipeline", "kind": "topics", "title": "Pipeline"}]}), encoding="utf-8")
    captured(root, rid="c-7")
    out = route.record(root, decision(record="c-7", routes=route_with(space="ada", subject="pipeline")), now=NOW)
    assert queued(root) == [] and out["attached"] == ["ada/pipeline"]


def test_a_routed_record_is_not_routed_twice_unless_refreshed(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(), now=NOW)
    with pytest.raises(route.RouteError, match="already routed"):
        route.record(root, decision(), now=NOW)
    assert len(queued(root)) == 1


def test_a_refreshed_record_replaces_its_queued_projection(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(routes=route_with(tier="window")), now=NOW)
    page = route.find_page(root, "c-1")
    fm, body = pages.read(page)
    pages.write(page, dict({k: v for k, v in fm.items() if k != "routing"}, refreshed_at=pages.iso(NOW)), body)
    route.record(root, decision(routes=route_with(tier="window")), now=NOW)
    assert len(queued(root)) == 1
    held = [pages.read(p)[0] for p in (root / "_outbound" / "held").glob("*.md")]
    assert [h["reason"] for h in held] == ["superseded"]


def test_refused_routes(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    with pytest.raises(route.RouteError, match="home"):
        route.record(root, decision(routes=route_with(space="beacon")), now=NOW)
    with pytest.raises(route.RouteError, match="no witness page"):
        route.record(root, decision(record="nope"), now=NOW)
    with pytest.raises(route.RouteError, match="confidence"):
        route.record(root, decision(confidence="sure"), now=NOW)
    assert queued(root) == [] and not (root / route.LOG).exists()


def test_the_cli_records_a_file_and_reports_refusals(tmp_path, capsys):
    root, _, _ = space_set(tmp_path)
    captured(root)
    good = tmp_path / "good.yaml"
    good.write_text(yaml.safe_dump(decision()), encoding="utf-8")
    assert route.main([str(root), "pending"]) == 0
    assert (root / route.PACK).exists() and "1 record" in capsys.readouterr().out
    assert route.main([str(root), "record", str(good)]) == 0
    capsys.readouterr()
    captured(root, rid="c-2")
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(decision(record="c-2", confidence="sure")), encoding="utf-8")
    assert route.main([str(root), "record", str(bad)]) == 1
    assert "refused" in capsys.readouterr().out and bad.exists(), "a refused file stays to be fixed"


# ----- answers and retraction ---------------------------------------------------------------
def test_an_owner_yes_applies_the_proposed_route_as_approved(tmp_path):
    root, _, _ = space_set(tmp_path, routing={"review_until": "2026-10-28"})
    captured(root)
    route.record(root, decision(confidence="low"), now=NOW)
    out = route.answer(root, "c-1", yes=True, by="Ada Okafor", now=NOW)
    (fm, _), = queued(root)
    assert out["queued"] == [fm["id"]] and fm["class"] == "at_once" and fm["approved_by"] == "Ada Okafor"
    assert not list((root / "_inbound" / "questions").glob("route-*.md"))


def test_an_owner_no_keeps_it_private(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(confidence="low"), now=NOW)
    assert route.answer(root, "c-1", yes=False, by="Ada Okafor", now=NOW) == {"private": True}
    assert queued(root) == []
    assert pages.read(route.find_page(root, "c-1"))[0]["routing"]["decision"] == "private"


def test_retract_withdraws_queued_and_retracts_shipped(tmp_path):
    root, harbor, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(), now=NOW)
    assert release.ship(root, now=NOW, offline_roots={"harbor": harbor}).shipped == 1
    captured(root, rid="c-8")
    route.record(root, decision(record="c-8", routes=route_with(tier="window")), now=NOW)
    out = route.retract(root, "c-8", by="Ada Okafor", now=NOW)
    assert len(out["withdrawn"]) == 1 and queued(root) == []
    out = route.retract(root, "c-1", by="Ada Okafor", now=NOW)
    assert len(out["retractions"]) == 1
    assert pages.read(route.find_page(root, "c-1"))[0]["routing"]["retracted_by"] == "Ada Okafor"


# ----- end to end: the projection lands and updates the home's status page ------------------
def test_the_projection_lands_and_the_home_status_page_shows_its_open_item(tmp_path):
    import status
    root, harbor, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(), now=NOW)
    assert release.ship(root, now=NOW, offline_roots={"harbor": harbor}).shipped == 1
    status.run(harbor, now=NOW)
    page = (harbor / "live" / "subjects" / "harbor.md").read_text(encoding="utf-8")
    owe = page.split("### We owe")[1].split("##")[0]
    assert "Send the first list (owner Ada Okafor, due 2026-10-23)" in owe
    assert "Kickoff" in page.split("## Timeline")[1]
    assert "2026-10-23" in page.split("## Coming up")[1]


# ----- the final review's findings ---------------------------------------------------------
def test_never_leaves_and_left_out_cover_what_the_projection_says_about_itself(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    held = route.record(root, decision(routes=route_with(informs=[
        {"kind": "open_item", "text": "Agree the settlement with Ben", "direction": "we owe"}])), now=NOW)
    assert held["held"][0][1] == "never_leaves"
    captured(root, rid="c-2")
    held = route.record(root, decision(record="c-2", routes=route_with(why="his salary came up")), now=NOW)
    assert held["held"][0][1] == "never_leaves"
    captured(root, rid="c-3")
    held = route.record(root, decision(record="c-3", routes=route_with(informs=[
        {"kind": "decision", "text": "The day rate stays as it is"}])), now=NOW)
    assert held["held"][0][1] == "left_out"
    assert queued(root) == []


def test_the_routers_reasons_stay_home(tmp_path):
    root, harbor, _ = space_set(tmp_path)
    captured(root, text=CALL + "\n[00:04:00] Ada: The settlement terms are separate.")
    route.record(root, decision(), now=NOW)
    (fm, _), = queued(root)
    assert "never-leaves" in fm["why"], "the owner sees why it waits"
    for path in (root / "_outbound" / "queue").glob("*.md"):
        qfm, qbody = pages.read(path)
        pages.write(path, dict(qfm, listed=True), qbody)
    assert release.ship(root, now=NOW + dt.timedelta(hours=25), offline_roots={"harbor": harbor}).shipped == 1
    landed, = list((harbor / "_inbound" / "candidates").glob("*.md"))
    lfm = pages.read(landed)[0]
    assert not {"why", "confidence", "routed_by"} & set(lfm), "the reasons stay in the releasing space"


def test_another_clients_name_holds_and_a_named_person_waits(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root, text="[00:01:00] Chloe: Acme Foods pays us double.\n[00:02:00] Ada: Noted.\n")
    out = route.record(root, decision(routes=route_with(
        parts=[{"text": "[00:01:00] Chloe: Acme Foods pays us double."}])), now=NOW)
    assert out["held"][0][1] == "left_out"
    captured(root, rid="m-1", kind="mail", participants=[], sender="chloe@harbor.example",
             text="Ben Liu will lead the rollout from Monday.\n")
    route.record(root, decision(record="m-1", routes=route_with(
        parts=[{"text": "Ben Liu will lead the rollout from Monday."}])), now=NOW)
    (fm, _), = queued(root)
    assert fm["class"] == "window" and fm["high_stakes"] is True


def test_a_projection_too_large_for_one_release_is_refused(tmp_path, monkeypatch):
    root, _, _ = space_set(tmp_path)
    captured(root)
    monkeypatch.setattr(sweep, "PART_BYTES", 50)
    with pytest.raises(route.RouteError, match="too large"):
        route.record(root, decision(routes=route_with(content="whole", parts=[])), now=NOW)


def test_an_owner_answer_through_sign_applies_the_route(tmp_path):
    import sign
    root, _, _ = space_set(tmp_path)
    captured(root)
    captured(root, rid="c-2")
    q1 = route.record(root, decision(confidence="low"), now=NOW)["question"]
    q2 = route.record(root, decision(record="c-2", confidence="low"), now=NOW)["question"]
    ids = {pages.read(root / q)[0]["id"]: q for q in (q1, q2)}
    first = [k for k, v in ids.items() if v == q1][0]
    second = [k for k, v in ids.items() if v == q2][0]
    sign.apply(root, {first: "yes", second: "no"}, by="Ada Okafor", now=NOW)
    (fm, _), = queued(root)
    assert fm["evidence"] == ["c-1"] and fm["approved_by"] == "Ada Okafor"
    assert pages.read(route.find_page(root, "c-2"))[0]["routing"]["decision"] == "private"
    assert route.pending(root, now=NOW)["records"] == []


def test_a_route_to_this_space_keeps_what_it_informs_here(tmp_path):
    import status
    root, _, _ = space_set(tmp_path)
    (root / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [
        {"id": "pipeline", "kind": "topics", "title": "Pipeline"}]}), encoding="utf-8")
    captured(root)
    route.record(root, decision(routes=route_with(space="ada", subject="pipeline")), now=NOW)
    status.run(root, now=NOW)
    page = (root / "live" / "subjects" / "pipeline.md").read_text(encoding="utf-8")
    assert "Send the first list (owner Ada Okafor, due 2026-10-23)" in page.split("### We owe")[1]


def test_after_a_retraction_a_corrected_decision_can_be_recorded(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(routes=route_with(tier="window")), now=NOW)
    route.retract(root, "c-1", by="Ada Okafor", now=NOW)
    assert route.pending(root, now=NOW)["records"] == [], "a retraction is not routed again by itself"
    out = route.record(root, decision(decision="private", routes=[]), now=NOW)
    assert out["queued"] == [] and pages.read(route.find_page(root, "c-1"))[0]["routing"]["decision"] == "private"


def test_a_low_confidence_decision_is_a_question_even_when_its_route_would_be_refused(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    out = route.record(root, decision(confidence="low", routes=route_with(space="beacon")), now=NOW)
    assert out["question"] and queued(root) == []


def test_the_cli_files_recorded_decisions_and_skips_what_is_already_routed(tmp_path, capsys):
    root, _, _ = space_set(tmp_path)
    captured(root)
    folder = root / "_meta" / "state" / "private" / "decisions"
    folder.mkdir(parents=True)
    (folder / "c-1.yaml").write_text(yaml.safe_dump(decision()), encoding="utf-8")
    assert route.main([str(root), "record", str(folder / "c-1.yaml")]) == 0
    assert not (folder / "c-1.yaml").exists() and (folder / "done" / "c-1.yaml").exists()
    (folder / "again.yaml").write_text(yaml.safe_dump(decision()), encoding="utf-8")
    assert route.main([str(root), "record", str(folder / "again.yaml")]) == 0
    assert "skipped" in capsys.readouterr().out


def test_pending_takes_a_limit(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    captured(root, rid="c-2")
    assert len(route.pending(root, now=NOW, limit=1)["records"]) == 1


def test_a_refresh_replaces_only_the_routers_own_projections(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    page = route.find_page(root, "c-1")
    other = {"id": "p-0000ee01", "type": "source", "title": "Kickoff", "status": "draft",
             "generated": pages.generated("sweep", NOW), "class": "window", "destination": "harbor",
             "subject": "harbor", "stage_after": pages.iso(NOW), "listed": False, "evidence": ["c-1"],
             "from_space": "ada", "kind": "call"}
    pages.write(root / "_outbound" / "queue" / "p-0000ee01.md", other, "From the identifier path.\n")
    fm, body = pages.read(page)
    pages.write(page, dict(fm, refreshed_at=pages.iso(NOW)), body)
    route.record(root, decision(routes=route_with(tier="window")), now=NOW)
    assert "p-0000ee01" in [f["id"] for f, _ in queued(root)], "an item the owner said may ship stays"


def test_record_refuses_where_routing_is_off(tmp_path):
    root, _, _ = space_set(tmp_path, routing=None)
    captured(root)
    with pytest.raises(route.RouteError, match="routing"):
        route.record(root, decision(), now=NOW)


def test_retract_queued_only_leaves_what_already_shipped(tmp_path):
    """Changing a decision that is still waiting must never retract what the owner already let go."""
    root, harbor, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(), now=NOW)
    assert release.ship(root, now=NOW, offline_roots={"harbor": harbor}).shipped == 1
    page = route.find_page(root, "c-1")
    fm, body = pages.read(page)
    pages.write(page, {k: v for k, v in fm.items() if k != "routing"}, body)   # a second, newer decision
    route.record(root, decision(routes=route_with(tier="window")), now=NOW)
    out = route.retract(root, "c-1", by="Ada Okafor", now=NOW, queued_only=True)
    assert len(out["withdrawn"]) == 1 and out["retractions"] == [] and queued(root) == []
    assert route.main([str(root), "retract", "c-1", "--by", "Ada Okafor", "--queued-only"]) == 0
