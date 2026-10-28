import pytest

import agents_md
from conftest import TEMPLATE, make_space

RULE_SEVEN = ("Agents edit files. The sync script commits: on the computer every 15 minutes, and once at the end of "
              "a cloud routine run. The platform merges. No session runs a git write itself.")
template_only = pytest.mark.skipif(not (TEMPLATE / ".github" / "template-marker").exists(),
                                   reason="reads the template's own rules.md and docs, which a space keeps as its own")


def test_project_profile_has_no_desk_words(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    text = agents_md.render(root)
    low = text.lower()
    assert "journal" not in low
    assert "capture" not in low
    for needle in ("knowledge/", "live/", "Needs you", "rules.md", "docs/shape-1.md",
                   ".claude/skills/", "Profile: **project**", "harbor"):
        assert needle in text


def test_personal_profile_has_the_desk_and_the_daily_note(tmp_path):
    root = make_space(tmp_path / "ada")
    text = agents_md.render(root)
    assert "journal/" in text
    assert "work/" in text
    assert "outbox/" in text
    assert "capture" in text.lower()


def test_brain_and_channel_zones(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain")
    text = agents_md.render(brain)
    assert "identity/" in text
    assert "work/" not in text
    assert "journal" not in text.lower()
    channel = make_space(tmp_path / "chan", profile="channel")
    text = agents_md.render(channel)
    assert "_inbound/" in text and "_outbound/" in text
    assert "knowledge/" not in text
    assert "journal" not in text.lower()


def test_never_list_and_kinds(tmp_path):
    root = make_space(tmp_path / "ada")
    text = agents_md.render(root)
    for word in ("sign", "send", "post", "spend", "delete"):
        assert word in text
    assert "sync script" in text
    assert "status: stable" in text
    for kind in ("clients", "projects", "people", "domains", "topics", "decisions", "sources"):
        assert f"`{kind}`" in text


def test_session_start_and_capture_follow_the_profile(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "_meta" / "templates").mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "templates" / "personal-instruction.md").write_text(
        "Your workspace is `{workspace_path}`.\n", encoding="utf-8")
    text = agents_md.render(root)
    assert "Needs you" in text and "daily note" in text
    assert "{workspace_path}" not in text  # the user-level template is never copied in
    project = make_space(tmp_path / "harbor", profile="project")
    other = agents_md.render(project)
    assert "Nothing is written to a daily note here" in other and "journal" not in other.lower()


def test_check_detects_stale_and_write_is_lf(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "AGENTS.md").write_text("stale\n", encoding="utf-8")
    assert agents_md.main([str(root), "--check"]) == 1
    assert agents_md.main([str(root)]) == 0
    raw = (root / "AGENTS.md").read_bytes()
    assert b"\r" not in raw
    assert raw.decode("utf-8") == agents_md.render(root)
    assert agents_md.main([str(root), "--check"]) == 0
    assert agents_md.check(root) is True


def test_help_prints_usage_and_writes_nothing(tmp_path, monkeypatch, capsys):
    root = make_space(tmp_path / "ada")
    monkeypatch.chdir(root)
    (root / "AGENTS.md").unlink(missing_ok=True)
    try:
        agents_md.main(["--help"])
    except SystemExit as stop:
        assert stop.code == 0
    else:
        raise AssertionError("--help must exit")
    assert "--check" in capsys.readouterr().out
    assert not (root / "AGENTS.md").exists()


def test_session_start_shows_needs_you_once_a_day(tmp_path):
    root = make_space(tmp_path / "ada")
    assert "needs_you.py --once-a-day" in agents_md.render(root)


@template_only
def test_rule_seven_names_the_cloud_landing():
    """A cloud routine run lands its own files once, at its end, through the sync script (cloud routines, spec 5)."""
    rules = " ".join((TEMPLATE / "rules.md").read_text(encoding="utf-8").split())
    assert f"7. {RULE_SEVEN} 8." in rules


@template_only
def test_the_shape_restates_rule_seven_in_rules_md_words():
    """docs/shape-1.md restates the hard rules for a tool, so its rule 7 keeps rules.md's words."""
    shape = " ".join((TEMPLATE / "docs" / "shape-1.md").read_text(encoding="utf-8").split())
    assert f"7. {RULE_SEVEN.removesuffix('.')}, and nothing writes directly into another space." in shape


def test_the_contract_says_rule_seven_as_rules_md_does(tmp_path):
    root = make_space(tmp_path / "ada")
    assert RULE_SEVEN in " ".join(agents_md.render(root).split())
    assert agents_md.main([str(root)]) == 0 and agents_md.main([str(root), "--check"]) == 0
