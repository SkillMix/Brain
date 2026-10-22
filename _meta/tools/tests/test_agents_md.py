import agents_md
from conftest import make_space


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
