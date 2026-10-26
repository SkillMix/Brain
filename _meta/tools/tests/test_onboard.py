"""onboard: the offline company case on the fixture company, detect, interview, check."""
import hashlib
import os
import sys
from pathlib import Path

import pytest
import subprocess

import yaml

import link
import onboard
import pages
import registry
import spaceyaml
import validate
from conftest import NOW, make_space

TEMPLATE = Path(__file__).resolve().parents[3]
FIXTURE_COMPANY = TEMPLATE / "_meta" / "tests" / "fixture" / "company.yaml"
# Template sync installs these tests in every space; a test that copies the whole repository
# as the template runs in the template only.
template_only = pytest.mark.skipif(not (TEMPLATE / ".github" / "template-marker").exists(),
                                   reason="copies the repository as the template")

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
    replies = iter(["Harbor Lights", "", "", "skip", "anyone's equity, ownership or pay; medical matters"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies, ""))
    out = onboard.interview({}, interactive=True, case="company")
    assert out["company"] == "Harbor Lights"
    skipped = {q["key"] for q in out["_skipped"]}
    assert out["owner"] and "owner" not in skipped          # empty input accepts the default
    assert out["owner_email"] == "" and "owner_email" not in skipped
    assert "domains" in skipped and onboard.parse_list(out["domains"]) == ["sales", "delivery", "operations"]
    assert out["never_leaves"] == ["anyone's equity, ownership or pay", "medical matters"]
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



@template_only
def test_a_repository_made_from_the_template_gets_every_folder(tmp_path, monkeypatch):
    """Online a new space starts as a full copy of the template, which ships no zone folders and
    no index, log, identity or live pages: onboard makes each one, and git keeps every folder."""
    import shutil
    monkeypatch.setattr(onboard, "run_cmd", never)
    root = tmp_path / "co"
    shutil.copytree(TEMPLATE, root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    assert (root / ".github" / "template-marker").exists()
    answers = onboard.interview({"company": "Lantern Works", "owner": "Ada Okafor"},
                                interactive=False, case="company")
    report = onboard.run(root, answers, offline=True)
    assert not report.failed(), [s.row() for s in report.failed()]
    for space in (root, tmp_path / "ada-okafor"):
        for sub in onboard.SUBDIRS:
            assert any((space / sub).iterdir()), f"{space.name}/{sub} is empty: git would drop it"
        for rel in ("index.md", "log.md", "knowledge/index.md", "live/now.md", "live/health.md"):
            assert (space / rel).exists(), f"{space.name}/{rel}"
        assert errors(space) == []
    assert (root / "identity" / "charter.md").exists() and (root / "identity" / "spine.md").exists()
    assert not [rel for rel in onboard.PROJECT_FILES if (root / rel).exists()]


def test_the_full_copy_is_skipped_in_a_space(tmp_path):
    """Template sync installs these tests in every space, which has no template marker and holds
    its mounts: copied whole there, the test above failed on the marker and copied the brain too."""
    import shutil
    space = tmp_path / "space"
    shutil.copytree(TEMPLATE / "_meta" / "tools", space / "_meta" / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    test = space / "_meta" / "tools" / "tests" / "test_onboard.py"
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           f"{test}::test_a_repository_made_from_the_template_gets_every_folder"],
                          cwd=str(space), capture_output=True, text=True,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert done.returncode == 0 and "1 skipped" in done.stdout, done.stdout[-2000:]


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
    assert not onboard.instruction_present(path, tmp_path / "be")   # a path that only starts the same is not it
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
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: subprocess.CompletedProcess(argv, 1, "", ""))
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
    assert items[0]["id"] == "invite-ben" and "ben-workspace/contents/_meta/state/runs.jsonl" in items[0]["check"]
    # the one sentence the colleague says, in the invitation and on the owner's item
    assert answers["say"].startswith("Onboard me to Lantern: https://github.com/")   # the company, not the host owner
    assert answers["say"].endswith("with my invitation lantern-org/brain:ben")
    assert answers["say"] in items[0]["text"]
    # no host login known: access cannot be checked, so the owner gets the access item anyway
    assert "login" not in answers
    access = next(i for i in items if i["id"] == "invite-ben-access")
    assert "host login" in access["text"] and "team" in access["recommendation"]
    # on the colleague's machine (offline here): a template copy, the answers, the brain
    monkeypatch.setattr(onboard, "run_cmd", never)
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


def _access_world(levels: dict, calls: list, reads: dict | None = None):
    """A fake host that answers permission reads (`levels` maps a repository to the colleague's
    permission on it) and the reads in `reads` (the command after `gh api` -> its output); every
    call is recorded."""
    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if " ".join(argv[2:]) in (reads or {}):
            return subprocess.CompletedProcess(argv, 0, reads[" ".join(argv[2:])] + "\n", "")
        repo = argv[2].split("repos/")[1].split("/collaborators/")[0] if len(argv) > 2 and "repos/" in argv[2] else ""
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
    # reads only (permissions, visibility, admin rights, the account's kind): an agent never grants access
    assert calls and all(c[:2] == ["gh", "api"] and "-X" not in c and "--method" not in c for c in calls)


def test_on_a_personal_account_the_access_item_names_a_collaborator_and_the_templates_owner(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", _access_world({}, calls, reads={
        "repos/lantern-org/brain -q .permissions.admin": "true", "users/lantern-org -q .type": "User",
        "repos/lantern-org/brain-template -q .permissions.admin": "false"}))
    brain = _invited_brain(tmp_path)
    onboard.invite(brain, "ben")
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "invitations.yaml").read_text(encoding="utf-8"))["items"]
    rec = next(i for i in items if i["id"] == "invite-ben-access")["recommendation"]
    assert "company-wide team" not in rec
    assert "add Ben Liu as a collaborator on lantern-org/brain: `gh api -X PUT " \
           "repos/lantern-org/brain/collaborators/ben-lw`" in rec
    assert "only lantern-org, who owns lantern-org/brain-template, can give Ben Liu read access" in rec
    assert all("-X" not in c for c in calls)   # the command is the owner's to run


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


def test_an_invitation_reads_back_done_only_once_the_workspace_has_synced(tmp_path, monkeypatch):
    # a setup that stopped right after its first push: the workspace repository exists, but no
    # sync ever landed a run line, so the invitation stays on the inviter's Needs you
    landed = []
    world = _access_world({"lantern-org/brain-template": "read", "lantern-org/brain": "read"}, [])

    def fake(argv, dry_run=False, cwd=None):
        line = " ".join(str(a) for a in argv)
        if line.startswith("gh repo view lantern-org/ben-workspace"):
            return subprocess.CompletedProcess(argv, 0, "name: lantern-org/ben-workspace\n", "")
        if "repos/lantern-org/ben-workspace/contents/_meta/state/runs.jsonl" in line and landed:
            return subprocess.CompletedProcess(argv, 0, "_meta/state/runs.jsonl\n", "")
        return world(argv, dry_run, cwd)
    monkeypatch.setattr(onboard, "run_cmd", fake)
    brain = _invited_brain(tmp_path)
    onboard.invite(brain, "ben")
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "invitations.yaml").read_text(encoding="utf-8"))["items"]
    check = next(i for i in items if i["id"] == "invite-ben")["check"]
    assert "lantern-org/ben-workspace" in check
    assert onboard.readback(check) is False   # the repository alone is not a working setup
    landed.append(True)                       # the first sync landed its run line
    assert onboard.readback(check) is True


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



PROJECT_FILES = ("CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md",
                 ".github/ISSUE_TEMPLATE/bug.md", ".github/PULL_REQUEST_TEMPLATE.md", "requirements.txt",
                 "requirements-dev.txt")


def test_a_new_space_never_presents_the_open_source_projects_files(tmp_path):
    """Offline (a copy) and online (a repository made from the template, marker still in it): the
    project's contributing guide, security policy, changelog and issue forms stay with the template."""
    src = make_space(tmp_path / "tpl", profile="template")
    for rel in PROJECT_FILES + ("README.md", "LICENSE", ".github/workflows/checks.yml"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("x\n", encoding="utf-8")
    dst = tmp_path / "offline"
    assert onboard.copy_template(src, dst)
    assert not [rel for rel in PROJECT_FILES if (dst / rel).exists()]
    assert not (dst / ".github" / "ISSUE_TEMPLATE").exists()
    assert (dst / "README.md").exists() and (dst / "LICENSE").exists()
    assert (dst / ".github" / "workflows" / "checks.yml").exists()
    onboard.remove_forbidden(onboard.Report("company"), src, "brain")   # the online clone
    assert not [rel for rel in PROJECT_FILES if (src / rel).exists()]
    assert not (src / ".github" / "ISSUE_TEMPLATE").exists() and not (src / ".github" / "template-marker").exists()
    assert (src / "README.md").exists() and (src / ".github" / "workflows" / "checks.yml").exists()


def test_a_space_keeps_its_own_files_of_those_names(tmp_path):
    """Only a fresh copy of the template drops them: a configured space's own changelog stays."""
    space = make_space(tmp_path / "ada", profile="project")
    (space / "CHANGELOG.md").write_text("# Our changes\n", encoding="utf-8")
    onboard.remove_forbidden(onboard.Report("person"), space, "project")
    assert (space / "CHANGELOG.md").exists()

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


def test_the_personal_instruction_carries_the_working_rules_and_the_planning_pointer(tmp_path):
    """The rules the old workspace kept by hand travel with the instruction: the friction log,
    default-don't-park, queueing tracker issues, owner notices, and, when space.yaml names
    one, the person's planning instructions."""
    import yaml
    root = make_space(tmp_path / "ada")
    block = onboard.instruction_block(root)
    assert "_meta/friction-log.md" in block and "conservative" in block
    assert "tracker.py" in block and "owner-email" in block
    assert "plan or review their day" not in block
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data["planning"] = "personal/planning/checkin-instructions.md"
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    block = onboard.instruction_block(root)
    assert "plan or review their day" in block and "personal/planning/checkin-instructions.md" in block


def test_a_new_live_workspace_starts_routing_live(tmp_path):
    """The owner chose no review period (2026-10-14): the router's tiers apply from day one; the
    `routing.review_until` setting stays available and is off unless someone sets it."""
    values = onboard.space_values({}, "ada", "Ada Okafor", "ada@lantern.example", ["Ada Okafor"], "brain")
    onboard.fill_space(tmp_path / "ada", "personal", **values)
    space = spaceyaml.load(tmp_path / "ada")
    assert space.routing_on and not [e for e in space.errors() if "routing" in e]
    assert space.review_until is None and "review_until" not in space.routing
    assert 89 <= (space.now() - space.routing_since).days <= 90
    fixture = dict(values, mode="fixture", time_scale=48)
    onboard.fill_space(tmp_path / "ben", "personal", **fixture)
    assert spaceyaml.load(tmp_path / "ben").routing_on is False, "the fixture keeps the identifier path"
    onboard.fill_space(tmp_path / "harbor", "project", **values)
    assert spaceyaml.load(tmp_path / "harbor").routing_on is False, "a project routes nothing of its own"


def test_a_public_template_leaves_only_the_brain_to_grant(tmp_path, monkeypatch):
    calls = []
    world = _access_world({"lantern-org/brain-template": "none", "lantern-org/brain": "none"}, calls)

    def fake(argv, dry_run=False, cwd=None):
        if [str(a) for a in argv] == ["gh", "api", "repos/lantern-org/brain-template", "-q", ".private"]:
            return subprocess.CompletedProcess(argv, 0, "false\n", "")
        return world(argv, dry_run, cwd)
    monkeypatch.setattr(onboard, "run_cmd", fake)
    brain = _invited_brain(tmp_path)
    onboard.invite(brain, "ben")
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "invitations.yaml").read_text(encoding="utf-8"))["items"]
    by_id = {i["id"]: i for i in items}
    assert by_id["invite-ben"]["text"].endswith("once they can read lantern-org/brain")
    access = by_id["invite-ben-access"]
    for key in ("text", "recommendation", "check"):
        assert "brain-template" not in access[key], key
    assert "lantern-org/brain" in access["recommendation"]


def test_inviting_someone_not_yet_registered_registers_them_or_says_how(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: subprocess.CompletedProcess(argv, 1, "", ""))
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern-works", subjects=[
        {"id": "harbor", "kind": "clients", "title": "Harbor"}])
    onboard.write_text(brain / "identity" / "charter.md", "---\ntitle: Charter\n---\n# Charter\n\n"
                       "This space is the brain of Lantern Works. Everyone in the company reads it.\n")
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {"case": "company", "repo": "lantern-org/brain"})
    assert onboard.main([str(brain), "--invite", "ravi"]) == 1
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0 and "--name" in out and "Traceback" not in out
    assert onboard.main([str(brain), "--invite", "harbor"]) == 1
    assert "not a person" in capsys.readouterr().out
    assert onboard.main([str(brain), "--invite", "ravi", "--name", "Ravi Mendes",
                         "--email", "ravi@lantern.example"]) == 0
    person = registry.Registry(brain, spaceyaml.load(brain)).get("ravi")
    assert person.kind == "people" and person.title == "Ravi Mendes"
    assert person.extra.get("email") == "ravi@lantern.example"
    answers = yaml.safe_load((brain / "_meta" / "state" / "invitations" / "ravi.yaml").read_text(encoding="utf-8"))
    assert answers["name"] == "Ravi Mendes" and answers["say"].startswith("Onboard me to Lantern Works:")
    assert registry.Registry(brain, spaceyaml.load(brain)).check() == []


# ----- the company profile: a cited draft, never over a signed page -------------------------------------
LINE = {"text": "Wayfinding and signage for ports and transit", "source": "site, About", "at": "2026-10-24"}


def test_the_profile_page_cites_every_line_and_skips_a_signed_page(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    onboard.write_profile(brain, {"offering": [{"text": "Wayfinding and signage for ports and transit", "source": "site, About", "at": "2026-10-24"}]}, NOW)
    text = (brain / "identity" / "profile.md").read_text(encoding="utf-8")
    assert "Wayfinding and signage for ports and transit (source: site, About, 2026-10-24)" in text
    fm, _ = pages.read(brain / "identity" / "profile.md")
    assert fm["status"] == "draft" and fm["type"] == "concept" and fm["generated"]["by"] == "onboard"
    # signed by the owner: never overwritten, whatever the research finds later
    signed = {**fm, "status": "stable", "signed_by": "Ada Okafor", "signed_at": "2026-10-25T08:00:00Z"}
    signed["fingerprint"] = pages.fingerprint("# Company profile\n\nSigned text.\n")
    pages.write(brain / "identity" / "profile.md", signed, "# Company profile\n\nSigned text.\n")
    before = (brain / "identity" / "profile.md").read_text(encoding="utf-8")
    onboard.write_profile(brain, {"offering": [{**LINE, "text": "Something new"}]}, NOW)
    assert (brain / "identity" / "profile.md").read_text(encoding="utf-8") == before
    assert [e for e in validate.validate(brain) if not e.startswith("warning:")] == []


def test_a_profile_page_that_someone_else_wrote_is_left_alone_too(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    pages.write(brain / "identity" / "profile.md", {"id": pages.new_id(), "type": "concept", "title": "Company profile",
                "status": "draft", "generated": pages.generated("ada", NOW)}, "# Company profile\n\nMine.\n")
    onboard.write_profile(brain, {"offering": [LINE]}, NOW)
    assert "Mine." in (brain / "identity" / "profile.md").read_text(encoding="utf-8")


def test_the_profile_lists_the_sections_in_order_one_cited_bullet_a_line_and_drops_what_has_no_source(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    profile = {"people": [{"text": "Ada Okafor, founder", "source": "file, Pitch deck.pdf", "at": "2026-08-01"}],
               "company": [{"text": "A signage studio", "source": "register, Entry", "at": "2026-10-01"}],
               "offering": [LINE, {"text": "No source for this", "source": "", "at": "2026-10-24"}, {"text": "", "source": "x", "at": "2026-10-24"}],
               "brand": [], "invented": [{"text": "Not a section", "source": "x", "at": "2026-10-24"}],
               "clients": [{"text": "A line with no date", "source": "documents, Brand book"}]}
    onboard.write_profile(brain, profile, NOW)
    text = (brain / "identity" / "profile.md").read_text(encoding="utf-8")
    bullets = [l for l in text.splitlines() if l.startswith("- ")]
    assert bullets == ["- A signage studio (source: register, Entry, 2026-10-01)",
                       f"- {LINE['text']} (source: site, About, 2026-10-24)",
                       "- Ada Okafor, founder (source: file, Pitch deck.pdf, 2026-08-01)",
                       "- A line with no date (source: documents, Brand book, 2026-10-12)"]   # the day it was drafted
    assert "No source for this" not in text and "Not a section" not in text
    headings = [l for l in text.splitlines() if l.startswith("## ")]
    assert len(headings) == 4 and "## Brand" not in text   # a section with no line has no heading


def test_the_profile_fills_what_we_do_in_the_spine_and_nothing_else_of_it(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    onboard.write_identity(brain, "Lantern Works", "Ada Okafor", [], [], NOW)
    before = (brain / "identity" / "spine.md").read_text(encoding="utf-8")
    assert "(to be confirmed by the owner)" in before
    onboard.write_profile(brain, {"offering": [LINE, {**LINE, "text": "Maps for clinics"}], "company": [LINE]}, NOW)
    fm, body = pages.read(brain / "identity" / "spine.md")
    assert fm["status"] == "draft" and "(to be confirmed by the owner)" not in body
    assert (f"## What we do\n\n- {LINE['text']} (source: site, About, 2026-10-24)\n"
            "- Maps for clinics (source: site, About, 2026-10-24)\n\n## What we never do") in body
    assert body.startswith("# Spine") and "- We never send, post, spend or delete" in body
    # new research later: the section is made again from the new lines, not added to
    onboard.write_profile(brain, {"offering": [{**LINE, "text": "Signs only"}]}, NOW)
    body = pages.read(brain / "identity" / "spine.md")[1]
    assert "Signs only" in body and "Maps for clinics" not in body and body.count("## What we do") == 1
    # no offering: the spine stays as it was
    onboard.write_identity(brain, "Lantern Works", "Ada Okafor", [], [], NOW)
    onboard.write_profile(brain, {"company": [LINE]}, NOW)
    assert (brain / "identity" / "spine.md").read_text(encoding="utf-8").count("(to be confirmed by the owner)") == 1


def test_a_signed_spine_is_never_filled(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    onboard.write_identity(brain, "Lantern Works", "Ada Okafor", [], [], NOW)
    fm, body = pages.read(brain / "identity" / "spine.md")
    pages.write(brain / "identity" / "spine.md", {**fm, "status": "stable", "signed_by": "Ada Okafor",
                "signed_at": "2026-10-25T08:00:00Z", "fingerprint": pages.fingerprint(body)}, body)
    before = (brain / "identity" / "spine.md").read_text(encoding="utf-8")
    onboard.write_profile(brain, {"offering": [LINE]}, NOW)
    assert (brain / "identity" / "spine.md").read_text(encoding="utf-8") == before
    assert (brain / "identity" / "profile.md").exists()   # the profile is its own page


def test_the_profile_is_one_needs_you_item_in_its_own_file_and_no_other_item_is_touched(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    onboard.write_needs_you(brain, [{"key": "identity", "ask": "Sign the identity", "default": "sign it"},
                                    {"key": "domains", "ask": "Which domains?", "default": "none"}])
    onboard.write_profile(brain, {"offering": [LINE]}, NOW)
    onboard.write_profile(brain, {"offering": [LINE, {**LINE, "text": "More"}]}, NOW)   # again
    own = yaml.safe_load((brain / "_inbound" / "needs-you" / "onboard-profile.yaml").read_text(encoding="utf-8"))["items"]
    assert len(own) == 1 and own[0]["id"] == "onboard-profile" and own[0]["kind"] == "question"
    assert own[0]["text"] == "Correct any line of your company profile, or sign it"
    assert own[0]["recommendation"] and own[0]["default"] == "the default stays" and own[0]["depends"] == 1
    assert set(own[0]) == {"id", "kind", "text", "recommendation", "default", "deadline", "depends", "payload", "fingerprint"}
    first = yaml.safe_load((brain / "_inbound" / "needs-you" / "onboard.yaml").read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in first] == ["onboard-identity", "onboard-domains"]   # the other writer's file is as it was
    import needs_you
    assert {"onboard-identity", "onboard-domains", "onboard-profile"} <= {i.id for i in needs_you.collect(brain, online=False)}
    # and the onboard run's own rewrite of its file keeps the profile item, which is in another file
    onboard.write_needs_you(brain, [{"key": "identity", "ask": "Sign the identity", "default": "sign it"}])
    assert [i["id"] for i in yaml.safe_load((brain / "_inbound" / "needs-you" / "onboard-profile.yaml").read_text(encoding="utf-8"))["items"]] == ["onboard-profile"]


def test_a_signed_profile_asks_nothing_and_a_profile_with_no_lines_writes_nothing(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    assert onboard.write_profile(brain, {}, NOW) is None and onboard.write_profile(brain, {"offering": [{"text": "x", "source": ""}]}, NOW) is None
    assert not (brain / "identity" / "profile.md").exists()
    assert not list((brain / "_inbound" / "needs-you").glob("onboard-profile.yaml"))
    path = onboard.write_profile(brain, {"offering": [LINE]}, NOW)
    assert path == brain / "identity" / "profile.md" and path.exists()
    fm, body = pages.read(path)
    pages.write(path, {**fm, "status": "stable", "signed_by": "Ada Okafor", "signed_at": "2026-10-25T08:00:00Z",
                       "fingerprint": pages.fingerprint(body)}, body)
    item = brain / "_inbound" / "needs-you" / "onboard-profile.yaml"
    import needs_you
    assert "onboard-profile" in {i.id for i in needs_you.collect(brain, online=False)}   # the draft asked to be signed
    assert onboard.write_profile(brain, {"offering": [LINE]}, NOW) == path               # the owner signed it, and research ran again
    assert yaml.safe_load(item.read_text(encoding="utf-8")) == {"items": []}             # the question is withdrawn, not left to be asked
    assert "onboard-profile" not in {i.id for i in needs_you.collect(brain, online=False)}
    assert onboard.write_profile(brain, {}, NOW) == path and yaml.safe_load(item.read_text(encoding="utf-8")) == {"items": []}   # no line either
    item.unlink()
    assert onboard.write_profile(brain, {"offering": [LINE]}, NOW) == path and not item.exists()   # and none is written for a signed page


# ----- one computer, one runner, and a pause (Task 14) ------------------------------------------------
import datetime as dt   # noqa: E402

import routine   # noqa: E402
import start   # noqa: E402

LOCAL = dt.datetime(2026, 10, 24, 23, 10).astimezone()   # late evening on this computer's own clock


def schtasks(monkeypatch, present: set) -> list:
    """schtasks as --check meets it: the `present` tasks answer /Query until they are deleted."""
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    monkeypatch.setattr(onboard.doctor, "diagnose", lambda *a, **k: [])
    deleted = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        if argv[:2] == ["schtasks", "/Delete"]:
            deleted.append(argv[-1])
        name = argv[argv.index("/TN") + 1] if "/TN" in argv else ""
        gone = argv[:2] == ["schtasks", "/Query"] and (name in deleted or name not in present)
        return subprocess.CompletedProcess(argv, 1 if gone else 0, "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    import offboard
    monkeypatch.setattr(offboard, "run_cmd", fake)
    return deleted


def test_check_leaves_the_probe_task_alone_while_a_setup_is_running(tmp_path, monkeypatch):
    ada = make_space(tmp_path / "ada", name="ada")
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"case": "person", "probe_task": {"name": "space-probe-ada"}})
    deleted = schtasks(monkeypatch, {"space-probe-ada"})
    lock = start.take_lock()   # a build is proving the routines with that task right now
    assert lock is not None
    assert not [s for s in onboard.check(ada) if "probe" in s.name] and deleted == []
    assert onboard.read_yaml(ada / "_meta" / "state" / "onboard.yaml")["probe_task"] == {"name": "space-probe-ada"}
    lock.unlink()
    assert [s.name for s in onboard.check(ada) if "probe" in s.name] == ["routine probe task space-probe-ada removed"]
    assert deleted == ["space-probe-ada"]


def test_check_on_a_computer_that_does_not_run_the_routines_says_where_they_run(tmp_path, monkeypatch):
    ada = make_space(tmp_path / "ada", name="ada")
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"case": "person", "routine_tasks": [
        {"name": "space-daily-chain-ada", "kind": "schtasks", "check": ["schtasks", "/Query", "/TN", "space-daily-chain-ada", "/XML"]}]})
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    routine.claim_host(ada, "os", NOW)
    monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")
    schtasks(monkeypatch, set())   # the desktop's task is not on the laptop
    rows = [s for s in onboard.check(ada) if s.name.startswith("routine")]
    assert [(s.name, s.done) for s in rows] == [("routines on this computer", True)]
    assert "ADA-DESKTOP" in rows[0].result and "run my routines here" in rows[0].result


def test_a_pause_ends_at_six_the_next_morning_or_when_the_person_says():
    assert onboard.pause_end(None, LOCAL) == dt.datetime(2026, 10, 25, 6, 0).astimezone()
    small_hours = dt.datetime(2026, 10, 24, 2, 0).astimezone()
    assert onboard.pause_end(None, small_hours) == dt.datetime(2026, 10, 24, 6, 0).astimezone()   # this morning's six
    assert onboard.pause_end("2026-11-02", LOCAL) == dt.datetime(2026, 11, 2, 6, 0).astimezone()
    assert onboard.pause_end("2026-11-02T09:30", LOCAL) == dt.datetime(2026, 11, 2, 9, 30).astimezone()   # local time
    zoned = onboard.pause_end("2026-11-02T09:30Z", LOCAL)
    assert zoned == dt.datetime(2026, 11, 2, 9, 30, tzinfo=dt.timezone.utc)
    for said in ("2026-10-20", "next week", "2026-13-01"):
        with pytest.raises(ValueError):
            onboard.pause_end(said, LOCAL)


def cron_tasks(ada, monkeypatch, here: bool) -> None:
    """Ada's routines recorded as cron lines; `here`: this computer's crontab holds them."""
    line = "33 3 * * * routine.py daily-chain"
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"routine_tasks": [
        {"name": "space-daily-chain-ada", "kind": "cron", "check": ["crontab", "-l"], "line": line}]})
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: subprocess.CompletedProcess(
        argv, 0 if here else 1, f"{line}\n" if here else "", "" if here else "no crontab for ada"))


def test_pausing_with_nothing_recorded_records_this_computer_and_resuming_clears_it(tmp_path, monkeypatch, capsys):
    ada = make_space(tmp_path / "ada", name="ada")
    cron_tasks(ada, monkeypatch, here=True)
    assert onboard.main([str(ada), "--pause", "--until", "2099-01-02"]) == 0
    said = capsys.readouterr().out
    assert said.startswith("Your routines are paused until") and "2 January" in said
    record = routine.host(ada)
    assert record["machine"] == routine.this_machine() and record["runner"] == "os"
    assert routine.gate(ada, NOW).startswith("paused until 2099-01-02 06:00")
    assert onboard.main([str(ada), "--resume"]) == 0 and routine.gate(ada, NOW) is None
    assert onboard.main([str(ada), "--resume"]) == 0 and "not paused" in capsys.readouterr().out.splitlines()[-1]


def test_a_pause_says_the_apps_own_tasks_are_not_paused_by_it(tmp_path, capsys):
    ada = make_space(tmp_path / "ada", name="ada")
    task = tmp_path / "scheduled-tasks" / "daily" / "SKILL.md"
    task.parent.mkdir(parents=True)
    task.write_text("the routine\n", encoding="utf-8")
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"routine_checks": [
        {"name": "routine scheduled 1", "check": f"test -f {task.as_posix()}"}]})
    assert onboard.main([str(ada), "--pause"]) == 0
    assert "does not stop" in capsys.readouterr().out and routine.host(ada)["runner"] == "harness"


def test_a_pause_in_the_past_or_on_a_record_that_is_not_one_changes_nothing(tmp_path, capsys):
    ada = make_space(tmp_path / "ada", name="ada")
    assert onboard.main([str(ada), "--pause", "--until", "2020-01-01"]) == 1
    assert "passed" in capsys.readouterr().out and routine.host(ada) is None
    (ada / routine.HOST).write_text("machine: [\n", encoding="utf-8")
    assert onboard.main([str(ada), "--pause"]) == 1
    assert "unclear" in capsys.readouterr().out and (ada / routine.HOST).read_text(encoding="utf-8") == "machine: [\n"


def test_a_pause_with_nothing_recorded_is_made_on_the_computer_that_runs_them(tmp_path, monkeypatch, capsys):
    """Nothing recorded and none of the routines' tasks here: pausing here would move them here, where none runs."""
    ada = make_space(tmp_path / "ada", name="ada")
    cron_tasks(ada, monkeypatch, here=False)
    assert onboard.main([str(ada), "--pause"]) == 1
    said = capsys.readouterr().out
    assert "computer that runs them" in said and "pause my routines" in said
    assert routine.host(ada) is None and routine.gate(ada, NOW) is None   # the computer that runs them goes on


@pytest.mark.parametrize("flags", [["--until", "2099-01-02"], ["--pause", "--resume"], ["--resume", "--until", "2099-01-02"],
                                   ["--routines-here", "--pause"], ["--routines-here", "--resume"]])
def test_flags_that_do_not_go_together_are_refused_plainly(tmp_path, capsys, flags):
    ada = make_space(tmp_path / "ada", name="ada")
    assert onboard.main([str(ada), *flags]) == 2
    said = capsys.readouterr().out.strip()
    assert len(said.splitlines()) == 1 and "--pause" in said and "one at a time" in said
    assert routine.host(ada) is None


SKILL = TEMPLATE / ".claude" / "skills" / "onboard"
PHRASES = ("pause my routines", "resume my routines", "run my routines here")


def test_the_three_routine_phrases_reach_the_onboard_skill():
    description = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    instruction = " ".join((TEMPLATE / "_meta" / "templates" / "personal-instruction.md").read_text(encoding="utf-8").split())
    routing = instruction[instruction.index("The toolbox is in"):]
    to_onboard = routing[routing.index("(sign)"):routing.index("(onboard)")]
    for phrase in PHRASES:
        assert f'"{phrase}"' in description and f'"{phrase}"' in to_onboard


def test_a_second_computers_built_message_says_where_the_routines_run():
    built = (SKILL / "messages.md").read_text(encoding="utf-8").split("## built\n")[1].split("\n## ")[0]
    assert "routines_where" in built and "{routines_where}" in built


def test_the_skill_turns_the_apps_tasks_off_by_a_path_in_any_slash_and_case():
    text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    section = text[text.index('"Pause my routines", "resume my routines", "run my routines here"'):]
    section = section[:section.index("## ")]
    assert "slash" in section and "case" in section and "another computer" in section and "enabled" in section


def test_copy_template_leaves_what_the_checkouts_git_ignores(tmp_path):
    """A tool's scratch folder, a worktree, anything the template checkout's git ignores is not in a
    fresh clone, so it never travels into a new space; files not committed yet still do."""
    src = make_space(tmp_path / "tpl", profile="template")
    for rel in ("_meta/tools/x.py", "_meta/tools/new_tool.py", ".superpowers/sdd/notes.md",
                ".claude/worktrees/w1/README.md", "_meta/tools/cache.tmp"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("x\n", encoding="utf-8")
    (src / ".superpowers" / "sdd" / ".gitignore").write_text("*\n", encoding="utf-8")
    (src / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(src)], check=True, capture_output=True)
    (src / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (src / ".git" / "info" / "exclude").write_text(".claude/worktrees/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "_meta/tools/x.py"], check=True, capture_output=True)
    dst = tmp_path / "new"
    assert onboard.copy_template(src, dst)
    assert (dst / "_meta" / "tools" / "x.py").exists() and (dst / "_meta" / "tools" / "new_tool.py").exists()
    assert not (dst / ".superpowers").exists() and not (dst / ".claude" / "worktrees").exists()
    assert not (dst / "_meta" / "tools" / "cache.tmp").exists()
def test_instruction_carries_the_four_rules(tmp_path):
    """Working in a codebase (docs/design/2026-10-22-codebase-link-design.md 4.3): triggers, three
    rings, writing by readers, capture with a pointer; Needs you once a day."""
    ws = make_space(tmp_path / "ada")
    text = onboard.instruction_block(ws)
    for needle in ("link.py", "resolve", "Three rings", "readers: wider", "ref: <owner/repo>", "--once-a-day"):
        assert needle in text


def step(report, name):
    return next(s for s in report.steps if s.name == name)


def _joining(tmp_path, subjects=None):
    """A person joining offline: a template copy, their answers, the brain beside it."""
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=subjects)
    ws = tmp_path / "ada"
    onboard.copy_template(onboard.TEMPLATE_ROOT, ws)
    answers = onboard.interview({"name": "Ada Okafor"}, interactive=False, case="person")
    return ws, answers, {"brain": str(brain), "home": tmp_path, "user_instructions": str(tmp_path / "CLAUDE.md")}


def test_onboard_reports_the_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    monkeypatch.setattr(link, "scan", lambda root, now=None, **k: {"seen": 2, "linked": 1, "asked": 1, "skipped": ""})
    ws, answers, kw = _joining(tmp_path)
    row = step(onboard.run(ws, answers, offline=True, **kw), "codebases linked")
    assert row.done and row.result == "1 linked, 1 asked" and not row.needs_owner


def test_onboard_scan_failure_is_not_an_owner_step(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    monkeypatch.setattr(link, "scan", lambda *a, **k: (_ for _ in ()).throw(OSError("no gh")))
    ws, answers, kw = _joining(tmp_path)
    report = onboard.run(ws, answers, offline=True, **kw)
    row = step(report, "codebases linked")
    assert row.done and row.result.startswith("skipped") and not row.needs_owner
    assert not report.failed(), [s.row() for s in report.failed()]


def test_onboard_offline_scan_asks_no_host(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    monkeypatch.setattr(link, "_gh", lambda *a: pytest.fail("the host was asked offline"))
    ws, answers, kw = _joining(tmp_path, subjects=[
        {"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}}])
    row = step(onboard.run(ws, answers, offline=True, **kw), "codebases linked")
    assert row.done and row.result == "skipped: offline"
