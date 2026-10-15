"""access.py: the repository's readers on the host, against the space's audience."""
import yaml

import access
from conftest import make_space

PEOPLE = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
          {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-gh"}},
          {"id": "chloe", "kind": "people", "title": "Chloe Martin", "match": {"github": "chloe-gh"}}]


def harbor(tmp_path, people=PEOPLE):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor",
                      audience=["Ada Okafor", "Ben Liu"], subjects=people)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/lantern/harbor.git\n',
                                          encoding="utf-8")
    return root


def test_a_reader_outside_the_audience_is_one_item_for_the_owner(tmp_path):
    root = harbor(tmp_path)
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "ben-gh", "chloe-gh"])
    assert report.repo == "lantern/harbor" and report.extra == ["chloe-gh"] and report.unverified == []
    access.write_needs_you(root, report)
    items = yaml.safe_load((root / access.NEEDS_YOU).read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in items] == ["access-chloe-gh"] and items[0]["for"] == "Ada Okafor"


def test_audience_names_without_a_login_are_named_and_unknown_readers_not_guessed(tmp_path):
    root = harbor(tmp_path, people=PEOPLE[:1] + PEOPLE[2:])
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "someone", "chloe-gh"])
    assert report.unverified == ["Ben Liu"] and report.extra == ["chloe-gh"]


def a_space(tmp_path, audience, people=PEOPLE, profile="project"):
    root = make_space(tmp_path / "lantern", profile=profile, name="lantern", owner="Ada Okafor",
                      audience=audience, subjects=people)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/lantern/brain.git\n',
                                          encoding="utf-8")
    return root


def test_everyone_is_every_registered_person_so_colleagues_are_not_flagged(tmp_path):
    root = a_space(tmp_path, "everyone", profile="brain")
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "Ben-GH", "chloe-gh"])
    assert report.extra == [] and report.unverified == []


def test_everyone_still_flags_a_reader_nobody_registered(tmp_path):
    root = a_space(tmp_path, "everyone", profile="brain")
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "ben-gh", "stranger"])
    assert report.extra == ["stranger"]


def test_everyone_leaves_out_an_archived_person(tmp_path):
    people = PEOPLE[:2] + [dict(PEOPLE[2], state="archived")]
    root = a_space(tmp_path, "everyone", people=people, profile="brain")
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "ben-gh", "chloe-gh"])
    assert report.extra == ["chloe-gh"]


def test_everyone_names_a_registered_person_without_a_login(tmp_path):
    people = PEOPLE[:2] + [{k: v for k, v in PEOPLE[2].items() if k != "match"}]
    root = a_space(tmp_path, "everyone", people=people, profile="brain")
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "ben-gh"])
    assert report.unverified == ["Chloe Martin"] and report.extra == []


def test_crew_is_the_owner_when_the_space_lists_no_one_else(tmp_path):
    root = a_space(tmp_path, "crew")
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "ben-gh"])
    assert report.extra == ["ben-gh"] and report.unverified == []


def test_owner_is_the_owner_alone(tmp_path):
    root = a_space(tmp_path, "owner")
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "ben-gh"])
    assert report.extra == ["ben-gh"] and report.unverified == []


def test_nothing_is_written_when_the_host_cannot_list_readers(tmp_path):
    root = harbor(tmp_path)
    report = access.check(root, readers_fn=lambda repo: None)
    assert report.skipped and not (root / access.NEEDS_YOU).exists()
