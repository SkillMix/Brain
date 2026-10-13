"""onboard: the offline company case on the fixture company, detect, interview, check."""
import hashlib
from pathlib import Path

import pytest
import subprocess

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

    def content(root):  # the check's one write: the owner steps it lists on Needs you
        return {k: v for k, v in tree_hashes(root).items()
                if not k.endswith("_inbound/needs-you/onboard-owner.yaml")}

    before = content(target)
    first = onboard.check(target / "brain")
    after_first = tree_hashes(target)
    second = onboard.check(target / "brain")
    assert [s.row() for s in first] == [s.row() for s in second]
    assert any(s.name.startswith("space.yaml") and s.done for s in first)
    assert tree_hashes(target) == after_first
    assert content(target) == before
    ada = onboard.check(target / "ada")
    assert any("mount brain" in s.name and s.done for s in ada)
    assert content(target) == before


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
    # the one sentence the colleague says, in the invitation and on the owner's item
    assert answers["say"].startswith("Onboard me to lantern-org: https://github.com/")
    assert answers["say"].endswith("with my invitation lantern-org/brain:ben")
    assert answers["say"] in items[0]["text"]
    # no host login known: access cannot be checked, so the owner gets the access item anyway
    assert "login" not in answers
    access = next(i for i in items if i["id"] == "invite-ben-access")
    assert "host login" in access["text"] and "team" in access["recommendation"]
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


def _access_world(levels: dict, calls: list):
    """A fake host that answers only permission reads: `levels` maps a repository to the
    colleague's permission on it; every call is recorded."""
    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        repo = argv[2].split("repos/")[1].split("/collaborators/")[0] if len(argv) > 2 else ""
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/permission") and repo in levels:
            return subprocess.CompletedProcess(argv, 0, levels[repo] + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "not found")
    return fake


def _invited_brain(tmp_path) -> Path:
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=[
        {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-lw"}}])
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {"case": "company", "repo": "lantern-org/brain"})
    onboard.write_yaml(brain / "_meta" / "state" / "template-version.yaml", {"repo": "lantern-org/brain-template"})
    return brain


def test_an_invitation_checks_the_colleagues_read_access_and_asks_the_owner_for_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", _access_world(
        {"lantern-org/brain-template": "read", "lantern-org/brain": "none"}, calls))
    brain = _invited_brain(tmp_path)
    assert registry.Registry(brain, spaceyaml.load(brain)).check() == []   # a host login is a match key
    answers = yaml.safe_load(onboard.invite(brain, "ben").read_text(encoding="utf-8"))
    assert answers["login"] == "ben-lw"
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "invitations.yaml").read_text(encoding="utf-8"))["items"]
    by_id = {i["id"]: i for i in items}
    assert "works once" in by_id["invite-ben"]["text"]
    access = by_id["invite-ben-access"]
    assert "lantern-org/brain" in access["text"] and "lantern-org/brain-template" not in access["text"]
    rec = access["recommendation"]
    assert "team" in rec and "lantern-org/brain-template" in rec and "lantern-org/brain" in rec and "Ben Liu" in rec
    for repo in ("lantern-org/brain-template", "lantern-org/brain"):
        assert f"gh api repos/{repo}/collaborators/ben-lw/permission" in access["check"]
    # reads only: an agent never grants access
    assert calls and all(c[:2] == ["gh", "api"] and c[2].endswith("/permission") and "-X" not in c
                         and "--method" not in c for c in calls)


def test_the_access_item_leaves_once_the_colleague_can_read_both(tmp_path, monkeypatch):
    levels = {"lantern-org/brain-template": "none", "lantern-org/brain": "none"}
    monkeypatch.setattr(onboard, "run_cmd", _access_world(levels, []))
    brain = _invited_brain(tmp_path)
    onboard.invite(brain, "ben")
    path = brain / "_inbound" / "needs-you" / "invitations.yaml"
    assert "invite-ben-access" in [i["id"] for i in yaml.safe_load(path.read_text(encoding="utf-8"))["items"]]
    levels.update({"lantern-org/brain-template": "read", "lantern-org/brain": "write"})
    onboard.invite(brain, "ben")
    assert [i["id"] for i in yaml.safe_load(path.read_text(encoding="utf-8"))["items"]] == ["invite-ben"]


def test_an_offline_space_is_made_under_home_when_given(tmp_path):
    src = make_space(tmp_path / "src", profile="personal")
    given = onboard.create_space(onboard.Report("person"), src, "harbor", "harbor", True, None,
                                 tmp_path / "home", None, False)
    assert given == (tmp_path / "home" / "harbor", None) and (tmp_path / "home" / "harbor" / "space.yaml").exists()
    default = onboard.create_space(onboard.Report("person"), src, "beacon", "beacon", True, None, None, None, False)
    assert default == (tmp_path / "beacon", None) and (tmp_path / "beacon" / "space.yaml").exists()


def test_copy_template_never_copies_mounted_spaces(tmp_path):
    src = make_space(tmp_path / "live", profile="project")
    for rel in ("brain/space.yaml", "brain/knowledge/secret.md", "mounts/other/space.yaml"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("mounted\n", encoding="utf-8")
    (src / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (src / "_meta" / "tools" / "x.py").write_text("print(1)\n", encoding="utf-8")
    dst = tmp_path / "new"
    assert onboard.copy_template(src, dst)
    assert (dst / "_meta" / "tools" / "x.py").exists()
    assert not (dst / "brain").exists() and not (dst / "mounts").exists()


def test_the_workspace_is_allowed_once_and_offboard_takes_it_back(tmp_path):
    import json
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"additionalDirectories": ["D:/other"]}, "x": 1}),
                        encoding="utf-8")
    ws = tmp_path / "ada"
    ws.mkdir()
    assert onboard.allow_directory(settings, ws) is True
    assert onboard.allow_directory(settings, ws) is False   # once
    assert onboard.directory_allowed(settings, ws)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["x"] == 1 and "D:/other" in data["permissions"]["additionalDirectories"]
    assert onboard.disallow_directory(settings, ws) is True
    assert not onboard.directory_allowed(settings, ws)
    assert json.loads(settings.read_text(encoding="utf-8"))["permissions"]["additionalDirectories"] == ["D:/other"]
