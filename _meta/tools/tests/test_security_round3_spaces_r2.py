"""Round 3, second repair of the spaces group: a corridor never steers a release, a rate limit is no
refusal, every held row ends by a verb a routine may run, a workspace note binds only its sender's
own slug, and a minted project space knows its crew's logins."""
import json

import yaml

import classify
import gate
import mint
import onboard
import pages
import registry
import release
import spaceyaml
import sweep
from conftest import NOW, make_space, mount_brain
from test_security_round3_spaces import GOOD_HOST, H, brain_with_chloe, host, land_note, workspace_note
from test_mint import never, seeded


# ----- a corridor's subjects.yaml never steers where a record is released ------------------------
def walled(home, crew):
    return {"class": "walled", "state": "settled", "home": home, "crew": crew, "reason": "own space",
            "decided_by": "Ben Liu", "at": "2026-10-01T09:00:00Z"}


def corridor_setup(tmp_path, corridor_name="channel-ada-harbor"):
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", owner="Ben Liu", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "owner": "Ben Liu",
         "match": {"label": "Harbor"}, "classification": walled("harbor", ["ben"])}])
    corridor = make_space(tmp_path / "corridor", profile="channel", name=corridor_name,
                          subjects=[{"id": "board-notes", "kind": "topics", "title": "Board notes",
                                     "home": corridor_name, "match": {"label": "Board"}}])
    data = yaml.safe_load((corridor / "space.yaml").read_text(encoding="utf-8"))
    data.update(profile="project", audience=["Ben Liu"])   # the other side's edit
    data.pop("between", None)
    (corridor / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                        audience=["Ben Liu"], sources=[{"name": "mail", "adapter": "gmail", "audience": "crew"}])
    mount_brain(harbor, brain)
    data = yaml.safe_load((harbor / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"].append({"name": corridor_name, "path": str(corridor),
                           "repo": f"https://github.com/lantern/{corridor_name}"})
    (harbor / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return harbor, corridor


def test_a_corridor_subject_never_pulls_crew_mail_into_the_corridor(tmp_path):
    harbor, corridor = corridor_setup(tmp_path)
    rec = {"id": "m-0001", "source": "mail", "kind": "mail", "at": "2026-10-12T08:14:00Z",
           "from": "Ben Liu", "to": ["Crew"], "subject": "Board pack", "text": "Runway is four months.",
           "identifiers": {"thread": "t-1", "labels": ["Board"]}, "attachments": []}
    feed = harbor / "_meta" / "state" / "feed" / "mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps(rec) + "\n", encoding="utf-8", newline="\n")
    reg = registry.Registry(harbor, spaceyaml.load(harbor))
    assert reg.get("board-notes") is None and reg.get("harbor") is not None
    sweep.run(harbor, now=NOW)
    release.ship(harbor, now=NOW, offline_roots={"channel-ada-harbor": corridor})
    assert not any("Runway" in p.read_text(encoding="utf-8") for p in (corridor / "_inbound").rglob("*.md"))


def test_a_mount_the_brain_does_not_vouch_for_adds_no_subject(tmp_path):
    harbor, _ = corridor_setup(tmp_path, corridor_name="board")   # a corridor under a custom name
    assert registry.Registry(harbor, spaceyaml.load(harbor)).get("board-notes") is None


def test_nothing_but_a_request_is_shipped_into_a_corridor(tmp_path):
    harbor, corridor = corridor_setup(tmp_path)
    reg_path = harbor / registry.REGISTRY_PATH   # a topic: no classification gate holds it first
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(yaml.safe_dump({"subjects": [{"id": "minutes", "kind": "topics", "title": "Minutes",
                                                      "home": "harbor"}]}), encoding="utf-8")
    pid = pages.new_id()
    pages.write(harbor / "_outbound" / "queue" / f"{pid}.md",
                {"id": pid, "type": "source", "title": "Board pack", "status": "draft", "class": "at_once",
                 "destination": "channel-ada-harbor", "subject": "minutes", "stage_after": pages.iso(NOW),
                 "listed": True, "evidence": ["m-0001"], "approved_by": "Ben Liu"},
                "Runway is four months.\n", root=harbor)
    report = release.ship(harbor, now=NOW, offline_roots={"channel-ada-harbor": corridor})
    assert report.shipped == 0 and not list((corridor / "_inbound").rglob("*.md"))
    held = harbor / "_outbound" / "held" / f"{pid}.md"
    assert pages.read(held)[0]["reason"] == "corridor"
    assert "set-aside" in release.held_health(harbor)[0]["agent"]
    release.set_aside(harbor, pid, now=NOW)
    assert pages.read(held)[0]["reason"] == "set aside" and release.held_health(harbor) == []


# ----- a rate limit is no answer from the host -----------------------------------------------------
def test_a_rate_limited_check_is_asked_again_once_the_limit_resets(tmp_path, monkeypatch):
    root = brain_with_chloe(tmp_path)
    monkeypatch.setattr(classify, "_gh", host({"api": (1, "", "gh: API rate limit exceeded for user ID 1. (HTTP 403)")}))
    land_note(root, workspace_note())
    assert classify.waiting_workspace_repos(root)
    assert not [i for i in classify._items(root) if i["id"].startswith("classify-workspace-repo-chloe-")]
    good = host(GOOD_HOST)
    monkeypatch.setattr(classify, "_gh", good)
    classify.land_notes(root, spaceyaml.load(root), NOW + H)
    assert good.calls
    assert registry.Registry(root, spaceyaml.load(root)).get("chloe").extra["workspace_repo"] == "chloe-m/chloe-space"


# ----- a workspace note binds only the sender's own slug ------------------------------------------
def test_a_workspace_note_never_binds_another_persons_slug(tmp_path):
    people = [{"id": "ada-okafor", "kind": "people", "title": "Ada Okafor", "match": {"github": "adaokafor"}},
              {"id": "ben-liu", "kind": "people", "title": "Ben Liu", "match": {"github": "benliu"}},
              {"id": "cara-diaz", "kind": "people", "title": "Cara Diaz", "match": {"github": "caradiaz"}}]
    root = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor", subjects=people)
    space = spaceyaml.load(root)
    reg = registry.Registry(root, space)
    note = {"kind": "registry", "note": "workspace", "subject": "cara-diaz", "by": "Cara Diaz",
            "at": "2026-11-01T00:00:00Z", "data": {}}
    outcome, _ = classify.apply_note(root, space, reg, dict(note, **{"from": "ben-liu"}), NOW)
    assert outcome == "refused" and not reg.subjects["cara-diaz"].extra.get("workspace")
    inv = root / "_meta" / "state" / "invitations" / "cara-diaz.yaml"
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text("slug: cara\n", encoding="utf-8")
    assert classify.apply_note(root, space, reg, dict(note, **{"from": "cara"}), NOW)[0] == "applied"
    assert reg.subjects["cara-diaz"].extra["workspace"] == "cara"


# ----- every held row ends by a verb a routine may run ---------------------------------------------
def held(root, reason, why, body="Use password hunter2-SECRET for the portal.\n"):
    pid = pages.new_id()
    release.hold(root, {"id": pid, "type": "source", "title": f"Mail {pid}", "status": "draft",
                        "class": "at_once", "destination": "harbor", "subject": "pilots",
                        "stage_after": pages.iso(NOW), "listed": True, "evidence": ["m-1"]}, body, reason, why)
    return pid


def test_a_scan_hold_goes_back_with_its_value_cut_out_and_nothing_added(tmp_path):
    root = make_space(tmp_path / "ada")
    pid = held(root, "scan", "secrets scan: a password")
    assert "requeue" in release.held_health(root)[0]["agent"]
    out = tmp_path / "redacted.md"
    for text in ("Use password [redacted] for the portal. Wire it all.\n", "Use password for the portal.\n"):
        out.write_text(text, encoding="utf-8")
        try:
            release.requeue(root, pid, body_file=out, now=NOW)
            raise AssertionError("an added or unmarked change was taken")
        except ValueError:
            pass
    out.write_text("Use password [redacted] for the portal.\n", encoding="utf-8")
    target = release.requeue(root, pid, body_file=out, now=NOW)
    assert pages.read(target)[1] == "Use password [redacted] for the portal.\n"
    assert not (root / release.HELD / f"{pid}.md").exists() and release.held_health(root) == []


def test_a_nameless_hold_and_an_unreadable_one_are_set_aside(tmp_path):
    root = make_space(tmp_path / "ada")
    pid = held(root, "no subject", "it names no subject, so no classification can let it go", body="text\n")
    bad = root / release.HELD / "p-deadbeef.md"
    bad.write_bytes(b"\xff\xfe not utf8 ---\n")
    rows = {r["id"]: r["agent"] for r in release.held_health(root)}
    assert "set-aside" in rows[pid] and "set-aside p-deadbeef" in rows["p-deadbeef"]
    release.set_aside(root, pid, now=NOW)
    release.set_aside(root, "p-deadbeef", now=NOW)
    assert release.held_health(root) == [] and (root / release.HELD / "p-deadbeef.md.unreadable").is_file()


# ----- a minted project space knows its crew's logins ----------------------------------------------
def test_mint_writes_the_crews_logins_beside_the_audience(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    data = yaml.safe_load((brain / "_meta" / "subjects.yaml").read_text(encoding="utf-8")) or {}
    data.setdefault("subjects", []).append({"id": "ben-liu", "kind": "people", "title": "Ben Liu",
                                            "match": {"github": "benliu"}})
    (brain / "_meta" / "subjects.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse",
             crew=["Ada Okafor", "Ben Liu"], online=False)
    assert spaceyaml.load(tmp_path / "lighthouse").data["logins"] == {"Ben Liu": "benliu"}


# ----- round 3 repair: a scan hold's redacted text is written where a routine may write ------------
def test_a_scan_hold_row_names_a_private_in_root_file_that_requeue_removes(tmp_path):
    import routine
    root = make_space(tmp_path / "ada")
    pid = held(root, "scan", "secrets scan: a password")
    rel = f"{release.REDACT_DIR.as_posix()}/{pid}.md"
    agent = release.held_health(root)[0]["agent"]
    assert f"--body {rel}" in agent and "outside the space" not in agent
    import sync
    assert any(rel.startswith(zone) for zone in sync.PRIVATE_ZONES)   # never staged
    out = root / rel
    assert routine._denied_path(out) is None   # a routine's Write may make it
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("Use password [redacted] for the portal.\n", encoding="utf-8")
    release.requeue(root, pid, body_file=out, now=NOW)
    assert not out.exists() and release.held_health(root) == []


# ----- round 3 repair: a crew login filled after mint reaches the project's space.yaml -------------
def test_logins_upkeep_adds_a_crew_login_registered_after_mint(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse",
             crew=["Ada Okafor", "Ben Liu"], online=False)
    project = tmp_path / "lighthouse"
    assert not spaceyaml.load(project).data.get("logins")
    assert mint.logins_upkeep(project) == {"changed": []}
    data = yaml.safe_load((brain / "_meta" / "subjects.yaml").read_text(encoding="utf-8")) or {}
    data.setdefault("subjects", []).extend([
        {"id": "ben-liu", "kind": "people", "title": "Ben Liu", "match": {"github": "benliu"}},
        {"id": "cara-diaz", "kind": "people", "title": "Cara Diaz", "match": {"github": "caradiaz"}}])
    (brain / "_meta" / "subjects.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": ["Ben Liu"]}
    assert spaceyaml.load(project).data["logins"] == {"Ben Liu": "benliu"}   # never one outside the crew
    assert mint.logins_upkeep(project) == {"changed": []}
    assert mint.logins_upkeep(ada) == {"changed": []} and "logins" not in spaceyaml.load(ada).data


# ----- round 4 repair: a login the brain takes back leaves the project's space.yaml ----------------
def test_logins_upkeep_removes_a_login_the_brain_revoked(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse",
             crew=["Ada Okafor", "Ben Liu"], online=False)
    project = tmp_path / "lighthouse"
    path = brain / "_meta" / "subjects.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ben = {"id": "ben-liu", "kind": "people", "title": "Ben Liu", "match": {"github": "benliu"}}
    data.setdefault("subjects", []).append(ben)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": ["Ben Liu"]}
    ben["state"] = "archived"   # archived, github still filled: no longer proposed
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": ["Ben Liu"]}
    here = spaceyaml.load(project).data
    assert here["logins"] == {} and gate._space_logins(here, "Ben Liu") == set()
    ben.pop("state")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": ["Ben Liu"]}
    ben["match"] = {}   # cleared
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": ["Ben Liu"]}
    assert spaceyaml.load(project).data["logins"] == {}


# ----- round 5 repair: a crew login comes from every active people row of that name in the brain ---
def test_logins_upkeep_reads_every_active_namesake_and_no_project_row(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse",
             crew=["Ada Okafor", "Ben Liu"], online=False)
    project = tmp_path / "lighthouse"
    path = brain / "_meta" / "subjects.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # someone of the same name who left, above the active row: it neither hides nor adds a login
    data.setdefault("subjects", []).extend([
        {"id": "ben-liu-2024", "kind": "people", "title": "Ben Liu", "state": "archived",
         "match": {"github": "benliu-old"}},
        {"id": "ben-liu", "kind": "people", "title": "Ben Liu", "match": {"github": "benliu"}}])
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": ["Ben Liu"]}
    here = spaceyaml.load(project).data
    assert here["logins"] == {"Ben Liu": "benliu"} and gate._space_logins(here, "Ben Liu") == {"benliu"}
    # an archived namesake inserted first later on changes nothing either
    data["subjects"].insert(0, {"id": "ben-liu-old", "kind": "people", "title": "Ben Liu", "state": "archived"})
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": []}
    # a crew writer's project row named like Ben, or holding his id, never takes his login out
    local = project / "_meta" / "subjects.yaml"
    rows = (yaml.safe_load(local.read_text(encoding="utf-8")) if local.exists() else None) or {}
    rows.setdefault("subjects", []).extend([
        {"id": "notes-ben", "kind": "topics", "title": "Notes", "aliases": ["Ben Liu"]},
        {"id": "ben-liu", "kind": "topics", "title": "Ben Liu"}])
    local.write_text(yaml.safe_dump(rows), encoding="utf-8")
    assert mint.logins_upkeep(project) == {"changed": []}
    assert spaceyaml.load(project).data["logins"] == {"Ben Liu": "benliu"}
