"""template_archive: the template as one reproducible zip and its checksum."""
import hashlib
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

import template_archive

TEMPLATE = Path(__file__).resolve().parents[3]
RELEASE = TEMPLATE / ".github" / "workflows" / "release.yml"
# Template sync installs these tests in every space; a test that archives the whole repository
# as the template runs in the template only.
template_only = pytest.mark.skipif(not (TEMPLATE / ".github" / "template-marker").exists(),
                                   reason="archives the repository as the template")


def tiny_repo(path: Path) -> Path:
    """Two tracked files and one untracked one; nothing is committed, the index is the list."""
    (path / "sub").mkdir(parents=True)
    (path / "a.txt").write_text("alpha\n", encoding="utf-8", newline="\n")
    (path / "sub" / "b.txt").write_text("beta\n", encoding="utf-8", newline="\n")
    (path / "untracked.txt").write_text("not in git\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "a.txt", "sub/b.txt"], cwd=path, check=True, capture_output=True)
    return path


@template_only
def test_the_archive_holds_the_tracked_template_and_no_state(tmp_path):
    zip_path, sums = template_archive.build(TEMPLATE, tmp_path, "v0.1.0")
    names = zipfile.ZipFile(zip_path).namelist()
    assert "brain-template-v0.1.0/_meta/tools/start.py" in names
    assert not any("/.git/" in n or "__pycache__" in n for n in names)
    assert template_archive.verify(zip_path, sums)


@template_only
def test_a_changed_byte_fails_the_check(tmp_path):
    zip_path, sums = template_archive.build(TEMPLATE, tmp_path, "v0.1.0")
    zip_path.write_bytes(zip_path.read_bytes() + b"x")
    assert not template_archive.verify(zip_path, sums)


def test_the_zip_holds_one_top_folder_of_the_tracked_files_and_the_sums_one_line(tmp_path):
    repo = tiny_repo(tmp_path / "repo")
    zip_path, sums = template_archive.build(repo, tmp_path / "out", "v0.1.0")
    assert zip_path == tmp_path / "out" / "brain-template-v0.1.0.zip"
    assert sums == tmp_path / "out" / "SHA256SUMS"
    archive = zipfile.ZipFile(zip_path)
    assert archive.namelist() == ["brain-template-v0.1.0/a.txt", "brain-template-v0.1.0/sub/b.txt"]
    assert archive.read("brain-template-v0.1.0/sub/b.txt") == b"beta\n"
    # the form `sha256sum -c` reads, with a plain line feed on every system
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert sums.read_bytes() == f"{digest}  brain-template-v0.1.0.zip\n".encode()


def test_two_builds_of_the_same_tree_are_byte_identical(tmp_path):
    repo = tiny_repo(tmp_path / "repo")
    for when in (1_000_000_000, 1_700_000_000):   # the files are touched between the builds
        for rel in ("a.txt", "sub/b.txt"):
            os.utime(repo / rel, (when, when))
        template_archive.build(repo, tmp_path / str(when), "v0.1.0")
    one, two = (tmp_path / str(when) for when in (1_000_000_000, 1_700_000_000))
    assert (one / "brain-template-v0.1.0.zip").read_bytes() == (two / "brain-template-v0.1.0.zip").read_bytes()
    assert (one / "SHA256SUMS").read_bytes() == (two / "SHA256SUMS").read_bytes()
    infos = zipfile.ZipFile(one / "brain-template-v0.1.0.zip").infolist()
    assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}
    assert {i.external_attr >> 16 for i in infos} == {0o100644}


@pytest.mark.parametrize("tag", ["", "v1/x", "v1 0", "../v1", "-v1", "v1\n"])
def test_a_tag_that_cannot_name_a_file_is_refused(tmp_path, tag):
    repo = tiny_repo(tmp_path / "repo")
    with pytest.raises(ValueError):
        template_archive.build(repo, tmp_path / "out", tag)
    assert not (tmp_path / "out").exists()


def test_the_check_needs_the_zips_own_line_in_the_sums(tmp_path):
    repo = tiny_repo(tmp_path / "repo")
    zip_path, sums = template_archive.build(repo, tmp_path / "out", "v0.1.0")
    renamed = zip_path.with_name("brain-template-v0.2.0.zip")
    renamed.write_bytes(zip_path.read_bytes())
    assert not template_archive.verify(renamed, sums)               # the sums name another file
    assert not template_archive.verify(zip_path, tmp_path / "none")  # no sums file
    assert not template_archive.verify(tmp_path / "none.zip", sums)  # no zip
    sums.write_text("not a checksum line\n", encoding="utf-8")
    assert not template_archive.verify(zip_path, sums)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sums.write_bytes(f"{digest} *{zip_path.name}\r\n".encode())     # binary marker and a Windows line end
    assert template_archive.verify(zip_path, sums)


def test_the_command_line_exits_0_on_a_match_and_1_when_not(tmp_path, capsys):
    repo = tiny_repo(tmp_path / "repo")
    out = tmp_path / "out"
    assert template_archive.main(["build", str(repo), str(out), "v0.1.0"]) == 0
    zip_path, sums = out / "brain-template-v0.1.0.zip", out / "SHA256SUMS"
    assert str(zip_path) in capsys.readouterr().out
    assert template_archive.main(["verify", str(zip_path), str(sums)]) == 0
    zip_path.write_bytes(zip_path.read_bytes() + b"x")
    assert template_archive.main(["verify", str(zip_path), str(sums)]) == 1
    assert template_archive.main(["build", str(tmp_path / "missing"), str(out), "v0.1.0"]) == 1
    assert "template_archive:" in capsys.readouterr().err


@template_only
def test_the_release_workflow_builds_on_a_tag_with_the_write_token_in_one_job():
    flow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    triggers = flow.get("on", flow.get(True))   # YAML 1.1 reads the key `on` as true
    assert triggers == {"push": {"tags": ["v*"]}}
    assert flow["permissions"] == {"contents": "read"}
    job = flow["jobs"]["release"]
    assert job["permissions"] == {"contents": "write"}
    for step in job["steps"]:
        # the tag reaches a shell through env only: a tag name can hold shell syntax
        assert "${{" not in step.get("run", ""), step.get("name")
        if "uses" in step:
            ref = step["uses"].split("@", 1)[1]
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), step["uses"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "python _meta/tools/template_archive.py build . dist \"$TAG\"" in runs
    assert "gh release view" in runs and "gh release upload" in runs and "--clobber" in runs
