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
