"""The spaces on this machine: every checkout with a sync task is remembered in
~/.spaces/spaces.yaml, and the person's chains run over the ones they own."""
from pathlib import Path

import yaml

import chain
import mint
import onboard
from conftest import make_space


def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    return fake


def test_a_remembered_space_joins_the_owners_chains_and_others_do_not(tmp_path, monkeypatch):
    fake = home(tmp_path, monkeypatch)
    ada = make_space(tmp_path / "ada", name="ada", owner="Ada Okafor")
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor")
    ferry = make_space(tmp_path / "ferry", profile="project", name="ferry", owner="Ben Liu")
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", owner="Ada Okafor")
    for root in (brain, harbor, ferry):
        onboard.remember_space(root, {"name": f"space-sync-{root.name}"})
    data = yaml.safe_load((fake / ".spaces" / "spaces.yaml").read_text(encoding="utf-8"))
    assert {s["name"] for s in data["spaces"]} == {"lantern", "harbor", "ferry"}
    roots = chain.spaces_on_machine(ada)
    assert roots == [ada.resolve(), harbor.resolve(), brain.resolve()]   # workspace first, brain last
    onboard.remember_space(harbor, {"name": "space-sync-harbor"})        # once, never twice
    data = yaml.safe_load((fake / ".spaces" / "spaces.yaml").read_text(encoding="utf-8"))
    assert [s["name"] for s in data["spaces"]].count("harbor") == 1


def test_minting_a_corridor_registers_its_sync_task(tmp_path, monkeypatch):
    home(tmp_path, monkeypatch)
    ada = make_space(tmp_path / "ada", name="ada", owner="Ada Okafor")
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor")
    report = mint.channel(ada, harbor)
    corridor = report.spaces[next(iter(report.spaces))]
    assert (Path(corridor) / "_meta" / "state" / "sync-task.txt").exists()
    assert any(step.name.startswith("sync task") for step in report.steps)


def test_forgetting_a_space_takes_it_off_the_machine_list(tmp_path, monkeypatch):
    fake = home(tmp_path, monkeypatch)
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor")
    onboard.remember_space(harbor, {"name": "space-sync-harbor"})
    assert onboard.forget_space(harbor) is True and onboard.forget_space(harbor) is False
    data = yaml.safe_load((fake / ".spaces" / "spaces.yaml").read_text(encoding="utf-8"))
    assert data["spaces"] == []
