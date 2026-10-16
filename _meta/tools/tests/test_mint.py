"""mint: a desk seed becomes a project space, offline."""
import subprocess
from pathlib import Path

import pytest

import mint
import onboard
import pages
import registry
import spaceyaml
import validate
import yaml
from conftest import make_space, mount_brain, write_page


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def never(*args, **kwargs):
    raise AssertionError(f"run_cmd was called offline: {args}")


def seeded(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    ada = make_space(tmp_path / "ada", name="ada")
    mount_brain(ada, brain)
    seed = ada / "work" / "projects" / "lighthouse" / "kickoff"
    seed.mkdir(parents=True)
    (seed / "brief.md").write_text("# Brief\n\nA lighthouse refit for the harbour board.\n",
                                   encoding="utf-8")
    write_page(ada, "work/projects/lighthouse/kickoff/plan.md", id="p-0000beef", type="process",
               title="Plan", status="stable", signed_by="Ada Okafor",
               signed_at="2026-10-12T09:00:00Z", fingerprint=pages.fingerprint("Body.\n"))
    return brain, ada


def test_mint_offline_creates_project_space_from_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    report = mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse",
                      crew=["Ada Okafor", "Ben Liu"], online=False)
    assert not report.failed(), [s.row() for s in report.failed()]
    project = tmp_path / "lighthouse"
    space = spaceyaml.load(project)
    assert space.profile == "project"
    assert space.audience == ["Ada Okafor", "Ben Liu"]
    assert space.parent == "brain" and space.owner == "Ada Okafor"
    assert space.mount_root("brain") is not None
    assert not (project / "work").exists() and not (project / "journal").exists()
    brief = project / "knowledge" / "projects" / "lighthouse" / "brief.md"
    plan = project / "knowledge" / "projects" / "lighthouse" / "plan.md"
    fm, body = pages.read(brief)
    assert pages.ID_RE.match(fm["id"]) and fm["type"] == "entity" and fm["title"] == "Brief"
    assert fm["status"] == "draft" and fm["generated"]["by"] == "mint"
    assert "lighthouse refit" in body
    fm2, _ = pages.read(plan)
    assert fm2["id"] == "p-0000beef" and fm2["status"] == "draft" and fm2["title"] == "Plan"
    assert "signed_by" not in fm2 and "fingerprint" not in fm2
    reg = registry.Registry(ada)
    subject = reg.get("lighthouse")
    assert subject is not None and subject.kind == "projects" and subject.home == "lighthouse"
    assert errors(project) == [] and errors(ada) == []
    items = yaml.safe_load((ada / "_inbound" / "needs-you" / "mint.yaml").read_text())["items"]
    assert any("lighthouse-crew" in i["text"] and "Ben Liu" in i["text"] for i in items)
    assert all(i["kind"] == "question" for i in items)
    receipt = yaml.safe_load((ada / "_meta" / "state" / "mint-lighthouse.yaml").read_text())
    assert receipt["phases"]["copy"] == 2 and receipt["name"] == "lighthouse"
    assert any(s.needs_owner and "team" in s.name for s in report.steps)


def test_mint_is_rerunnable_and_sets_home_on_existing_subject(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    reg = registry.Registry(ada)
    reg.add({"id": "lighthouse", "kind": "projects", "title": "Lighthouse"}, by="test")
    reg.save()
    mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse", crew=["Ada Okafor"],
             online=False)
    first = pages.read(tmp_path / "lighthouse" / "knowledge" / "projects" / "lighthouse" / "brief.md")[0]
    again = mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse",
                     crew=["Ada Okafor"], online=False)
    assert not again.failed()
    second = pages.read(tmp_path / "lighthouse" / "knowledge" / "projects" / "lighthouse" / "brief.md")[0]
    assert first["id"] == second["id"]
    reg = registry.Registry(ada)
    assert len(reg.all()) == 1 and reg.get("lighthouse").home == "lighthouse"
    items = yaml.safe_load((ada / "_inbound" / "needs-you" / "mint.yaml").read_text())["items"]
    assert len([i for i in items if i["id"] == "mint-lighthouse-team"]) == 1


def test_mint_rejects_a_path_outside_the_desk(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    try:
        mint.run(ada, seed="knowledge/projects/x", name="x", crew=[], online=False)
    except ValueError as exc:
        assert "work/" in str(exc)
    else:
        raise AssertionError("a seed outside work/ was accepted")


def test_mint_links_channel_and_tracker_by_name_and_proposes_them(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse", crew=["Ada Okafor"], online=False)
    subject = registry.Registry(ada).get("lighthouse")
    assert subject.match.get("channel") == "#lighthouse" and subject.match.get("tracker") == "LIGHTHOUSE"
    items = yaml.safe_load((ada / "_inbound" / "needs-you" / "mint.yaml").read_text(encoding="utf-8"))["items"]
    ids = {i["id"] for i in items}
    assert {"mint-lighthouse-channel", "mint-lighthouse-tracker"} <= ids


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
            for p in sorted(Path(root).rglob("*"))}


@pytest.mark.parametrize("mode", ["seed", "seed-offline", "channel", "vault", "mount"])
def test_a_dry_run_leaves_the_tree_untouched(tmp_path, monkeypatch, mode):
    """No new space, receipt, registry line, Needs-you item or crew mount row: only the plan."""
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        calls.append(dry_run)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    brain, ada = seeded(tmp_path)
    (ada / "_meta" / "state" / "template-origin.yaml").write_text("repo: lantern/brain-template\n",
                                                                  encoding="utf-8")
    other = make_space(tmp_path / "ben", name="ben", owner="Ben Liu")
    argv = {"seed": ["--seed", "work/projects/lighthouse/kickoff", "--name", "lighthouse",
                     "--crew", "Ada Okafor", "Ben Liu", "--org", "lantern"],
            "seed-offline": ["--seed", "work/projects/lighthouse/kickoff", "--name", "lighthouse",
                             "--crew", "Ada Okafor", "Ben Liu", "--offline"],
            "channel": ["--channel", str(other), "--org", "lantern"],
            "vault": ["--vault", "--name", "pay", "--audience", "Ada Okafor", "--org", "lantern"],
            "mount": ["--mount", "harbor", "https://github.com/lantern/harbor"]}[mode]
    before = tree(tmp_path)
    assert mint.main([str(ada), *argv, "--home", str(tmp_path / "spaces"), "--dry-run"]) == 0
    after = tree(tmp_path)
    assert [p for p in sorted(set(before) | set(after)) if before.get(p, "gone") != after.get(p, "gone")] == []
    assert all(calls)


def test_a_sub_team_mounts_every_ancestor_flat(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", never)
    monkeypatch.setattr(onboard, "run_cmd", never)
    brain, ada = seeded(tmp_path)
    division = make_space(tmp_path / "delivery", profile="project", name="delivery")
    mount_brain(division, brain)
    data = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"].append({"name": "delivery", "repo": "https://example.test/org/delivery", "path": str(division)})
    (ada / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    report = mint.run(ada, seed="work/projects/lighthouse/kickoff", name="lighthouse", crew=["Ada Okafor"],
                      online=False, parent="delivery")
    assert not report.failed(), [s.row() for s in report.failed()]
    space = spaceyaml.load(tmp_path / "lighthouse")
    assert space.parent == "delivery"
    assert {m["name"] for m in space.mounts} == {"brain", "delivery"}
    assert space.mount_root("delivery") is not None and space.mount_root("brain") is not None
