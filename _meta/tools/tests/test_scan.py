"""scan.py: the generic secrets scan.

Every token-shaped string below is assembled from parts at runtime, so this file
never carries a token shape itself and passes the scan it tests.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scan


def j(*parts: str) -> str:
    return "".join(parts)


PRIVATE_KEY = j("-----BEGIN ", "PRIVATE KEY-----")
AWS = j("AKIA", "ABCDEFGHIJKLMNOP")
GITHUB = j("ghp_", "a" * 36)
SLACK = j("xoxb-", "123456789012-abcdefghij")
SK = j("sk-", "abcdefghijklmnopqrstuvwxyz")
JWT = j("eyJ", "a" * 12, ".", "b" * 12, ".", "c" * 12)
PW_LINE = j("pass", "word: hunter2secret")
IBAN = j("GB82", "WEST", "12345698765432")
SECRET_VALUES = ("ABCDEFGHIJKLMNOP", "a" * 36, "123456789012", "abcdefghijklmnopqrstuvwxyz",
                 "hunter2secret", "12345698765432")

CASES = [
    ("private_key", PRIVATE_KEY),
    ("aws_key", AWS),
    ("github_token", GITHUB),
    ("slack_token", SLACK),
    ("api_key_sk", SK),
    ("jwt", JWT),
    ("password", PW_LINE),
    ("iban", IBAN),
]


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.mark.parametrize("kind,text", CASES, ids=[c[0] for c in CASES])
def test_each_kind_is_detected_with_its_line(tmp_path, kind, text):
    path = write(tmp_path, "note.md", f"first line\nsecond line has {text} in it\nthird\n")
    found = scan.scan_paths([path])
    assert [f.kind for f in found] == [kind]
    assert found[0].line_no == 2
    assert str(found[0].path).endswith("note.md")


def test_placeholders_and_plain_words_are_not_findings(tmp_path):
    text = "\n".join([
        j("pass", "word: <your-pass", "word>"),
        j("pass", "word=***"),
        j("pw", "d: xxx"),
        j("pass", "wd: [redacted]"),
        j("pass", "word: ${SECRET}"),
        j("pass", "word: {{ vault.secret }}"),
        j("pass", "word:"),
        j("pass", "word: \"\""),
        j("pass", "word: null"),
        j("PASS", "WORD = os.environ[\"DB_PASS", "WORD\"]"),
        j("pass", "word = getpass.getpass()"),
        j("pass", "wd: settings.database.secret"),
        "left_out: [price, pass" + "word, credential]",
        "no keys, tokens, pass" + "words or access links enter a repository",
        "AKIA is four letters; " + j("ghp_", "short") + " is too short",
        "the desk-cleanup20260101abcdefghijk job",
    ])
    path = write(tmp_path, "clean.md", text + "\n")
    assert scan.scan_paths([path]) == []


def test_finding_never_carries_the_value(tmp_path):
    text = "\n".join(t for _, t in CASES) + "\n"
    path = write(tmp_path, "many.md", text)
    found = scan.scan_paths([path])
    assert len(found) == len(CASES)
    assert set(scan.Finding.__dataclass_fields__) == {"kind", "path", "line_no"}
    dump = repr(found)
    for value in SECRET_VALUES:
        assert value not in dump
    assert "BEGIN" not in dump


def test_large_binary_and_missing_files_are_skipped(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes((PRIVATE_KEY + "\n").encode("utf-8") * 80000)  # over 2 MB
    assert big.stat().st_size > 2 * 1024 * 1024
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00\x01\x02" + PRIVATE_KEY.encode("utf-8"))
    latin = tmp_path / "latin.txt"
    latin.write_bytes(b"\xff\xfe" + PRIVATE_KEY.encode("utf-8"))
    assert scan.scan_paths([big, blob, latin, tmp_path / "nope.txt", tmp_path]) == []


def test_iban_needs_a_valid_checksum(tmp_path):
    bad = write(tmp_path, "bad.md", j("GB82", "WEST", "12345698765433") + "\n")
    assert scan.scan_paths([bad]) == []
    spaced = write(tmp_path, "spaced.md", j("GB82 ", "WEST ", "1234 5698 7654 32") + "\n")
    assert [f.kind for f in scan.scan_paths([spaced])] == ["iban"]


def test_cli_prints_kind_path_line_and_exit_code(tmp_path, capsys):
    path = write(tmp_path, "note.md", PW_LINE + "\n")
    assert scan.main([str(path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("password ")
    assert out.rstrip().endswith("note.md:1")
    assert "hunter2secret" not in out
    clean = write(tmp_path, "clean.md", "nothing here\n")
    assert scan.main([str(clean)]) == 0
    assert capsys.readouterr().out.strip() == ""


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    done = subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def test_changed_since_covers_commits_staged_and_working_changes(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    write(repo, "a.md", "clean\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    base = _git(repo, "rev-parse", "HEAD").strip()
    write(repo, "b.md", "also clean\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two")
    assert scan.main(["--root", str(repo), "--changed-since", base]) == 0
    write(repo, "committed.md", AWS + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "three")
    assert scan.main(["--root", str(repo), "--changed-since", base]) == 1
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    write(repo, "a.md", "clean\n" + SK + "\n")  # working change, not staged
    assert scan.main(["--root", str(repo), "--changed-since", base]) == 1
    _git(repo, "checkout", "-q", "--", "a.md")
    write(repo, "staged.md", JWT + "\n")
    _git(repo, "add", "staged.md")
    assert scan.main(["--root", str(repo), "--changed-since", base]) == 1
    _git(repo, "reset", "-q", "--", "staged.md")
    os.remove(repo / "staged.md")
    assert scan.main(["--root", str(repo), "--changed-since", base]) == 0


def test_a_change_that_only_deletes_files_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "changed_since", lambda root, ref: [])
    assert scan.main(["--changed-since", "origin/main", "--root", str(tmp_path)]) == 0
    assert scan.main(["--root", str(tmp_path)]) == 2


ORDINARY_LINKS = [
    "https://docs.google.com/document/d/1Xy2AbC3dEf4GhI5jKl6MnO7pQr8StU9vWx0YzAbCdE/edit?usp=sharing",
    "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?resourcekey=0-AbCdEfGhIjKlMnOp",
    "https://docs.google.com/document/d/1AbC/edit?usp=sharing&token=AHk9fL2xQpR7mN3vB8cZ1wE5tY6uI0oP",
    "https://calendar.google.com/calendar/event?eid=NnRhMmNkZXJ0eWJ6dWFzZGZnaGprbCB4&ctz=Etc/UTC",
    "https://us02web.zoom.us/j/81234567890?" + "p" + "wd=QWErtyUIopASdfGHjkLZxcVBnm1234.1",
    "https://accounts.google.com/o/oauth2/auth?state=" + "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQabcdefghij",
    "https://claude.ai/artifact/AbCdEfGhIjKlMnOpQrStUv",
    "https://claude.ai/code/artifact/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
]


@pytest.mark.parametrize("link", ORDINARY_LINKS)
def test_share_meeting_and_artifact_links_are_not_secrets(link):
    assert scan.scan_text(f"See {link} for the plan.", "x") == []


def test_real_secrets_are_still_found_inside_and_outside_links():
    token = "gh" + "p_" + "A" * 36
    assert [f.kind for f in scan.scan_text(f"https://docs.google.com/d/1/edit?x={token}", "x")] == ["github_token"]
    assert [f.kind for f in scan.scan_text("https://reset.example/?pass" + "word=hunter2tall", "x")] == ["password"]
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQabcdefghij"
    assert [f.kind for f in scan.scan_text(f"https://api.example.com/cb#id_token={jwt}", "x")] == ["jwt"]
