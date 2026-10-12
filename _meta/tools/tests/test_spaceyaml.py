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
