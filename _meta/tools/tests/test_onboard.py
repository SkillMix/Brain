"""onboard: the offline company case on the fixture company, detect, interview, check."""
import hashlib
from pathlib import Path

import pytest
import yaml

import onboard
import registry
import spaceyaml
import validate
from conftest import make_space

TEMPLATE = Path(__file__).resolve().parents[3]
FIXTURE_COMPANY = TEMPLATE / "_meta" / "tests" / "fixture" / "company.yaml"

MINIMAL = {
    "company": "Lantern Works",
    "top_owner": {"name": "Ada Okafor", "slug": "ada", "email": "ada@lantern.example"},
    "people": [
        {"name": "Ada Okafor", "slug": "ada", "email": "ada@lantern.example", "parent": "brain",
         "domain": "delivery"},
        {"name": "Ben Liu", "slug": "ben", "email": "ben@lantern.example", "parent": "brain",
         "domain": "delivery"},
        {"name": "Chloe Martin", "slug": "chloe", "email": "chloe@lantern.example", "parent": "brain",
         "domain": "sales"},
    ],
    "domains": [{"id": "sales", "owner": "chloe"}, {"id": "delivery", "owner": "ben"}],
    "clients": [{"id": "tidewater-ferries", "title": "Tidewater Ferries"},
                {"id": "alder-clinic", "title": "Alder Clinic"}],
    "projects": [
        {"id": "harbor", "client": "tidewater-ferries", "crew": ["ada", "ben"],
         "match": {"label": "Harbor", "channel": "#harbor"}},
        {"id": "beacon", "client": "alder-clinic", "crew": ["ben", "chloe"], "match": {"label": "Beacon"}},
    ],
    "standing_choices": {"ada": [{"destination": "harbor", "kind": "mail"}]},
    "time_scale": 1440,
    "mode": "fixture",
}


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def never(*args, **kwargs):
    raise AssertionError(f"run_cmd was called offline: {args}")


def company_yaml(tmp_path) -> Path:
    if FIXTURE_COMPANY.exists():
        return FIXTURE_COMPANY
    path = tmp_path / "company.yaml"
    path.write_text(yaml.safe_dump(MINIMAL, sort_keys=False), encoding="utf-8")
    return path


def tree_hashes(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture
def lantern(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    target = tmp_path / "lw"
    report = onboard.fixture_spaces(target, company_yaml(tmp_path))
    return target, report


def test_fixture_company_offline_builds_six_valid_spaces(lantern):
    target, report = lantern
    assert report.case == "company"
    assert not report.failed(), [s.row() for s in report.failed()]
    for name in ("brain", "ada", "ben", "chloe", "harbor", "beacon"):
        assert (target / name / "space.yaml").exists(), name
        assert errors(target / name) == [], name
    brain = spaceyaml.load(target / "brain")
    assert brain.profile == "brain"
    assert brain.data.get("kinds") == spaceyaml.DEFAULTS["kinds"]
    assert brain.mode == "fixture" and brain.time_scale == 1440
    reg = registry.Registry(target / "brain", brain)
    assert reg.get("sales").kind == "domains" and reg.get("sales").owner == "Chloe Martin"
    assert reg.get("delivery").owner == "Ben Liu"
    assert reg.get("harbor").home == "harbor" and reg.get("harbor").part_of == "tidewater-ferries"
    assert reg.get("ada").kind == "people"
    for slug in ("ada", "ben", "chloe"):
        space = spaceyaml.load(target / slug)
        assert space.profile == "personal" and space.parent == "brain"
        assert space.mount_root("brain") is not None
        assert space.kinds == brain.kinds
        assert space.mode == "fixture" and space.time_scale == 1440
        assert (target / slug / "work").is_dir() and (target / slug / "journal").is_dir()
    ada = spaceyaml.load(target / "ada")
    assert ada.standing_choices == [{"destination": "harbor", "kind": "mail"}]
    assert ada.owner == "Ada Okafor" and ada.audience == ["Ada Okafor"]
    for project in ("harbor", "beacon"):
        space = spaceyaml.load(target / project)
        assert space.profile == "project"
        assert not (target / project / "work").exists()
        assert not (target / project / "journal").exists()
    assert spaceyaml.load(target / "harbor").audience == ["Ada Okafor", "Ben Liu"]
    assert not (target / "brain" / "work").exists()
    assert not (target / "brain" / ".github" / "template-marker").exists()
    assert (target / "brain" / "identity" / "charter.md").exists()
    assert report.user_instructions is not None
    assert onboard.instruction_present(report.user_instructions, target / "ada")
    assert (target / "brain" / "_meta" / "state" / "onboard.yaml").exists()
    assert (target / "brain" / "_meta" / "state" / "sync-task.txt").exists()


def test_check_twice_is_identical_and_changes_nothing(lantern):
    target, _ = lantern
    before = tree_hashes(target)
    first = onboard.check(target / "brain")
    second = onboard.check(target / "brain")
    assert [s.row() for s in first] == [s.row() for s in second]
    assert any(s.name.startswith("space.yaml") and s.done for s in first)
    assert tree_hashes(target) == before
    ada = onboard.check(target / "ada")
    assert any("mount brain" in s.name and s.done for s in ada)
    assert tree_hashes(target) == before


def test_detect_cases(tmp_path):
    template = make_space(tmp_path / "t", profile="template")
    assert onboard.detect(template) == "company"
    assert onboard.detect(template, brain="https://example.test/org/brain") == "person"
    assert onboard.detect(template, seed="work/projects/x/y") == "project"
    assert onboard.detect(make_space(tmp_path / "p")) == "configured"
    assert onboard.detect(tmp_path / "missing") == "company"


def test_interview_defaults_and_records_skips():
    out = onboard.interview({"company": "Lantern Works"}, interactive=False, case="company")
    assert out["company"] == "Lantern Works"
    assert onboard.parse_list(out["domains"]) == ["sales", "delivery", "operations"]
    assert out["never_leaves"] == spaceyaml.DEFAULTS["never_leaves"]
    skipped = {q["key"] for q in out["_skipped"]}
    assert "company" not in skipped and "domains" in skipped and "name" not in skipped
    person = onboard.interview({}, interactive=False, case="person")
    assert person["parent"] == "brain" and "company" not in person


def test_interview_interactive_accepts_default_and_skip(monkeypatch):
    replies = iter(["Harbor Lights", "", "", "skip", "equity, medical"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies, ""))
    out = onboard.interview({}, interactive=True, case="company")
    assert out["company"] == "Harbor Lights"
    skipped = {q["key"] for q in out["_skipped"]}
    assert out["owner"] and "owner" not in skipped          # empty input accepts the default
    assert out["owner_email"] == "" and "owner_email" not in skipped
    assert "domains" in skipped and onboard.parse_list(out["domains"]) == ["sales", "delivery", "operations"]
    assert out["never_leaves"] == ["equity", "medical"]
    assert "feeds" not in skipped and onboard.parse_list(out["feeds"]) == []


def test_skipped_questions_become_needs_you_items(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    root = tmp_path / "co"
    onboard.copy_template(TEMPLATE, root)
    answers = onboard.interview({"company": "Lantern Works", "owner": "Ada Okafor"},
                                interactive=False, case="company")
    report = onboard.run(root, answers, offline=True)
    assert not report.failed(), [s.row() for s in report.failed()]
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "onboard.yaml").read_text())["items"]
    ids = {i["id"] for i in items}
    assert "onboard-never_leaves" in ids and "onboard-company" not in ids
    for item in items:
        assert item["kind"] == "question" and item["payload"] == "space.yaml"
        assert set(item) >= {"id", "kind", "text", "recommendation", "default", "deadline",
                             "depends", "payload", "fingerprint"}
    assert spaceyaml.load(root).name == "lantern-works"
    workspace = tmp_path / "ada-okafor"
    assert spaceyaml.load(workspace).profile == "personal"
    assert errors(root) == [] and errors(workspace) == []
    assert not (root / ".github" / "template-marker").exists()
    # a second run keeps what is right and adds nothing twice
    before = (root / "space.yaml").read_text(encoding="utf-8")
    again = onboard.run(root, answers, offline=True)
    assert not again.failed()
    assert (root / "space.yaml").read_text(encoding="utf-8") == before
    reg = registry.Registry(root)
    assert sorted(s.id for s in reg.all()) == ["ada-okafor", "delivery", "operations", "sales"]
    assert all(reg.get(d).owner == "Ada Okafor" for d in ("sales", "delivery", "operations"))


def test_instruction_block_is_idempotent(tmp_path):
    path = tmp_path / "home" / ".claude" / "CLAUDE.md"
    workspace = tmp_path / "ada"
    assert onboard.install_instruction(path, workspace) is True
    assert onboard.install_instruction(path, workspace) is False
    text = path.read_text(encoding="utf-8")
    assert text.count(onboard.MARK_OPEN) == 1 and text.count(onboard.MARK_CLOSE) == 1
    assert onboard.instruction_present(path, workspace)
    other = tmp_path / "ben"
    onboard.install_instruction(path, other)
    text = path.read_text(encoding="utf-8")
    assert text.count(onboard.MARK_OPEN) == 1 and onboard.instruction_present(path, other)
    assert not onboard.instruction_present(path, workspace)
    assert onboard.remove_block(text).strip() == ""


def test_child_space_state_names_only_itself(tmp_path):
    """The onboarding root keeps the whole company report; every other space keeps only the
    steps about itself, so another client's name never lands in a project space's state."""
    brain = make_space(tmp_path / "brain", profile="brain")
    harbor = make_space(tmp_path / "harbor", profile="project")
    report = onboard.Report("founder", spaces={"brain": brain, "harbor": harbor,
                                                "beacon": tmp_path / "beacon"})
    report.add("create harbor", True, "gh repo view acme/harbor", "cloned into harbor")
    report.add("create beacon", True, "gh repo view acme/beacon", "cloned into beacon")
    report.add("registry", True, "python _meta/tools/registry.py . check")
    report.add("validate harbor", True, "python _meta/tools/validate.py harbor", "clean")
    report.add("merge settings acme/harbor", True, "gh api repos/acme/harbor", "ok")
    onboard.write_state(harbor, "person", True, tmp_path / "ui.md", harbor, None, "acme/harbor",
                        report, full=False)
    text = (harbor / "_meta" / "state" / "onboard.yaml").read_text(encoding="utf-8")
    state = yaml.safe_load(text)
    assert "beacon" not in text and "brain" not in text.replace("brain: ", "")
    assert set(state["spaces"]) == {"harbor"}
    assert [s["name"] for s in state["steps"]] == ["create harbor", "validate harbor",
                                                   "merge settings acme/harbor"]
    onboard.write_state(brain, "founder", True, tmp_path / "ui.md", None, None, "acme/brain",
                        report, full=True)
    full = (brain / "_meta" / "state" / "onboard.yaml").read_text(encoding="utf-8")
    assert "beacon" in full and "registry" in full


def _gh_world(existing: set, spaces: set):
    """A fake outside world: `existing` repositories exist, `spaces` among them already hold a space.yaml."""
    import subprocess

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        full = next((a for a in argv if "/" in a and not a.startswith("-")), "")
        if argv[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(argv, 0 if full in existing else 1, "", "" if full in existing else "not found")
        if argv[:2] == ["gh", "api"] and "contents/space.yaml" in " ".join(argv):
            repo = argv[2].split("repos/")[1].split("/contents")[0]
            return subprocess.CompletedProcess(argv, 0 if repo in spaces else 1, "", "")
        if argv[:3] == ["gh", "repo", "clone"]:
            dst = Path(cwd) / argv[3].split("/")[-1] if len(argv) < 5 else Path(argv[4])
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "space.yaml").write_text("shape: 1\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["gh", "repo", "create"]:
            if full in existing:
                return subprocess.CompletedProcess(argv, 1, "", "GraphQL: Could not clone: Name already exists on this account (cloneTemplateRepository)")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return fake


def test_name_collision_is_an_owner_item_with_a_recommendation(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", _gh_world({"acme/harbor"}, set()))
    report = onboard.Report("company")
    dst, full = onboard.create_space(report, tmp_path / "root", "harbor", "harbor", False, "acme",
                                     tmp_path / "home", "acme/space-template", False)
    assert dst is None and full == "acme/harbor"
    step = report.steps[-1]
    assert step.needs_owner and not step.done
    assert "harbor-space" in step.result and "already exists" in step.result
    assert not report.failed()


def test_existing_space_repository_is_adopted(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", _gh_world({"acme/harbor"}, {"acme/harbor"}))
    report = onboard.Report("company")
    dst, full = onboard.create_space(report, tmp_path / "root", "harbor", "harbor", False, "acme",
                                     tmp_path / "home", "acme/space-template", False)
    assert dst == tmp_path / "home" / "harbor" and (dst / "space.yaml").exists()
    assert report.steps[-1].done and "adopted" in report.steps[-1].result


def test_sync_task_runs_the_release_leg(tmp_path):
    root = make_space(tmp_path / "ada")
    report = onboard.Report("person")
    rec = onboard.register_sync(report, root, "ada", 15, offline=True, dry_run=False)
    text = (root / "_meta" / "state" / "sync-task.txt").read_text(encoding="utf-8")
    assert "--release-leg" in text and "--release-leg" in " ".join(rec["create"]) + (rec["line"] or "")


def test_check_reruns_owner_read_backs(tmp_path):
    """An owner step whose read-back now passes (the feed file exists) is done on the next
    `--check`; one whose read-back still fails stays on the owner's list."""
    root = make_space(tmp_path / "ada")
    feed = root / "_meta" / "state" / "feed" / "mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text('{"id": "m-1"}\n', encoding="utf-8")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "offline": True, "steps": [
            {"name": "connect mail", "done": False, "check": f"test -s {feed}",
             "result": "the owner signs in", "needs_owner": True},
            {"name": "connect chat", "done": False, "check": f"test -s {feed.with_name('chat.jsonl')}",
             "result": "the owner signs in", "needs_owner": True}]})
    by_name = {s.name: s for s in onboard.check(root)}
    assert by_name["connect mail"].done and not by_name["connect mail"].needs_owner
    assert not by_name["connect chat"].done and by_name["connect chat"].needs_owner
    assert onboard.readback("something unknown") is None


def test_check_prunes_offboarded_spaces_from_the_root_state(tmp_path):
    """The onboarding root forgets a space that was offboarded (or whose folder is gone):
    its row and the steps naming only it move to `offboarded`, and are never re-checked."""
    root = make_space(tmp_path / "brain", profile="brain")
    gone = make_space(tmp_path / "quarry", profile="project")
    onboard.write_yaml(gone / "_meta" / "state" / "offboard.yaml", {"space": "quarry"})
    kept = make_space(tmp_path / "harbor", profile="project")
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "company", "offline": True,
        "spaces": {"brain": str(root), "harbor": str(kept), "quarry": str(gone),
                   "vanished": str(tmp_path / "vanished")},
        "steps": [{"name": "create quarry", "done": True, "check": f"test -d {gone}", "result": "ok"},
                  {"name": "crew team quarry", "done": False, "check": "gh api orgs/x/teams/quarry",
                   "result": "owner", "needs_owner": True},
                  {"name": "create harbor", "done": True, "check": f"test -d {kept}", "result": "ok"}]})
    steps = onboard.check(root)
    state = yaml.safe_load((root / "_meta" / "state" / "onboard.yaml").read_text(encoding="utf-8"))
    assert set(state["spaces"]) == {"brain", "harbor"}
    assert set(state["offboarded"]) == {"quarry", "vanished"}
    assert [r["name"] for r in state["steps"]] == ["create harbor"]
    assert not any("quarry" in s.name for s in steps)
    before = (root / "_meta" / "state" / "onboard.yaml").read_text(encoding="utf-8")
    onboard.check(root)
    assert (root / "_meta" / "state" / "onboard.yaml").read_text(encoding="utf-8") == before


def test_go_live_switches_a_shadow_space_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern")
    root = make_space(tmp_path / "ws", profile="personal", mode="shadow", time_scale=48,
                      mounts=[{"name": "brain", "repo": "https://example.test/org/brain-next",
                               "path": str(brain)},
                              {"name": "harbor", "repo": "https://example.test/org/harbor",
                               "path": "mounts/harbor"}])
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "offline": True, "workspace": str(tmp_path / "shadow" / "ws"),
        "spaces": {"ws": str(tmp_path / "shadow" / "ws")}, "steps": []})
    answers = {"mounts": {"brain": "https://example.test/org/brain",
                          "harbor": "https://example.test/org/harbor-space"}}
    report = onboard.go_live(root, answers, offline=True)
    assert not report.failed(), [s.row() for s in report.steps]
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    assert data["mode"] == "live" and "time_scale" not in data
    assert {m["name"]: m["repo"] for m in data["mounts"]} == answers["mounts"]
    state = yaml.safe_load((root / "_meta" / "state" / "onboard.yaml").read_text(encoding="utf-8"))
    assert state["workspace"] == str(root) and state["spaces"] == {"ws": str(root)}
    assert errors(root) == []
    before = tree_hashes(root)
    again = onboard.go_live(root, answers, offline=True)
    assert not again.failed()
    assert tree_hashes(root) == before


def test_check_lists_open_owner_steps_on_needs_you_until_they_pass(tmp_path):
    root = make_space(tmp_path / "ada")
    token = tmp_path / "token-proof.txt"
    onboard.write_yaml(root / "_meta" / "state" / "onboard.yaml", {
        "case": "person", "offline": True, "steps": [
            {"name": "add the mount token", "done": False, "check": f"test -s {token}",
             "result": "the owner adds the repository secret", "needs_owner": True}]})
    onboard.check(root)
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "onboard-owner.yaml").read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in items] == ["onboard-add-the-mount-token"] and items[0]["check"].startswith("test -s")
    token.write_text("done\n", encoding="utf-8")
    onboard.check(root)
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "onboard-owner.yaml").read_text(encoding="utf-8"))["items"]
    assert items == []


def test_an_invitation_carries_the_crew_projects_and_the_colleague_onboards_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=[
        {"id": "ben", "kind": "people", "title": "Ben Liu"}])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", audience=["Ada Okafor", "Ben Liu"])
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {
        "case": "company", "repo": "lantern-org/brain", "spaces": {"lantern": str(brain), "harbor": str(harbor)}})
    target = onboard.invite(brain, "ben")
    answers = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert answers["name"] == "Ben Liu" and answers["brain"] == "https://github.com/lantern-org/brain"
    assert [p["name"] for p in answers["crew_projects"]] == ["harbor"]
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "invitations.yaml").read_text(encoding="utf-8"))["items"]
    assert items[0]["id"] == "invite-ben" and items[0]["check"].startswith("gh repo view")
    # on the colleague's machine (offline here): a template copy, the answers, the brain
    ws = tmp_path / "ben-workspace"
    onboard.copy_template(onboard.TEMPLATE_ROOT, ws)
    answers["crew_projects"][0]["path"] = str(harbor)
    report = onboard.run(ws, answers, offline=True, brain=str(brain), home=tmp_path,
                         user_instructions=str(tmp_path / "CLAUDE.md"))
    assert not report.failed(), [s.row() for s in report.failed()]
    space = spaceyaml.load(ws)
    assert space.profile == "personal" and space.owner == "Ben Liu"
    assert {m["name"] for m in space.mounts} == {"brain", "harbor"} and space.mount_root("harbor") is not None
    assert errors(ws) == []
