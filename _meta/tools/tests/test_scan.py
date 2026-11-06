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
GITHUB_PAT = j("github_", "pat_", "A1" * 15)
GITLAB = j("gl", "pat-", "x" * 20)
PW_LINE = j("pass", "word: hunter2secret")
IBAN = j("GB82", "WEST", "12345698765432")
SECRET_VALUES = ("ABCDEFGHIJKLMNOP", "a" * 36, "123456789012", "abcdefghijklmnopqrstuvwxyz",
                 "hunter2secret", "12345698765432")

CASES = [
    ("private_key", PRIVATE_KEY),
    ("aws_key", AWS),
    ("github_token", GITHUB),
    ("gitlab_token", GITLAB),
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


def test_large_binary_and_non_utf8_files_are_scanned_and_missing_ones_skipped(tmp_path):
    # Before: a file over 1 MB, a binary file and a non-UTF-8 file were skipped as clean, so
    # sync committed them unscanned. Now each is read whole and its key is found.
    big = tmp_path / "big.txt"
    big.write_bytes((PRIVATE_KEY + "\n").encode("utf-8") * 80000)  # over 2 MB
    assert big.stat().st_size > 2 * 1024 * 1024
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00\x01\x02" + PRIVATE_KEY.encode("utf-8"))
    latin = tmp_path / "latin.txt"
    latin.write_bytes(b"\xe9t\xe9 " + PRIVATE_KEY.encode("utf-8"))
    for path in (big, blob, latin):
        assert {f.kind for f in scan.scan_paths([path])} == {"private_key"}, path.name
    assert scan.scan_paths([tmp_path / "nope.txt", tmp_path]) == []


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


LINK_JWT = j("eyJ", "a" * 20, ".", "b" * 20, ".", "c" * 20)
TOKEN_LINKS = [
    ("https://help.example.net/portal/7/AB-42?token=" + LINK_JWT + "&sda_source=notification-email",
     "https://help.example.net/portal/7/AB-42?token=[link-token]&sda_source=notification-email"),
    ("https://help.example.net/portal/7/AB-42/unsubscribe?jwt=" + LINK_JWT,
     "https://help.example.net/portal/7/AB-42/unsubscribe?jwt=[link-token]"),
    ("https://code.example.org/users/me/emails/123456789/confirm_verification/" + "Ab12" * 6,
     "https://code.example.org/users/me/emails/123456789/confirm_verification/[link-token]"),
    ("https://mail.example.com/CL0/https:%2F%2Fus01web.zoom.us%2Fj%2F1234567%3F" + "p" + "wd=Abcd1234Efgh/1/0100",
     "https://mail.example.com/CL0/https:%2F%2Fus01web.zoom.us%2Fj%2F1234567%3F" + "p" + "wd=[link-token]/1/0100"),
    ("https://app.example.com/users/invitations/accept?invitation_token=[redacted]&invitation_method=user",
     "https://app.example.com/users/invitations/accept?invitation_token=[link-token]&invitation_method=user"),
]


@pytest.mark.parametrize("raw,scrubbed", TOKEN_LINKS)
def test_tokens_in_links_are_cut_and_the_rest_of_the_link_stays(raw, scrubbed):
    assert scan.scrub_link_tokens(f"View request: {raw} now") == f"View request: {scrubbed} now"
    assert scan.scan_text(scan.scrub_link_tokens(raw), "x") == []


def test_share_meeting_and_artifact_links_and_plain_text_are_left_as_they_are():
    teams = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_" + "Ab1" * 12 + "%40thread.v2/0"
    for link in ORDINARY_LINKS + [teams]:
        assert scan.scrub_link_tokens(f"See {link} for the plan.") == f"See {link} for the plan."
    text = "Meeting ID: 123 456 789 012 345\nthe desk-cleanup20260101abcdefghijk job\n"
    assert scan.scrub_link_tokens(text) == text


NOT_ALARMS = [
    "View request: https://help.example.net/portal/7/AB-42?token=" + LINK_JWT,
    "https://help.example.net/portal/7/AB-42?token=[redacted]&sda_source=notification-email",
    "https://app.example.com/accept?invitation_token=[link-token]",
    "Meeting ID: 123 456 789 012 345 Passcode: [redacted]",
    "Vergadering-id: 123 456 789 012 34 Wachtwoordcode: [redacted]",
    "Telefoon +00 0 000 00 00 Pincode: [redacted]",
    "Your verification code: [redacted]",
    "BIC ABCDXXYYZZZ IBAN " + IBAN,
    "BIC ABCDXXYYZZZ IBAN [redacted]",
    "Active management with a 3D ri[redacted] model guides product design",
    "a token in the text " + JWT,
    "Enter this code on the interview page to verify your email address.\n\n[redacted]\n\n"
    "This code expires in 10 minutes.",
]


@pytest.mark.parametrize("text", NOT_ALARMS)
def test_link_tokens_meeting_codes_and_bank_numbers_raise_no_alarm(text):
    assert scan.alarms(text) == []


ALARMS = [
    ("Login : someone@example.org pass" + "word: [redacted]", "password"),
    ("Gebruiker: someone@example.org Wacht" + "woord: [redacted]", "password"),
    ("Pas" + "woord: NotReal-0000", "password"),
    ("Pass" + "wort: [redacted]", "password"),
    ("Mot de " + "passe : [redacted]", "password"),
    ("je wacht" + "woord is gewoon [redacted]", "redacted"),
    ("here are the details [redacted]", "redacted"),
    ("deploy with " + GITHUB, "github_token"),
    (PRIVATE_KEY, "private_key"),
]


@pytest.mark.parametrize("text,kind", ALARMS, ids=[a[1] + str(i) for i, a in enumerate(ALARMS)])
def test_an_obvious_password_or_key_raises_an_alarm(text, kind):
    assert kind in scan.alarms(text)


def test_real_secrets_are_still_found_inside_and_outside_links():
    token = "gh" + "p_" + "A" * 36
    assert [f.kind for f in scan.scan_text(f"https://docs.google.com/d/1/edit?x={token}", "x")] == ["github_token"]
    assert [f.kind for f in scan.scan_text("https://reset.example/?pass" + "word=hunter2tall", "x")] == ["password"]
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQabcdefghij"
    assert [f.kind for f in scan.scan_text(f"https://api.example.com/cb#id_token={jwt}", "x")] == ["jwt"]


def test_platform_tokens_in_a_pasted_remote_are_found_and_alarm():
    # a remote URL pasted into a note, its user info carrying a token
    for token, kind in ((GITHUB_PAT, "github_token"), (GITLAB, "gitlab_token")):
        text = f"cloned from https://oauth2:{token}@gitlab.example/northwind/voice.git"
        assert [f.kind for f in scan.scan_text(text, "x")] == [kind] and kind in scan.alarms(text)
