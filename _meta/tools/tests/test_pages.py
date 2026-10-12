import pages


def test_fingerprint_is_stable_across_line_endings_and_trailing_newlines():
    a = pages.fingerprint("Line one\nLine two\n")
    b = pages.fingerprint("Line one\r\nLine two")
    c = pages.fingerprint("Line one\nLine two\n\n\n")
    assert a == b == c
    assert a != pages.fingerprint("Line one\nLine 2\n")


def test_write_and_read_round_trip(tmp_path):
    path = tmp_path / "knowledge" / "projects" / "harbor" / "brief.md"
    fm = {"id": "p-0a1b2c3d", "type": "entity", "title": "Harbor", "status": "draft",
          "generated": {"by": "test", "at": "2026-10-12T09:00:00Z"}}
    pages.write(path, fm, "Body text.\r\nSecond line.")
    read_fm, body = pages.read(path)
    assert read_fm == fm
    assert body == "Body text.\nSecond line.\n"


def test_new_id_shape_and_id_links():
    assert pages.ID_RE.match(pages.new_id())
    assert pages.id_links("see [the brief](id:p-0a1b2c3d) and [x](id:p-ffffffff)") == [
        "p-0a1b2c3d", "p-ffffffff"]


def test_window_days_prefers_review_every():
    windows = {"snapshot": 7, "concept": 180}
    assert pages.window_days({"type": "concept"}, windows) == 180
    assert pages.window_days({"type": "concept", "review_every": "30d"}, windows) == 30
    assert pages.window_days({"type": "source"}, windows) is None
