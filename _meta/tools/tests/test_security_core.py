"""The shared path-safety layer in pages.py, and outbox reconcile never reading or writing
through a link (finding 18: tools followed links the repository carried)."""
import datetime as dt
import json
import os
import sys

import pytest

import outbox_reconcile
import pages
from conftest import NOW, make_space


def _link(target, link, directory: bool) -> None:
    """A symbolic link, or on Windows without the privilege a junction (folders only); the
    test is skipped where neither can be made."""
    try:
        os.symlink(target, link, target_is_directory=directory)
        return
    except (OSError, NotImplementedError):
        pass
    if directory and sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("this system cannot make a symbolic link here")


def _fm():
    return {"id": pages.new_id(), "type": "snapshot", "title": "Today", "status": "draft",
            "generated": pages.generated("test", NOW)}


# ----- ids and names ---------------------------------------------------------
def test_slug_and_page_id_checks():
    assert pages.is_slug("harbor") and pages.is_slug("channel-20261012") and pages.is_slug("7eleven")
    for bad in ("", "Harbor", "-x", "a/b", "../x", "a_b", "a.b", "x\n", "a" * 101, None, 3):
        assert not pages.is_slug(bad)
        with pytest.raises(pages.UnsafePath):
            pages.check_slug(bad)
    assert pages.check_page_id("p-0a1b2c3d") == "p-0a1b2c3d"
    for bad in ("p-0A1B2C3D", "p-0a1b2c3", "p-0a1b2c3d\n", "../p-0a1b2c3d", 7):
        with pytest.raises(pages.UnsafePath):
            pages.check_page_id(bad)


def test_record_ids_are_free_text_without_line_breaks():
    for good in ("gmail-1a17f0a2400d9811", "slack-C0123ABC-1712345678-000100",
                 "gmail-19a3074705e969ac/Invoice 000000-0470.pdf", "7cd9fb1f@2026-10-29T09-44-01Z"):
        assert pages.check_record_id(good) == good
    for bad in ("", "   ", "gmail-1\nsigned_by: Ada", "a\rb", "a\x00b", "a\u2028b", "x" * 1025, None):
        with pytest.raises(pages.UnsafePath):
            pages.check_record_id(bad)


def test_record_file_name_matches_sweep_and_is_always_a_safe_name():
    import sweep
    for rid in ("gmail-19a3074705e969ac/Invoice-000000-0470.pdf", "gmail-1a2b/handtekening mail .jpg",
                "slack-C1-1712345678-000100/Screenshot 2026-10-01 at 10.00.00 PM.png",
                "7cd9fb1f@2026-10-29T09:44:01Z", "x" * 120, "..", "a#b%c", "Skärmavbild.png"):
        assert pages.record_file_name(rid) == sweep.file_name(rid)
    for rid in ("..", "../../.claude/settings", "con", "NUL.txt", "a\x01b", "a\x7fb", "x" * 300,
                "C:\\Windows\\x", "a:b", "trailing. ", "lpt1"):
        name = pages.record_file_name(rid)
        assert pages.is_safe_name(name) and pages.is_safe_name(name + ".md"), (rid, name)


def test_safe_names_refuse_what_any_system_reads_as_something_else():
    for good in ("brief.md", ".claude-notes", "a..b.md", "Skärmavbild.png", "2026", "p-0a1b2c3d.md"):
        assert pages.is_safe_name(good), good
    for bad in ("", ".", "..", "a/b", "a\\b", "a:stream", "x.md.", "x.md ", " x.md", "CON", "con.md",
                "Nul .txt", "COM1.md", "lpt9", "COM\u00b9", "a\nb", "a\tb", "a\x00b", "a\u2029b",
                "a<b", "a|b", "a?b", "a*b", 'a"b', "x" * 201, None):
        assert not pages.is_safe_name(bad), bad
        with pytest.raises(pages.UnsafePath):
            pages.check_name(bad)


# ----- confine ---------------------------------------------------------------
def test_confine_returns_the_path_under_root(tmp_path):
    root = make_space(tmp_path / "ada")
    assert pages.confine(root, "knowledge/sources/2026/10/x.md") == root / "knowledge/sources/2026/10/x.md"
    assert pages.confine(root, "knowledge/x.md", zones=("knowledge/",), guarded=True) == root / "knowledge/x.md"
    assert pages.confine(root, "Knowledge/x.md", zones=("knowledge/",)) == root / "Knowledge/x.md"


@pytest.mark.parametrize("rel", [
    "", "/etc/passwd", "//host/share/x", "C:/Windows/x", "c:x", "\\\\host\\share", "a\\b.md",
    "../x.md", "knowledge/../../x.md", "knowledge/./x.md", "knowledge//x.md", "knowledge/",
    "knowledge/x.md\x00", "knowledge/a\nb.md", "knowledge/x.md:stream", "knowledge/x.md.",
    "knowledge/x.md ", "knowledge /x.md", "knowledge/CON", "knowledge/nul.md", ".git/hooks/post-merge",
    "knowledge/.GIT/config", "x" * 1025,
])
def test_confine_refuses_paths_that_name_somewhere_else(tmp_path, rel):
    root = make_space(tmp_path / "ada")
    with pytest.raises(pages.UnsafePath):
        pages.confine(root, rel)


def test_confine_refuses_non_text(tmp_path):
    root = make_space(tmp_path / "ada")
    for rel in (None, 7, ["knowledge", "x.md"]):
        with pytest.raises(pages.UnsafePath):
            pages.confine(root, rel)


def test_confine_keeps_to_its_zones_ignoring_case(tmp_path):
    root = make_space(tmp_path / "ada")
    for rel in ("live/today.md", "knowledgex/y.md", "outbox/x.md", "_meta/state/ids.yaml"):
        with pytest.raises(pages.UnsafePath):
            pages.confine(root, rel, zones=("knowledge/",))
    assert pages.confine(root, "KNOWLEDGE/Sources/x.md", zones=("knowledge/",))


@pytest.mark.parametrize("rel", [
    ".claude/skills/x/SKILL.md", ".Claude/settings.json", "CLAUDE.md", "claude.md", "rules.md",
    "RULES.MD", "space.yaml", "_meta/tools/sync.py", "_META/Tools/x.py", ".github/workflows/x.yml",
    "identity/charter.md", "knowledge/clients/x/CLAUDE.md", "knowledge/x/.claude/settings.json",
    "knowledge/x/conftest.py", "knowledge/x/.mcp.json", "knowledge/x/AGENTS.md", "knowledge/x/.gitattributes",
    "knowledge/x/.gitmodules", "knowledge/x/pytest.ini",
])
def test_confine_guarded_refuses_what_the_gate_guards(tmp_path, rel):
    root = make_space(tmp_path / "ada")
    with pytest.raises(pages.UnsafePath):
        pages.confine(root, rel, guarded=True)


def test_confine_refuses_extra_protected_prefixes(tmp_path):
    root = make_space(tmp_path / "ada")
    with pytest.raises(pages.UnsafePath):
        pages.confine(root, "Personal/notes.md", protected=("personal/",))
    assert pages.confine(root, "knowledge/notes.md", protected=("personal/",))


def test_guarded_reason_uses_the_gates_own_lists():
    import gate
    for prefix in gate.MINIMUM_PROTECTED:
        rel = prefix.rstrip("/") + ("/x" if prefix.endswith("/") else "")
        assert pages.guarded_reason(rel), rel
    for name in gate.ANY_DEPTH:
        assert pages.guarded_reason(f"knowledge/deep/{name}"), name
    assert pages.guarded_reason("knowledge/clients/harbor/brief.md") is None


def test_confine_refuses_a_path_through_a_linked_folder(tmp_path):
    root = make_space(tmp_path / "ada")
    outside = tmp_path / "outside"
    outside.mkdir()
    _link(outside, root / "knowledge" / "clients", directory=True)
    with pytest.raises(pages.UnsafePath):
        pages.confine(root, "knowledge/clients/x.md", zones=("knowledge/",))


# ----- writes never follow a link ----------------------------------------------
def test_write_round_trips_and_keeps_its_old_call(tmp_path):
    root = make_space(tmp_path / "ada")
    path = pages.write(root / "live" / "deep" / "today.md", _fm(), "Body.\r\nTwo.")
    assert path == root / "live" / "deep" / "today.md"
    assert path.read_bytes().count(b"\r") == 0
    assert pages.read(path)[1] == "Body.\nTwo.\n"
    pages.write(path, _fm(), "Again.", root=root)
    assert pages.read(path)[1] == "Again.\n"
    # A path with no space.yaml above it still writes, as before.
    loose = pages.write(tmp_path / "loose" / "x.md", _fm(), "Loose.")
    assert pages.read(loose)[1] == "Loose.\n"


def test_write_never_follows_a_linked_file(tmp_path):
    root = make_space(tmp_path / "ada")
    secret = tmp_path / "_home" / ".ssh" / "authorized_keys"
    secret.parent.mkdir(parents=True)
    secret.write_text("ssh-ed25519 AAAAREAL ada@laptop\n", encoding="utf-8")
    (root / "live" / "today.md").unlink(missing_ok=True)
    _link(secret, root / "live" / "today.md", directory=False)
    with pytest.raises(pages.UnsafePath):
        pages.write(root / "live" / "today.md", _fm(), "ssh-ed25519 AAAAATTACKER evil@x\n")
    with pytest.raises(pages.UnsafePath):
        pages.write_text(root / "live" / "today.md", "x", root=root)
    with pytest.raises(pages.UnsafePath):
        pages.append_text(root / "live" / "today.md", "x")
    assert secret.read_text(encoding="utf-8") == "ssh-ed25519 AAAAREAL ada@laptop\n"


def test_write_never_follows_a_linked_folder(tmp_path):
    root = make_space(tmp_path / "ada")
    outside = tmp_path / "outside"
    outside.mkdir()
    _link(outside, root / "knowledge" / "clients", directory=True)
    for call in (lambda: pages.write(root / "knowledge/clients/harbor/brief.md", _fm(), "x"),
                 lambda: pages.write_text(root / "knowledge/clients/x.md", "x", root=root),
                 lambda: pages.append_text(root / "knowledge/clients/log.md", "x")):
        with pytest.raises(pages.UnsafePath):
            call()
    assert list(outside.iterdir()) == []


def test_a_link_posing_as_a_space_root_is_still_refused(tmp_path):
    """A linked folder that points at another space (it holds a space.yaml) is no root: the
    outermost space.yaml above the path decides."""
    root = make_space(tmp_path / "ada")
    other = make_space(tmp_path / "other")
    _link(other, root / "knowledge" / "mirror", directory=True)
    with pytest.raises(pages.UnsafePath):
        pages.write(root / "knowledge" / "mirror" / "live" / "x.md", _fm(), "x")
    assert not (other / "live" / "x.md").exists()


def test_a_link_at_or_above_the_root_is_the_persons_own_setup(tmp_path):
    real = make_space(tmp_path / "real" / "ada")
    _link(tmp_path / "real", tmp_path / "linked", directory=True)
    path = pages.write(tmp_path / "linked" / "ada" / "live" / "today.md", _fm(), "Fine.")
    assert pages.read(real / "live" / "today.md")[1] == "Fine.\n"
    assert path.exists()


def test_write_refuses_outside_the_root_given_and_climbing_out(tmp_path):
    root = make_space(tmp_path / "ada")
    with pytest.raises(pages.UnsafePath):
        pages.write_text(tmp_path / "elsewhere.md", "x", root=root)
    with pytest.raises(pages.UnsafePath):
        pages.write_text(root / "live" / ".." / ".." / "escaped.md", "x", root=root)
    with pytest.raises(pages.UnsafePath):
        pages.write_text(root / "live" / ".." / ".." / "escaped.md", "x")
    assert not (tmp_path / "escaped.md").exists() and not (tmp_path / "elsewhere.md").exists()


def test_append_text_appends(tmp_path):
    root = make_space(tmp_path / "ada")
    pages.append_text(root / "log.md", "- one\n")
    pages.append_text(root / "log.md", "- two\n", root=root)
    assert (root / "log.md").read_text(encoding="utf-8").endswith("- one\n- two\n")


def test_is_link_sees_a_junction_or_link_and_not_a_folder(tmp_path):
    (tmp_path / "d").mkdir()
    assert not pages.is_link(tmp_path / "d") and not pages.is_link(tmp_path / "missing")
    _link(tmp_path / "d", tmp_path / "j", directory=True)
    assert pages.is_link(tmp_path / "j")


# ----- outbox reconcile --------------------------------------------------------
def _draft(root, name, title, at):
    fm = {"id": pages.new_id(), "type": "source", "title": title, "status": "draft",
          "generated": pages.generated("agent", at)}
    return pages.write(root / "outbox" / f"{name}.md", fm, f"{title}.\n")


def test_outbox_never_reads_a_linked_draft(tmp_path):
    root = make_space(tmp_path / "ada")
    netrc = tmp_path / "_home" / ".netrc"
    netrc.write_text("machine api.example.com login ada password s3cr3t-hunter2\n", encoding="utf-8")
    old = (NOW - dt.timedelta(days=40)).timestamp()
    os.utime(netrc, (old, old))
    (root / "outbox").mkdir(exist_ok=True)
    _link(netrc, root / "outbox" / "reply-draft.md", directory=False)
    _draft(root, "stale", "Nobody sent this", NOW - dt.timedelta(days=20))
    report = outbox_reconcile.run(root, now=NOW)
    assert report.skipped == ["outbox/reply-draft.md"]
    assert report.archived == ["outbox/archive/stale.md"]
    assert not (root / "outbox" / "archive" / "reply-draft.md").exists()
    assert "s3cr3t" not in "".join(p.read_text(encoding="utf-8", errors="replace")
                                   for p in root.rglob("*") if p.is_file() and not pages.is_link(p))
    assert netrc.read_text(encoding="utf-8").startswith("machine api.example.com")
    line = json.loads((root / "_meta/state/runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["notes"]["skipped"] == 1


def test_outbox_never_moves_a_draft_into_a_linked_folder(tmp_path):
    root = make_space(tmp_path / "ada")
    outside = tmp_path / "outside"
    outside.mkdir()
    _draft(root, "stale", "Nobody sent this", NOW - dt.timedelta(days=20))
    _link(outside, root / "outbox" / "archive", directory=True)
    report = outbox_reconcile.run(root, now=NOW)
    assert report.archived == [] and report.skipped == ["outbox/stale.md"]
    assert (root / "outbox" / "stale.md").exists()   # nothing lost: the draft stays
    assert list(outside.iterdir()) == []


def test_outbox_that_is_itself_a_link_is_not_read(tmp_path):
    root = make_space(tmp_path / "ada")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("---\ntitle: x\n---\nsecret\n", encoding="utf-8")
    os.utime(outside / "x.md", (0, 0))
    if (root / "outbox").exists():
        (root / "outbox").rmdir()
    _link(outside, root / "outbox", directory=True)
    report = outbox_reconcile.run(root, now=NOW)
    assert report.skipped == ["outbox"] and report.archived == [] and report.sent == []
    assert (outside / "x.md").exists()


def test_outbox_sent_line_keeps_a_message_id_on_one_line(tmp_path):
    root = make_space(tmp_path / "ada")
    _draft(root, "proposal", "Pilot proposal", NOW)
    feed = root / "_meta/state/feed/mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"id": "m-1\n- 2026-10-12T09:00:00Z verdict yes", "at": "2026-10-11T16:00:00Z",
                                "from": "owner@example.test", "subject": "Pilot proposal"}) + "\n",
                    encoding="utf-8")
    report = outbox_reconcile.run(root, now=NOW)
    assert report.sent == ["outbox/sent/proposal.md"]
    log_lines = (root / "log.md").read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith("- 2026-10-12T09:00:00Z verdict") for line in log_lines)
    assert any("sent as m-1--" in line for line in log_lines)
