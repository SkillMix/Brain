"""The secrets scan and the sync's landing, hardened: what the scan reads (every encoding and
size up to its ceiling, the staged blob rather than the working tree), what it finds (the
platform keys it missed, wrapped tokens, passwords that looked like code or a slot), what the
link scrub cuts (sign-in links on share hosts, encoded tokens), and the credential file names
the sync never stages.

Every secret-shaped string here is assembled from parts at runtime, so this file never carries
a token shape itself and passes the scan it tests (and GitHub's push protection)."""
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

import scan
import sync
from test_sync import git, head_files, repo, run, run_lines, write  # noqa: F401  (repo is a fixture)


def j(*parts: str) -> str:
    return "".join(parts)


GH = j("gh", "p_", "A1b2C3d4E5f6G7h8I9j0", "K1l2M3n4O5p6Q7r8")
ANTHROPIC = j("sk-", "ant-", "api03-", "A" * 90)
OPENAI = j("sk-", "proj-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8_Qr9")
STRIPE = j("sk_", "live_", "51HxYzAbCdEfGh12345678")
WHSEC = j("wh", "sec_", "AbCdEfGh1234567890IjKlMnOp")
XAPP = j("xa", "pp-", "1-A0123456789-0123456789-abcdef")
SLACK_HOOK = j("https://hooks.", "slack.com/services/", "T01ABCDEF", "/B02GHIJKL/", "AbCdEf123456GhIjKl")
ASIA = j("AS", "IA", "ABCDEFGHIJ123456")
AWS_SECRET = j("aws_secret_", "access_key = ", "wJalrXUtnFEMI/K7MDENG/bPxRfiCY", "1234567890")
GOOGLE_API = j("AI", "za", "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q")
GOCSPX = j("GOC", "SPX-", "AbCdEfGh1234567890IjKlMn")
YA29 = j("ya", "29.", "a0AfH6SMBx1234567890abcdefGHIJ")
AZURE = j("DefaultEndpointsProtocol=https;Account", "Key=", "Ab1" * 30, "==")
PGP = j("-----BEGIN PGP ", "PRIVATE KEY BLOCK-----")
PUTTY = j("PuTTY-User-", "Key-File-3: ssh-ed25519")
URL_CREDS = j("postgres://", "admin:", "Hunter2Secret", "@db.example.com/app")
AUTH = j("Author", "ization: Bearer ", "AbCdEf1234567890GhIjKl")
NPM = j("np", "m_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")
HF = j("h", "f_", "AbCdEfGhIjKlMnOpQrStUvWxYz123456")
LABELLED = j("MY_SERVICE_", "API_KEY=", "Zq8Xw7Vu6Ts5Rq4Po3Nm2Lk1Jh")
SENDGRID = j("S", "G.", "AbCdEfGhIjKlMnOp12", ".", "QrStUvWxYz123456AbCd")

NEW_KINDS = [
    ("anthropic_key", ANTHROPIC), ("openai_key", "OPENAI_API_KEY=" + OPENAI), ("stripe_key", STRIPE),
    ("stripe_key", WHSEC), ("slack_token", XAPP), ("slack_webhook", SLACK_HOOK), ("aws_key", ASIA),
    ("aws_secret", AWS_SECRET), ("google_key", GOOGLE_API), ("google_key", GOCSPX), ("google_key", YA29),
    ("azure_key", AZURE), ("private_key", PGP), ("private_key", PUTTY), ("url_credentials", URL_CREDS),
    ("auth_header", AUTH), ("npm_token", NPM), ("huggingface_token", HF), ("labelled_key", LABELLED),
    ("sendgrid_key", SENDGRID),
]
SECRET_PARTS = ("A" * 90, "Hunter2Secret", "AbCdEf1234567890GhIjKl", "Zq8Xw7Vu6Ts5Rq4Po3Nm2Lk1Jh",
                "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "wJalrXUtnFEMI")


# ----- #39: the token formats the scan missed --------------------------------------------------
@pytest.mark.parametrize("kind,text", NEW_KINDS, ids=[f"{k}{i}" for i, (k, _) in enumerate(NEW_KINDS)])
def test_each_new_kind_is_found_and_raises_an_alarm(kind, text):
    found = scan.scan_text(f"first\nthe value {text} here\nlast\n", "x")
    assert kind in {f.kind for f in found}
    assert {f.line_no for f in found} == {2}
    assert kind in scan.alarms(f"the value {text} here")


def test_no_finding_carries_the_value():
    text = "\n".join(t for _, t in NEW_KINDS) + "\n"
    dump = repr(scan.scan_text(text, "x")) + repr(scan.alarms(text))
    for part in SECRET_PARTS:
        assert part not in dump


@pytest.mark.parametrize("text", [
    "sk-learn-is-a-library-for-machine-learning",
    j("API_", "KEY=<your-key>"),
    j("api_", "key: ${API_KEY}"),
    j("CLIENT_", "SECRET=", "changeme"),
    "https://example.com/a:b and https://example.com/path:with:colons",
    j("Author", "ization: Bearer $TOKEN"),
    j("Author", "ization: Bearer <token>"),
    j("postgres://", "user:<password>@db.example.com/app"),
    j("postgres://", "user:${DB_PASSWORD}@db.example.com/app"),
    "the next_page_token: 12345678901234567890 is a page cursor",
    '{"routine": "sync", "cost_tokens": 0, "notes": {"committed": true}}',
    "ASIAPACIFIC2026REGIONS is a word, and so is asia",
])
def test_ordinary_text_and_placeholders_are_no_finding(text):
    assert scan.scan_text(text, "x") == []
    assert scan.alarms(text) == []


def test_a_token_wrapped_over_two_lines_is_found_on_both():
    text = f"intro\ntoken {GH[:20]}\n{GH[20:]} and more\nend\n"
    found = scan.scan_text(text, "x")
    assert [(f.kind, f.line_no) for f in found] == [("github_token", 2), ("github_token", 3)]
    assert "github_token" in scan.alarms(text)
    whole = f"token {GH}\nThanks for reading\n"   # a whole token at a line's end wraps onto nothing
    assert [(f.kind, f.line_no) for f in scan.scan_text(whole, "x")] == [("github_token", 1)]


# ----- #41: passwords that looked like code or a template slot ---------------------------------
REAL_PASSWORDS = ["getaway2024!", "$ecretPa55!", "envelope77", "Configuratie9", "readyset42", "{Tr0ub4dor&3}",
                  "Settings#2024", "Inputs4All", "[Summer2024]", "%Pa55%", "<Kx9!mm>", "abc(1)"]


@pytest.mark.parametrize("value", REAL_PASSWORDS)
def test_a_labelled_real_password_is_found(value):
    for word in ("pass" + "word: ", "pass" + "word=", "wacht" + "woord: ", "pw" + "d: "):
        line = word + value
        assert [f.kind for f in scan.scan_text(line, "x")] == ["password"], line
        assert scan.alarms(line) == ["password"], line


@pytest.mark.parametrize("value", [
    "<your password>", "<your-password>", "${{ secrets.DB }}", "{{ vault.secret }}", "%DB_PASS%", "{pw}",
    "[your password here]", "[redacted]", "$DB_PASSWORD", "${db_password}", 'os.environ["DB_PASS"]',
    "getpass.getpass()", "settings.database.secret", "***", "xxx", "null", '""'])
def test_a_password_slot_or_lookup_is_no_finding(value):
    line = "pass" + "word: " + value
    assert scan.scan_text(line, "x") == [], line
    # a value the export cut is a password the person should still hear about (an alarm, no finding)
    assert scan.alarms(line) == (["password"] if value == "[redacted]" else []), line


# ----- #19: every encoding, every size up to the ceiling ---------------------------------------
def _bytes_cases():
    text = f"# Notes\n\nthe key {GH} is here\n"
    return {
        "utf16_bom": text.encode("utf-16"),
        "utf16le_no_bom": text.encode("utf-16-le"),
        "utf16be_bom": b"\xfe\xff" + text.encode("utf-16-be"),
        "utf32_bom": text.encode("utf-32"),
        "latin1": ("café été\n" + text).encode("latin-1"),
        "cp1252": ("“quoted” €\n" + text).encode("cp1252"),
        "nul_in_head": b"\x00\x01\x02binary head\n" + text.encode("ascii"),
        "bom_in_front_of_ascii": b"\xff\xfe" + text.encode("ascii"),
    }


@pytest.mark.parametrize("name", list(_bytes_cases()))
def test_a_token_is_found_whatever_the_encoding(tmp_path, name):
    path = tmp_path / f"{name}.md"
    path.write_bytes(_bytes_cases()[name])
    assert "github_token" in {f.kind for f in scan.scan_paths([path])}
    assert "github_token" in {f.kind for f in scan.scan_bytes(path.read_bytes(), name)}


def test_a_large_text_file_is_scanned_whole_and_a_clean_one_is_clean(tmp_path):
    big = tmp_path / "routing.jsonl"
    row = json.dumps({"id": "r-0001", "why": "the mail is about the plan " + "x" * 60}) + "\n"
    big.write_text(row * 22000 + json.dumps({"why": f"the mail shares the key {GH}"}) + "\n", encoding="utf-8")
    assert big.stat().st_size > 2_000_000
    found = scan.scan_paths([big])
    assert [(f.kind, f.line_no) for f in found] == [("github_token", 22001)]
    clean = tmp_path / "clean.jsonl"
    clean.write_text(row * 22000, encoding="utf-8")
    assert scan.scan_paths([clean]) == []


def test_a_binary_file_without_a_token_is_no_finding(tmp_path):
    rng = random.Random(7)
    blob = tmp_path / "picture.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(rng.randrange(256) for _ in range(300_000)))
    assert scan.scan_paths([blob]) == []


def test_a_file_over_the_ceiling_is_read_in_chunks_never_left_unscanned(tmp_path, monkeypatch, capsys):
    """Round 3: past SCAN_MAX_BYTES a file is read in chunks, not called `unscanned`; a clean one is
    clean, a token in it is found on its own line."""
    monkeypatch.setattr(scan, "SCAN_MAX_BYTES", 1000)
    monkeypatch.setattr(scan, "CHUNK_BYTES", 600)
    monkeypatch.setattr(scan, "CHUNK_OVERLAP", 200)
    path = tmp_path / "huge.log"
    path.write_text("clean line\n" * 200, encoding="utf-8")
    assert scan.scan_paths([path]) == []
    assert scan.scan_bytes(path.read_bytes(), "huge.log") == []
    assert scan.main([str(path)]) == 0
    path.write_text("clean line\n" * 150 + f"key {GH}\n" + "clean line\n" * 50, encoding="utf-8")
    assert [(f.kind, f.line_no) for f in scan.scan_paths([path])] == [("github_token", 151)]
    assert scan.main([str(path)]) == 1
    assert "github_token" in capsys.readouterr().out


def test_changed_since_fails_on_a_utf16_change(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root / "a.md", "clean\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "one")
    base = git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "a.md").write_bytes(f"clean\nkey {GH}\n".encode("utf-16"))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "two")
    assert scan.main(["--root", str(root), "--changed-since", base]) == 1


# ----- #40: credential file names ---------------------------------------------------------------
@pytest.mark.parametrize("rel,expected", [
    (".env", True), ("work/.env.local", True), (".ENV", True), ("work/client_secret_1234.json", True),
    ("work/.npmrc", True), ("work/keys/id_ed25519", True), ("work/x.pem", True), ("work/a.key", True),
    ("work/my-service-account.json", True), ("work/credentials", True), ("token.json", True),
    (".env.example", False), ("work/keys/id_ed25519.pub", False), ("work/notes.md", False),
    ("knowledge/topics/credentials/index.md", False), ("work/environment.md", False)])
def test_credential_file_names(rel, expected):
    assert scan.credential_file(rel) is expected


def test_scan_paths_names_a_credential_file_whatever_it_holds(tmp_path):
    env = write(tmp_path / ".env", "NOTHING_SECRET_LOOKING=1\n")
    assert [f.kind for f in scan.scan_paths([env])] == ["credential_file"]
    example = write(tmp_path / ".env.example", "API_KEY=\n")
    assert scan.scan_paths([example]) == []


def test_the_template_gitignore_ignores_credential_files(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    template = Path(scan.__file__).resolve().parents[2] / ".gitignore"
    (root / ".gitignore").write_bytes(template.read_bytes())

    def ignored(rel: str) -> bool:
        return git(root, "check-ignore", "-q", "--no-index", rel, check=False).returncode == 0

    for rel in (".env", ".env.local", "work/client_secret_1.json", "work/.npmrc", "work/keys/id_ed25519",
                "work/x.pem", "work/a.key", "work/sa-service-account.json", "work/token.json"):
        assert ignored(rel), rel
    for rel in (".env.example", "work/keys/id_ed25519.pub", "work/notes.md", "knowledge/topics/credentials/index.md"):
        assert not ignored(rel), rel


# ----- #59: tokens in sign-in and signed links --------------------------------------------------
HEX64 = "0123456789abcdef" * 4
SCRUBBED = [
    (f"https://claude.ai/magic-link#{HEX64}:c29tZW9uZUBleGFtcGxlLm9yZw==", "https://claude.ai/magic-link#[link-token]"),
    ("https://claude.ai/login?token=AbCd1234EfGh5678IjKl", "https://claude.ai/login?token=[link-token]"),
    ("https://accounts.google.com/signin/continue?code=4/0AbCdEf123456&state=abc",
     "https://accounts.google.com/signin/continue?code=[link-token]&state=abc"),
    ("https://storage.googleapis.com/b/o.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Credential=sa%40p.iam."
     "gserviceaccount.com%2F20260101%2Fauto%2Fstorage%2Fgoog4_request&X-Goog-Signature=" + HEX64,
     "https://storage.googleapis.com/b/o.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Credential=[link-token]"
     "&X-Goog-Signature=[link-token]"),
    ("https://app.example.com/reset?token=Ab12Cd34Ef%2FGh56Ij78Kl90",
     "https://app.example.com/reset?token=[link-token]"),
    ("https://app.example.com/x?token=Ab12/Cd34+Ef56Gh78Ij90=", "https://app.example.com/x?token=[link-token]"),
    ("https://app.example.com/x?t=Ab12Cd34Ef56Gh78Ij90", "https://app.example.com/x?t=[link-token]"),
    ("https://app.example.com/x?api_" + "key=Ab12Cd34Ef56Gh78Ij90", "https://app.example.com/x?api_key=[link-token]"),
    ("https://app.example.com/x?client_secret=Ab12Cd34Ef56Gh78",
     "https://app.example.com/x?client_secret=[link-token]"),
    ("HTTPS://APP.EXAMPLE.COM/reset?token=Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56",
     "HTTPS://APP.EXAMPLE.COM/reset?token=[link-token]"),
    ("https://links.example.com/m/Ab12Cd34Ef56Gh78Ij90Kl", "https://links.example.com/m/[link-token]"),
    ("https://app.example.com/magic#Ab12Cd34Ef56Gh78Ij90", "https://app.example.com/magic#[link-token]"),
]


@pytest.mark.parametrize("raw,scrubbed", SCRUBBED)
def test_sign_in_and_signed_link_tokens_are_cut(raw, scrubbed):
    assert scan.scrub_link_tokens(f"Open {raw} now") == f"Open {scrubbed} now"


@pytest.mark.parametrize("link", [
    "https://docs.google.com/document/d/1AbC/edit?usp=sharing&token=AHk9fL2xQpR7mN3vB8cZ1wE5tY6uI0oP",
    "https://accounts.google.com/o/oauth2/auth?state=" + j("eyJ", "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                                                           "dQw4w9WgXcQabcdefghij"),
    "https://claude.ai/artifact/AbCdEfGhIjKlMnOpQrStUv",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s",
    "https://example.com/redirect?u=https%3A%2F%2Fexample.org%2Fa%2Fpage-2026-11-05-about-the-plan",
    "https://www.dropbox.com/s/abc123def456ghi/plan.pdf?dl=0",
])
def test_share_links_times_and_redirect_targets_stay(link):
    assert scan.scrub_link_tokens(f"See {link} here") == f"See {link} here"


# ----- the sync: what it stages and what it scans -----------------------------------------------
def test_a_tracked_log_over_1mb_with_a_token_is_left_local(repo, capsys):
    log = repo / "_meta" / "state" / "routing.jsonl"
    write(log, json.dumps({"id": "r-0", "why": "first"}) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "log")
    row = json.dumps({"id": "r-1", "why": "the mail is about the plan " + "x" * 70}) + "\n"
    write(log, log.read_text(encoding="utf-8") + row * 11000 + json.dumps({"why": f"key {GH}"}) + "\n")
    assert log.stat().st_size > 1_000_000
    assert run(repo) == 0
    assert GH not in git(repo, "show", "HEAD:_meta/state/routing.jsonl").stdout
    assert "_meta/state/routing.jsonl" in git(repo, "status", "--porcelain").stdout
    assert run_lines(repo)[-1]["notes"]["scan_hits"] == 1
    assert GH not in capsys.readouterr().out


@pytest.mark.parametrize("encoding", ["utf-16", "latin-1"])
def test_a_tracked_file_rewritten_in_another_encoding_is_left_local(repo, encoding):
    (repo / "work" / "notes.md").write_bytes(f"# Notes\n\ncafé key {GH}\n".encode(encoding))
    assert run(repo) == 0
    assert git(repo, "show", "HEAD:work/notes.md").stdout == "# Notes\n\nline one\n"   # as the fixture committed it
    assert "work/notes.md" in git(repo, "status", "--porcelain").stdout   # left local


def test_only_does_not_land_a_large_untracked_file_with_a_token(repo):
    big = write(repo / "work" / "moved" / "big.md", "clean line of text\n" * 60000 + f"key {GH}\n")
    write(repo / "work" / "moved" / "page.md", "# Page\n")
    assert big.stat().st_size > 1_000_000
    assert run(repo, "--only", "work/moved") == 0
    files = head_files(repo)
    assert "work/moved/page.md" in files and "work/moved/big.md" not in files
    assert big.exists()


def test_a_staged_blob_over_the_ceiling_is_streamed_and_lands_when_clean(repo, monkeypatch, capsys):
    """Round 3: a blob past SCAN_MAX_BYTES is streamed out of git and read in chunks: a clean one
    lands, one with a token past the first chunk is left local."""
    monkeypatch.setattr(scan, "SCAN_MAX_BYTES", 2000)
    monkeypatch.setattr(scan, "CHUNK_BYTES", 1500)
    monkeypatch.setattr(scan, "CHUNK_OVERLAP", 300)
    write(repo / "work" / "notes.md", "# Notes\n\n" + "clean line\n" * 400)
    write(repo / "work" / "small.md", "small and clean\n", minutes_old=20)
    write(repo / "work" / "big.md", "clean line\n" * 300 + f"key {GH}\n", minutes_old=20)
    assert run(repo) == 0
    files = head_files(repo)
    assert "work/small.md" in files and "work/notes.md" in files and "work/big.md" not in files
    assert GH not in capsys.readouterr().out
    assert run_lines(repo)[-1]["notes"]["scan_hits"] == 1


# ----- #60: the staged blob, not the working tree ----------------------------------------------
def test_the_scan_reads_what_is_staged_not_the_working_tree(repo, monkeypatch):
    write(repo / "work" / "notes.md", f"# Notes\n\ntoken {GH}\n")
    real_add = sync.Run.add

    def add(self, rels):
        real_add(self, rels)
        write(repo / "work" / "notes.md", "# Notes\n\nclean\n")   # saved again after the add

    monkeypatch.setattr(sync.Run, "add", add)
    assert run(repo) == 0
    assert git(repo, "show", "HEAD:work/notes.md").stdout == "# Notes\n\nline one\n"   # as the fixture committed it
    assert run_lines(repo)[-1]["notes"]["scan_hits"] == 1


def test_a_secret_written_after_the_add_is_not_what_is_judged(repo, monkeypatch):
    write(repo / "work" / "notes.md", "# Notes\n\nclean edit\n")
    real_add = sync.Run.add

    def add(self, rels):
        real_add(self, rels)
        write(repo / "work" / "notes.md", f"# Notes\n\ntoken {GH}\n")

    monkeypatch.setattr(sync.Run, "add", add)
    assert run(repo) == 0
    assert "clean edit" in git(repo, "show", "HEAD:work/notes.md").stdout
    assert GH in (repo / "work" / "notes.md").read_text(encoding="utf-8")   # left for the next run's scan


# ----- #40: the sync never stages a credential file ---------------------------------------------
def test_credential_files_are_never_staged_even_without_gitignore(repo, capsys):
    paths = {
        ".env": f"ANTHROPIC_API_KEY={ANTHROPIC}\nSTRIPE_SECRET_KEY={STRIPE}\n",
        "work/client_secret_1.json": json.dumps({"installed": {"client_secret": GOCSPX}}) + "\n",
        "work/.npmrc": "//registry.npmjs.org/:_authToken=" + NPM + "\n",
        "work/keys/id_ed25519": "not a PEM header, but a key file all the same\n",
        "work/x.pem": "MIIBVgIBADANBgkqhkiG9w0BAQEFAASCAUAwggE8AgEAAkEA\n",
    }
    for rel, text in paths.items():
        write(repo / rel, text, minutes_old=20)
    write(repo / ".env.example", "ANTHROPIC_API_KEY=\n", minutes_old=20)
    assert run(repo) == 0
    files = head_files(repo)
    assert ".env.example" in files
    status = git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    for rel in paths:
        assert rel not in files, rel
        assert (repo / rel).exists() and f"?? {rel}" in status, rel
    line = run_lines(repo)[-1]
    assert line["notes"]["credential_files"] == len(paths)
    dump = json.dumps(line) + capsys.readouterr().out
    assert "sk-" + "ant" not in dump and "A" * 40 not in dump and GOCSPX not in dump


def test_a_tracked_or_hand_staged_credential_file_is_not_landed(repo):
    write(repo / ".env", "FIRST=1\n")
    git(repo, "add", "-f", ".env")
    git(repo, "commit", "-q", "-m", "an old space tracked it")
    write(repo / ".env", f"FIRST=1\nANTHROPIC_API_KEY={ANTHROPIC}\n")
    write(repo / "work" / "x.pem", "MIIBVgIBADANBgkqhkiG9w0BAQEFAASCAUAwggE8AgEAAkEA\n")
    git(repo, "add", "-f", "work/x.pem")   # staged by hand before the run
    write(repo / "work" / "notes.md", "# Notes\n\nedited\n")
    assert run(repo) == 0
    files = head_files(repo)
    assert "work/notes.md" in files and ".env" not in files and "work/x.pem" not in files
    assert git(repo, "show", "HEAD:.env").stdout == "FIRST=1\n"
    assert "work/x.pem" not in git(repo, "ls-files").stdout


def test_deleting_a_tracked_credential_file_lands(repo):
    write(repo / ".env", "FIRST=1\n")
    git(repo, "add", "-f", ".env")
    git(repo, "commit", "-q", "-m", "an old space tracked it")
    (repo / ".env").unlink()
    assert run(repo) == 0
    assert ".env" not in git(repo, "ls-files").stdout


def test_cat_file_reads_blobs_in_order(repo):
    oids = []
    for text in ("first\n", "second blob\n", ""):
        done = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(repo), input=text.encode(),
                              capture_output=True, check=True)
        oids.append(done.stdout.decode().strip())
    space_run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push"]))
    got = space_run.cat_file("--batch", oids + ["0" * 40])
    assert [(kind, data) for kind, _, data in got] == [
        ("blob", b"first\n"), ("blob", b"second blob\n"), ("blob", b""), ("missing", b"")]
    sizes = space_run.cat_file("--batch-check", oids)
    assert [size for _, size, _ in sizes] == [6, 12, 0]


# ===== The re-test: bypasses of the fixes above, each closed ======================================
VALUE = j("Zq8Xw7Vu6Ts5", "Rq4Po3Nm2Lk1Jh")   # a key-shaped value under some label


# ----- #39: keys under the labels a .env file or a JSON document gives them ------------------------
@pytest.mark.parametrize("kind,line", [
    ("labelled_key", j("GITHUB_", "TOKEN=", VALUE)),
    ("labelled_key", j("export NOTETAKER_", "KEY=", VALUE)),
    ("labelled_key", j("SEC", "RET=", VALUE)),
    ("labelled_key", j("STRIPE_", "SECRET: ", VALUE)),
    ("labelled_key", j('{"tok', 'en": "', VALUE, '"}')),
    ("labelled_key", j("sec", "ret: ", VALUE)),
    ("password", j("DB_", "PASS=", "Hunter2tall")),
    ("password", j('{"pass', 'word": "', "Hunter2tall", '"}')),
])
def test_a_key_under_an_environment_or_json_label_is_found(kind, line):
    assert kind in {f.kind for f in scan.scan_text(line + "\n", "x")}, line
    assert kind in scan.alarms(line), line


@pytest.mark.parametrize("line", [
    j("cache_", 'key = "user_profile_v2_settings_2024"'),
    j('const ', 'KEY="skillmix-notes-state-2026-10-22-v1";'),
    j("ADS_", "KEY=", "abcd1234-5678-90ab-cdef-", "1234567890ab"),
    j("TOKEN_", "URL=https://oauth2.example.com/token"),
    j("key", ": knowledge/clients/acme2024/notes/page.md"),
    j("SIGNING_", "KEY=knowledge/clients/acme2024/notes.md"),
    j('{"pass', 'word": pw}'),
    j('{"pass', 'word": ""}'),
    j("first_", "pass = 1"),
    j("Unsubscribe: https://linear.app/unsubscribe?", "token=", "2d2b05a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5"),
])
def test_a_name_a_path_or_a_link_under_such_a_label_is_no_finding(line):
    assert scan.scan_text(line + "\n", "x") == [], line
    assert scan.alarms(line) == [], line


@pytest.mark.parametrize("kind,token", [
    ("ai_key", j("xa", "i-", "A1b2" * 20)), ("ai_key", j("gs", "k_", "A1b2" * 13)),
    ("ai_key", j("r", "8_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")), ("ai_key", j("pp", "lx-", "A1b2" * 12)),
    ("platform_token", j("shp", "at_", "a1b2" * 8)), ("platform_token", j("do", "p_v1_", "a1b2" * 16)),
    ("platform_token", j("sb", "p_", "a1b2" * 10)), ("platform_token", j("da", "pi", "a1b2" * 8)),
    ("platform_token", j("123456789", "0:AA", "A1b2" * 8, "C")), ("gitlab_token", j("gl", "dt-", "A1b2" * 6)),
])
def test_other_provider_keys_are_found(kind, token):
    assert kind in {f.kind for f in scan.scan_text(f"the key {token} here\n", "x")}
    assert kind in scan.alarms(f"the key {token} here")


def test_a_token_wrapped_at_a_quoted_printable_soft_break_is_found():
    token = j("sk-", "ant-", "api03-", "Ab1Cd")
    text = f"key {token}=\n{'Ef2' * 20} rest\n"
    found = {(f.kind, f.line_no) for f in scan.scan_text(text, "x")}
    assert {("anthropic_key", 1), ("anthropic_key", 2)} <= found


# ----- #41: a slot, a cut or a lookup with a password written onto it -----------------------------
@pytest.mark.parametrize("value", ["{{}}Hunter2tall", "${{}}Hunter2x", "[redacted]Hunter2tall", "config.Hunter2!",
                                   "<abc>Hunter2tall", "$DB_PASSx9!"])
def test_a_password_written_onto_a_slot_or_a_cut_is_found(value):
    line = "pass" + "word: " + value
    assert [f.kind for f in scan.scan_text(line, "x")] == ["password"], line
    assert scan.alarms(line) == ["password"], line


@pytest.mark.parametrize("value", ["process.env.DB_PASSWORD!", "[link-token]/1/0100", '"${{ secrets.DB }}",',
                                   "{{ vault.secret }}."])
def test_a_lookup_or_a_cut_followed_by_punctuation_stays_a_placeholder(value):
    assert scan.scan_text("pass" + "word: " + value, "x") == []


# ----- #19: what an archive holds ------------------------------------------------------------------
def _zip(members: dict, method=None) -> bytes:
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method or zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_a_token_inside_an_office_file_zip_or_gzip_is_found(tmp_path):
    import gzip
    run_split = "<w:r><w:t>" + ANTHROPIC[:14] + "</w:t></w:r><w:r><w:t>" + ANTHROPIC[14:] + "</w:t></w:r>"
    cases = {
        "notes.docx": _zip({"word/document.xml": "<w:document>" + run_split + "</w:document>"}),
        "sheet.xlsx": _zip({"xl/sharedStrings.xml": f"<si><t>key {GH}</t></si>"}),
        "bundle.zip": _zip({"inner.zip": _zip({"a.txt": f"key {GH}\n"})}),
        "log.gz": gzip.compress(f"line\nkey {GH}\n".encode() * 3),
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        kinds = {f.kind for f in scan.scan_paths([path])}
        assert kinds & {"anthropic_key", "github_token"}, name
    assert scan.scan_bytes(_zip({"a.txt": "nothing here\n"}), "clean.zip") == []


def test_an_archive_that_unpacks_past_the_ceiling_or_nests_too_deep_is_unscanned(monkeypatch):
    monkeypatch.setattr(scan, "SCAN_MAX_BYTES", 50_000)
    bomb = _zip({"a.txt": "0" * 200_000})
    assert len(bomb) < 50_000
    assert [f.kind for f in scan.scan_bytes(bomb, "bomb.zip")] == ["unscanned"]
    deep = b"clean\n"
    for _ in range(scan.UNPACK_DEPTH + 1):
        deep = _zip({"x.zip": deep})
    assert [f.kind for f in scan.scan_bytes(deep, "deep.zip")] == ["unscanned"]


# ----- #40: more credential file names, and the folders that hold keys -----------------------------
@pytest.mark.parametrize("rel,expected", [
    ("work/prod.env", True), (".envrc", True), ("work/.ssh/config", True), ("work/.aws/config", True),
    ("work/keys/id_rsa_work", True), ("work/keys/id_rsa_work.pub", False), ("work/secrets.yaml", True),
    ("work/.pgpass", True), ("work/AuthKey_ABC123.p8", True), ("work/kubeconfig", True),
    ("knowledge/topics/secrets/index.md", False), ("work/secrets.md", False), ("work/environment.md", False),
    ("work\\.ssh\\id", True)])
def test_more_credential_file_names(rel, expected):
    assert scan.credential_file(rel) is expected


def test_the_template_gitignore_ignores_the_further_credential_files(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_bytes((Path(scan.__file__).resolve().parents[2] / ".gitignore").read_bytes())

    def ignored(rel: str) -> bool:
        return git(root, "check-ignore", "-q", "--no-index", rel, check=False).returncode == 0

    for rel in ("work/prod.env", ".envrc", "work/.ssh/config", "work/keys/id_rsa_work", "work/secrets.yaml",
                "work/AuthKey_ABC123.p8", "work/.aws/credentials"):
        assert ignored(rel), rel
    for rel in ("work/keys/id_rsa_work.pub", "work/secrets.md", "knowledge/topics/secrets/index.md"):
        assert not ignored(rel), rel


def test_a_checkout_inside_a_credential_folder_is_no_finding_of_its_own(tmp_path):
    page = write(tmp_path / ".docker" / "space" / "work" / "notes.md", "nothing here\n")
    assert scan.scan_paths([page], tmp_path / ".docker" / "space") == []
    assert [f.kind for f in scan.scan_paths([page], tmp_path)] == ["credential_file"]


# ----- #19 / #60: the history a pull request lands, not only its last state -------------------------
def _history_repo(tmp_path, monkeypatch) -> tuple[Path, str]:
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root / "a.md", "clean\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root, git(root, "rev-parse", "HEAD").stdout.strip()


def test_changed_since_fails_on_a_secret_one_commit_added_and_the_next_removed(tmp_path, monkeypatch, capsys):
    root, base = _history_repo(tmp_path, monkeypatch)
    write(root / "work" / "notes.md", f"key {GH}\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "add")
    git(root, "rm", "-q", "work/notes.md")
    git(root, "commit", "-q", "-m", "remove")
    assert scan.main(["--root", str(root), "--changed-since", base]) == 1
    out = capsys.readouterr().out
    assert "github_token work/notes.md@" in out and GH not in out


def test_changed_since_fails_on_a_credential_file_committed_and_deleted(tmp_path, monkeypatch):
    root, base = _history_repo(tmp_path, monkeypatch)
    write(root / ".env", "NOTHING=1\n")
    git(root, "add", "-f", ".env")
    git(root, "commit", "-q", "-m", "add")
    git(root, "rm", "-q", ".env")
    git(root, "commit", "-q", "-m", "remove")
    assert scan.main(["--root", str(root), "--changed-since", base]) == 1


def test_changed_since_reads_what_a_merge_itself_adds(tmp_path, monkeypatch):
    root, base = _history_repo(tmp_path, monkeypatch)
    git(root, "checkout", "-q", "-b", "side")
    write(root / "side.md", "side\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "side")
    git(root, "checkout", "-q", "main")
    write(root / "main.md", "main\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "main")
    git(root, "merge", "-q", "--no-commit", "side")
    write(root / "side.md", f"side\nkey {GH}\n")   # written while merging: in no parent
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "merge")
    git(root, "rm", "-q", "side.md")
    git(root, "commit", "-q", "-m", "remove")
    assert scan.main(["--root", str(root), "--changed-since", base]) == 1


def test_changed_since_passes_a_clean_history(tmp_path, monkeypatch):
    root, base = _history_repo(tmp_path, monkeypatch)
    write(root / "b.md", "also clean\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "two")
    git(root, "rm", "-q", "b.md")
    git(root, "commit", "-q", "-m", "three")
    assert scan.main(["--root", str(root), "--changed-since", base]) == 0


# ----- #59: sign-in parameters on share hosts, encoded inner links -----------------------------------
@pytest.mark.parametrize("raw,scrubbed", [
    ("https://claude.ai/api/auth/verify_magic_link?token=Ab12Cd34Ef56Gh78Ij90",
     "https://claude.ai/api/auth/verify_magic_link?token=[link-token]"),
    ("https://docs.google.com/document/d/1abc/edit?usp=sharing&access_token=Ab12Cd34Ef56Gh78Ij90",
     "https://docs.google.com/document/d/1abc/edit?usp=sharing&access_token=[link-token]"),
    ("https://app.example.com/x?next=https%3A%2F%2Fa.example.org%2F%3Ftoken%3DAb12Cd34Ef56Gh78Ij90",
     "https://app.example.com/x?next=https%3A%2F%2Fa.example.org%2F%3Ftoken%3D[link-token]"),
])
def test_sign_in_parameters_are_cut_on_share_hosts_and_in_encoded_links(raw, scrubbed):
    assert scan.scrub_link_tokens(raw) == scrubbed


def test_a_claude_path_that_only_contains_a_sign_in_word_stays():
    link = "https://claude.ai/project/author-notes/AbCdEfGhIjKlMnOpQrStUv"
    assert scan.scrub_link_tokens(link) == link


# ----- #60: Git LFS, and commits the sync makes besides its own -------------------------------------
def _fake_lfs(repo: Path, tmp_path: Path, store: bool) -> None:
    """A clean filter that turns `work/lfs/*` into an LFS pointer, as git-lfs does, and with `store`
    also keeps the content in `.git/lfs/objects`, where git-lfs keeps it for the push."""
    script = tmp_path / "lfs_clean.py"
    script.write_text(
        "import hashlib, pathlib, sys\n"
        "data = sys.stdin.buffer.read()\n"
        "digest = hashlib.sha256(data).hexdigest()\n"
        f"if {store!r}:\n"
        "    target = pathlib.Path('.git/lfs/objects') / digest[:2] / digest[2:4] / digest\n"
        "    target.parent.mkdir(parents=True, exist_ok=True)\n"
        "    target.write_bytes(data)\n"
        "sys.stdout.buffer.write(b'version https://git-lfs.github.com/spec/v1\\noid sha256:' + digest.encode()"
        " + b'\\nsize ' + str(len(data)).encode() + b'\\n')\n", encoding="utf-8")
    python = Path(sys.executable).as_posix()
    git(repo, "config", "filter.lfs.clean", f'"{python}" "{script.as_posix()}"')
    attributes = repo / ".git" / "info" / "attributes"
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text("work/lfs/** filter=lfs\n", encoding="utf-8")


def test_a_secret_behind_an_lfs_pointer_is_left_local(repo, tmp_path):
    _fake_lfs(repo, tmp_path, store=False)
    write(repo / "work" / "lfs" / "data.md", f"key {GH}\n", minutes_old=20)
    write(repo / "work" / "small.md", "small and clean\n", minutes_old=20)
    assert run(repo) == 0
    files = head_files(repo)
    assert "work/small.md" in files and "work/lfs/data.md" not in files


def test_the_lfs_store_is_what_is_judged(repo, tmp_path, monkeypatch):
    _fake_lfs(repo, tmp_path, store=True)
    write(repo / "work" / "lfs" / "data.md", f"key {GH}\n", minutes_old=20)
    real_add = sync.Run.add

    def add(self, rels):
        real_add(self, rels)
        write(repo / "work" / "lfs" / "data.md", "clean now\n")   # the store keeps what was added

    monkeypatch.setattr(sync.Run, "add", add)
    assert run(repo) == 0
    assert "work/lfs/data.md" not in head_files(repo)
    assert run_lines(repo)[-1]["notes"]["scan_hits"] >= 1


def test_a_clean_lfs_file_lands(repo, tmp_path):
    _fake_lfs(repo, tmp_path, store=True)
    write(repo / "work" / "lfs" / "data.md", "nothing secret\n", minutes_old=20)
    assert run(repo) == 0
    assert "work/lfs/data.md" in head_files(repo)
    # what landed is the pointer, so the scan of the blob alone would never have seen the content
    assert git(repo, "show", "HEAD:work/lfs/data.md").stdout.startswith("version https://git-lfs")


def test_every_commit_the_sync_makes_scans_the_index_first(repo):
    write(repo / "work" / "hand.md", f"key {GH}\n")
    write(repo / "_meta" / "tests" / "answers.yaml", "tests: []\n")
    git(repo, "add", "--", "work/hand.md", "_meta/tests/answers.yaml")   # one meant, one staged by hand
    space_run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push"]))
    assert space_run.commit("chore: sync") is True
    files = head_files(repo)
    assert "_meta/tests/answers.yaml" in files and "work/hand.md" not in files
    assert space_run.notes["scan_hits"] == 1
    assert "?? work/hand.md" in git(repo, "status", "--porcelain", "--untracked-files=all").stdout
