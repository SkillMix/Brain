import yaml
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


def _harbor_with(tmp_path, **match):
    root = make_space(tmp_path / "s", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "match": match},
        {"id": "beacon", "kind": "projects", "title": "Beacon", "match": {"label": "Beacon"}}])
    reg = registry.Registry(root)
    return reg, lambda record: [s.id for s in reg.match(record)]


def test_match_by_mail_domain_on_any_address_of_the_record(tmp_path):
    """A client's mail domain attaches mail, calls and events by their addresses, never by text."""
    reg, hit = _harbor_with(tmp_path, domain="harbor.example")
    assert hit({"kind": "mail", "from": "Chloe <chloe@harbor.example>", "to": ["ada@lantern.example"]}) == ["harbor"]
    assert hit({"kind": "mail", "from": "ada@lantern.example", "to": [], "cc": ["Ben@Harbor.Example"]}) == ["harbor"]
    assert hit({"kind": "call", "participants": ["ada@lantern.example", "chloe@eu.harbor.example"]}) == ["harbor"]
    assert hit({"kind": "mail", "from": "eve@notharbor.example", "to": ["harbor.example@lantern.example"]}) == []
    assert hit({"kind": "mail", "from": "ada@lantern.example", "text": "write to chloe@harbor.example"}) == []
    assert reg.check() == []


def test_match_by_title_keyword_for_calls_and_events_only(tmp_path):
    reg, hit = _harbor_with(tmp_path, keywords=["Harbor", "pier work"])
    assert hit({"kind": "call", "subject": "Weekly: Lantern x Harbor"}) == ["harbor"]
    assert hit({"kind": "event", "subject": "Pier work alignment"}) == ["harbor"]
    assert hit({"kind": "event", "title": "Harbor - Lantern"}) == ["harbor"]
    assert hit({"kind": "mail", "subject": "Harbor go-live"}) == [], "mail attaches by domain or label"
    assert hit({"kind": "call", "subject": "Lunch", "text": "Harbor came up"}) == [], "the title only"
    assert hit({"kind": "call", "subject": "Harborview offsite"}) == [], "whole words only"
    reg, hit = _harbor_with(tmp_path / "one", keywords="Harbor")
    assert hit({"kind": "call", "subject": "Harbor weekly"}) == ["harbor"]
    assert reg.check() == []


def test_register_fields_round_trip(tmp_path):
    """The register's fields (history spec 4.4): period, client, crew and work are kept, written
    back only when set, and anything else stays in `extra`."""
    root = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "period": {"from": "2025-03", "to": "2025-09"},
         "client": "tidewater", "crew": ["ada", "ben"], "work": "A data pipeline for the port", "colour": "blue"},
        {"id": "tidewater", "kind": "clients", "title": "Tidewater"}])
    reg = registry.Registry(root)
    harbor = reg.get("harbor")
    assert harbor.period == {"from": "2025-03", "to": "2025-09"}
    assert (harbor.client, harbor.crew, harbor.work) == ("tidewater", ["ada", "ben"], "A data pipeline for the port")
    assert harbor.extra == {"colour": "blue"}
    assert reg.get("tidewater").to_dict() == {"id": "tidewater", "kind": "clients", "title": "Tidewater",
                                              "state": "active"}
    reg.save()
    again = registry.Registry(root).get("harbor")
    assert again.to_dict() == harbor.to_dict()
    assert {k: again.to_dict()[k] for k in ("period", "client", "crew", "work", "colour")} == {
        "period": {"from": "2025-03", "to": "2025-09"}, "client": "tidewater", "crew": ["ada", "ben"],
        "work": "A data pipeline for the port", "colour": "blue"}


def test_matched_by_names_how_a_record_attached(tmp_path):
    reg, _ = _harbor_with(tmp_path, label="Harbor", domain="harbor.example")
    harbor = reg.get("harbor")
    both = {"kind": "mail", "from": "chloe@harbor.example", "identifiers": {"labels": ["Harbor"]}}
    assert reg.matched_by(harbor, both) == {"label", "domain"}
    assert reg.matched_by(harbor, dict(both, identifiers={})) == {"domain"}
    assert reg.matched_by(harbor, {"kind": "mail", "from": "x@lantern.example"}) == set()


def test_engagement_states_are_valid_and_open_deals_stay_live():
    import registry
    for state in ("delivered", "stopped", "open", "won", "lost"):
        assert state in registry.STATES
    assert "open" in registry.LIVE_STATES and "active" in registry.LIVE_STATES
    assert "delivered" not in registry.LIVE_STATES


def _mount(space_root, name, target_root):
    data = yaml.safe_load((space_root / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] = [m for m in (data.get("mounts") or []) if m.get("name") != name] + \
        [{"name": name, "path": str(target_root)}]
    (space_root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_a_home_adds_the_names_its_parent_keeps_out(tmp_path):
    """The brain keeps id, title and home; the home space holds the aliases and match keys, and a
    workspace that mounts the home reads them on top of the brain's entry."""
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor"}])
    home = make_space(tmp_path / "harbor", profile="project", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
         "aliases": ["Lighthouse"], "match": {"domain": "harbor.example", "label": "harbor"}}])
    mount_brain(home, brain)
    own = registry.Registry(home, spaceyaml.load(home))
    assert own.check() == []
    assert own.get("harbor").aliases == ["Lighthouse"]
    ada = make_space(tmp_path / "ada", profile="personal", subjects=[])
    mount_brain(ada, brain)
    _mount(ada, "harbor", home)
    reg = registry.Registry(ada, spaceyaml.load(ada))
    harbor = reg.get("harbor")
    assert harbor.space == "brain" and reg.home_of("harbor") == "harbor"
    assert harbor.aliases == ["Lighthouse"] and harbor.match["domain"] == "harbor.example"
    assert reg.find("Lighthouse").id == "harbor"
    assert [s.id for s in reg.match({"kind": "mail", "from": "chloe@harbor.example"})] == ["harbor"]
    assert registry.Registry(brain).get("harbor").aliases == []


def test_only_the_home_adds_names(tmp_path):
    """Another space mounted beside the brain cannot add names to a subject homed elsewhere."""
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "harbor", "kind": "projects", "home": "harbor"}])
    other = make_space(tmp_path / "beacon", profile="project", subjects=[
        {"id": "harbor", "kind": "projects", "aliases": ["Decoy"]}])
    mount_brain(other, brain)
    assert any("re-mints" in e for e in registry.Registry(other, spaceyaml.load(other)).check())
    ada = make_space(tmp_path / "ada", profile="personal", subjects=[])
    mount_brain(ada, brain)
    _mount(ada, "beacon", other)
    assert registry.Registry(ada, spaceyaml.load(ada)).get("harbor").aliases == []


def test_add_puts_one_client_in_the_registry_with_its_domain_and_owner(tmp_path, capsys):
    root = make_space(tmp_path / "brain", profile="brain", name="lantern-works")
    assert registry.main([str(root), "add", "Dune Bakery", "--kind", "clients", "--domain", "dune.example",
                          "--owner", "Ada Okafor"]) == 0
    out = capsys.readouterr().out
    assert "added dune-bakery" in out and "classifies it" in out
    subject = registry.Registry(root).get("dune-bakery")
    assert subject.kind == "clients" and subject.title == "Dune Bakery" and subject.owner == "Ada Okafor"
    assert subject.match == {"domain": "dune.example"} and registry.Registry(root).check() == []
    assert registry.main([str(root), "add", "dune-bakery", "--kind", "clients"]) == 1   # an id is never re-minted
    assert registry.main([str(root), "add", "Pier", "--kind", "planets"]) == 1           # not one of the kinds
    assert "refused" in capsys.readouterr().out and registry.Registry(root).get("pier") is None


# ----- codebase links (docs/design/2026-10-22-codebase-link-design.md) -----------------------------
def test_registry_check_rejects_bad_repo_entries(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor-voice-agent", "kind": "projects",
        "repos": [{"repo": "northwind/harbor-voice", "readers": "maybe", "linked_by": "guess"}]}])
    errs = registry.Registry(root).check()
    assert any("readers" in e for e in errs) and any("linked_by" in e for e in errs)
    root = make_space(tmp_path / "ben", subjects=[{"id": "harbor-voice-agent", "kind": "projects",
        "repos": [{"readers": "inside"}, {"repo": "northwind/harbor-voice", "checked": "last week"}]}])
    errs = registry.Registry(root).check()
    assert any("no repo" in e for e in errs) and any("checked" in e for e in errs)


def test_registry_check_accepts_a_good_link(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor-voice-agent", "kind": "projects",
        "repos": [{"repo": "northwind/harbor-voice", "repo_id": "R_1", "linked_by": "detect",
                   "readers": "wider", "checked": "2026-10-12", "linked": "2026-10-12"}]}])
    assert registry.Registry(root).check() == []


def test_identifiers_subject_matches_only_its_subject(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor-voice-agent", "kind": "projects"},
                                                   {"id": "voice", "kind": "topics", "match": {"keywords": ["voice"]}}])
    reg = registry.Registry(root)
    record = {"kind": "event", "subject": "voice notes", "identifiers": {"subject": "harbor-voice-agent"}}
    hits = reg.match(record)
    assert [s.id for s in hits] == ["harbor-voice-agent"]
    assert reg.matched_by(hits[0], record) == {"subject"}


def test_identifiers_repo_matches_the_linked_subject(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", subjects=[{"id": "harbor-voice-agent",
        "kind": "projects", "repos": [{"repo": "northwind/harbor-voice"}]},
        {"id": "harbor-old", "kind": "projects", "repos": [{"repo": "northwind/harbor-voice", "gone": "2026-10-01"}]}])
    reg = registry.Registry(root)
    record = {"kind": "document", "identifiers": {"repo": "Northwind/harbor-voice"}}
    hits = reg.match(record)
    assert [s.id for s in hits] == ["harbor-voice-agent"]
    assert reg.matched_by(hits[0], record) == {"repos"}


def test_a_linked_repository_belongs_to_its_subject_alone(tmp_path):
    # harbor names the repository in match.repo (as onboarding writes it); its part harbor-voice-agent links it
    root = make_space(tmp_path / "harbor", profile="project", subjects=[
        {"id": "harbor", "kind": "projects", "match": {"repo": "northwind/harbor-voice"}},
        {"id": "harbor-voice-agent", "kind": "projects", "part_of": "harbor",
         "repos": [{"repo": "northwind/harbor-voice"}]}])
    reg = registry.Registry(root)
    assert [s.id for s in reg.match({"kind": "change", "identifiers": {"repo": "northwind/harbor-voice"}})] == [
        "harbor-voice-agent"]
