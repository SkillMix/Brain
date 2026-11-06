"""The classification gate (docs/design/2026-10-16-classification-gate-design.md):
registry fields, the gate, the registry notes, the verbs and the one-off list."""
import registry
from conftest import make_space

SETTLED_WALLED = {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ada"],
                  "reason": "given its own space", "decided_by": "Ada Okafor",
                  "at": "2026-10-01T09:00:00Z"}


def _errors(tmp_path, subjects):
    root = make_space(tmp_path / "s", profile="brain", subjects=subjects)
    return registry.Registry(root).check()


# ----- Task 1: the registry fields and their check --------------------------------------------
def test_registry_checks_a_classification(tmp_path):
    assert _errors(tmp_path, [{"id": "harbor", "kind": "projects",
                               "classification": SETTLED_WALLED}]) == []
    bad_class = _errors(tmp_path, [{"id": "harbor", "kind": "projects",
                                    "classification": dict(SETTLED_WALLED, **{"class": "shared"})}])
    assert any("classification" in e and "class" in e for e in bad_class)
    bad_state = _errors(tmp_path, [{"id": "harbor", "kind": "projects",
                                    "classification": dict(SETTLED_WALLED, state="done")}])
    assert any("state" in e for e in bad_state)
    no_home = dict(SETTLED_WALLED)
    no_home.pop("home")
    assert any("home" in e for e in _errors(tmp_path, [{"id": "harbor", "kind": "projects",
                                                        "classification": no_home}]))
    no_crew = dict(SETTLED_WALLED, crew=[])
    assert any("crew" in e for e in _errors(tmp_path, [{"id": "harbor", "kind": "projects",
                                                        "classification": no_crew}]))


def test_registry_refuses_obligation_wording(tmp_path):
    good = {"kind": "confidentiality", "date": "2026-04-14", "seen_in": "ada",
            "at": "2026-10-01T09:00:00Z", "evidence": ["m-1"]}
    assert _errors(tmp_path, [{"id": "harbor", "kind": "clients", "obligations": [good]}]) == []
    worded = _errors(tmp_path, [{"id": "harbor", "kind": "clients",
                                 "obligations": [dict(good, fact="covers the pricing annex")]}])
    assert any("obligation" in e and "fact" in e for e in worded)
    assert any("date" in e for e in _errors(tmp_path, [{"id": "harbor", "kind": "clients",
                                                        "obligations": [dict(good, date="April")]}]))
    assert any("kind" in e for e in _errors(tmp_path, [{"id": "harbor", "kind": "clients",
                                                        "obligations": [dict(good, kind="nda")]}]))


def test_registry_checks_a_workspace_slug(tmp_path):
    assert _errors(tmp_path, [{"id": "ada", "kind": "people", "title": "Ada Okafor",
                               "workspace": "ada"}]) == []
    errs = _errors(tmp_path, [{"id": "ada", "kind": "people", "title": "Ada Okafor",
                               "workspace": "Not A Slug"}])
    assert any("workspace" in e for e in errs)


# ----- Task 2: the gate -------------------------------------------------------------------------
import json  # noqa: E402

import classify  # noqa: E402
import spaceyaml  # noqa: E402
from conftest import mount_brain  # noqa: E402

PEOPLE = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "workspace": "ada"},
          {"id": "ben", "kind": "people", "title": "Ben Liu", "workspace": "ben"},
          {"id": "chloe", "kind": "people", "title": "Chloe Martin"}]


def company(**extra):
    return dict({"class": "company", "state": "settled", "home": "lantern", "reason": "ordinary work",
                 "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}, **extra)


def brain_and(tmp_path, subjects, *names, owners=None):
    """A brain named lantern (owner Ada) with `subjects` and the people, and one workspace per name
    mounting it. Returns (brain root, {name: workspace root})."""
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", owner="Ada Okafor",
                       subjects=PEOPLE + list(subjects))
    owners = owners or {"ada": "Ada Okafor", "ben": "Ben Liu", "chloe": "Chloe Martin"}
    spaces = {}
    for name in names:
        ws = make_space(tmp_path / name, name=name, owner=owners[name])
        mount_brain(ws, brain)
        spaces[name] = ws
    return brain, spaces


def ctx(root):
    space = spaceyaml.load(root)
    return space, registry.Registry(root, space)


def test_gate_holds_an_unclassified_project(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "harbor", "kind": "projects", "home": "lantern"},
                                 {"id": "pilots", "kind": "topics", "home": "lantern"}], "ben")
    space, reg = ctx(ws["ben"])
    held = classify.gate(space, reg, "harbor", "lantern")
    assert held and held[0] == "unclassified"
    assert classify.gate(space, reg, "harbor", "ben") is None      # nothing leaves: no gate
    assert classify.gate(space, reg, "pilots", "lantern") is None  # not a gated kind


def test_gate_frees_a_company_subject_to_its_home_only(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "harbor", "kind": "projects", "home": "lantern",
                                  "classification": company()}], "ben")
    space, reg = ctx(ws["ben"])
    assert classify.gate(space, reg, "harbor", "lantern") is None
    assert classify.gate(space, reg, "harbor", "elsewhere")[0] == "unclassified"


def test_gate_keeps_an_owner_subject_home(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "harbor", "kind": "clients", "classification": company(
        **{"class": "owner", "home": None})}], "ben")
    space, reg = ctx(ws["ben"])
    assert classify.gate(space, reg, "harbor", "lantern")[0] == "kept"


def test_a_project_follows_its_client_and_the_strictest_wins(tmp_path):
    walled = dict(SETTLED_WALLED, home="tidewater", crew=["ada"])
    _, ws = brain_and(tmp_path, [
        {"id": "tide", "kind": "clients", "classification": walled},
        {"id": "harbor", "kind": "projects", "part_of": "tide", "classification": company()},
        {"id": "dock", "kind": "projects", "part_of": "open-client"},
        {"id": "open-client", "kind": "clients", "classification": company()}], "ben")
    space, reg = ctx(ws["ben"])
    found = classify.effective(reg, "harbor")
    assert found["class"] == "walled" and found["home"] == "tidewater" and found["subject"] == "tide"
    assert classify.gate(space, reg, "harbor", "lantern")[0] == "unclassified"
    assert classify.gate(space, reg, "harbor", "tidewater") is None
    assert classify.effective(reg, "dock")["class"] == "company"   # follows its client


def test_an_open_client_closes_its_settled_project(tmp_path):
    _, ws = brain_and(tmp_path, [
        {"id": "tide", "kind": "clients", "classification": company(state="open")},
        {"id": "harbor", "kind": "projects", "part_of": "tide", "classification": dict(SETTLED_WALLED)}], "ben")
    space, reg = ctx(ws["ben"])
    assert classify.effective(reg, "harbor") is None
    assert classify.gate(space, reg, "harbor", "harbor")[0] == "unclassified"


def test_subject_owner_ignores_domain_and_space_owner(tmp_path):
    _, ws = brain_and(tmp_path, [
        {"id": "sales", "kind": "domains", "owner": "Chloe Martin"},
        {"id": "deal", "kind": "deals", "part_of": "sales"},
        {"id": "tide", "kind": "clients", "owner": "Ben Liu"},
        {"id": "harbor", "kind": "projects", "part_of": "tide"}], "ada")
    _, reg = ctx(ws["ada"])
    assert classify.subject_owner(reg, "deal") is None
    assert classify.subject_owner(reg, "harbor") == "Ben Liu"


def test_decider_self_stand_in_other_and_no_owner(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "x", "kind": "clients", "owner": "Ben Liu"},
                                 {"id": "y", "kind": "clients", "owner": "Chloe Martin"},
                                 {"id": "z", "kind": "clients"}], "ada", "ben")
    ada, ada_reg = ctx(ws["ada"])
    ben, ben_reg = ctx(ws["ben"])
    assert classify.decider(ben, ben_reg, "x") == "self"
    assert classify.decider(ada, ada_reg, "x") == "other"
    assert classify.decider(ada, ada_reg, "y") == "stand_in"     # Chloe has no workspace yet
    assert classify.decider(ben, ben_reg, "y") == "other"
    assert classify.decider(ada, ada_reg, "z") == "no_owner"


def test_first_release_until_one_release_since_classification(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "harbor", "kind": "projects", "home": "lantern",
                                  "classification": company()},
                                 {"id": "dock", "kind": "projects", "classification": dict(SETTLED_WALLED)}],
                      "ben")
    root = ws["ben"]
    space, reg = ctx(root)
    released = root / "_meta" / "state" / "released.jsonl"
    released.write_text(json.dumps({"id": "p-1", "at": "2026-09-30T09:00:00Z", "subject": "harbor"}) + "\n",
                        encoding="utf-8")
    assert classify.first_release(root, space, reg, "harbor") is True
    with released.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": "p-2", "at": "2026-10-02T09:00:00Z", "subject": "harbor"}) + "\n")
    assert classify.first_release(root, space, reg, "harbor") is False
    assert classify.first_release(root, space, reg, "dock") is False   # walled: no first-release wait


# ----- Task 3: registry notes and the brain applying them ----------------------------------------
import datetime as dt  # noqa: E402

import landing  # noqa: E402
import release  # noqa: E402
import status  # noqa: E402
import yaml  # noqa: E402

AT = dt.datetime(2026, 10, 12, 9, 0, tzinfo=dt.timezone.utc)


def brain_entry(brain, sid):
    data = yaml.safe_load((brain / "_meta" / "subjects.yaml").read_text(encoding="utf-8"))
    return next(s for s in data["subjects"] if s["id"] == sid)


def notes_log(brain):
    path = brain / "_meta" / "state" / "registry-notes.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def drop_note(brain, sid, note, by, sender, data):
    """A registry note as it arrives at the brain after a ship."""
    folder = brain / "_inbound" / "signals"
    folder.mkdir(parents=True, exist_ok=True)
    signal = {"id": f"p-{abs(hash((sid, note, by))) % 10**8:08d}", "kind": "registry", "note": note,
              "subject": sid, "destination": "lantern", "at": "2026-10-12T08:00:00Z", "by": by,
              "from": sender, "data": data}
    (folder / f"{signal['id']}.yaml").write_text(yaml.safe_dump(signal), encoding="utf-8")


def test_a_note_ships_to_the_brain_and_lands_in_its_registry(tmp_path):
    brain, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients"}], "ben")
    path = classify.note(ws["ben"], "tide", "owner", {"person": "Ben Liu", "evidence": ["m-1"]}, now=AT)
    assert path is not None and path.parent.name == "signals"
    release.ship(ws["ben"], now=AT, offline_roots={"lantern": brain})
    assert not path.exists()
    assert list((brain / "_inbound" / "signals").glob("*.yaml"))
    landing.land(brain, now=AT)
    assert brain_entry(brain, "tide")["owner"] == "Ben Liu"
    assert not list((brain / "_inbound" / "signals").glob("*.yaml"))
    assert [r["outcome"] for r in notes_log(brain)] == ["applied"]


def test_a_registry_signal_is_never_readdressed(tmp_path):
    brain, ws = brain_and(tmp_path, [{"id": "harbor", "kind": "projects", "home": "harbor"}], "ben")
    classify.note(ws["ben"], "harbor", "owner", {"person": "Ben Liu", "evidence": ["m-1"]}, now=AT)
    release.ship(ws["ben"], now=AT, offline_roots={"lantern": brain})
    landed = list((brain / "_inbound" / "signals").glob("*.yaml"))
    assert len(landed) == 1 and yaml.safe_load(landed[0].read_text(encoding="utf-8"))["destination"] == "lantern"


def test_the_brain_refuses_a_classification_from_a_non_decider(tmp_path):
    brain, _ = brain_and(tmp_path, [{"id": "x", "kind": "clients", "owner": "Ben Liu"}])
    drop_note(brain, "x", "classification", "Chloe Martin", "chloe",
              {"class": "company", "reason": "ordinary work", "evidence": ["m-1"]})
    classify.land_notes(brain, now=AT)
    assert "classification" not in brain_entry(brain, "x")
    rows = notes_log(brain)
    assert rows[0]["outcome"] == "refused" and "Ben Liu" in rows[0]["why"]


def test_a_note_for_a_subject_the_brain_does_not_hold_is_refused(tmp_path):
    brain, _ = brain_and(tmp_path, [])
    drop_note(brain, "nowhere", "owner", "Ben Liu", "ben", {"person": "Ben Liu", "evidence": ["m-1"]})
    assert classify.land_notes(brain, now=AT) == {"applied": 0, "refused": 1}


def test_notes_record_a_workspace_reopen_on_an_obligation_and_mark_the_check(tmp_path):
    brain, _ = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor",
                                     "classification": company(not_yet_checked=["chloe"], checked_with=["ada"])}])
    drop_note(brain, "chloe", "workspace", "Chloe Martin", "chloe", {})
    drop_note(brain, "tide", "obligation", "Chloe Martin", "chloe",
              {"kind": "confidentiality", "date": "2026-04-14", "evidence": ["m-9"]})
    assert classify.land_notes(brain, now=AT) == {"applied": 2, "refused": 0}
    assert brain_entry(brain, "chloe")["workspace"] == "chloe"
    tide = brain_entry(brain, "tide")
    assert tide["classification"]["state"] == "open"
    assert tide["classification"]["not_yet_checked"] == [] and "chloe" in tide["classification"]["checked_with"]
    assert tide["obligations"] == [{"kind": "confidentiality", "date": "2026-04-14", "seen_in": "chloe",
                                    "at": "2026-10-12T08:00:00Z", "evidence": ["m-9"]}]
    assert registry.Registry(brain).check() == []


def test_status_ignores_registry_signals(tmp_path):
    brain, _ = brain_and(tmp_path, [{"id": "tide", "kind": "clients"}])
    drop_note(brain, "tide", "owner", "Ben Liu", "ben", {"person": "Ben Liu", "evidence": ["m-1"]})
    assert status.moved_signals(brain, spaceyaml.load(brain), AT) == {}


# ----- Task 4: the verbs, the pending pack and the Needs-you items --------------------------------
import pytest  # noqa: E402

DAN = {"id": "dan", "kind": "people", "title": "Dan Ito", "workspace": "dan"}
OWNERS = {"ada": "Ada Okafor", "ben": "Ben Liu", "chloe": "Chloe Martin", "dan": "Dan Ito"}


def signals(root):
    folder = root / "_outbound" / "signals"
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.yaml"))]


def items(root):
    path = root / "_inbound" / "needs-you" / "classify.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("items", []) if path.exists() else []


def answer(root, item_id, verb, by="Ada Okafor", note=None):
    """The person's answer as sign.py records it: a row in the verdict store, never a log line."""
    status.record_verdict(root, item_id, by, verb=verb, at="2026-10-12T10:00:00Z")
    if note:
        status.record_verdict(root, item_id, by, note=note, at="2026-10-12T10:00:00Z")


def test_ask_names_every_workspace_but_mine_and_a_deadline(tmp_path):
    _, ws = brain_and(tmp_path, [DAN, {"id": "tide", "kind": "clients", "owner": "Ada Okafor"}],
                      "ada", "ben", owners=OWNERS)
    result = classify.ask(ws["ada"], "tide", now=AT)
    assert result["asked"] == ["ben", "dan"] and result["ready"] is False
    sent = signals(ws["ada"])[0]
    assert sent["note"] == "ask" and sent["data"]["ask_until"] == "2026-10-13T09:00:00Z"
    with pytest.raises(classify.ClassifyError):
        classify.ask(ws["ben"], "tide", now=AT)


def test_decide_waits_for_answers_until_the_deadline(tmp_path):
    asking = {"state": "asking", "asked": ["ben", "dan"], "answered": ["ben"],
              "ask_until": "2026-10-13T09:00:00Z", "decided_by": "Ada Okafor", "at": "2026-10-12T09:00:00Z"}
    _, ws = brain_and(tmp_path, [DAN, {"id": "tide", "kind": "clients", "owner": "Ada Okafor",
                                       "classification": asking}], "ada", owners=OWNERS)
    entry = next(e for e in classify.pending(ws["ada"], now=AT)["decide"] if e["subject"] == "tide")
    assert entry["next"] == "wait"
    with pytest.raises(classify.ClassifyError):   # company may not skip a workspace still due
        classify.decide(ws["ada"], "tide", "company", "ordinary work", evidence=["m-1"], now=AT)
    later = AT + dt.timedelta(hours=25)
    entry = next(e for e in classify.pending(ws["ada"], now=later)["decide"] if e["subject"] == "tide")
    assert entry["next"] == "decide"
    classify.decide(ws["ada"], "tide", "company", "ordinary work", evidence=["m-1"], now=later)
    data = [s for s in signals(ws["ada"]) if s["note"] == "classification"][0]["data"]
    assert data["checked_with"] == ["ada", "ben"] and data["not_yet_checked"] == ["chloe", "dan"]


def test_a_non_owner_workspace_never_classifies(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}], "ben")
    with pytest.raises(classify.ClassifyError):
        classify.decide(ws["ben"], "tide", "company", "ordinary work", evidence=["m-1"], now=AT)
    assert signals(ws["ben"]) == []


def test_closed_default_needs_the_owner_for_company(tmp_path):
    brain, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}], "ada")
    data = yaml.safe_load((brain / "space.yaml").read_text(encoding="utf-8"))
    (brain / "space.yaml").write_text(yaml.safe_dump(dict(data, classification_default="closed")), encoding="utf-8")
    result = classify.decide(ws["ada"], "tide", "company", "ordinary work", evidence=["m-1"], now=AT)
    assert result.get("proposal") and signals(ws["ada"]) == []
    item = [i for i in items(ws["ada"]) if i["id"].startswith("classify-decide-tide")][0]
    answer(ws["ada"], item["id"], "yes")
    classify.apply_verdicts(ws["ada"], now=AT)
    sent = [s for s in signals(ws["ada"]) if s["note"] == "classification"][0]
    assert sent["data"]["class"] == "company" and sent["data"]["approved_by"] == "Ada Okafor"
    assert not [i for i in items(ws["ada"]) if i["id"] == item["id"]]


def test_open_default_needs_a_reason_to_wall(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}], "ada")
    with pytest.raises(classify.ClassifyError):
        classify.decide(ws["ada"], "tide", "walled", "feels private", crew=["ada"], home="tide", now=AT)
    classify.decide(ws["ada"], "tide", "walled", "a confidentiality agreement", evidence=["m-9"],
                    crew=["ada"], home="tide", now=AT)
    assert signals(ws["ada"])[0]["data"]["class"] == "walled"


def test_obligation_carries_kind_and_date_only(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients"}], "ben")
    classify.obligation(ws["ben"], "tide", "confidentiality", date="2026-04-14", evidence=["m-9"], now=AT)
    data = signals(ws["ben"])[0]["data"]
    assert data == {"kind": "confidentiality", "date": "2026-04-14", "evidence": ["m-9"]}


def test_obligation_refuses_wording_and_bad_dates(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients"}], "ben")
    for kwargs in ({"kind": "nda", "evidence": ["m-9"]}, {"kind": "confidentiality", "date": "April", "evidence": ["m-9"]},
                   {"kind": "confidentiality", "evidence": []}):
        with pytest.raises(classify.ClassifyError):
            classify.obligation(ws["ben"], "tide", now=AT, **kwargs)
    with pytest.raises(SystemExit):
        classify.main([str(ws["ben"]), "obligation", "tide", "--kind", "confidentiality",
                       "--evidence", "m-9", "--fact", "covers the annex"])
    assert signals(ws["ben"]) == []


def test_owner_unknown_asks_who_handles_it_once(tmp_path):
    from conftest import write_page
    _, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "home": "lantern"}], "ada", "ben")
    write_page(ws["ben"], "_outbound/held/p-11111111.md", id="p-11111111", type="source", subject="tide",
               destination="lantern", reason="unclassified", why="tide waits")
    assert [e["subject"] for e in classify.pending(ws["ben"], now=AT)["owner_unknown"]] == ["tide"]
    classify.pending(ws["ben"], now=AT)
    assert [i["id"] for i in items(ws["ben"])] == ["classify-owner-tide"]
    assert classify.pending(ws["ada"], now=AT)["owner_unknown"] == []


def test_walled_without_a_space_plans_a_mint_after_a_window(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}], "ada")
    classify.decide(ws["ada"], "tide", "walled", "a confidentiality agreement", evidence=["m-9"],
                    crew=["ada", "ben"], home="tidewater", now=AT)
    plan = [i for i in items(ws["ada"]) if i["id"].startswith("classify-mint-tide")][0]
    assert plan["deadline"] == "2026-10-13T09:00:00Z" and "tidewater" in plan["text"]
    assert list((ws["ada"] / "_meta" / "state" / "notices" / "pending").glob("*.json"))
    assert classify.pending(ws["ada"], now=AT)["mint_due"] == []
    due = classify.pending(ws["ada"], now=AT + dt.timedelta(hours=25))["mint_due"]
    assert due == [{"subject": "tide", "home": "tidewater", "crew": ["ada", "ben"], "item": plan["id"]}]


def test_announce_sends_the_workspace_note_once(tmp_path):
    _, ws = brain_and(tmp_path, [], "ada", "chloe")
    path = classify.announce(ws["chloe"], now=AT)
    assert path is not None and signals(ws["chloe"])[0]["note"] == "workspace"
    assert classify.announce(ws["chloe"], now=AT) is None
    assert classify.announce(ws["ada"], now=AT) is None   # the brain knows ada's workspace


# ----- Task 8: existing installs, the one list ----------------------------------------------------------
def backlog_world(tmp_path):
    brain, ws = brain_and(tmp_path, [
        {"id": "tide", "kind": "clients", "title": "Tide", "owner": "Ada Okafor", "home": "lantern"},
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "lantern"},
        {"id": "pilots", "kind": "topics", "title": "Pilots"}], "ada")
    rows = [{"subject": "tide", "owner": "Ada Okafor", "class": "company", "reason": "ordinary client work"},
            {"subject": "harbor", "owner": "Chloe Martin", "class": "walled", "reason": "a confidentiality agreement",
             "evidence": ["m-9"], "home": "harbor", "crew": ["ada", "ben"]}]
    return brain, ws["ada"], rows


def backlog_item(root):
    return [i for i in items(root) if i["id"].startswith("classify-backlog")]


def test_backlog_is_one_item_with_a_line_per_subject(tmp_path):
    _, root, rows = backlog_world(tmp_path)
    classify.backlog(root, rows, now=AT)
    item, = backlog_item(root)
    assert "1. Tide: company, owner Ada Okafor: ordinary client work" in item["text"]
    assert "2. Harbor: walled, owner Chloe Martin: a confidentiality agreement" in item["text"]
    assert item["recommendation"] == "yes: classify them as listed"
    assert item["default"] == "nothing about them leaves"
    assert classify.pending(root, now=AT)["decide"] == []   # decided on the list, not one by one
    with pytest.raises(classify.ClassifyError):              # a topic is not gated
        classify.backlog(root, [{"subject": "pilots", "class": "company", "reason": "x"}], now=AT)


def test_backlog_yes_classifies_every_row_with_the_owner(tmp_path):
    _, root, rows = backlog_world(tmp_path)
    classify.backlog(root, rows, now=AT)
    answer(root, backlog_item(root)[0]["id"], "yes")
    out = classify.apply_verdicts(root, now=AT)
    assert out["errors"] == []
    sent = {s["subject"]: s["data"] for s in signals(root) if s["note"] == "classification"}
    assert sent["tide"]["class"] == "company" and sent["tide"]["approved_by"] == "Ada Okafor"
    assert sent["harbor"]["class"] == "walled" and sent["harbor"]["owner"] == "Chloe Martin"
    assert backlog_item(root) == []
    assert [i for i in items(root) if i["id"].startswith("classify-mint-harbor")]   # made while answering


def test_backlog_edit_changes_named_lines_only(tmp_path):
    _, root, rows = backlog_world(tmp_path)
    classify.backlog(root, rows, now=AT)
    answer(root, backlog_item(root)[0]["id"], "edit", note="2 owner")
    classify.apply_verdicts(root, now=AT)
    sent = {s["subject"]: s["data"]["class"] for s in signals(root) if s["note"] == "classification"}
    assert sent == {"tide": "company", "harbor": "owner"}


def test_backlog_leaves_homes_alone_until_answered(tmp_path):
    brain, root, rows = backlog_world(tmp_path)
    classify.backlog(root, rows, now=AT)
    assert signals(root) == [] and brain_entry(brain, "harbor")["home"] == "lantern"
    answer(root, backlog_item(root)[0]["id"], "no")
    classify.apply_verdicts(root, now=AT)
    assert signals(root) == [] and backlog_item(root) == []


# ----- the final review's fixes -------------------------------------------------------------------
def note_at(brain, name, sid, note, by, sender, at, data):
    """A registry note with its own file name and time (the order they land in is the test)."""
    folder = brain / "_inbound" / "signals"
    folder.mkdir(parents=True, exist_ok=True)
    signal = {"id": name, "kind": "registry", "note": note, "subject": sid, "destination": "lantern",
              "at": at, "by": by, "from": sender, "data": data}
    (folder / f"{name}.yaml").write_text(yaml.safe_dump(signal), encoding="utf-8")


def test_the_command_line_cannot_claim_the_persons_yes(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}], "ada")
    for flag in ("--by", "--owner"):
        with pytest.raises(SystemExit):
            classify.main([str(ws["ada"]), "decide", "tide", "--class", "company", "--reason", "x",
                           flag, "Ada Okafor"])
    assert signals(ws["ada"]) == []


def test_an_owner_note_never_replaces_an_umbrella_owner(tmp_path):
    brain, _ = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"},
                                    {"id": "tide-pilot", "kind": "projects", "part_of": "tide"}])
    drop_note(brain, "tide-pilot", "owner", "Ben Liu", "ben", {"person": "Ben Liu", "evidence": ["m-1"]})
    assert classify.land_notes(brain, now=AT) == {"applied": 0, "refused": 1}
    assert "owner" not in brain_entry(brain, "tide-pilot")


def test_an_unowned_subject_is_classified_only_for_an_owner_the_sender_may_decide_for(tmp_path):
    brain, _ = brain_and(tmp_path, [{"id": "x", "kind": "clients"}])
    drop_note(brain, "x", "classification", "Ben Liu", "ben",
              {"class": "company", "reason": "ordinary work", "evidence": ["m-1"], "approved_by": "Ben Liu",
               "owner": "Ada Okafor"})
    assert classify.land_notes(brain, now=AT) == {"applied": 0, "refused": 1}
    assert "classification" not in brain_entry(brain, "x") and "owner" not in brain_entry(brain, "x")


def test_a_list_row_for_an_owner_with_a_workspace_is_refused(tmp_path):
    _, root, rows = backlog_world(tmp_path)
    with pytest.raises(classify.ClassifyError, match="Ben Liu"):
        classify.backlog(root, [dict(rows[1], owner="Ben Liu")], now=AT)


def test_notes_land_in_the_order_they_were_made(tmp_path):
    brain, _ = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}])
    note_at(brain, "p-00000001", "tide", "obligation", "Ben Liu", "ben", "2026-10-12T08:30:00Z",
            {"kind": "confidentiality", "date": "2026-04-14", "evidence": ["m-9"]})
    note_at(brain, "p-00000002", "tide", "classification", "Ada Okafor", "ada", "2026-10-12T08:00:00Z",
            {"class": "company", "reason": "ordinary work", "evidence": [], "obligations_seen": 0})
    assert classify.land_notes(brain, now=AT) == {"applied": 2, "refused": 0}
    assert brain_entry(brain, "tide")["classification"]["state"] == "open"


def test_a_company_note_that_missed_an_obligation_lands_open(tmp_path):
    seen = {"kind": "confidentiality", "date": "2026-04-14", "seen_in": "ben", "at": "2026-10-11T08:00:00Z",
            "evidence": ["m-9"]}
    brain, ws = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor",
                                      "obligations": [seen]}], "ada")
    note_at(brain, "p-00000003", "tide", "classification", "Ada Okafor", "ada", "2026-10-12T08:00:00Z",
            {"class": "company", "reason": "ordinary work", "evidence": [], "obligations_seen": 0,
             "approved_by": "Ada Okafor"})
    classify.land_notes(brain, now=AT)
    c = brain_entry(brain, "tide")["classification"]
    assert c["class"] == "company" and c["state"] == "open"
    note_at(brain, "p-00000004", "tide", "classification", "Ada Okafor", "ada", "2026-10-12T08:10:00Z",
            {"class": "company", "reason": "ordinary work", "evidence": [], "obligations_seen": 1,
             "approved_by": "Ada Okafor"})
    classify.land_notes(brain, now=AT)
    assert brain_entry(brain, "tide")["classification"]["state"] == "settled"
    classify.decide(ws["ada"], "tide", "company", "ordinary work", by="Ada Okafor", now=AT)
    sent, = [s for s in signals(ws["ada"]) if s["note"] == "classification"]
    assert sent["data"]["obligations_seen"] == 1


def test_one_bad_note_never_stops_the_others(tmp_path):
    brain, _ = brain_and(tmp_path, [{"id": "tide", "kind": "clients", "owner": "Ada Okafor"}])
    folder = brain / "_inbound" / "signals"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "p-00000005.yaml").write_text(   # an unquoted time (a datetime) and a class that is a list
        "id: p-00000005\nkind: registry\nnote: classification\nsubject: tide\ndestination: lantern\n"
        "at: 2026-10-12T08:00:00Z\nby: Ada Okafor\nfrom: ada\n"
        "data: {class: [company], reason: ordinary work, evidence: [m-1]}\n", encoding="utf-8")
    drop_note(brain, "chloe", "workspace", "Chloe Martin", "chloe", {})
    assert classify.land_notes(brain, now=AT) == {"applied": 1, "refused": 1}
    assert brain_entry(brain, "chloe")["workspace"] == "chloe"


def test_company_in_a_workspace_registry_names_the_brain_as_home(tmp_path):
    _, ws = brain_and(tmp_path, [], "ada")
    path = ws["ada"] / "_meta" / "subjects.yaml"
    path.write_text(yaml.safe_dump({"subjects": [{"id": "dock", "kind": "deals", "title": "Dock",
                                                  "owner": "Ada Okafor"}]}), encoding="utf-8")
    classify.decide(ws["ada"], "dock", "company", "ordinary work", by="Ada Okafor", now=AT)
    entry = yaml.safe_load(path.read_text(encoding="utf-8"))["subjects"][0]
    assert entry["home"] == "lantern" and entry["classification"]["home"] == "lantern"


def test_a_project_with_its_own_space_is_walled_without_more_evidence(tmp_path):
    _, ws = brain_and(tmp_path, [{"id": "harbor", "kind": "projects", "title": "Harbor", "owner": "Ada Okafor",
                                  "home": "harbor"}], "ada")
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor")
    data = yaml.safe_load((ws["ada"] / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] += [{"name": "harbor", "path": str(harbor)}]
    (ws["ada"] / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    row = {"subject": "harbor", "owner": "Ada Okafor", "class": "walled", "reason": "it has its own space",
           "home": "harbor", "crew": ["ada", "ben"]}
    with pytest.raises(classify.ClassifyError, match="evidence"):   # a space that does not exist yet needs its reason
        classify.backlog(ws["ada"], [dict(row, home="tidewater")], now=AT)
    classify.backlog(ws["ada"], [row], now=AT)                       # the space is the fact
    assert classify.decide(ws["ada"], "harbor", "walled", "it has its own space", crew=["ada", "ben"],
                           home="harbor", now=AT)["note"] is not None


# ----- codebase links: the link note (docs/design/2026-10-22-codebase-link-design.md) -------------
import pages  # noqa: E402


def test_link_note_applies_in_own_registry(tmp_path, now):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor-voice-agent", "kind": "projects", "owner": "Ada Okafor"}])
    classify.note(root, "harbor-voice-agent", "link",
                  {"repo": "northwind/harbor-voice", "linked_by": "detect", "linked": now.date().isoformat()}, now=now)
    repos = registry.Registry(root).get("harbor-voice-agent").extra["repos"]
    assert repos[0]["repo"] == "northwind/harbor-voice" and repos[0]["linked_by"] == "detect"
    assert registry.Registry(root).check() == []


def test_link_note_updates_by_repo_id_and_removes(tmp_path, now):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor-voice-agent", "kind": "projects", "owner": "Ada Okafor"}])
    today, later = now.date().isoformat(), (now + dt.timedelta(days=1)).date().isoformat()
    classify.note(root, "harbor-voice-agent", "link", {"repo": "northwind/harbor-voice", "repo_id": "R_1",
                                                       "linked_by": "detect", "linked": today}, now=now)
    classify.note(root, "harbor-voice-agent", "link", {"repo": "northwind/harbor-voice-2", "repo_id": "R_1",
                                                       "linked": later}, now=now)   # no `was`: not a rename
    repos = registry.Registry(root).get("harbor-voice-agent").extra["repos"]
    assert len(repos) == 1 and repos[0]["repo"] == "northwind/harbor-voice" and repos[0]["linked"] == today
    classify.note(root, "harbor-voice-agent", "link", {"repo": "northwind/harbor-voice-2", "repo_id": "R_1",
                                                       "was": ["northwind/harbor-voice"]}, now=now)   # a rename
    (entry,) = registry.Registry(root).get("harbor-voice-agent").extra["repos"]
    assert (entry["repo"], entry["was"], entry["linked"]) == ("northwind/harbor-voice-2", ["northwind/harbor-voice"], today)
    classify.note(root, "harbor-voice-agent", "link", {"repo": "northwind/harbor-voice", "remove": True}, now=now)
    assert registry.Registry(root).get("harbor-voice-agent").extra.get("repos", []) == []


def _link_signal(by, sender, data):
    return {"id": pages.new_id(), "kind": "registry", "note": "link", "subject": "harbor-voice-agent",
            "by": by, "from": sender, "data": data}


def test_link_note_refused_from_a_stranger(tmp_path, now):
    brain, _ = brain_and(tmp_path, [{"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ada"]}])
    space, reg = ctx(brain)
    signal = _link_signal("Carl Ng", "carl", {"repo": "northwind/harbor-voice"})
    outcome, why = classify.apply_note(brain, space, reg, signal, now)
    assert outcome == "refused" and "may link a repository" in why
    assert "repos" not in reg.get("harbor-voice-agent").extra


def test_link_note_from_the_home_merges_fields(tmp_path, now):
    today = now.date().isoformat()
    brain, _ = brain_and(tmp_path, [{"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ada"],
                                     "repos": [{"repo": "northwind/harbor-voice", "linked_by": "detect",
                                                "linked": today}]}])
    space, reg = ctx(brain)
    signal = _link_signal("Ben Liu", "harbor", {"repo": "northwind/harbor-voice", "readers": "wider", "checked": today})
    outcome, _ = classify.apply_note(brain, space, reg, signal, now)
    entry = reg.get("harbor-voice-agent").extra["repos"][0]
    assert outcome == "applied" and entry["readers"] == "wider" and entry["linked_by"] == "detect"
    assert entry["linked"] == today and len(reg.get("harbor-voice-agent").extra["repos"]) == 1


@pytest.mark.parametrize("first", ["rename", "new clone"])
def test_link_notes_keep_one_entry_per_repository(tmp_path, now, first):
    # the brain holds harbor-voice-agent (home harbor) linked to northwind/harbor-voice (R_1); harbor's check
    # follows its rename to northwind/harbor-voice-2 while ada's workspace links a clone under the new name
    brain, _ = brain_and(tmp_path, [{"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ada"],
                                     "repos": [{"repo": "northwind/harbor-voice", "repo_id": "R_1",
                                                "linked_by": "detect", "linked": "2026-10-01"}]}])
    space, reg = ctx(brain)
    notes = {"rename": _link_signal("Ben Liu", "harbor", {"repo": "northwind/harbor-voice-2", "repo_id": "R_1",
                                                         "was": ["northwind/harbor-voice"]}),
             "new clone": _link_signal("Ada Okafor", "ada", {"repo": "northwind/harbor-voice-2", "linked_by": "detect",
                                                            "linked": now.date().isoformat()})}
    for name in sorted(notes, key=lambda n: n != first):
        assert classify.apply_note(brain, space, reg, notes[name], now)[0] == "applied"
    (entry,) = reg.get("harbor-voice-agent").extra["repos"]
    assert (entry["repo"], entry["repo_id"], entry["was"], entry["linked"]) == (
        "northwind/harbor-voice-2", "R_1", ["northwind/harbor-voice"], "2026-10-01")
    # a note under the old name, from a view that missed the rename, never renames the link back
    old = _link_signal("Ben Liu", "harbor", {"repo": "northwind/harbor-voice", "repo_id": "R_1", "readers": "inside"})
    assert classify.apply_note(brain, space, reg, old, now)[0] == "applied"
    (entry,) = reg.get("harbor-voice-agent").extra["repos"]
    assert (entry["repo"], entry["was"], entry["readers"]) == ("northwind/harbor-voice-2", ["northwind/harbor-voice"],
                                                               "inside")


def test_a_merge_keeps_every_old_name(tmp_path, now):
    # hv-y was renamed hv-z, then hv-z was renamed hv-x: A is the link (now hv-x, was hv-z), B an entry from a
    # clone made before the first rename; B's rename note lands on A by id and B merges in
    brain, _ = brain_and(tmp_path, [{"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ada"],
                                     "repos": [{"repo": "northwind/hv-x", "repo_id": "R_1", "was": ["northwind/hv-z"],
                                                "linked": "2026-10-01"},
                                               {"repo": "northwind/hv-y", "linked": "2026-10-05"}]}])
    space, reg = ctx(brain)
    note = _link_signal("Ben Liu", "harbor", {"repo": "northwind/hv-x", "repo_id": "R_1", "was": ["northwind/hv-y"]})
    assert classify.apply_note(brain, space, reg, note, now)[0] == "applied"
    (entry,) = reg.get("harbor-voice-agent").extra["repos"]
    assert entry["repo"] == "northwind/hv-x" and sorted(entry["was"]) == ["northwind/hv-y", "northwind/hv-z"]


def test_may_link_home_owners_crew_and_the_brain(tmp_path):
    brain, ws = brain_and(tmp_path, [
        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ben"]},
        {"id": "dock", "kind": "projects", "owner": "Chloe Martin",
         "classification": dict(SETTLED_WALLED, home="dock", crew=["ben"])},
        {"id": "tide-app", "kind": "projects", "home": "tide", "crew": ["chloe"]},
        {"id": "product", "kind": "topics", "home": "lantern"}], "ben")
    _, reg = ctx(brain)
    hva, dock, product = (reg.get(sid) for sid in ("harbor-voice-agent", "dock", "product"))
    assert classify.may_link(reg, hva, "Ben Liu", "ben")              # its crew
    assert classify.may_link(reg, hva, "Carl Ng", "harbor")           # its home
    assert classify.may_link(reg, hva, "Ada Okafor", "ada")           # the holder's owner
    assert not classify.may_link(reg, hva, "Chloe Martin", "chloe")
    assert classify.may_link(reg, dock, "Chloe Martin", "chloe")      # its owner
    assert classify.may_link(reg, dock, "Ben Liu", "ben")             # its walled classification's crew
    assert classify.may_link(reg, product, "Chloe Martin", "chloe")   # homed in the brain: anyone registered
    assert not classify.may_link(reg, product, "Carl Ng", "carl")
    _, ben = ctx(ws["ben"])   # Ben owns his workspace, not the brain that holds tide-app
    assert not classify.may_link(ben, ben.get("tide-app"), "Ben Liu", "ben")
