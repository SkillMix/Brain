import registry
import spaceyaml
from conftest import make_space, mount_brain


def test_loop_is_reported(tmp_path):
    root = make_space(tmp_path / "s", subjects=[
        {"id": "a", "kind": "topics", "part_of": "b"},
        {"id": "b", "kind": "topics", "part_of": "a"}])
    errs = registry.Registry(root).check()
    assert any("loop" in e for e in errs)


def test_owner_inherits_from_umbrella_and_home(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", owner="Ada Okafor", subjects=[
        {"id": "sales", "kind": "domains", "owner": "Chloe Martin"},
        {"id": "retailer-deal", "kind": "projects", "part_of": "sales", "home": "brain"},
        {"id": "loose", "kind": "topics"}])
    reg = registry.Registry(root)
    assert reg.owner_of("retailer-deal") == "Chloe Martin"
    assert reg.owner_of("loose") == "Ada Okafor"
    assert reg.home_of("retailer-deal") == "brain"
    assert reg.domain_of("retailer-deal").id == "sales"


def test_match_by_identifier_only_and_mentions_as_hints(tmp_path):
    root = make_space(tmp_path / "s", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor",
         "match": {"label": "Harbor", "channel": "#harbor"}},
        {"id": "beacon", "kind": "projects", "title": "Beacon", "match": {"label": "Beacon"}}])
    reg = registry.Registry(root)
    record = {"subject": "Re: Harbor go-live", "text": "Beacon is unrelated here",
              "identifiers": {"labels": ["Harbor"]}}
    assert [s.id for s in reg.match(record)] == ["harbor"]
    assert sorted(s.id for s in reg.mentions(record)) == ["beacon", "harbor"]
    assert reg.match({"identifiers": {"labels": ["Harbor", "Beacon"]}}) and \
        len(reg.match({"identifiers": {"labels": ["Harbor", "Beacon"]}})) == 2
    assert reg.match({"text": "Harbor only in text", "identifiers": {}}) == []


def test_ancestor_ids_are_reused_not_reminted(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "harbor", "kind": "projects", "home": "harbor"}])
    ada = make_space(tmp_path / "ada", profile="personal", subjects=[
        {"id": "harbor", "kind": "projects"}])
    mount_brain(ada, brain)
    reg = registry.Registry(ada, spaceyaml.load(ada))
    assert any("re-mints" in e for e in reg.check())
    clean = make_space(tmp_path / "ben", profile="personal", subjects=[])
    mount_brain(clean, brain)
    reg2 = registry.Registry(clean, spaceyaml.load(clean))
    assert reg2.get("harbor").space == "brain"
    assert reg2.home_of("harbor") == "harbor"
    try:
        reg2.add({"id": "harbor", "kind": "projects"})
        assert False, "should refuse"
    except ValueError:
        pass


def test_unknown_kind_against_space(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "x", "kind": "departments"}])
    errs = registry.Registry(root, spaceyaml.load(root)).check()
    assert any("unknown kind" in e for e in errs)
