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
template_only = pytest.mark.skipif(not spaceyaml.is_template_repo(TEMPLATE),
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


def everything_under(*roots) -> dict:
    """Every folder and file under `roots`, a file with its content's hash."""
    return {path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "folder"
            for root in roots for path in sorted(Path(root).rglob("*"))}


def dry_run(tmp_path, root, answers: dict, *more) -> None:
    """`onboard.py <root> --dry-run` offline with `answers`, spaces under a `--home` of their own; nothing under the
    root or the home may change."""
    home = tmp_path / "spaces"
    home.mkdir()
    given = tmp_path / "answers.yaml"
    given.write_text(yaml.safe_dump(answers), encoding="utf-8")
    before = everything_under(root, home)
    assert onboard.main([str(root), "--answers", str(given), "--offline", "--dry-run", "--home", str(home), *more]) == 0
    assert everything_under(root, home) == before


def test_a_dry_run_of_a_company_says_what_it_would_create_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "run_cmd", never)
    root = tmp_path / "co"
    onboard.copy_template(TEMPLATE, root)
    dry_run(tmp_path, root, {"company": "Lantern Works", "owner": "Ada Okafor", "owner_email": "ada@example.test"})
    out = capsys.readouterr().out
    assert "create ada-okafor" in out and "dry run" in out and "nothing written" in out


def test_a_dry_run_of_a_person_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern")
    ws, instructions = tmp_path / "ada", tmp_path / "CLAUDE.md"
    onboard.copy_template(onboard.TEMPLATE_ROOT, ws)
    before = everything_under(brain)
    dry_run(tmp_path, ws, {"name": "Ada Okafor", "email": "ada@example.test"}, "--brain", str(brain),
            "--user-instructions", str(instructions))
    assert everything_under(brain) == before and not instructions.exists()
    assert "nothing written" in capsys.readouterr().out



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


@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux"])
def test_sync_task_runs_the_release_leg(tmp_path, monkeypatch, system):
    """On every system the scheduler runs the release leg. launchd's bootstrap line names only the agent's
    file, so on macOS the leg is in that file's ProgramArguments."""
    import plistlib
    monkeypatch.setattr(onboard, "SYSTEM", system)
    monkeypatch.setattr(onboard, "user_id", lambda: 501)
    root = make_space(tmp_path / "ada")
    report = onboard.Report("person")
    rec = onboard.register_sync(report, root, "ada", 15, offline=True, dry_run=False)
    text = (root / "_meta" / "state" / "sync-task.txt").read_text(encoding="utf-8")
    if system == "Darwin":
        agent = text[text.index("<?xml"):text.index("</plist>") + len("</plist>")]
        launched = " ".join(plistlib.loads(agent.encode("utf-8"))["ProgramArguments"])
    else:
        launched = " ".join(rec["create"]) + (rec["line"] or "")
    assert "--release-leg" in text and "--release-leg" in launched


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


@pytest.mark.parametrize("error", [OSError(22, "Invalid argument"), OverflowError("date value out of range")])
def test_a_pause_this_computers_clock_cannot_place_is_a_plain_line(monkeypatch, error):
    """Windows' clock places no moment without a zone past 3000 or before 1970 (OSError): `--pause 9999-12-31` says
    so in a plain line, never a traceback."""
    import types

    class Unplaced(dt.datetime):
        def astimezone(self, tz=None):
            if tz is None and self.tzinfo is None and not 1970 < self.year < 3000:
                raise error
            return super().astimezone(tz)
    monkeypatch.setattr(onboard, "dt", types.SimpleNamespace(**{**vars(dt), "datetime": Unplaced}))
    now = Unplaced(2026, 10, 24, 18, 0).astimezone()
    with pytest.raises(ValueError, match="too far ahead"):
        onboard.pause_end("9999-12-31", now)
    with pytest.raises(ValueError, match="too far ahead"):
        onboard.pause_end("9999-12-31T00:00:00", now)
    with pytest.raises(ValueError, match="has passed"):
        onboard.pause_end("1960-01-01", now)


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


def test_a_pause_says_the_apps_own_tasks_read_it_first(tmp_path, capsys):
    ada = make_space(tmp_path / "ada", name="ada")
    task = tmp_path / "scheduled-tasks" / "daily" / "SKILL.md"
    task.parent.mkdir(parents=True)
    task.write_text("the routine\n", encoding="utf-8")
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"routine_checks": [
        {"name": "routine scheduled 1", "check": f"test -f {task.as_posix()}"}]})
    assert onboard.main([str(ada), "--pause"]) == 0
    said = capsys.readouterr().out
    assert "does not stop" not in said and routine.host(ada)["runner"] == "harness"
    assert said.rstrip().endswith("They run as scheduled tasks in the Claude app, and each one reads the pause first "
                                  "and stops.")


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


CLOUD_TASKS = {"sweep": "trig_01AAAAAAAA"}
ANOTHER = "m-0a1b2c3d"   # a computer other than the one the move ran on


def cloud_hosted(ada) -> dict:
    """The record says Ada's sweep runs in the cloud, moved from this computer, which keeps the daily."""
    return routine.claim_cloud(ada, CLOUD_TASKS, "Europe/Amsterdam", ["ada/ada"],
                               {"read": ["mcp__mail__get_message"], "write": [], "send": []}, NOW)


def test_another_computer_says_the_sweep_runs_in_the_cloud_and_registers_nothing(tmp_path, monkeypatch):
    ada = make_space(tmp_path / "ada", name="ada")
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"case": "person", "routine_tasks": [
        {"name": "space-daily-chain-ada", "kind": "schtasks", "check": ["schtasks", "/Query", "/TN", "space-daily-chain-ada", "/XML"]}]})
    cloud_hosted(ada)
    monkeypatch.setattr(routine, "this_machine", lambda: ANOTHER)
    said = ("Your sweep runs in the cloud as a scheduled task, and your daily on the computer it moved from; this "
            "computer's sync lands what the sweep leaves open")
    elsewhere = onboard.routines_elsewhere(ada)
    assert (elsewhere.name, elsewhere.done, elsewhere.result) == (onboard.ELSEWHERE_STEP, True, said)
    schtasks(monkeypatch, set())   # no routine task of this computer's to read back
    rows = [s for s in onboard.check(ada) if s.name.startswith("routine")]
    assert [(s.name, s.done) for s in rows] == [(onboard.CLOUD_STEP, True)]   # the cloud's own row, below
    monkeypatch.setattr(onboard, "register_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    report = onboard.Report(case="test")
    assert onboard.register_routines(report, ada) == [] and report.steps[-1].result == said   # none beside the cloud's


def test_the_computer_the_move_ran_on_keeps_its_routines_beside_the_cloud_row(tmp_path, monkeypatch):
    """Only the sweep moves: the computer the move ran on keeps its daily, so its build registers its routines (its
    own sweep steps aside when it starts) and its check reads them back beside the cloud's row."""
    ada = make_space(tmp_path / "ada", name="ada")
    cloud_hosted(ada)
    assert onboard.routines_elsewhere(ada) is None
    prompts = ada / "_meta" / "state" / "routines"
    prompts.mkdir(parents=True)
    for name, schedule in (("daily-chain", "33 6 * * *"), ("sweep-chain", "7 7,13,19 * * *")):
        (prompts / f"{name}.md").write_text(f'---\nname: {name}\nschedule: "{schedule}"\n---\nRun.\n', encoding="utf-8")
    monkeypatch.setattr(onboard, "_no_claude", lambda report: False)

    def register(report, label, task, *args, **kwargs):
        report.add(label, True, f"schtasks /Query /TN {task}")
        return {"name": task, "kind": "schtasks", "check": ["schtasks", "/Query", "/TN", task, "/XML"]}
    monkeypatch.setattr(onboard, "register_task", register)
    report = onboard.Report(case="test")
    names = [task["name"] for task in onboard.register_routines(report, ada)]
    assert names == [f"{onboard.task_prefix()}{name}-ada" for name in ("daily-chain", "sweep-chain")]
    assert routine.host(ada)["runner"] == "cloud"   # registered here, claimed nowhere: the record stays the cloud's
    schtasks(monkeypatch, set(names))
    rows = [s.name for s in onboard.check(ada) if s.name.startswith("routine")]
    assert rows[0] == onboard.CLOUD_STEP and {f"routine task {name}" for name in names} <= set(rows)
    monkeypatch.setattr(routine, "this_machine", lambda: ANOTHER)   # any other computer: the cloud's row alone
    assert [s.name for s in onboard.check(ada) if s.name.startswith("routine")] == [onboard.CLOUD_STEP]


def without_computer(ada) -> None:
    """Ada's cloud record as one written before it named the computer the move ran on: every computer keeps the leg
    and the daily, as the computer's leg reads it."""
    path = ada / routine.HOST
    kept = [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
            if not line.startswith("computer:")]
    path.write_text("".join(kept), encoding="utf-8", newline="\n")
    record = routine.host(ada)
    assert record["runner"] == "cloud" and "computer" not in record


def test_under_the_cloud_the_computer_the_move_ran_on_says_where_its_daily_runs(tmp_path, monkeypatch):
    """Under a cloud record, the computer the move ran on keeps the daily where its scheduler held it: its own OS
    scheduler (nothing to say), or the app's own scheduled tasks (IN_THE_APP); a record that names no computer is
    every computer's. Any other computer runs nothing, its apps' tasks or not: the sweep runs in the cloud and the
    daily on the computer it moved from (IN_THE_CLOUD)."""
    ada = make_space(tmp_path / "ada", name="ada")
    cloud_hosted(ada)
    here = routine.this_machine()
    machine = [here]
    monkeypatch.setattr(routine, "this_machine", lambda: machine[0])

    def said():
        step = onboard.routines_elsewhere(ada)
        if step is None:
            return None
        assert (step.name, step.done) == (onboard.ELSEWHERE_STEP, True)
        return step.result

    assert said() is None
    machine[0] = ANOTHER
    assert said() == onboard.IN_THE_CLOUD
    machine[0] = here
    routines_run_here(ada)   # the daily runs as the app's own scheduled task here
    assert said() == onboard.IN_THE_APP
    machine[0] = ANOTHER
    assert said() == onboard.IN_THE_CLOUD   # those tasks are not this computer's to read back
    without_computer(ada)
    assert said() == onboard.IN_THE_APP
    (ada / "_meta" / "state" / "onboard.yaml").unlink()
    assert said() is None


def run_line(ada, name, hours_ago, **more) -> None:
    """One run line in Ada's runs.jsonl, as the sync brings it from main, `hours_ago` before now."""
    at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    with (ada / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"routine": name, "at": pages.iso(at), "space": "ada", **more}) + "\n")


def test_the_check_shows_the_cloud_task_and_how_long_ago_the_sweep_wrote_a_run_line(tmp_path, monkeypatch):
    """Spec section 6: the plain check reads the task from the record, and the sweep's newest run line from
    runs.jsonl, which this computer's sync keeps at main. The row fails nothing: a sweep that has not run yet is the
    cloud's to start."""
    ada = make_space(tmp_path / "ada", name="ada")
    cloud_hosted(ada)
    schtasks(monkeypatch, set())
    task = "Your sweep runs in the cloud, as the scheduled task trig_01AAAAAAAA, and your daily on your computer. "

    def row():
        rows = [s for s in onboard.check(ada) if s.name.startswith("routine")]
        assert [(s.name, s.done, s.needs_owner) for s in rows] == [(onboard.CLOUD_STEP, True, False)]
        return rows[0].result

    assert row() == task + "It has written no run line yet."
    proof(ada, "sweep", hours_ago=0.5)   # a proof run is no run of the sweep
    run_line(ada, "sync", 0.5, cloud="sweep", notes={"left_out": 0})   # nor is the landing that carried it
    run_line(ada, "daily", 0.25)   # nor the daily, which runs on the computer
    assert row() == task + "It has written no run line yet."
    run_line(ada, "sweep_chain", 50)   # the chain tool's line (chain.py)
    with (ada / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8") as fh:   # read past, never a failure
        fh.write('{"routine": "sweep-chain", "at": "not a time"}\n{not json\n'
                 '{"routine": "sweep-chain", "at": "0001-01-01T00:00:00+01:00"}\n')   # before the first moment UTC has
    assert row() == task + "The sweep's newest run line was written 2 days ago."
    run_line(ada, "sweep-chain", 5)   # the agent's own line (the routine prompt)
    run_line(ada, "sweep_chain", 3)
    assert row() == task + "The sweep's newest run line was written 3 hours ago."
    run_line(ada, "sweep-chain", 0.25)   # the agent's own line counts by itself when it is the newest
    assert row() == task + "The sweep's newest run line was written 15 minutes ago."


def test_the_plain_check_prints_the_cloud_row_whole(tmp_path, monkeypatch, capsys):
    """The row is there to be read: the terminal shows the task and its age, not a done row's first 70
    characters."""
    ada = make_space(tmp_path / "ada", name="ada")
    tasks = {"sweep": "trig_01HZX3K9QWABCDEFGHJKMNPQRS"}
    routine.claim_cloud(ada, tasks, "Europe/Amsterdam", ["ada/ada"], {"read": [], "write": [], "send": []}, NOW)
    schtasks(monkeypatch, set())
    run_line(ada, "sweep_chain", 50)
    assert onboard.main([str(ada), "--check"]) == 0
    printed = next(line for line in capsys.readouterr().out.splitlines() if onboard.CLOUD_STEP in line)
    assert printed.startswith("ok ")
    for needed in (*tasks.values(), "your daily on your computer", "2 days ago"):
        assert needed in printed


def test_the_cloud_row_says_the_sweep_is_stopped_after_a_proof_showed_a_refusal_missing(tmp_path, monkeypatch,
                                                                                         capsys):
    """Proved in place, the task stays off once a proof showed a refusal missing: the sweep runs nowhere, so the row
    fails and names what to say, until a later proof shows every refusal again. However long ago that proof was: a
    missing refusal is mended only by a proof that shows it."""
    ada = make_space(tmp_path / "ada", name="ada")
    cloud_hosted(ada)
    schtasks(monkeypatch, set())
    run_line(ada, "sweep_chain", 3)
    proof(ada, "sweep", hours_ago=72, edit=False)

    def row():
        rows = [s for s in onboard.check(ada) if s.name.startswith("routine")]
        assert [s.name for s in rows] == [onboard.CLOUD_STEP]
        return rows[0]

    stopped = row()
    assert not stopped.done and not stopped.needs_owner
    assert stopped.result == (
        "Your sweep is stopped: the sweep task's proof run shows that the cloud no longer refuses a change to a "
        "protected file, so its task stays off; say 'run my routines here' to run it on this computer again. The "
        "cloud's task is trig_01AAAAAAAA. The sweep's newest run line was written 3 hours ago.")
    assert onboard.main([str(ada), "--check"]) == 1
    assert stopped.result in capsys.readouterr().out
    proof(ada, "sweep", hours_ago=60)   # proved again since, if not in the last two days
    assert row().done and row().result.startswith("Your sweep runs in the cloud")


@pytest.mark.parametrize("seconds, said", [(-30, "less than a minute ago"), (30, "less than a minute ago"),
                                           (60, "1 minute ago"), (59 * 60 + 59, "59 minutes ago"),
                                           (3600, "1 hour ago"), (47 * 3600 + 3599, "47 hours ago"),
                                           (48 * 3600, "2 days ago"), (30 * 86400, "30 days ago")])
def test_how_long_ago_is_said_in_whole_units(seconds, said):
    assert onboard.ago(NOW - dt.timedelta(seconds=seconds), NOW) == said


def test_a_pause_with_the_sweep_in_the_cloud_says_when_the_cloud_hears_of_it(tmp_path, capsys):
    ada = make_space(tmp_path / "ada", name="ada")
    cloud_hosted(ada)
    assert onboard.main([str(ada), "--pause", "--until", "2099-01-02"]) == 0
    said = capsys.readouterr().out
    assert said.startswith("Your routines are paused until") and said.rstrip().endswith(
        "The cloud's next sweep reads the pause once this computer's sync lands it; away from this computer, turn "
        "its task off on claude.ai instead.")
    assert "does not stop" not in said and "hears of it at its next sync" not in said
    assert routine.cloud_gate(ada, NOW).startswith("paused until 2099-01-02") and routine.host(ada)["tasks"] == CLOUD_TASKS
    assert onboard.main([str(ada), "--resume"]) == 0 and routine.cloud_gate(ada, NOW) is None


# ----- run my sweep in the cloud (cloud routines, Task 12; docs/design/2026-10-28-cloud-sweep-design.md) ----------
import json   # noqa: E402
import re   # noqa: E402

import ghrest   # noqa: E402

MOVE = ["--routines-cloud", "--sweep", CLOUD_TASKS["sweep"], "--timezone", "Europe/Amsterdam"]
SWEEP_GRANT = {"read": ["mcp__mail__get_message", "mcp__calendar__list_events"], "write": [], "send": []}


def proof(ada, chain, hours_ago=1.0, **seen) -> None:
    """One proof run's line as it lands, `hours_ago` before now (`cloud.py proof-line`): the edit, the tool and the
    request past the network limit refused unless `seen` says otherwise."""
    at = pages.iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago))
    line = {"routine": "cloud-proof", "chain": chain, "edit": True, "tool": True, "network": True, "at": at, **seen}
    with (ada / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def routines_run_here(ada) -> Path:
    """This computer runs Ada's routines, so it can keep the daily once the sweep moves (`onboard.runs_here`): with
    nothing recorded, the app's own scheduled task of the daily reads back here. Returns that task's file."""
    task = ada.parent / "scheduled-tasks" / "daily-chain" / "SKILL.md"
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text("the routine\n", encoding="utf-8")
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"routine_checks": [
        {"name": "routine scheduled 1", "check": f"test -f {task.as_posix()}"}]})
    assert onboard.runs_here(ada)
    return task


def rendered(chain, roots, grant) -> None:
    """The task body `cloud.py trigger` left on this computer for `chain`, with the repositories and the grant."""
    path = Path.home() / ".spaces" / f"cloud-{chain}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": f"Ada Okafor's {chain}", "enabled": False, "roots": roots, "grant": grant}),
                    encoding="utf-8")


def proven(tmp_path):
    """Ada's workspace, whose routines run on this computer, with the sweep's task rendered here; no proof run yet."""
    ada = make_space(tmp_path / "ada", name="ada")
    routines_run_here(ada)
    rendered("sweep", ["ada/ada", "ada/ada-harbor"], SWEEP_GRANT)
    return ada


def reading_history(ada) -> None:
    onboard.write_yaml(ada / "_meta" / "state" / "backfill.yaml", {"parts": [{"id": "p1", "status": "pending"}]})


def test_the_move_is_refused_without_a_proof(tmp_path, capsys):
    ada = proven(tmp_path)
    assert onboard.main([*MOVE, str(ada)]) == 2
    said = capsys.readouterr().out.strip()
    assert len(said.splitlines()) == 1 and said.startswith("The sweep task has no proof run") and "48 hours" in said
    assert routine.host(ada) is None


def test_a_proof_older_than_two_days_does_not_count(tmp_path, capsys):
    ada = proven(tmp_path)
    proof(ada, "sweep", hours_ago=49)
    assert onboard.main([*MOVE, str(ada)]) == 2 and "The sweep task has no proof run" in capsys.readouterr().out
    proof(ada, "sweep", hours_ago=47)
    assert onboard.main([*MOVE, str(ada)]) == 0 and routine.host(ada)["runner"] == "cloud"


def test_a_proof_that_leaves_a_refusal_out_does_not_count(tmp_path, capsys):
    ada = proven(tmp_path)
    proof(ada, "sweep", hours_ago=3, tool=None)
    proof(ada, "sweep", hours_ago=2, edit="yes")   # only a refusal seen counts, never a word for one
    proof(ada, "sweep", at="0001-01-01T00:00:00+01:00")   # a moment before UTC's first is no moment: read past
    assert onboard.main([*MOVE, str(ada)]) == 2 and "The sweep task has no proof run" in capsys.readouterr().out
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 0


def test_a_proof_line_that_says_nothing_of_the_network_does_not_prove(tmp_path, capsys):
    """A proof owes three refusals, the request past the network limit among them: a line with the edit and the tool
    refused and no word on the network proves nothing, and names no refusal missing either."""
    ada = proven(tmp_path)
    with (ada / "_meta" / "state" / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"routine": "cloud-proof", "chain": "sweep", "edit": True, "tool": True,
                             "at": pages.iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2))}) + "\n")
    assert onboard.cloud_proofs(ada) == set() and onboard.cloud_unrefused(ada) == {}
    assert onboard.main([*MOVE, str(ada)]) == 2 and "The sweep task has no proof run" in capsys.readouterr().out
    assert routine.host(ada) is None
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 0 and routine.host(ada)["runner"] == "cloud"


def test_a_proof_proves_on_its_own_line_whatever_a_landing_left_out(tmp_path, capsys):
    """The proof line is the whole proof: it counts as soon as the sync brought it here, with no landing to wait for,
    and no sync line after it, whatever it left out or says of a proof, mends or stops anything."""
    ada = proven(tmp_path)
    proof(ada, "sweep")
    assert onboard.cloud_proofs(ada) == {"sweep"} and onboard.cloud_unrefused(ada) == {}
    run_line(ada, "sync", 0.5, cloud="sweep", notes={"left_out": 0, "proof": True})   # a landing that left nothing out
    run_line(ada, "sync", 0.4, cloud="sweep", notes={"left_out": 2})
    assert onboard.cloud_proofs(ada) == {"sweep"} and onboard.cloud_unrefused(ada) == {}
    proof(ada, "sweep", hours_ago=0.3, edit=False)
    run_line(ada, "sync", 0.2, cloud="sweep", notes={"left_out": 3, "proof": True})   # left out: still no refusal made
    assert onboard.cloud_proofs(ada) == set() and onboard.cloud_unrefused(ada) == {"sweep": ["edit"]}
    proof(ada, "sweep", hours_ago=0.1)
    assert onboard.main([*MOVE, str(ada)]) == 0 and routine.host(ada)["runner"] == "cloud"


def test_a_refusal_missing_is_not_hidden_by_a_later_proof_that_proves_nothing(tmp_path, capsys):
    """A refusal the cloud did not make stays missing until a later proof shows every refusal: a later line that
    leaves one out or writes it as a word proves nothing either way, and no number of hours mends it."""
    ada = proven(tmp_path)
    proof(ada, "sweep", hours_ago=72, edit=False)
    proof(ada, "sweep", hours_ago=3, edit=None)
    proof(ada, "sweep", hours_ago=2, tool="yes")
    proof(ada, "sweep", network="yes")
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert capsys.readouterr().out.strip() == (
        "The sweep task's proof run shows that the cloud does not refuse a change to a protected file, so your sweep "
        "stays where it runs.")
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 0 and routine.host(ada)["runner"] == "cloud"


def test_a_refusal_missing_is_said_before_a_wrong_id_a_wrong_zone_or_the_history(tmp_path, capsys):
    """The skill keeps the task off for a refusal missing alone, so nothing else said may hide it."""
    ada = proven(tmp_path)
    cloud_hosted(ada)
    proof(ada, "sweep", tool=False)
    reading_history(ada)
    moved = [*MOVE]
    moved[moved.index("--sweep") + 1] = "my-sweep"
    moved[moved.index("--timezone") + 1] = "Not a zone!"
    assert onboard.main([*moved, str(ada)]) == 2
    assert capsys.readouterr().out.strip() == (
        "Your sweep is stopped: the sweep task's proof run shows that the cloud no longer refuses an app tool it was "
        "not given, so its task stays off; say 'run my routines here' to run it on this computer again.")


def test_only_the_newest_proof_counts(tmp_path, capsys):
    """An older proof that showed every refusal no longer counts once a later one in the window leaves a refusal
    out: the later line names no refusal missing (`cloud_unrefused`), yet it is the task as it is, and it proves
    nothing."""
    ada = proven(tmp_path)
    proof(ada, "sweep", hours_ago=2)
    proof(ada, "sweep", hours_ago=1, tool=None)
    assert onboard.cloud_proofs(ada) == set() and onboard.cloud_unrefused(ada) == {}
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert capsys.readouterr().out.strip().startswith("The sweep task has no proof run from the last 48 hours")
    assert routine.host(ada) is None


def test_a_proof_that_shows_a_refusal_missing_names_it(tmp_path, capsys):
    """A refusal the cloud did not make (`false` on the newest proof line) is named in plain words, and an older
    proof that showed every refusal no longer counts: the newest proof run is the task as it is."""
    ada = proven(tmp_path)
    proof(ada, "sweep", hours_ago=3)
    proof(ada, "sweep", hours_ago=2, tool=False)
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert capsys.readouterr().out.strip() == (
        "The sweep task's proof run shows that the cloud does not refuse an app tool it was not given, so your sweep "
        "stays where it runs.")
    assert routine.host(ada) is None
    proof(ada, "sweep", edit=False, tool=False)
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert capsys.readouterr().out.strip() == (
        "The sweep task's proof run shows that the cloud does not refuse a change to a protected file or an app tool "
        "it was not given, so your sweep stays where it runs.")
    proof(ada, "sweep")   # proved again since: the refusals hold now
    assert onboard.main([*MOVE, str(ada)]) == 0 and routine.host(ada)["runner"] == "cloud"


def test_a_proof_that_reached_a_host_past_the_network_limit_is_a_refusal_missing(tmp_path, capsys):
    """The environment's network limit is the cloud's own, and the proof checks it: a request to a host outside it
    that went through is a refusal missing, named in plain words; a line that does not say proves nothing."""
    ada = proven(tmp_path)
    proof(ada, "sweep", network=False)
    assert onboard.cloud_proofs(ada) == set() and onboard.cloud_unrefused(ada) == {"sweep": ["network"]}
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert capsys.readouterr().out.strip() == (
        "The sweep task's proof run shows that the cloud does not refuse a request to a host outside its network "
        "limit, so your sweep stays where it runs.")
    assert routine.host(ada) is None
    proof(ada, "sweep", network=None)   # a proof line that says nothing of the network hides nothing
    assert onboard.cloud_proofs(ada) == set() and onboard.cloud_unrefused(ada) == {"sweep": ["network"]}
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 0 and routine.host(ada)["runner"] == "cloud"


def test_a_refusal_missing_while_the_sweep_runs_in_the_cloud_says_it_is_stopped(tmp_path, capsys):
    """Proved in place, the task the sweep runs as is off; a proof that shows a refusal missing keeps it off (the
    skill never turns it back on), so nothing runs the sweep until the person moves the routines here."""
    ada = proven(tmp_path)
    cloud_hosted(ada)
    before = (ada / routine.HOST).read_text(encoding="utf-8")
    proof(ada, "sweep", edit=False)
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert capsys.readouterr().out.strip() == (
        "Your sweep is stopped: the sweep task's proof run shows that the cloud no longer refuses a change to a "
        "protected file, so its task stays off; say 'run my routines here' to run it on this computer again.")
    assert (ada / routine.HOST).read_text(encoding="utf-8") == before


def test_a_green_proof_claims_the_cloud_for_the_sweep_alone(tmp_path, capsys):
    """The move turns off the app's own scheduled task that ran the sweep and keeps the daily's, read back as
    before: the daily stays on this computer."""
    ada = proven(tmp_path)
    tasks = {}
    for chain in ("daily", "sweep"):   # the app's own scheduled tasks that ran each chain
        tasks[chain] = tmp_path / "sweeps" / "scheduled-tasks" / f"{chain}-chain" / "SKILL.md"   # a folder above says sweep
        tasks[chain].parent.mkdir(parents=True)
        tasks[chain].write_text("the routine\n", encoding="utf-8")
    checks = [{"name": f"routine scheduled {n}", "check": f"test -f {tasks[chain].as_posix()}"}
              for n, chain in enumerate(("daily", "sweep"), 1)]
    onboard.write_yaml(ada / "_meta" / "state" / "onboard.yaml", {"routine_checks": checks})
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == onboard.CLOUD_MOVED.format(**CLOUD_TASKS) and "trig_01AAAAAAAA" in out[0]
    assert out[1:] == [f"turn off: {tasks['sweep'].as_posix()}"]
    record = routine.host(ada)
    assert (record["runner"], record["machine"], record["tasks"], record["timezone"], record["computer"]) == \
           ("cloud", routine.CLOUD_MACHINE, CLOUD_TASKS, "Europe/Amsterdam", routine.this_machine())
    assert record["roots"] == ["ada/ada", "ada/ada-harbor"]   # what the task attaches
    assert record["grant"] == SWEEP_GRANT
    assert routine.gate(ada, NOW, "sweep-chain") == routine.CLOUD and routine.cloud_gate(ada, NOW) is None
    assert routine.gate(ada, NOW, "daily-chain") is None and routine.gate(ada, NOW) is None   # the daily runs here
    assert onboard.read_yaml(ada / "_meta" / "state" / "onboard.yaml")["routine_checks"] == checks[:1]


def test_the_move_renders_the_routine_prompts_again(tmp_path, capsys):
    """A prompt rendered before this version has no tracker step in the daily, the tracker's one writer while the
    sweep is in the cloud, and no `--chain sweep` in the sweep: the move renders them all again."""
    ada = proven(tmp_path)
    rendered = ada / "_meta" / "state" / "routines"
    rendered.mkdir(parents=True, exist_ok=True)
    for name in ("daily-chain.md", "sweep-chain.md"):
        (rendered / name).write_text("---\nname: old\n---\nan old render\n", encoding="utf-8")
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 0
    capsys.readouterr()
    daily = (rendered / "daily-chain.md").read_text(encoding="utf-8")
    sweep = (rendered / "sweep-chain.md").read_text(encoding="utf-8")
    assert "tracker.py" in daily and "--chain daily" in daily and ada.as_posix() in daily
    assert "--chain sweep" in sweep


def test_a_dry_run_is_refused_beside_what_does_not_honour_it(tmp_path, capsys):
    ada = proven(tmp_path)
    proof(ada, "sweep")
    before = routine.host(ada)
    for mode in ([*MOVE, str(ada)], ["--routines-here", str(ada)], ["--pause", str(ada)]):
        assert onboard.main([*mode, "--dry-run"]) == 2, mode
        assert "--dry-run goes with" in capsys.readouterr().out
    assert routine.host(ada) == before


def test_an_offline_sync_task_dry_run_records_nothing(tmp_path):
    root = make_space(tmp_path / "ada")
    onboard.register_sync(onboard.Report("person"), root, "ada", 15, offline=True, dry_run=True)
    assert not (root / "_meta" / "state" / "sync-task.txt").exists()


def test_the_move_needs_the_task_rendered_on_this_computer(tmp_path, capsys):
    ada = proven(tmp_path)
    (Path.home() / ".spaces" / "cloud-sweep.json").write_text("{not json", encoding="utf-8")
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 2
    said = capsys.readouterr().out.strip()
    assert len(said.splitlines()) == 1 and "sweep task was not rendered" in said and routine.host(ada) is None


def test_the_move_waits_until_the_history_is_read(tmp_path, capsys):
    ada = proven(tmp_path)
    proof(ada, "sweep")
    reading_history(ada)
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert "history is still being read" in capsys.readouterr().out and routine.host(ada) is None


def refused(capsys) -> str:
    """The one sentence a refused move said, in plain words: no doubled stop, no word of the record."""
    said = capsys.readouterr().out.strip()
    assert len(said.splitlines()) == 1 and said.startswith("Your sweep stays where it runs: ")
    assert ".." not in said and "record" not in said and said.endswith(".")
    return said


def test_a_task_id_of_the_wrong_shape_moves_nothing(tmp_path, capsys):
    ada = proven(tmp_path)
    proof(ada, "sweep")
    moved = [*MOVE]
    moved[moved.index("--sweep") + 1] = "my-sweep"
    assert onboard.main([*moved, str(ada)]) == 2
    said = refused(capsys)
    assert "my-sweep is not" in said and "trig_" in said and routine.host(ada) is None


@pytest.mark.parametrize("zone", ["Not a zone!", "Mars/Olympus"])
def test_a_zone_that_is_none_moves_nothing(tmp_path, capsys, zone):
    import zoneinfo
    if zone == "Mars/Olympus" and not zoneinfo.available_timezones():
        pytest.skip("no zone data on this computer: only the shape of a zone is checked")
    ada = proven(tmp_path)
    proof(ada, "sweep")
    moved = [*MOVE]
    moved[moved.index("--timezone") + 1] = zone
    assert onboard.main([*moved, str(ada)]) == 2
    said = refused(capsys)
    assert zone in said and "Europe/Amsterdam" in said and routine.host(ada) is None


def test_a_zone_without_zone_data_is_checked_by_its_shape_only(tmp_path, monkeypatch):
    import zoneinfo
    monkeypatch.setattr(zoneinfo, "available_timezones", lambda: set())   # Windows without the tzdata package

    def nowhere(key):
        raise zoneinfo.ZoneInfoNotFoundError(key)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", nowhere)
    assert onboard.zone_unknown("Europe/Amsterdam") is False and onboard.zone_unknown("Not a zone!") is True


def test_a_render_that_reads_no_app_moves_nothing(tmp_path, capsys):
    """A sweep that may read no app would read nothing: the move is refused, whatever else the render was given and
    whatever the proof."""
    ada = proven(tmp_path)
    rendered("sweep", ["ada/ada"], {"read": [], "write": ["mcp__tracker__create_issue"],
                                    "send": ["mcp__mail__send_message"]})
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert capsys.readouterr().out.strip() == onboard.CLOUD_NO_GRANT and routine.host(ada) is None


def test_a_render_whose_repositories_are_paths_moves_nothing(tmp_path, capsys):
    ada = proven(tmp_path)
    rendered("sweep", ["/home/ada/spaces/brain"], SWEEP_GRANT)
    proof(ada, "sweep")
    assert onboard.main([*MOVE, str(ada)]) == 2
    assert "render it again" in refused(capsys) and routine.host(ada) is None


def test_a_second_move_names_the_earlier_task_still_running(tmp_path, capsys):
    """A space minted after the move is added by moving again: a new task replaces the one that ran, which still
    runs (and passes the cloud's gate) until it is turned off, so the move names it."""
    ada = proven(tmp_path)
    cloud_hosted(ada)
    proof(ada, "sweep")
    moved = [*MOVE]
    moved[moved.index("--sweep") + 1] = "trig_02CCCCCCCC"
    assert onboard.main([*moved, str(ada)]) == 0
    (said,) = capsys.readouterr().out.strip().splitlines()
    assert said.endswith("The cloud's earlier task trig_01AAAAAAAA still runs beside the new one: turn it off now, so "
                         "the sweep never runs twice.")
    assert routine.host(ada)["tasks"] == {"sweep": "trig_02CCCCCCCC"}
    assert onboard.main([*moved, str(ada)]) == 0   # the same task again: nothing runs beside it
    assert "earlier" not in capsys.readouterr().out


def test_cloud_proofs_take_a_moment_without_a_zone_as_this_computers_own(tmp_path):
    ada = proven(tmp_path)
    proof(ada, "sweep")
    assert onboard.cloud_proofs(ada, dt.datetime.now()) == {"sweep"}


def github(monkeypatch, repos: dict) -> list:
    """GitHub as the check meets it, through REST: `repos` maps owner/name to (whether the person administers it,
    and so can push to it; what main's enforce_admins says: True, False, or None for a main with no protection). Any
    other repository is not found. Returns every call."""
    calls = []

    def gh(*args):
        calls.append(args)
        found = re.fullmatch(r"repos/([^/]+/[^/]+)(/branches/main/protection/enforce_admins)?", args[-1])
        if args[0] != "api" or not found or found.group(1) not in repos:
            return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")
        admin, held = repos[found.group(1)]
        if not found.group(2):
            return subprocess.CompletedProcess(args, 0, json.dumps({"full_name": found.group(1),
                                                                    "permissions": {"admin": admin, "push": admin, "pull": True}}), "")
        if not admin:
            return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")
        if held is None:
            return subprocess.CompletedProcess(args, 1, "", "gh: Branch not protected (HTTP 404)")
        return subprocess.CompletedProcess(args, 0, json.dumps({"enabled": held}), "")
    monkeypatch.setattr(ghrest, "runner", lambda cwd: gh)
    return calls


def on_github(root: Path, url: str) -> Path:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", url], check=True, capture_output=True)
    return root


def cloud_ready(tmp_path, monkeypatch, here=True) -> Path:
    """Ada's workspace on GitHub, mounting a brain they do not administer, with apps granted to their routines, which
    run on this computer (`here`)."""
    ada = make_space(tmp_path / "ada", name="ada", mounts=[
        {"name": "brain", "repo": "https://github.com/example/brain.git", "path": "brain", "branch": "main"}])
    on_github(ada, "https://github.com/ada/ada.git")
    if here:
        routines_run_here(ada)
    monkeypatch.setattr(routine, "grant", lambda ws, *a, **k: {**SWEEP_GRANT, "reconnect": [], "notes": [],
                                                                "person": "owner@example.test", "shell": ""})
    return ada


def test_the_check_names_a_repository_without_enforce_admins(tmp_path, monkeypatch, capsys):
    ada = cloud_ready(tmp_path, monkeypatch)
    github(monkeypatch, {"ada/ada": (True, False), "example/brain": (False, None)})
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and "ada/ada" in lines[0] and "past the checks" in lines[0]   # not the brain: not theirs to set
    github(monkeypatch, {"ada/ada": (True, None), "example/brain": (False, None)})
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
    assert "does not protect main" in capsys.readouterr().out
    calls = github(monkeypatch, {"ada/ada": (True, True), "example/brain": (False, None)})
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 0
    assert capsys.readouterr().out.strip() == onboard.CLOUD_CLEAR
    assert {c[-1] for c in calls} >= {"repos/ada/ada/branches/main/protection/enforce_admins", "repos/example/brain"}
    assert all("graphql" not in c and c[0] == "api" for c in calls) and routine.host(ada) is None


def test_the_check_names_unread_history(tmp_path, monkeypatch, capsys):
    ada = cloud_ready(tmp_path, monkeypatch)
    github(monkeypatch, {"ada/ada": (True, True), "example/brain": (False, None)})
    reading_history(ada)
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
    assert capsys.readouterr().out.strip().splitlines() == [onboard.CLOUD_HISTORY]


def test_the_check_names_a_missing_grant_and_a_space_github_cannot_reach(tmp_path, monkeypatch, capsys):
    ada = cloud_ready(tmp_path, monkeypatch)
    monkeypatch.setattr(routine, "grant", lambda ws, *a, **k: {"read": [], "write": [], "send": [], "reconnect": [],
                                                                "notes": [], "person": "", "shell": ""})
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor")   # theirs, never pushed to GitHub
    onboard.write_yaml(Path.home() / ".spaces" / "spaces.yaml", {"spaces": [{"root": str(harbor)}]})
    github(monkeypatch, {"ada/ada": (True, True), "example/brain": (False, None)})
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == onboard.CLOUD_NO_GRANT
    assert len(lines) == 2 and "harbor" in lines[1] and "no repository on GitHub" in lines[1]


def test_the_check_says_when_github_cannot_be_asked(tmp_path, monkeypatch, capsys):
    ada = cloud_ready(tmp_path, monkeypatch)
    monkeypatch.setattr(ghrest, "runner", lambda cwd: lambda *a: None)   # gh is not installed
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
    assert capsys.readouterr().out.strip().splitlines() == [onboard.CLOUD_NO_GH]


def test_the_check_names_the_task_the_sweep_already_runs_as(tmp_path, monkeypatch, capsys):
    """Moving again (a space minted later): the task it runs as is proved and updated, never made twice."""
    ada = cloud_ready(tmp_path, monkeypatch)
    github(monkeypatch, {"ada/ada": (True, True), "example/brain": (False, None)})
    cloud_hosted(ada)
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 0
    clear, already = capsys.readouterr().out.strip().splitlines()
    assert clear == onboard.CLOUD_CLEAR and already == onboard.CLOUD_ALREADY.format(**CLOUD_TASKS)
    assert "trig_01AAAAAAAA" in already and "update" in already


def test_a_replacing_computer_is_refused_until_it_runs_the_routines_and_a_record_naming_no_computer_moves_from_any(
        tmp_path, monkeypatch, capsys):
    """Spec 4.5: a new computer takes the leg over only once it runs the routines: the daily stays on the computer
    the move runs on, so from a computer the record does not name the move is refused (it says 'run my routines
    here' first) and the record is unchanged. An older record that names no computer moves from any computer, which
    then keeps the leg. While the sweep runs in the cloud its build proves nothing, so it has no probe of its own:
    the check and the move go by the grant the record kept at the move. A probe of its own, once there is one, is
    what this computer sees, and it decides."""
    import test_routines
    ada = on_github(test_routines.granted(tmp_path), "https://github.com/ada/ada.git")
    got = routine.grant(ada)
    moved = {part: got[part] for part in routine.GRANT_KEYS}
    assert moved["read"]   # the old computer's verdicts and probe grant the routines their apps
    routine.claim_cloud(ada, CLOUD_TASKS, "Europe/Amsterdam", ["ada/ada"], moved, NOW)
    routine.probe_path().unlink()   # the new computer: the same workspace, no probe, another id
    monkeypatch.setattr(routine, "this_machine", lambda: ANOTHER)
    github(monkeypatch, {"ada/ada": (True, True)})
    not_here = onboard.CLOUD_STAY.format(why=onboard.CLOUD_NOT_HERE)
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1   # no grant missing: the record's counts
    assert capsys.readouterr().out.strip().splitlines() == [not_here, onboard.CLOUD_ALREADY.format(**CLOUD_TASKS)]
    rendered("sweep", ["ada/ada"], onboard.cloud_grant(ada))   # the task rendered here, proved in place and landed
    proof(ada, "sweep")
    before = (ada / routine.HOST).read_text(encoding="utf-8")
    assert onboard.main([*MOVE, str(ada)]) == 2 and refused(capsys) == not_here
    assert (ada / routine.HOST).read_text(encoding="utf-8") == before
    without_computer(ada)   # a record that names no computer: this one keeps the daily as well
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 0
    clear, already = capsys.readouterr().out.strip().splitlines()
    assert clear == onboard.CLOUD_CLEAR and already == onboard.CLOUD_ALREADY.format(**CLOUD_TASKS)
    assert onboard.main([*MOVE, str(ada)]) == 0
    assert capsys.readouterr().out.strip() == onboard.CLOUD_MOVED.format(**CLOUD_TASKS)
    record = routine.host(ada)
    assert record["grant"] == moved and record["computer"] == ANOTHER   # this computer keeps the leg now
    routine.write_probe({"tools": [], "mcp_servers": []})   # this computer's own probe: it sees no app
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
    assert capsys.readouterr().out.strip().splitlines()[0] == onboard.CLOUD_NO_GRANT


def test_the_move_is_refused_on_a_computer_that_does_not_run_the_routines(tmp_path, monkeypatch, capsys):
    """Only the sweep moves, and the daily stays on the computer the move runs on: from a computer that runs none of
    the routines the daily would have no home, so the check names it and the move refuses there, whatever the proof
    and changing nothing; from the computer that runs them, it moves."""
    ada = cloud_ready(tmp_path, monkeypatch, here=False)
    github(monkeypatch, {"ada/ada": (True, True), "example/brain": (False, None)})
    rendered("sweep", ["ada/ada"], SWEEP_GRANT)
    proof(ada, "sweep")
    not_here = onboard.CLOUD_STAY.format(why=onboard.CLOUD_NOT_HERE)
    here = routine.this_machine()
    machine = [here]
    monkeypatch.setattr(routine, "this_machine", lambda: machine[0])

    def refuses():
        host = ada / routine.HOST
        before = host.read_text(encoding="utf-8") if host.exists() else None
        assert not onboard.runs_here(ada) and not_here in onboard.cloud_blockers(ada)
        assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
        assert capsys.readouterr().out.strip().splitlines()[0] == not_here
        assert onboard.main([*MOVE, str(ada)]) == 2 and refused(capsys) == not_here
        assert (host.read_text(encoding="utf-8") if host.exists() else None) == before

    refuses()   # nothing recorded, and none of the routines' tasks reads back here
    machine[0] = ANOTHER
    routine.claim_host(ada, "os", NOW)   # another computer runs them
    machine[0] = here
    refuses()
    machine[0] = ANOTHER
    cloud_hosted(ada)   # the sweep moved from another computer, which keeps the daily
    machine[0] = here
    refuses()
    machine[0] = ANOTHER   # the computer that runs them moves it again
    assert onboard.runs_here(ada) and not_here not in onboard.cloud_blockers(ada)
    assert onboard.main([*MOVE, str(ada)]) == 0 and routine.host(ada)["computer"] == ANOTHER


def test_the_check_says_the_sweep_is_stopped_rather_than_that_it_already_runs_in_the_cloud(
        tmp_path, monkeypatch, capsys):
    """The record says cloud, so every computer's own sweep steps aside, and a proof showed a refusal missing, so the
    cloud's task stays off: nothing runs the sweep, and the check says so, with 'run my routines here'."""
    ada = cloud_ready(tmp_path, monkeypatch)
    github(monkeypatch, {"ada/ada": (True, True), "example/brain": (False, None)})
    proof(ada, "sweep", hours_ago=72, tool=False)
    assert onboard.cloud_stopped(ada) == {}   # a task made new for the proof: the sweep runs where it ran
    cloud_hosted(ada)
    assert onboard.cloud_stopped(ada) == {"sweep": ["tool"]}
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 0
    clear, stopped = capsys.readouterr().out.strip().splitlines()
    assert clear == onboard.CLOUD_CLEAR and stopped == onboard.unrefused_sentence(ada, {"sweep": ["tool"]})
    assert stopped.startswith("Your sweep is stopped") and "run my routines here" in stopped
    assert "already runs in the cloud" not in stopped
    proof(ada, "sweep", hours_ago=60)   # proved again since: it runs in the cloud again
    assert onboard.cloud_stopped(ada) == {}
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 0
    assert "already runs in the cloud" in capsys.readouterr().out


def answers(routes: dict):
    """A `gh` that answers each REST route with (exit code, JSON or text); any other route is not found."""
    calls = []

    def gh(*args):
        calls.append(args[-1])
        code, out = routes.get(args[-1], (1, "gh: Not Found (HTTP 404)"))
        text = out if isinstance(out, str) else json.dumps(out)
        return subprocess.CompletedProcess(args, code, text if code == 0 else "", "" if code == 0 else text)
    gh.calls = calls
    return gh


def test_a_repository_whose_rights_cannot_be_read_is_named():
    gh = answers({"repos/ada/ada": (0, {"full_name": "ada/ada"})})   # no permissions in the answer
    assert onboard._admins_held(gh, "ada/ada", "your workspace") == onboard.CLOUD_PROTECTION_UNREAD.format(
        slug="ada/ada", what="your workspace")
    gh = answers({"repos/ada/ada": (0, {"full_name": "ada/ada", "permissions": "admin"})})
    assert "could not be read" in onboard._admins_held(gh, "ada/ada", "your workspace")


def test_an_unprotected_main_the_person_can_push_to_is_named_though_not_theirs_to_set():
    pushes = {"full_name": "example/brain", "permissions": {"admin": False, "push": True, "pull": True}}
    gh = answers({"repos/example/brain": (0, pushes), "repos/example/brain/branches/main": (0, {"protected": False})})
    said = onboard._admins_held(gh, "example/brain", "the mount brain of your workspace")
    assert "example/brain" in said and "does not protect main" in said and "administers it" in said
    gh = answers({"repos/example/brain": (0, pushes), "repos/example/brain/branches/main": (0, {"protected": True})})
    assert onboard._admins_held(gh, "example/brain", "the mount brain") is None   # the checks bind a pusher already
    assert "repos/example/brain/branches/main/protection/enforce_admins" not in gh.calls   # not theirs to read
    gh = answers({"repos/example/brain": (0, pushes)})   # the branch cannot be read
    assert onboard._admins_held(gh, "example/brain", "the mount brain") == onboard.CLOUD_PROTECTION_UNREAD.format(
        slug="example/brain", what="the mount brain")
    reads = {"full_name": "example/brain", "permissions": {"admin": False, "push": False, "pull": True}}
    gh = answers({"repos/example/brain": (0, reads)})
    assert onboard._admins_held(gh, "example/brain", "the mount brain") is None and gh.calls == ["repos/example/brain"]


@pytest.mark.parametrize("url, slug", [
    ("https://github.com/example/brain.git", "example/brain"),
    ("ssh://git@github.com/example/brain.git", "example/brain"),
    ("git@github.com:example/brain.git", "example/brain"),
    ("http://local_proxy@127.0.0.1:34567/git/example/brain", "example/brain"),
    ("/home/ada/spaces/brain", None),
    ("C:\\Users\\ada\\brain", None),
    ("C:/Users/ada/brain", None),
    ("../brain", None),
    ("file:///home/ada/spaces/brain", None),
    ("", None),
])
def test_a_mount_names_its_repository_only_by_a_url(url, slug):
    assert onboard._slug_of(url) == slug


def test_a_mount_made_offline_has_no_repository_on_github(tmp_path, monkeypatch, capsys):
    """A mount whose row names a local folder, and whose checkout has no origin, is no repository the cloud can
    attach: it is said so, never read as a repository on GitHub of the same two names."""
    ada = cloud_ready(tmp_path, monkeypatch)
    brain = tmp_path / "spaces" / "brain"
    brain.mkdir(parents=True)
    data = spaceyaml.load(ada)
    data.data["mounts"] = [{"name": "brain", "repo": str(brain), "path": "brain", "branch": "main"}]
    spaceyaml.save(data)
    calls = github(monkeypatch, {"ada/ada": (True, True), "spaces/brain": (False, None)})
    assert onboard.main(["--routines-cloud", "--check", str(ada)]) == 1
    (line,) = capsys.readouterr().out.strip().splitlines()
    assert "The mount brain of your workspace has no repository on GitHub" in line
    assert "repos/spaces/brain" not in {c[-1] for c in calls}


def test_new_spaces_get_enforce_admins(tmp_path, monkeypatch):
    calls, bodies, main_says = [], [], {"space.yaml": "profile: personal\n", "hold": 0}

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if "--input" in argv:
            bodies.append(json.loads(Path(argv[argv.index("--input") + 1]).read_text(encoding="utf-8")))
        if argv[:2] == ["git", "show"]:
            return subprocess.CompletedProcess(argv, 0, main_says["space.yaml"], "")
        if argv[:2] == ["git", "push"]:   # the first fill lands: main is the space's own from now on
            main_says["space.yaml"] = "profile: personal\n"
        if argv[-1].endswith("/protection/enforce_admins"):
            return subprocess.CompletedProcess(argv, main_says["hold"], "", "gh: Branch not protected (HTTP 404)"
                                               if main_says["hold"] else "")
        return subprocess.CompletedProcess(argv, 0, "ada\n" if argv[:3] == ["gh", "api", "user"] else "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    ada = on_github(make_space(tmp_path / "ada", name="ada"), "https://github.com/ada/ada.git")
    report = onboard.Report(case="test")
    onboard.platform_settings(report, ada, "ada/ada", None, False)   # a space whose main is set up: admins held at once
    assert bodies[-1]["enforce_admins"] is True and bodies[-1]["required_status_checks"]["contexts"] == ["checks"]
    main_says["space.yaml"] = "profile: template\n"   # a new one: its first fill goes to main before the rule binds
    onboard.platform_settings(report, ada, "ada/ada", None, False)
    assert bodies[-1]["enforce_admins"] is False
    del calls[:]
    onboard.commit_fill(report, ada, True, False)
    pushed = calls.index(["git", "push", "-q", "origin", "HEAD:main"])
    held = calls.index(["gh", "api", "-X", "POST", "repos/ada/ada/branches/main/protection/enforce_admins"])
    assert pushed < held and not report.failed(), [s.row() for s in report.failed()]
    assert report.steps[-1].name == "admins held to the checks ada/ada"
    main_says["hold"] = 1   # a project minted before its platform settings: nothing to hold yet, and nothing fails
    main_says["space.yaml"] = "profile: template\n"
    steps = len(report.steps)
    onboard.commit_fill(report, ada, True, False)
    assert len(report.steps) == steps + 1 and not report.failed()


def test_admins_are_held_on_the_repository_given_and_a_missed_hold_is_a_failing_row(tmp_path, monkeypatch):
    calls, main = [], {"says": "profile: template\n"}

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:2] == ["git", "push"]:   # the first fill lands
            main["says"] = "profile: personal\n"
        return subprocess.CompletedProcess(argv, 0, main["says"] if argv[:2] == ["git", "show"] else "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    ada = make_space(tmp_path / "ada", name="ada")   # its origin cannot be read here (no .git folder)
    report = onboard.Report(case="test")
    onboard.commit_fill(report, ada, True, False)
    step = report.steps[-1]
    assert step.name == "admins held to the checks" and step in report.failed() and "enforce_admins" in step.check
    assert not any("enforce_admins" in " ".join(c) for c in calls)
    del calls[:]
    main["says"] = "profile: template\n"
    onboard.commit_fill(report, ada, True, False, repo="ada/ada")
    assert ["gh", "api", "-X", "POST", "repos/ada/ada/branches/main/protection/enforce_admins"] in calls
    assert (report.steps[-1].name, report.steps[-1].done) == ("admins held to the checks ada/ada", True)


def test_taking_the_routines_back_names_the_task_to_disable_first(tmp_path, monkeypatch, capsys):
    ada = make_space(tmp_path / "ada", name="ada")
    cloud_hosted(ada)
    prompt = ada / "_meta" / "state" / "routines" / "sweep-chain.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text('---\nname: sweep-chain\nschedule: "7 7,13,19 * * *"\n---\nSweep.\n', encoding="utf-8")
    said_before_proof = []
    monkeypatch.setattr(onboard, "_no_claude", lambda report: False)
    monkeypatch.setattr(start, "prove_routines", lambda report, ws: said_before_proof.append(
        capsys.readouterr().out) or {"ok": True, "checks": [], "notes": [], "why": None})

    def register(report, label, task, *args, **kwargs):
        report.add(label, True, f"crontab -l | grep {task}")
        return {"name": task, "kind": "cron", "check": ["crontab", "-l"]}
    monkeypatch.setattr(onboard, "register_task", register)
    assert onboard.main([str(ada), "--routines-here"]) == 0
    (first,) = said_before_proof[0].strip().splitlines()   # said before this computer proves anything
    assert first == onboard.CLOUD_OFF_FIRST.format(**CLOUD_TASKS) and "trig_01AAAAAAAA" in first
    record = routine.host(ada)
    assert (record["runner"], record["machine"]) == ("os", routine.this_machine()) and "tasks" not in record
    capsys.readouterr()
    assert onboard.main([str(ada), "--routines-here"]) == 0   # from a computer, nothing to turn off in the cloud
    assert "trig_" not in said_before_proof[1] + capsys.readouterr().out


@pytest.mark.parametrize("flags", [["--routines-cloud"], ["--routines-cloud", "--sweep", "trig_01AAAAAAAA"],
                                   ["--routines-cloud", "--timezone", "UTC"],
                                   ["--sweep", "trig_01AAAAAAAA", "--timezone", "UTC"],
                                   ["--routines-cloud", "--check", "--timezone", "UTC"]])
def test_the_move_takes_its_check_or_both_answers(tmp_path, capsys, flags):
    ada = make_space(tmp_path / "ada", name="ada")
    assert onboard.main([str(ada), *flags]) == 2
    said = capsys.readouterr().out.strip()
    assert len(said.splitlines()) == 1 and "--routines-cloud" in said and "--daily" not in said
    assert routine.host(ada) is None


@pytest.mark.parametrize("flags", [["--until", "2099-01-02"], ["--pause", "--resume"], ["--resume", "--until", "2099-01-02"],
                                   ["--routines-here", "--pause"], ["--routines-here", "--resume"],
                                   ["--routines-cloud", "--check", "--pause"], ["--routines-cloud", "--routines-here"]])
def test_flags_that_do_not_go_together_are_refused_plainly(tmp_path, capsys, flags):
    ada = make_space(tmp_path / "ada", name="ada")
    assert onboard.main([str(ada), *flags]) == 2
    said = capsys.readouterr().out.strip()
    assert len(said.splitlines()) == 1 and "--pause" in said and "one at a time" in said
    assert routine.host(ada) is None


SKILL = TEMPLATE / ".claude" / "skills" / "onboard"
PHRASES = ("pause my routines", "resume my routines", "run my routines here", "run my routines in the cloud")


def test_the_cloud_sentence_reaches_the_onboard_skill_with_its_proof_before_the_move():
    description = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    assert '"run my routines in the cloud"' in description
    text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    section = text[text.index('## "Run my routines in the cloud"'):]
    section = section[:section.index("## ", 3)]
    for needed in ("--routines-cloud --check", "--proof", "RemoteTrigger", "enforce_admins", "--timezone",
                   "--environment"):
        assert needed in section
    assert section.index("--proof") < section.index("--routines-cloud --sweep") and "--daily" not in section


def test_the_skill_reads_a_proof_runs_log_only_and_proves_the_task_the_sweep_already_runs_as():
    text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    never = text[text.index("## Never"):]
    never = never[:never.index("## ", 3)]
    assert "routine's log" in never and "proof run" in never   # the one log it reads, and why it may
    section = text[text.index('## "Run my routines in the cloud"'):]
    section = section[:section.index("## ", 3)]
    proving = section[section.index("2. **Prove the task.**"):section.index("3. **Move.**")]
    assert "already runs" in proving and "update" in proving and "exports and reads nothing" in proving
    # It tries three things, an edit, a tool and a request, and no change made with a command: the platform refuses
    # that change and then the run's landing with it, so a proof with it could never land. Its line is the proof.
    for needed in ("an edit of `rules.md`", "an app tool it was not given", "a request to a host outside the network "
                   "limit", "writes its proof line and lands it", "this computer's sync brought the line here"):
        assert needed in proving
    assert "with a command" not in proving and "left out" not in proving and "--shell" not in proving
    # A proof that shows a refusal missing keeps the task off for good: put back, it would run where the proof showed
    # a refusal does not hold. Proved in place, it was the sweep's own, so the sweep is stopped: say so, and offer to
    # run it on the computer.
    missing = proving[proving.index("**A refusal missing.**"):proving.index("**Anything else.**")]
    for needed in ("`edit`, `tool` or `network` false", "never turn it back on", "stopped", "no longer refuses",
                   '"run my routines here"', "`enabled: false`"):
        assert needed in missing
    assert "enabled: true" not in missing and "put" not in missing
    assert missing.index("`enabled: false`") < missing.index("never turn it back on")
    here = text[text.index("**Run my routines here**"):text.index('## "Run my routines in the cloud"')]
    back_on = here[here.index("turn the cloud task back on"):]
    assert "never when it was off because a proof showed a refusal missing" in back_on[:back_on.index(")")]
    # Proved in place, the task is off and carries the proof body: a move that stops for what says nothing against
    # the proof, here or in step 3, puts it back as it ran, or every computer's sweep steps aside for a cloud that no
    # longer runs it.
    back = proving[proving.index("**Anything else.**"):]
    for needed in ("step 3", "48 hours", "not rendered", "did not merge", "the person stopping", "without `--proof`",
                   "cloud.py trigger <workspace> --chain sweep", "`enabled: true`", "keeps running in the cloud"):
        assert needed in back
    moving = section[section.index("3. **Move.**"):section.index("4. **Say it.**")]
    assert "earlier" in moving and "put back" in moving and "step 2" in moving and "daily keeps running" in moving
    refusing = moving[moving.index("does not refuse"):moving.index("put back")]   # the tool's sentence, before any put back
    assert "refusal missing" in refusing and "leave the task off" in refusing
    raw = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    raw = raw[raw.index('## "Run my routines in the cloud"'):]
    assert all(len(line) <= 120 for line in raw[:raw.index("\n## ", 3)].splitlines())   # wrapped as the rest


def test_is_this_set_up_says_the_cloud_row_though_it_does_not_fail():
    """Only failing rows are shown, but the cloud's row is the one place the agent reads where the routines run in
    the cloud and when each chain last ran, so it is said whenever it is there."""
    text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
    section = text[text.index('## "Is this set up?"'):]
    section = section[:section.index("## ", 3)]
    assert "failing rows" in section and "`routines in the cloud`" in section
    assert onboard.CLOUD_STEP == "routines in the cloud"


def test_the_routine_phrases_reach_the_onboard_skill():
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
