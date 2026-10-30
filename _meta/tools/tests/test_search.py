"""The engine file and the search command: plain search by default, an index only when chosen."""
import json
import shutil

import pytest
import yaml

import search
import validate
from conftest import make_space, mount_brain, write_page


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def test_config_defaults_and_validation(tmp_path):
    root = make_space(tmp_path / "ada")
    cfg = search.config(root)
    assert cfg["engine"] == "none" and cfg["link_step"] == "off" and cfg["atoms"] == "off"
    (root / "_meta" / "search.yaml").write_text(yaml.safe_dump({"engine": "none", "link_step": "on"}),
                                                encoding="utf-8")
    assert any("link_step" in e for e in errors(root))
    (root / "_meta" / "search.yaml").write_text(yaml.safe_dump(
        {"engine": "none", "link_step": "on",
         "measured": [{"feature": "link_step", "gain": 0.05, "at": "2026-10-01"}]}), encoding="utf-8")
    assert not any("link_step" in e for e in errors(root))


def test_plain_query_searches_every_layer_and_expands_to_the_subject(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "sales", "kind": "domains", "title": "Sales"}])
    write_page(brain, "knowledge/domains/sales/pipeline.md", type="concept", title="Pipeline",
               subject="sales", body="The pilot budget is 40k.\n")
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    mount_brain(root, brain)
    write_page(root, "knowledge/projects/harbor/harbor.md", type="entity", title="Harbor",
               subject="harbor", body="Harbor pilot goes live on 3 November.\n")
    hits = search.query(root, "pilot")
    by_subject = {(h.layer, h.subject) for h in hits}
    assert ("", "harbor") in by_subject and ("brain", "sales") in by_subject
    assert all(h.line for h in hits)
    assert search.query(root, "nothing like this") == []


def test_index_engine_builds_queries_and_prunes(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    page = write_page(root, "knowledge/projects/harbor/harbor.md", type="entity", title="Harbor",
                      subject="harbor", body="Harbor migration dry run on the 14th.\n")
    (root / "_meta" / "search.yaml").write_text(yaml.safe_dump({"engine": "index"}), encoding="utf-8")
    built = search.build(root)
    assert built["files"] >= 1 and (root / "_meta" / "state" / "search-index.json").exists()
    assert [h.subject for h in search.query(root, "migration")] == ["harbor"]
    page.unlink()
    dropped = search.prune(root)
    assert dropped == 1
    assert search.query(root, "migration") == []
    assert search.main([str(root), "query", "harbor"]) in (0, 1)


def ringed_workspace(tmp_path):
    """ada's workspace mounting the brain (a lesson page), harbor (home of harbor-voice-agent)
    and beacon (another client's project); every layer, and ada's own pages, say "webhook"."""
    from test_link import mount
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "harbor", "kind": "projects", "client": "northwind", "home": "harbor"},
        {"id": "harbor-voice-agent", "kind": "projects", "part_of": "harbor", "home": "harbor"},
        {"id": "harbor-ops", "kind": "projects", "part_of": "harbor-voice-agent", "home": "harbor"},
        {"id": "beacon-app", "kind": "projects", "client": "beacon", "home": "beacon"}])
    write_page(brain, "knowledge/topics/lessons/retries.md", body="Retry a webhook twice.\n")
    harbor = make_space(tmp_path / "harbor", profile="project")
    write_page(harbor, "knowledge/projects/harbor-voice-agent/calls.md", body="The webhook fires on hang-up.\n")
    beacon = make_space(tmp_path / "beacon", profile="project")
    write_page(beacon, "knowledge/projects/beacon-app/hooks.md", body="Beacon's webhook signs with a key.\n")
    ws = make_space(tmp_path / "ada")
    mount_brain(ws, brain)
    mount(ws, "harbor", harbor)
    mount(ws, "beacon", beacon)
    for sid in ("harbor-voice-agent", "harbor", "harbor-ops", "beacon-app"):
        write_page(ws, f"knowledge/projects/{sid}/notes.md", subject=sid, body=f"{sid} webhook notes.\n")
    return ws


def test_query_for_subject_keeps_rings_one_and_two(tmp_path):
    ws = ringed_workspace(tmp_path)
    for engine in ("none", "index"):   # the plain path and the engine path alike
        (ws / "_meta" / "search.yaml").write_text(yaml.safe_dump({"engine": engine}), encoding="utf-8")
        hits = search.query(ws, "webhook", for_subject="harbor-voice-agent")
        assert {h.layer for h in hits} == {"", "brain", "harbor"}
        assert {h.subject for h in hits if h.layer == ""} == {"harbor-voice-agent", "harbor", "harbor-ops"}
    assert search.main([str(ws), "query", "--for", "harbor-voice-agent", "webhook"]) == 0


def test_query_for_subject_keeps_a_page_routed_to_the_ring(tmp_path):
    ws = ringed_workspace(tmp_path)
    write_page(ws, "knowledge/sources/2026/10/call.md", subject="harbor-voice-agent", body="webhook call\n")
    write_page(ws, "knowledge/sources/2026/10/mail.md", routing={"routes": ["ada/harbor"]}, body="webhook mail\n")
    write_page(ws, "knowledge/sources/2026/10/other.md", subject="beacon-app", body="webhook other\n")
    paths = {h.path for h in search.query(ws, "webhook", for_subject="harbor-voice-agent") if h.layer == ""}
    assert {"knowledge/sources/2026/10/call.md", "knowledge/sources/2026/10/mail.md"} <= paths
    assert "knowledge/sources/2026/10/other.md" not in paths


def test_query_without_for_is_unchanged(tmp_path):
    ws = ringed_workspace(tmp_path)
    assert {h.layer for h in search.query(ws, "webhook")} == {"", "brain", "harbor", "beacon"}


# ----- `lines`: the matching lines with their context, on ripgrep when it is installed -----------------
RG = shutil.which("rg")


@pytest.fixture(params=["python", "rg"])
def backend(request, monkeypatch):
    """Every `lines` test runs on both backends, so they cannot drift apart."""
    if request.param == "rg" and not RG:
        pytest.skip("ripgrep is not installed")
    monkeypatch.setattr(search, "_ripgrep", lambda: RG if request.param == "rg" else None)
    return request.param


def put(root, rel, text, newline="\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def lined_space(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "sales", "kind": "domains", "title": "Sales"}])
    put(brain, "knowledge/domains/sales/pipeline.md", "Intro\nThe pilot budget is 40k.\nEnd\n")
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    mount_brain(root, brain)
    put(root, "knowledge/projects/harbor/notes.md",
        "one\ntwo\nHarbor PILOT goes live on 3 November.\nfour\nfive\nsix\nseven\nThe pilot ends in May.\nnine\n")
    return root


def shown(found):
    return {(f.layer, f.path): f.lines for f in found}


def test_lines_show_each_match_with_its_line_number_and_context(tmp_path, backend):
    found = search.lines(lined_space(tmp_path), "pilot", context=1)
    assert shown(found) == {
        ("", "knowledge/projects/harbor/notes.md"): [
            (2, "two", False), (3, "Harbor PILOT goes live on 3 November.", True), (4, "four", False),
            (7, "seven", False), (8, "The pilot ends in May.", True), (9, "nine", False)],
        ("brain", "knowledge/domains/sales/pipeline.md"): [
            (1, "Intro", False), (2, "The pilot budget is 40k.", True), (3, "End", False)]}
    assert [f.subject for f in found] == ["harbor", "sales"]      # this space first, then the mounts
    assert all(f.more == 0 for f in found)


def test_lines_merge_context_that_overlaps_and_show_no_context_at_zero(tmp_path, backend):
    root = lined_space(tmp_path)
    near = search.lines(root, "pilot", context=3)[0].lines
    assert [n for n, _, _ in near] == list(range(1, 10))           # 3+-3 and 8+-3 meet: one block, no repeats
    bare = search.lines(root, "pilot", context=0)[0].lines
    assert bare == [(3, "Harbor PILOT goes live on 3 November.", True), (8, "The pilot ends in May.", True)]


def test_lines_take_the_text_literally_unless_asked_for_a_pattern(tmp_path, backend):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/api/a.md", "Upload by hand\naxb\nvia API (v2)\n")
    assert search.lines(root, "a.b", context=0) == []
    assert [n for n, _, _ in search.lines(root, "(v2)", context=0)[0].lines] == [3]
    hits = search.lines(root, "upload|via api", context=0, regex=True)
    assert [n for n, _, _ in hits[0].lines] == [1, 3]


def test_lines_read_only_the_zones_a_search_reads(tmp_path, backend):
    root = make_space(tmp_path / "ada")                              # a personal space also reads work/
    for rel in ("knowledge/topics/t/a.md", "live/subjects/t.md", "work/topics/t/plan.md",
                ".claude/worktrees/old-session/knowledge/topics/t/a.md", "personal/diary.md",
                "journal/2026/10/2026-10-29.md", "knowledge/topics/t/data.txt", "knowledge/.git/x.md"):
        put(root, rel, "the needle is here\n")
    assert {f.path for f in search.lines(root, "needle")} == {
        "knowledge/topics/t/a.md", "live/subjects/t.md", "work/topics/t/plan.md"}


def test_lines_keep_ten_matches_a_file_and_count_the_rest(tmp_path, backend):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/long.md", "".join(f"hit {i}\n" for i in range(1, 16)))
    (found,) = search.lines(root, "hit", context=2)
    assert [n for n, _, m in found.lines if m] == list(range(1, 11))
    assert found.lines[-1] == (12, "hit 12", False)                  # the tenth match keeps its context
    assert found.more == 5


def test_lines_read_crlf_unicode_and_broken_bytes(tmp_path, backend):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/win.md", "first\nCafé Ölmühle opens\nlast\n", newline="\r\n")
    (root / "knowledge/topics/t/bad.md").write_bytes(b"ok\n\xff\xfe broken caf\xc3\xa9 \xc3\n")
    found = shown(search.lines(root, "café", context=1))
    assert found[("", "knowledge/topics/t/win.md")] == [
        (1, "first", False), (2, "Café Ölmühle opens", True), (3, "last", False)]
    bad = found[("", "knowledge/topics/t/bad.md")]
    assert [(n, m) for n, _, m in bad] == [(1, False), (2, True)] and "caf" in bad[1][1]


def test_lines_cut_a_very_long_line(tmp_path, backend):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/blob.md", "x" * 5000 + " needle " + "y" * 5000 + "\n")
    (found,) = search.lines(root, "needle", context=0)
    assert len(found.lines[0][1]) <= search.LINE_WIDTH + 1 and found.lines[0][1].endswith("…")


def test_lines_refuse_a_broken_pattern(tmp_path, backend):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/a.md", "text\n")
    with pytest.raises(ValueError):
        search.lines(root, "(unclosed", regex=True)
    assert search.lines(root, "(unclosed") == []                    # literal text is never a pattern


def test_lines_for_subject_keep_rings_one_and_two(tmp_path, backend):
    ws = ringed_workspace(tmp_path)
    found = search.lines(ws, "webhook", for_subject="harbor-voice-agent")
    assert {f.layer for f in found} == {"", "brain", "harbor"}
    assert {f.layer for f in search.lines(ws, "webhook")} == {"", "brain", "harbor", "beacon"}


def test_lines_on_the_command_line(tmp_path, backend, capsys):
    root = lined_space(tmp_path)
    assert search.main([str(root), "lines", "--context", "0", "pilot"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "knowledge/projects/harbor/notes.md [harbor]",
        "  3: Harbor PILOT goes live on 3 November.",
        "  --",
        "  8: The pilot ends in May.",
        "brain:knowledge/domains/sales/pipeline.md [sales]",
        "  2: The pilot budget is 40k."]
    assert search.main([str(root), "lines", "-C", "1", "--max", "1", "budget is"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "brain:knowledge/domains/sales/pipeline.md [sales]", "  1- Intro", "  2: The pilot budget is 40k.",
        "  3- End"]
    assert search.main([str(root), "lines", "--max", "1", "--regex", "pilot|budget"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "knowledge/projects/harbor/notes.md [harbor]" and not any(o.startswith("brain:") for o in out)
    assert out[-1] == "1 more file matches: narrow the text, or pass --max"
    assert search.main([str(root), "lines", "nothing like this"]) == 1
    assert search.main([str(root), "lines", "--regex", "(unclosed"]) == 2
    assert "search:" in capsys.readouterr().err
    assert search.main([str(root), "lines", "--for", "harbor", "pilot"]) == 0
    assert "brain:" in capsys.readouterr().out                       # the brain is ring 2


def test_lines_note_the_matches_a_file_holds_beyond_ten(tmp_path, backend, capsys):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/long.md", "".join(f"hit {i}\n" for i in range(1, 13)))
    assert search.main([str(root), "lines", "-C", "0", "hit"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "  (+2 more matches in this file)"


def test_ripgrep_json_is_read_with_its_byte_encoded_parts(tmp_path):
    path = tmp_path / "space" / "knowledge" / "topics" / "t" / "a.md"
    events = [
        {"type": "begin", "data": {"path": {"text": str(path)}}},
        {"type": "context", "data": {"path": {"text": str(path)}, "lines": {"text": "before\r\n"}, "line_number": 1}},
        {"type": "match", "data": {"path": {"text": str(path)}, "lines": {"bytes": "Y2Fm6SBuZWVkbGUK"},
                                    "line_number": 2, "submatches": []}},
        {"type": "end", "data": {"path": {"text": str(path)}}},
        {"type": "summary", "data": {}}]
    got = search._from_ripgrep("\n".join(json.dumps(e) for e in events) + "\n")
    assert got == {str(path): [(1, "before", False), (2, "caf� needle", True)]}


def test_the_query_skill_searches_with_the_tool_never_from_the_root_or_file_by_file():
    text = " ".join((search.Path(__file__).resolve().parents[3] / ".claude" / "skills" / "query" /
                     "SKILL.md").read_text(encoding="utf-8").split())
    assert 'search.py <root> lines [-C <n>] [--regex] [--for <subject>] "<term>"' in text
    assert "never from the space's root" in text and "never file by file" in text


def test_lines_search_a_page_with_a_stray_nul_byte(tmp_path, backend):
    """A PDF extract can carry one NUL byte; ripgrep would call the file binary and skip it."""
    root = make_space(tmp_path / "ada")
    (root / "knowledge/topics/t").mkdir(parents=True)
    (root / "knowledge/topics/t/pdf.md").write_bytes(b"page one\x00\nthe needle\n")
    (found,) = search.lines(root, "needle", context=1)
    assert found.lines == [(1, "page one", False), (2, "the needle", True)]


def test_lines_print_on_a_console_that_cannot_show_every_character(tmp_path, backend):
    import os
    import subprocess
    import sys
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/a.md", "meeting \U0001f4c5 needle\n")
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    if backend == "python":
        env["PATH"] = ""                                              # no rg to find
    done = subprocess.run([sys.executable, str(search.Path(search.__file__)), str(root), "lines", "needle"],
                          capture_output=True, env=env)
    assert done.returncode == 0, done.stderr
    assert "meeting 📅 needle".encode("utf-8") in done.stdout       # UTF-8 to a pipe, whatever the code page



def test_lines_take_their_flags_before_or_after_the_text(tmp_path, backend, capsys):
    root = lined_space(tmp_path)
    assert search.main([str(root), "lines", "--context", "0", "pilot"]) == 0
    before = capsys.readouterr().out
    assert search.main([str(root), "lines", "pilot", "--context", "0"]) == 0
    assert capsys.readouterr().out == before
    assert search.main([str(root), "lines", "goes", "-C", "0", "live"]) == 0      # the words around a flag join
    assert "  3: Harbor PILOT goes live on 3 November." in capsys.readouterr().out


def test_lines_refuse_an_empty_text_or_a_count_that_is_not_one(tmp_path, backend, capsys):
    root = lined_space(tmp_path)
    for argv in (["lines"], ["lines", "-C", "3"], ["lines", "-C", "x", "pilot"], ["lines", "--max", "many", "pilot"],
                 ["lines", "pilot", "--for"]):
        assert search.main([str(root)] + argv) == 2, argv
        assert capsys.readouterr().out == ""


def test_lines_find_a_line_with_a_unicode_line_separator(tmp_path, backend):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/a.md", "one\nthe needle\u2028 sits here\u0085 too\nthree\n")
    (found,) = search.lines(root, "needle", context=0)
    assert found.lines == [(2, "the needle\u2028 sits here\u0085 too", True)]


@pytest.mark.skipif(not RG, reason="ripgrep is not installed")
def test_both_backends_agree_on_awkward_files(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/crlf.md", "checked in October\nnext\n", newline="\r\n")
    (root / "knowledge/topics/t/bom.md").write_bytes(b"\xef\xbb\xbffoo with bom\n")
    (root / "knowledge/topics/t/u16.md").write_bytes("\ufefffoo utf16\n".encode("utf-16-le"))
    put(root, "knowledge/topics/t/greek.md", "the \u03bc value, the \u00b5 sign, \u017fpecial, \u03c3 and \u03c2\n")
    put(root, "knowledge/topics/t/UPPER.MD", "foo in an upper-case name\n")
    queries = [("october$", True), ("^foo", True), ("foo", False), ("foo utf16", False), ("\u039c", False),
               ("\u00b5", False), ("special", False), ("\u03a3", False), ("S", False), ("[\u03bc]", True)]

    def run(rg):
        monkeypatch.setattr(search, "_ripgrep", lambda: rg)
        return [shown(search.lines(root, text, context=1, regex=regex)) for text, regex in queries]
    assert run(RG) == run(None)


def test_lines_refuse_an_option_they_do_not_know(tmp_path, backend, capsys):
    """`-i` or `-C3` out of grep habit must not become part of the text and read as "no match"."""
    root = lined_space(tmp_path)
    for argv in (["lines", "-i", "pilot"], ["lines", "-C3", "pilot"], ["lines", "pilot", "--context=3"]):
        assert search.main([str(root)] + argv) == 2, argv
        assert "unknown option" in capsys.readouterr().err
    assert search.main([str(root), "lines", "--", "-C"]) == 1            # after `--`, a dash is text


def test_lines_match_exact_case_inside_a_scoped_pattern(tmp_path, backend):
    root = make_space(tmp_path / "ada")
    put(root, "knowledge/topics/t/a.md", "The IT team decides\nit is late\nsplit it\n")
    (found,) = search.lines(root, r"(?-i:\bIT\b)", context=0, regex=True)
    assert found.lines == [(1, "The IT team decides", True)]


def test_lines_for_subject_read_no_front_matter_that_cannot_name_the_ring(tmp_path, backend, monkeypatch):
    ws = ringed_workspace(tmp_path)
    write_page(ws, "knowledge/sources/2026/10/call.md", subject="harbor-voice-agent", body="webhook call\n")
    write_page(ws, "knowledge/sources/2026/10/other.md", subject="beacon-app", body="webhook other\n")
    read = []
    real = search.pages.read
    monkeypatch.setattr(search.pages, "read", lambda path, *a, **k: read.append(search.Path(path).name) or real(path, *a, **k))
    paths = {f.path for f in search.lines(ws, "webhook", for_subject="harbor-voice-agent") if not f.layer}
    assert "knowledge/sources/2026/10/call.md" in paths and "knowledge/sources/2026/10/other.md" not in paths
    assert "other.md" not in read                                        # beacon-app names no ring subject
