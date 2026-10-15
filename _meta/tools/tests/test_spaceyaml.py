import datetime as dt

import spaceyaml
from conftest import make_space, mount_brain


def test_scale_24h_is_30_minutes_at_time_scale_48(tmp_path):
    root = make_space(tmp_path / "s", mode="fixture", time_scale=48)
    space = spaceyaml.load(root)
    assert space.scale(24) == dt.timedelta(minutes=30)
    assert space.window() == dt.timedelta(minutes=30)
    assert not space.errors()


def test_time_scale_in_live_mode_is_an_error(tmp_path):
    root = make_space(tmp_path / "s", mode="live", time_scale=48)
    errs = spaceyaml.load(root).errors()
    assert any("time_scale" in e for e in errs)


def test_space_now_env_overrides_the_clock(tmp_path, monkeypatch):
    root = make_space(tmp_path / "s")
    monkeypatch.setenv("SPACE_NOW", "2026-10-12T09:00:00Z")
    assert spaceyaml.load(root).now() == dt.datetime(2026, 10, 12, 9, tzinfo=dt.timezone.utc)


def test_personal_space_inherits_kinds_from_brain_mount(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", kinds=["clients", "projects", "people"])
    ada = make_space(tmp_path / "ada", profile="personal")
    mount_brain(ada, brain)
    space = spaceyaml.load(ada)
    assert space.kinds == ["clients", "projects", "people"]
    assert "decisions" in space.all_kinds and "sources" in space.all_kinds
    assert not space.errors()


def test_floors_cannot_be_raised(tmp_path):
    root = make_space(tmp_path / "s", authority={"spend": "at_once"})
    assert any("spend" in e for e in spaceyaml.load(root).errors())


def test_window_floor_and_fixed_kinds(tmp_path):
    root = make_space(tmp_path / "s", profile="brain", window_hours=12, kinds=["clients", "sources"])
    errs = spaceyaml.load(root).errors()
    assert any("window_hours" in e for e in errs)
    assert any("fixed" in e for e in errs)


def test_template_profile_needs_marker(tmp_path):
    root = make_space(tmp_path / "t", profile="template")
    assert not spaceyaml.load(root).errors()
    (root / ".github" / "template-marker").unlink()
    assert any("template" in e for e in spaceyaml.load(root).errors())


def test_save_round_trip(tmp_path):
    root = make_space(tmp_path / "s")
    space = spaceyaml.load(root)
    space.data["standing_choices"] = [{"destination": "harbor", "kind": "mail"}]
    spaceyaml.save(space)
    assert spaceyaml.load(root).standing_choices == [{"destination": "harbor", "kind": "mail"}]


def test_routing_setting_is_off_by_default_and_read_when_present(tmp_path):
    import datetime as dt
    root = make_space(tmp_path / "ada")
    space = spaceyaml.load(root)
    assert space.routing_on is False and space.review_until is None and space.routing_since is None
    root2 = make_space(tmp_path / "ben", routing={"review_until": "2026-10-28", "since": "2026-07-16"})
    space2 = spaceyaml.load(root2)
    assert space2.routing_on is True
    assert space2.review_until == dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc)
    assert space2.routing_since == dt.datetime(2026, 7, 16, tzinfo=dt.timezone.utc)
    assert space2.errors() == []
    root3 = make_space(tmp_path / "eve", routing={"review_until": "soon"})
    assert any("routing" in e for e in spaceyaml.load(root3).errors())


def test_routing_history_since_and_archives(tmp_path):
    """The history window starts at `history_since`; archive-derived pages stay out unless
    `archives` is on (history spec 4.1, understand-first spec 2.3)."""
    space = spaceyaml.load(make_space(tmp_path / "ada", routing={}))
    assert space.routing_history_since is None and space.routing_archives is False
    root2 = make_space(tmp_path / "ben", routing={"since": "2026-07-16", "history_since": "2025-10-01",
                                                   "archives": True})
    space2 = spaceyaml.load(root2)
    assert space2.routing_history_since == dt.datetime(2025, 10, 1, tzinfo=dt.timezone.utc)
    assert space2.routing_archives is True and space2.errors() == []
    errs = spaceyaml.load(make_space(tmp_path / "eve", routing={"history_since": "a year back"})).errors()
    assert any("history_since" in e for e in errs)
    errs = spaceyaml.load(make_space(tmp_path / "fay", routing={"archives": "yes"})).errors()
    assert any("archives" in e for e in errs)
    assert spaceyaml.load(make_space(tmp_path / "gus", routing={"archives": "yes"})).routing_archives is False


def test_the_lists_are_categories_an_agent_reads():
    """Never matched as words: each default entry is a short sentence naming a kind of
    information, never the form it arrives in (verbatim transcript parts and attachments travel)."""
    for key in ("left_out", "never_leaves", "high_stakes"):
        entries = spaceyaml.DEFAULTS[key]
        assert entries and all(len(e.split()) >= 3 for e in entries), key
    left_out = " ".join(spaceyaml.DEFAULTS["left_out"]).lower()
    assert "transcript" not in left_out and "attachment" not in left_out
    assert all(word in left_out for word in ("price", "pay", "password", "client"))


def test_an_exception_list_is_accepted_and_ignored(tmp_path):
    space = spaceyaml.load(make_space(tmp_path / "ada", left_out_except=["transfer pricing"]))
    assert space.errors() == [] and not hasattr(space, "left_out_except")
