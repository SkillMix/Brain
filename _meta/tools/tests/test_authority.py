"""The badge: levels under floors, agent rows only lower, tools ask before they act."""
import yaml

import agents_md
import authority
import librarian
import pages
import release
import spaceyaml
from conftest import NOW, make_space, mount_brain, write_page


def set_yaml(root, **fields):
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data.update(fields)
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_floors_cap_levels_and_the_validator_refuses_a_rise(tmp_path):
    root = make_space(tmp_path / "ada")
    set_yaml(root, authority={"release": "at_once", "spend": "at_once"})
    errs = spaceyaml.load(root).errors()
    assert any("authority.release may not rise above `after_window`" in e for e in errs)
    assert any("authority.spend may not rise above `propose`" in e for e in errs)
    assert authority.level(spaceyaml.load(root), "release") == "after_window"


def test_the_brains_floors_and_never_leaves_reach_every_space_below(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    set_yaml(brain, floors={"release": "propose"}, never_leaves=["acquisition"])
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    space = spaceyaml.load(ada)
    assert "acquisition" in space.never_leaves and any("equity" in e for e in space.never_leaves)
    assert authority.level(space, "release") == "propose"
    assert any("authority.release may not rise above `propose`" in e for e in space.errors())


def test_an_agent_row_only_lowers_and_must_say_whom_it_acts_for(tmp_path):
    root = make_space(tmp_path / "ada")
    set_yaml(root, agents=[{"name": "helper", "acts_for": "owner", "badge": {"edit_inside": "propose"}},
                           {"name": "loud", "acts_for": "owner", "badge": {"write_knowledge": "at_once"}},
                           {"name": "nobody"}])
    space = spaceyaml.load(root)
    assert authority.level(space, "edit_inside", agent="helper") == "propose"
    assert authority.level(space, "edit_inside") == "at_once"
    errs = space.errors()
    assert any("never rises above the space's level" in e for e in errs)
    assert any("agents[2]" in e for e in errs)


def test_release_at_propose_waits_for_a_persons_yes(tmp_path):
    root = make_space(tmp_path / "ada")
    set_yaml(root, authority={"release": "propose"})
    space = spaceyaml.load(root)
    for pid, approved in (("p-0000e001", None), ("p-0000e002", "Ada Okafor")):
        write_page(root, f"_outbound/queue/{pid}.md", id=pid, type="source", title="A mail",
                   destination="harbor", stage_after="2026-10-11T00:00:00Z", listed=True,
                   **({"class": "window"}), **({"approved_by": approved} if approved else {}))
    ready = release._ready_items(root, NOW, release.ShipReport(), space)
    assert [fm["id"] for _, fm, _ in ready] == ["p-0000e002"]


def test_edit_inside_at_propose_turns_mechanical_moves_into_proposals(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    set_yaml(root, authority={"edit_inside": "propose"})
    write_page(root, "knowledge/topics/stray.md", id="p-0000e011", subject="harbor", title="Stray")
    report = librarian.run(root, now=NOW, scorer=lambda r: (1, 1))
    assert (root / "knowledge/topics/stray.md").exists()
    assert list((root / "_meta" / "state" / "librarian").glob("*.yaml"))
    assert report.proposed


def test_the_contract_states_which_models_may_read(tmp_path):
    root = make_space(tmp_path / "ada")
    set_yaml(root, models=["claude"])
    assert "outside its `models:` list (claude)" in agents_md.render(root)
    open_root = make_space(tmp_path / "ben")
    assert "models:` list" not in agents_md.render(open_root)
