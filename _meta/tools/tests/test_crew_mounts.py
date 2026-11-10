"""Crew mounts: a workspace mounts every project space its owner is crew on, and a release
never waits on a missing mount alone."""
import subprocess
from pathlib import Path

import yaml

import mint
import onboard
import registry
import release
import spaceyaml
import swap
import sweep
from conftest import NOW, make_space, mount_brain
from test_mint import seeded
from test_swap import code_repo
from test_sweep import BRAIN_SUBJECTS, mount, record, write_feed

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
          "match": {"repo": "lantern/harbor"}}
FIX = "python _meta/tools/mint.py . --mount harbor https://github.com/lantern/harbor"


def never(*args, **kwargs):
    raise AssertionError(f"run_cmd was called offline: {args}")


def workspace(tmp_path, brain, slug="ada", owner="Ada Okafor"):
    root = make_space(tmp_path / slug, name=slug, owner=owner)
    mount_brain(root, brain)
    return root


def crew_rows(steps) -> dict:
    return {s.name: s for s in steps if s.name.startswith("crew project ")}


def set_origin(repo, url):
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")


def brain_items(brain):
    path = brain / "_inbound" / "needs-you" / "crew-mounts.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["items"] if path.exists() else []


# ----- onboard --check ------------------------------------------------------------------
def test_check_fails_when_a_crew_project_space_is_not_mounted(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=[HARBOR])
    ada = workspace(tmp_path, brain)
    row = crew_rows(onboard.check(ada))["crew project harbor mounted"]
    assert not row.done and not row.needs_owner
    assert FIX in row.result


def test_check_passes_once_the_crew_project_is_mounted_by_name_or_by_repository(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=[HARBOR])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor",
                        audience=["Ada Okafor", "Ben Liu"])
    ada = workspace(tmp_path, brain)
    mount(ada, "harbor", harbor)
    assert crew_rows(onboard.check(ada))["crew project harbor mounted"].done
    data = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] = [m for m in data["mounts"] if m["name"] != "harbor"] + [
        {"name": "harbour-space", "repo": "https://github.com/Lantern/harbor.git", "path": str(harbor)}]
    (ada / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert crew_rows(onboard.check(ada))["crew project harbor mounted"].done


def test_check_asks_only_for_project_spaces_the_owner_is_crew_on(tmp_path):
    """Crew: the subject's owner (inherited, else the brain's owner) or its audience. A subject
    whose home is the brain, or that is archived, needs no mount."""
    subjects = [
        dict(HARBOR, owner="Ben Liu", audience=["Ada Okafor", "Ben Liu"]),
        {"id": "beacon", "kind": "projects", "home": "beacon", "owner": "Ben Liu",
         "match": {"repo": "lantern/beacon"}},
        {"id": "lighthouse", "kind": "projects", "home": "lighthouse", "match": {"repo": "lantern/lighthouse"}},
        {"id": "pricing", "kind": "topics", "home": "lantern", "match": {"repo": "lantern/pricing"}},
        {"id": "quarry", "kind": "projects", "home": "quarry", "state": "archived",
         "match": {"repo": "lantern/quarry"}},
        {"id": "dock", "kind": "projects", "home": "dock", "match": {"label": "Dock"}},
    ]
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=subjects)
    ada = workspace(tmp_path, brain)
    assert set(crew_rows(onboard.check(ada))) == {"crew project harbor mounted",
                                                  "crew project lighthouse mounted"}
    chloe = workspace(tmp_path, brain, "chloe", "Chloe Martin")
    assert crew_rows(onboard.check(chloe)) == {}


def test_repository_urls_are_normalised_to_https():
    assert registry.repo_url("lantern/harbor") == "https://github.com/lantern/harbor"
    assert registry.repo_url("git@github.com:lantern/harbor.git") == "https://github.com/lantern/harbor"
    assert registry.repo_url("https://github.com/lantern/harbor") == "https://github.com/lantern/harbor"
    assert registry.repo_url("") is None and registry.repo_url(None) is None
    assert registry.same_repo("https://github.com/Lantern/harbor.git/", "lantern/harbor")
    assert not registry.same_repo("lantern/harbor", "lantern/beacon")


# ----- the mount verb -------------------------------------------------------------------
def test_mount_writes_the_row_and_runs_the_setup_verb_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mint, "run_cmd", lambda argv, dry_run=False, cwd=None: calls.append((list(argv), cwd))
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    ada = make_space(tmp_path / "ada", name="ada")
    url = "https://github.com/lantern/harbor"
    assert mint.main([str(ada), "--mount", "harbor", url]) == 0
    rows = [m for m in spaceyaml.load(ada).mounts if m["name"] == "harbor"]
    assert rows == [{"name": "harbor", "repo": url, "path": "mounts/harbor", "branch": "main"}]
    (argv, cwd), = calls
    assert argv == ["git", "submodule", "add", "-b", "main", url, "mounts/harbor"]
    assert Path(cwd).resolve() == ada.resolve()
    (ada / "mounts" / "harbor").mkdir(parents=True)
    (ada / "mounts" / "harbor" / "space.yaml").write_text("shape: 1\n", encoding="utf-8")
    assert mint.main([str(ada), "--mount", "harbor", url]) == 0
    assert len(calls) == 1 and len([m for m in spaceyaml.load(ada).mounts if m["name"] == "harbor"]) == 1


# ----- swap --go-live and mint ----------------------------------------------------------
def test_swap_go_live_mounts_the_project_for_its_crew(tmp_path, monkeypatch):
    """Crew workspaces on this machine get the mount; a crew member whose workspace is elsewhere
    gets one Needs-you item in the brain with the exact mount line."""
    monkeypatch.setattr(onboard, "run_cmd", never)
    monkeypatch.setattr(mint, "run_cmd", never)
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern")
    ada = workspace(tmp_path, brain)
    repo = code_repo(tmp_path / "harbor")
    set_origin(repo, "https://github.com/lantern/harbor.git")
    swap.run(repo, "harbor", "Ada Okafor", ["Ada Okafor", "Ben Liu"], brain_path=str(brain))
    report = swap.go_live(repo, offline=True)
    assert not report.failed(), [s.row() for s in report.failed()]
    assert spaceyaml.load(ada).mount_root("harbor").resolve() == repo.resolve()
    (item,) = brain_items(brain)
    assert item["for"] == "Ben Liu" and item["id"] == "mount-harbor-ben-liu"
    assert FIX in item["text"] and item["check"]
    before = ((ada / "space.yaml").read_text(encoding="utf-8"), brain_items(brain))
    assert not swap.go_live(repo, offline=True).failed()
    assert ((ada / "space.yaml").read_text(encoding="utf-8"), brain_items(brain)) == before


def test_swap_go_live_applies_the_platform_settings_online(tmp_path, monkeypatch):
    """A swapped project is live from the start, so onboard's go-live switches nothing; the
    swap's go-live still arms auto-merge and the required check on its repository."""
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        calls.append([str(a) for a in argv])
        out = "lantern-bot\n" if argv[:3] == ["gh", "api", "user"] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(onboard, "run_cmd", fake)
    monkeypatch.setattr(mint, "run_cmd", fake)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))  # the sync task's wrapper
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern")
    repo = code_repo(tmp_path / "harbor")
    set_origin(repo, "https://github.com/lantern/harbor.git")
    swap.run(repo, "harbor", "Ada Okafor", ["Ada Okafor"], brain_path=str(brain))
    swap.go_live(repo, home=tmp_path / "spaces-home")
    patch = [c for c in calls if c[:5] == ["gh", "api", "-X", "PATCH", "repos/lantern/harbor"]]
    assert len(patch) == 1 and "allow_auto_merge=true" in patch[0]
    assert ["gh", "api", "-X", "PUT", "repos/lantern/harbor/branches/main/protection"] in [c[:5] for c in calls]


def test_mint_mounts_the_new_project_for_its_crew(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", never)
    monkeypatch.setattr(mint, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    ben = workspace(tmp_path, brain, "ben", "Ben Liu")
    report = mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse",
                      crew=["Ada Okafor", "Ben Liu", "Chloe Martin"], online=False)
    assert not report.failed(), [s.row() for s in report.failed()]
    for root in (ada, ben):
        assert spaceyaml.load(root).mount_root("lighthouse").resolve() == (tmp_path / "lighthouse").resolve()
    assert [(i["for"], i["id"]) for i in brain_items(brain)] == [("Chloe Martin", "mount-lighthouse-chloe-martin")]


# ----- release ----------------------------------------------------------------------------
def bare_clone(tmp_path, src, name):
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run(git + ["init", "--quiet", "-b", "main"], cwd=src, check=True)
    subprocess.run(git + ["add", "-A"], cwd=src, check=True)
    subprocess.run(git + ["commit", "--quiet", "-m", "x"], cwd=src, check=True)
    bare = tmp_path / f"{name}.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(src), str(bare)], check=True)
    return bare


def unmounted(tmp_path, harbor_repo):
    """Ada releases to harbor, whose repository only the brain's registry records."""
    subjects = [dict(s, match=dict(s.get("match") or {}, repo=str(harbor_repo))) if s["id"] == "harbor" else s
                for s in BRAIN_SUBJECTS]
    brain = make_space(tmp_path / "brain", profile="brain", subjects=subjects)
    ada = make_space(tmp_path / "ada", name="ada", sources=[{"name": "mail", "adapter": "gmail", "audience": "crew"}])
    mount_brain(ada, brain)
    return brain, ada


def test_release_reaches_a_destination_through_the_registry_when_no_mount_names_it(tmp_path, monkeypatch):
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                        audience=["Ada Okafor", "Ben Liu"])
    _, ada = unmounted(tmp_path, bare_clone(tmp_path, harbor, "harbor"))
    spaces_home = tmp_path / "spaces-home"
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))
    monkeypatch.setenv("SPACES_LOCAL_CLONES", "1")   # the bare repository is a local path
    monkeypatch.setattr(release, "publish", lambda checkout_path, slug, source, write: (write(), True)[1])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW)
    assert report.unreachable == 0 and report.shipped + report.signals >= 1
    assert list((spaces_home / "harbor" / "_inbound").rglob("*.*"))


def test_release_never_publishes_into_a_registry_repository_that_is_not_the_space(tmp_path, monkeypatch):
    code = tmp_path / "harbor-site"
    code.mkdir()
    (code / "README.md").write_text("# Harbor site\n", encoding="utf-8")
    _, ada = unmounted(tmp_path, bare_clone(tmp_path, code, "harbor-site"))
    spaces_home = tmp_path / "spaces-home"
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))
    monkeypatch.setattr(release, "publish", never)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW)
    assert report.shipped == 0 and report.unreachable == 1
    assert not (spaces_home / "harbor").exists()
    assert len(list((ada / "_outbound" / "queue").glob("*.md"))) == 1
