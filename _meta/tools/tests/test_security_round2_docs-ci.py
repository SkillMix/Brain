"""Round two of the hardening, docs and CI: template sync keeps following the template a space
was made from (read only where the owner's click lands a change), the secrets scan reads PDF
streams and zips with data before them, the docs pass the scan they describe, and the checks
workflow runs the answer and leak tests for every pull request, its comments saying what it does.

Every secret-shaped string here is assembled from parts at runtime, so this file never carries
a token shape itself and passes the scan it tests."""
import bz2
import io
import lzma
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest
import yaml

import scan
import template_sync
from conftest import make_space

REPO = Path(__file__).resolve().parents[3]
PROV = Path("_meta") / "state" / "template-version.yaml"
ORIGIN = Path("_meta") / "state" / "template-origin.yaml"


def j(*parts: str) -> str:
    return "".join(parts)


GH = j("gh", "p_", "A1b2C3d4E5f6G7h8I9j0", "K1l2M3n4O5p6Q7r8")


# ----- template sync follows the template the space was made from (review 4) ----------------------
def _origin(space: Path, repo: str) -> None:
    (space / ORIGIN).parent.mkdir(parents=True, exist_ok=True)
    (space / ORIGIN).write_text(yaml.safe_dump({"repo": repo}), encoding="utf-8")


def _remote(space: Path, url: str) -> None:
    (space / ".git").mkdir(exist_ok=True)
    (space / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")


class FakeGh:
    """`gh api repos/<x> -q <template_sync.TEMPLATE_RECORD>`, answering `answer` (or failing)."""

    def __init__(self, answer: str | None):
        self.answer, self.calls = answer, []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        assert argv[:2] == ["gh", "api"]
        if self.answer is None:
            return subprocess.CompletedProcess(argv, 1, "", "HTTP 401: Bad credentials")
        return subprocess.CompletedProcess(argv, 0, self.answer + "\n", "")


def test_a_space_made_from_another_template_follows_its_recorded_origin(tmp_path):
    space = make_space(tmp_path / "ada")
    _origin(space, "lantern/brain-template")
    assert template_sync.template_source(space, None) == "lantern/brain-template"
    # its provenance, which named that template all along, is no longer refused
    (space / PROV).write_text(yaml.safe_dump({"repo": "lantern/brain-template", "files": {}}), encoding="utf-8")
    assert template_sync.template_source(space, None) == "lantern/brain-template"
    # space.yaml's `template:` wins over the recorded origin
    other = make_space(tmp_path / "ben", template="harbor/tpl")
    _origin(other, "lantern/brain-template")
    assert template_sync.template_source(other, None) == "harbor/tpl"


def test_a_recorded_origin_that_is_no_github_name_stops_the_sync(tmp_path):
    for bad in ("../x", "-oProxyCommand=x/y", "file:///tmp/x", "https://evil.example/a/b"):
        space = make_space(tmp_path / f"s{abs(hash(bad))}")
        _origin(space, bad)
        with pytest.raises(template_sync.TemplateRefused):
            template_sync.template_source(space, None)


def test_the_template_github_recorded_is_followed(tmp_path, monkeypatch):
    space = make_space(tmp_path / "ada")
    _remote(space, "https://github.com/lantern/ada.git")
    gh = FakeGh("lantern/brain-template")
    monkeypatch.setattr(template_sync.subprocess, "run", gh)
    assert template_sync.template_source(space, None) == "lantern/brain-template"
    assert gh.calls[0][2] == "repos/lantern/ada"
    # a repository made from no template follows the official one
    monkeypatch.setattr(template_sync.subprocess, "run", FakeGh("null"))
    assert template_sync.template_source(space, None) == template_sync.OFFICIAL_TEMPLATE
    # ...and so does one made from a template since archived (a template that moved leaves one behind)
    gh = FakeGh("lantern/brain-template archived")
    monkeypatch.setattr(template_sync.subprocess, "run", gh)
    assert template_sync.template_source(space, None) == template_sync.OFFICIAL_TEMPLATE
    assert gh.calls[0][3:] == ["-q", template_sync.TEMPLATE_RECORD]
    # its provenance, written when it followed that template under its old name, is not refused
    (space / PROV).write_text(yaml.safe_dump({"repo": template_sync.OFFICIAL_TEMPLATE, "files": {}}), encoding="utf-8")
    assert template_sync.template_source(space, None) == template_sync.OFFICIAL_TEMPLATE
    (space / PROV).unlink()
    # GitHub cannot be asked: the sync stops rather than offer the official release
    monkeypatch.setattr(template_sync.subprocess, "run", FakeGh(None))
    with pytest.raises(template_sync.TemplateRefused, match="GitHub did not say"):
        template_sync.template_source(space, None)
    # ...unless the space already followed the official one: it goes on with that trusted template
    (space / PROV).write_text(yaml.safe_dump({"repo": template_sync.OFFICIAL_TEMPLATE, "files": {}}), encoding="utf-8")
    assert template_sync.template_source(space, None) == template_sync.OFFICIAL_TEMPLATE
    # a provenance naming another template never makes that one the source
    (space / PROV).write_text(yaml.safe_dump({"repo": "harbor/tpl", "files": {}}), encoding="utf-8")
    with pytest.raises(template_sync.TemplateRefused, match="GitHub did not say"):
        template_sync.template_source(space, None)
    (space / PROV).unlink()
    # what GitHub says must still be an owner/name
    monkeypatch.setattr(template_sync.subprocess, "run", FakeGh("-oProxyCommand=x/y"))
    assert template_sync.template_source(space, None) == template_sync.OFFICIAL_TEMPLATE


def test_only_a_github_origin_is_asked(tmp_path, monkeypatch):
    gh = FakeGh("lantern/brain-template")
    monkeypatch.setattr(template_sync.subprocess, "run", gh)
    for n, url in enumerate(("C:/repos/ada.git", "https://evil.example/lantern/ada.git", "file:///tmp/ada")):
        space = make_space(tmp_path / f"s{n}")
        _remote(space, url)
        assert template_sync.template_source(space, None) == template_sync.OFFICIAL_TEMPLATE
    assert gh.calls == []
    ssh = make_space(tmp_path / "ssh")
    _remote(ssh, "git@github.com:lantern/ada.git")
    assert template_sync.template_source(ssh, None) == "lantern/brain-template"


def test_the_provenance_still_cannot_name_the_source(tmp_path):
    """The fix keeps the first round's: a provenance naming a repository the protected record
    does not is refused, and nothing is copied."""
    space = make_space(tmp_path / "ada")
    _origin(space, "lantern/brain-template")
    (space / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (space / "_meta" / "tools" / "sweep.py").write_text("print('ours')\n", encoding="utf-8")
    (space / PROV).write_text(yaml.safe_dump({"repo": "attacker/evil-template", "files": {}}), encoding="utf-8")
    with pytest.raises(template_sync.TemplateRefused, match="provenance names"):
        template_sync.guarded(space)
    assert template_sync.main([str(space)]) == 1
    assert (space / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('ours')\n"


def test_a_local_copy_keeps_the_origin_in_the_provenance(tmp_path):
    tpl = tmp_path / "tpl"
    (tpl / "_meta" / "tools").mkdir(parents=True)
    (tpl / "_meta" / "tools" / "sweep.py").write_text("print('v2')\n", encoding="utf-8")
    space = make_space(tmp_path / "ada")
    _origin(space, "lantern/brain-template")
    assert template_sync.main([str(space), "--from", str(tpl), "--local"]) == 0
    assert yaml.safe_load((space / PROV).read_text(encoding="utf-8"))["repo"] == "lantern/brain-template"
    assert template_sync.template_source(space, None) == "lantern/brain-template"


def test_the_skill_names_local_for_a_local_copy():
    text = (REPO / ".claude" / "skills" / "template-sync" / "SKILL.md").read_text(encoding="utf-8")
    assert "`--from <path> --local`" in text
    assert "`--from <path>`" not in text.replace("`--from <path> --local`", "")


# ----- the scan reads PDF streams and zips with data before them (review 11) ----------------------
def _pdf(content: bytes) -> bytes:
    stream = zlib.compress(content)
    return (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\n"
            b"stream\r\n" + stream + b"\r\nendstream\nendobj\ntrailer\n<< >>\n%%EOF\n")


def _zip(members: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_a_token_in_a_compressed_pdf_stream_is_found():
    pdf = _pdf(f"BT /F1 12 Tf 72 712 Td (key {GH}) Tj ET".encode())
    assert GH.encode() not in pdf   # the token is only in the compressed stream
    assert [f.kind for f in scan.scan_bytes(pdf, "invoice.pdf")] == ["github_token"]
    assert scan.scan_bytes(_pdf(b"BT (nothing here) Tj ET"), "clean.pdf") == []


def test_a_pdf_stream_that_does_not_inflate_is_read_as_it_is():
    raw = b"%PDF-1.4\n1 0 obj\n<< /Length 9 >>\nstream\nnot zlib!\nendstream\nendobj\n"
    assert scan.scan_bytes(raw, "plain.pdf") == []


def test_a_pdf_that_inflates_past_the_ceiling_is_unscanned(monkeypatch):
    monkeypatch.setattr(scan, "SCAN_MAX_BYTES", 100_000)
    assert [f.kind for f in scan.scan_bytes(_pdf(b"0" * 200_000), "bomb.pdf")] == ["unscanned"]


def test_a_zip_with_data_before_it_is_read():
    archive = b"#!/bin/sh\nexit 0\n" + _zip({"a.txt": f"key {GH}\n"})
    assert zipfile.is_zipfile(io.BytesIO(archive))
    assert [f.kind for f in scan.scan_bytes(archive, "installer.sh")] == ["github_token"]


def test_bzip2_xz_and_zlib_are_read_within_the_ceiling(monkeypatch):
    body = f"line\nkey {GH}\n".encode()
    for name, data in (("a.bz2", bz2.compress(body)), ("a.xz", lzma.compress(body)), ("a.z", zlib.compress(body))):
        assert [f.kind for f in scan.scan_bytes(data, name)] == ["github_token"], name
    monkeypatch.setattr(scan, "SCAN_MAX_BYTES", 100_000)
    for name, data in (("b.bz2", bz2.compress(b"0" * 200_000)), ("b.xz", lzma.compress(b"0" * 200_000)),
                       ("b.z", zlib.compress(b"0" * 200_000))):
        assert [f.kind for f in scan.scan_bytes(data, name)] == ["unscanned"], name


def _pdf_of(*contents: bytes, eol: bytes = b"\n") -> bytes:
    out = b"%PDF-1.4\n"
    for n, content in enumerate(contents, 1):
        stream = zlib.compress(content)
        out += (b"%d 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream" % (n, len(stream)) + eol + stream
                + b"\nendstream\nendobj\n")
    return out + b"%%EOF\n"


def test_every_stream_of_a_pdf_is_read_not_only_the_first():
    """The `stream` inside `endstream` is no new stream: reading from there skipped every stream
    after the first."""
    page = f"BT (key {GH}) Tj ET".encode()
    for at in range(3):
        contents = [b"BT (cover) Tj ET"] * 3
        contents[at] = page
        assert [f.kind for f in scan.scan_bytes(_pdf_of(*contents), "deck.pdf")] == ["github_token"], at
    # a lenient writer's CR alone after `stream`
    assert [f.kind for f in scan.scan_bytes(_pdf_of(page, eol=b"\r"), "old.pdf")] == ["github_token"]


def test_a_token_a_pdf_page_splits_for_kerning_is_found():
    split = b"BT /F1 12 Tf [(" + GH[:9].encode() + b") -20 (" + GH[9:].encode() + b")] TJ ET"
    assert [f.kind for f in scan.scan_bytes(_pdf_of(split), "kerned.pdf")] == ["github_token"]
    hexed = b"BT [<" + GH[:9].encode().hex().encode() + b"> 15 <" + GH[9:].encode().hex().encode() + b">] TJ ET"
    assert [f.kind for f in scan.scan_bytes(_pdf_of(hexed), "hex.pdf")] == ["github_token"]
    assert scan.scan_bytes(_pdf_of(b"BT [(Lantern) -20 (report)] TJ ET"), "clean.pdf") == []


def test_every_member_of_a_gzip_or_bzip2_file_is_read(monkeypatch):
    import gzip
    first, second = b"nothing here\n", f"key {GH}\n".encode()
    assert [f.kind for f in scan.scan_bytes(gzip.compress(first) + gzip.compress(second), "a.gz")] == ["github_token"]
    assert [f.kind for f in scan.scan_bytes(bz2.compress(first) + bz2.compress(second), "a.bz2")] == ["github_token"]
    monkeypatch.setattr(scan, "SCAN_MAX_BYTES", 100_000)
    many = gzip.compress(b"0" * 60_000) * 3   # each member within the ceiling, all of them over it
    assert [f.kind for f in scan.scan_bytes(many, "many.gz")] == ["unscanned"]


def test_a_file_that_is_two_formats_at_once_is_read_as_each():
    """A zip after a PDF or a gzip file: the zip reader alone would leave the first part unread."""
    tail = _zip({"readme.txt": "nothing here\n"})
    page = _pdf_of(f"BT (key {GH}) Tj ET".encode())
    assert [f.kind for f in scan.scan_bytes(page + tail, "both.pdf")] == ["github_token"]
    import gzip
    assert [f.kind for f in scan.scan_bytes(gzip.compress(f"key {GH}\n".encode()) + tail, "both.gz")] \
        == ["github_token"]


def test_text_that_only_looks_compressed_is_read_as_text():
    assert scan.scan_bytes(b"x^ a note that starts like a zlib header\n", "note.txt") == []
    assert scan.scan_bytes(b"BZh is how bzip2 files begin\n", "note.md") == []


# ----- the docs pass the scan they describe (review 13) --------------------------------------------
def test_the_templates_own_docs_pass_its_scan():
    paths = [*(REPO / "docs").rglob("*.md"), REPO / "SECURITY.md", REPO / "README.md",
             *(REPO / ".claude" / "skills").rglob("SKILL.md")]
    assert [(f.kind, f.path, f.line_no) for f in scan.scan_paths([p for p in paths if p.exists()])] == []


def test_the_review_row_reads_as_two_clauses():
    text = (REPO / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "each proposal `--reviewed-by`" not in text and "each proposal; `--reviewed-by`" in text


# ----- the checks workflow (reviews 14, 15, 17) and SECURITY.md (review 16) ------------------------
def _checks() -> tuple[dict, str]:
    text = (REPO / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def _step(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name") == name)


def test_the_answer_and_leak_tests_run_for_a_pull_request_that_changes_the_tools():
    wf, _ = _checks()
    mounts, checks = wf["jobs"]["mounts"], wf["jobs"]["checks"]
    assert mounts["outputs"]["ran_tests"] == "${{ steps.mounted.outputs.ran_tests }}"
    step = _step(mounts, "Validate and test with the mounts")
    run = step["run"]
    assert step["id"] == "mounted"
    # a change to _meta/tools skips only the old validate.py, never the answer and leak tests
    before_tests = run[:run.index("tests_run.py")]
    assert "exit 0" not in before_tests
    assert 'python -I "$RUNNER_TEMP/base/_meta/tools/tests_run.py" .' in run
    assert 'echo "ran_tests=true" >> "$GITHUB_OUTPUT"' in run
    tests = _step(checks, "Tests as data")
    assert "needs.mounts.outputs.ran_tests != 'true'" in tests["if"] and ".gitmodules" not in tests["if"]


def test_the_workflow_comments_say_what_it_does():
    _, text = _checks()
    assert "default branch's gate and secrets scan" not in text
    assert "base branch's gate and secrets" in text
    # the mount step names the repository's own secrets, never the organisation's (its wording may wrap anywhere)
    flat = " ".join(line.strip().lstrip("#").strip() for line in text.splitlines())
    assert "organization-wide" not in text
    assert "never an organisation secret" in flat or "never organisation secrets" in flat
    assert "its own tools run in checks, without mounts" not in text


def test_security_md_describes_the_release_workflow_as_it_is():
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    assert "whatever a `v*` tag points at" not in text
    assert "only when its commit is on the default branch" in " ".join(text.split())
    assert "flags an organisation secret of either name" in " ".join(text.split())
