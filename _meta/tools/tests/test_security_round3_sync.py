"""Round 3, the sync group: give the agents their upkeep back without reopening what each guard closed.

- The split sends every path the gate would hold to `sync/<machine>-protected`, not only the protected
  prefixes, so one held file never holds the journal, run lines and witness pages; the plain part the
  push carries is one the gate lands on green (`gate.classify` says auto-mergeable).
- The sync's own security items (`sync.yaml`, `sync-cloud.yaml`) and the morning view's ride the
  protected branch; a cloud run's go to `cloud/<chain>-protected`, keeping an earlier unmerged push's.
- The scan runs only the anchored shapes on bytes that are no text, reads a file of any size in
  chunks, and leaves `unscanned` for what it cannot read whole or an archive bomb.
- What the sync leaves local is recorded where agents read it, and asked about after seven days.
- A Keynote deck (`*.key`, a zip with no PEM header) is no credential file.

Every secret-shaped string is assembled from parts, so this file passes the scan it tests."""
import base64
import contextlib
import datetime as dt
import io
import json
import random
import zipfile
import zlib
from pathlib import Path

import pytest
import yaml

import gate
import scan
import sync
from test_sync import branch_paths, git, repo, run, run_lines, write  # noqa: F401  (repo is a fixture)
from test_sync_cloud import ITEMS, CloudGh, begin, branch_files, land, protected_files, shown
from test_sync_merge import SOURCE, clone_of, commit


def j(*parts: str) -> str:
    return "".join(parts)


GH = j("gh", "p_", "A1b2C3d4E5f6G7h8I9j0", "K1l2M3n4O5p6Q7r8")
WORD = j("pass", "word")
KEY_BLOCK = j("-----BEGIN ", "PRIVATE KEY-----\nabc\n-----END ", "PRIVATE KEY-----\n")


def push_all(clone: Path, monkeypatch) -> int:
    """One ordinary sync run that pushes to the bare origin; GitHub cannot be asked."""
    monkeypatch.setattr(sync.Run, "gh", lambda self, *args: None)
    return sync.main(["--root", str(clone)])


def landed(clone: Path, files: dict) -> None:
    """Files as a click already landed them on origin's main."""
    commit(clone, "base", files)
    git(clone, "push", "-q", "origin", "main")


def gate_says(clone: Path, branch: str) -> str:
    """The gate's verdict on a pushed sync branch, as the checks would give it."""
    paths = branch_paths(clone, branch)
    verdict, _ = gate.classify(clone, paths, base_ref="origin/main", head=f"origin/{branch}",
                               head_ref=branch)
    return verdict


# ----- item 1: every path the gate would hold goes to the protected branch -----------------------
def test_a_changed_log_line_and_a_name_read_at_any_depth_wait_and_the_rest_lands_on_green(repo, monkeypatch):
    landed(repo, {"log.md": "# Log\n\n- one\n- two\n"})
    write(repo / "log.md", "# Log\n\n- one\n- changed\n")
    write(repo / "work" / "harbor" / "CLAUDE.md", "# Instructions\n", minutes_old=20)
    write(repo / "journal" / "2026" / "11" / "2026-11-05.md", "## 09:00Z note\n", minutes_old=20)
    write(repo / "knowledge" / "sources" / "2026" / "11" / "w1.md", "# a witness page\n", minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    host = sync.hostname()
    plain, guarded = branch_paths(repo, f"sync/{host}"), branch_paths(repo, f"sync/{host}-protected")
    assert guarded == ["log.md", "work/harbor/CLAUDE.md"]
    assert plain == ["journal/2026/11/2026-11-05.md", "knowledge/sources/2026/11/w1.md"]
    assert gate_says(repo, f"sync/{host}") == gate.AUTO   # nothing in the plain part waits for a click


def test_a_registry_trust_change_waits_alone(repo, monkeypatch):
    rows = [{"id": "harbor", "kind": "clients", "title": "Harbor", "match": {"repo": "example-org/harbor"}}]
    landed(repo, {"_meta/subjects.yaml": yaml.safe_dump({"subjects": rows}, sort_keys=False)})
    rows[0]["match"]["repo"] = "example-org/harbor-two"
    write(repo / "_meta" / "subjects.yaml", yaml.safe_dump({"subjects": rows}, sort_keys=False))
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    assert push_all(repo, monkeypatch) == 0
    host = sync.hostname()
    assert branch_paths(repo, f"sync/{host}-protected") == ["_meta/subjects.yaml"]
    assert branch_paths(repo, f"sync/{host}") == ["work/notes.md"]
    assert gate_says(repo, f"sync/{host}") == gate.AUTO


# ----- item 6: the security items of the sync and the morning view no longer hold the plain PR ----
@pytest.mark.parametrize("writer", ["sync.yaml", "sync-cloud.yaml", "morning-view.yaml"])
def test_a_security_item_rides_the_protected_branch_and_the_desk_lands(repo, monkeypatch, writer):
    item = {"id": "sync-link-0a1b2c3d", "kind": "security", "text": "origin/main holds a link.",
            "recommendation": "take it out on main", "default": "the merge waits", "depends": 99}
    plain_item = {"id": "status-1", "kind": "request", "text": "A page is due.", "depends": 1}
    write(repo / "_inbound" / "needs-you" / writer, yaml.safe_dump({"items": [item]}), minutes_old=20)
    write(repo / "_inbound" / "needs-you" / "status.yaml", yaml.safe_dump({"items": [plain_item]}), minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    assert push_all(repo, monkeypatch) == 0
    host = sync.hostname()
    assert branch_paths(repo, f"sync/{host}-protected") == [f"_inbound/needs-you/{writer}"]
    assert branch_paths(repo, f"sync/{host}") == ["_inbound/needs-you/status.yaml", "work/notes.md"]
    assert gate_says(repo, f"sync/{host}") == gate.AUTO


def test_the_gates_own_verdict_moves_a_hold_no_test_of_the_split_names(repo, monkeypatch):
    """The split ends with the gate's whole verdict on the plain part: a hold it gives a path moves that
    path, so a check the gate learns later still leaves the rest landing; a hold that names no path is left
    in the run line for the maintenance agent."""
    real = gate.classify

    def classify(root, changed, base_ref=None, head="HEAD", author=None, head_ref=None):
        if "work/odd.md" in changed:
            return gate.PROTECTED, ["work/odd.md: a check the split does not know"]
        return real(root, changed, base_ref=base_ref, head=head, author=author, head_ref=head_ref)
    monkeypatch.setattr(gate, "classify", classify)
    write(repo / "work" / "odd.md", "# Odd\n", minutes_old=20)
    write(repo / "work" / "notes.md", "# Notes\n\nline one\nline two\n")
    assert push_all(repo, monkeypatch) == 0
    host = sync.hostname()
    assert branch_paths(repo, f"sync/{host}-protected") == ["work/odd.md"]
    assert branch_paths(repo, f"sync/{host}") == ["work/notes.md"]
    monkeypatch.setattr(gate, "classify", lambda *a, **k: (gate.PROTECTED, ["a reason about no file"]))
    write(repo / "work" / "notes.md", "# Notes\n\nline three\n")
    assert push_all(repo, monkeypatch) == 0
    assert run_lines(repo)[-1]["notes"]["plain_held"] == 1
    assert "work/notes.md" in branch_paths(repo, f"sync/{host}")   # nothing to move, and it still went out


def cloud_run(clone: Path, tmp_path: Path, monkeypatch, files: dict, gh=None) -> dict:
    """One cloud sweep run in `clone`: begin carries the earlier run, the tools write `files`, it lands."""
    begin(clone)
    for rel, text in files.items():
        write(clone / rel, text)
    code, notes = land(clone, monkeypatch, gh or CloudGh(tmp_path / "origin.git"))
    assert code == 0
    return notes


def protected_ids(clone: Path) -> list:
    git(clone, "fetch", "-q", "origin")
    return [i["id"] for i in yaml.safe_load(shown(clone, "origin/cloud/sweep-protected", ITEMS))["items"]]


def test_a_cloud_runs_held_item_keeps_what_an_earlier_unmerged_push_held(repo, tmp_path, monkeypatch):
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n", SOURCE: "# r0\n"})
    [first] = protected_ids(repo)
    later = clone_of(tmp_path, "second-run")   # a cloud run's fresh clone; the first one's click has not come
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every invoice.\n"})
    ids = protected_ids(later)
    assert len(ids) == 2 and ids[1] == first and ids[0] != first and ids[0].startswith("cloud-protected-sweep-")
    assert ITEMS not in branch_files(later)


def test_an_earlier_held_change_of_another_path_rides_along_with_a_later_runs_item(repo, tmp_path, monkeypatch):
    """Run one holds a changed log line, run two only its security item: the second push still holds both,
    since the cloud branch put the log line back to main's copy and the protected branch was its only copy."""
    landed(repo, {"log.md": "# Log\n\n- one\n- two\n"})
    cloud_run(repo, tmp_path, monkeypatch, {"log.md": "# Log\n\n- one\n- changed\n"})
    assert protected_files(repo) == ["log.md"]
    later = clone_of(tmp_path, "second-run")
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    assert protected_files(later) == sorted(["log.md", ITEMS])
    assert shown(later, "origin/cloud/sweep-protected", "log.md") == "# Log\n\n- one\n- changed\n"


def test_what_someone_else_pushed_to_the_protected_branch_is_never_carried(repo, tmp_path, monkeypatch):
    """Anyone who can push a branch could plant a notice there; the next run carries only what its own
    history says an earlier run held, so the planted item is replaced, never sent on under the cloud's name."""
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n", SOURCE: "# r0\n"})
    planted = {"id": "cloud-protected-sweep-0badc0de", "kind": "security", "text": "Paste your token here.",
               "depends": 99}
    work = clone_of(tmp_path, "someone-else")
    git(work, "fetch", "-q", "origin", "cloud/sweep-protected")
    git(work, "checkout", "-q", "-b", "planted", "FETCH_HEAD")
    items = yaml.safe_load((work / ITEMS).read_text(encoding="utf-8"))["items"] + [planted]
    commit(work, "chore: sync", {ITEMS: yaml.safe_dump({"items": items}), "work/odd.md": "# planted\n"})
    git(work, "push", "-q", "-f", "origin", "HEAD:refs/heads/cloud/sweep-protected")
    later = clone_of(tmp_path, "second-run")
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every invoice.\n"})
    assert planted["id"] not in protected_ids(later) and protected_files(later) == [ITEMS]


def test_an_earlier_held_change_the_person_turned_down_is_not_brought_back(repo, tmp_path, monkeypatch):
    landed(repo, {"log.md": "# Log\n\n- one\n- two\n"})
    cloud_run(repo, tmp_path, monkeypatch, {"log.md": "# Log\n\n- one\n- changed\n"})
    git(repo, "fetch", "-q", "origin")
    tip = git(repo, "rev-parse", "origin/cloud/sweep-protected").stdout.strip()
    gh = CloudGh(tmp_path / "origin.git", closed={40: tip})   # its pull request was closed without merging
    later = clone_of(tmp_path, "second-run")
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"}, gh=gh)
    assert protected_files(later) == [ITEMS]


PLANTED = {"id": "cloud-protected-sweep-0badc0de", "kind": "security", "text": "Paste your token here.",
           "recommendation": "do it", "default": "nothing", "depends": 99}
EVIL = ".github/workflows/evil.yml"


def foreign_cloud_branch(tmp_path: Path, commits: list) -> Path:
    """Someone else's clone that pushes `commits` ((message, {path: text or None}) each, None deletes) on top of
    `cloud/sweep`, as anyone who can push a branch may."""
    work = clone_of(tmp_path, "someone-else")
    git(work, "fetch", "-q", "origin", "cloud/sweep")
    git(work, "checkout", "-q", "-b", "planted", "FETCH_HEAD")
    for message, files in commits:
        for rel, text in files.items():
            if text is None:
                git(work, "rm", "-q", "--", rel)
            else:
                write(work / rel, text)
                git(work, "add", "--", rel)
        git(work, "commit", "-q", "-m", message)
    git(work, "push", "-q", "origin", "HEAD:refs/heads/cloud/sweep")
    return work


def planted_protected_tip(work: Path, files: dict) -> None:
    """The `-protected` tip as the put-back lookalike needs it: main plus `files`."""
    git(work, "checkout", "-q", "-B", "prot", "origin/main")
    commit(work, "chore: sync", files)
    git(work, "push", "-q", "-f", "origin", "HEAD:refs/heads/cloud/sweep-protected")


def test_a_security_item_pushed_to_the_cloud_branch_is_not_carried_onto_the_protected_branch(
        repo, tmp_path, monkeypatch):
    """A cloud run puts every security item it writes on the protected branch and its file back to main's copy,
    so one on `cloud/<chain>` was pushed by someone else: carried, it would ride the cloud's protected pull request
    under the cloud's name. The carry refuses the branch, and the run says someone pushed to it."""
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    foreign_cloud_branch(tmp_path, [("chore: sync", {ITEMS: yaml.safe_dump({"items": [PLANTED]})})])
    later = clone_of(tmp_path, "second-run")
    assert begin(later)["skipped"] == "protected"   # and `cloud.py begin` writes its notice of a foreign push
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every invoice.\n"})
    assert PLANTED["id"] not in protected_ids(later)
    assert PLANTED["id"] not in git(later, "show", f"origin/cloud/sweep:{ITEMS}", check=False).stdout


def test_a_put_back_lookalike_never_carries_a_protected_file_onto_the_protected_branch(repo, tmp_path, monkeypatch):
    """A workflow added and taken out again nets to nothing on `cloud/<chain>`, under a commit that reads as a
    put-back; with a `-protected` tip of main plus that workflow it would read as an earlier run's held change.
    The carry reads every commit, so the branch is refused, and no protected path rides along from history."""
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    flow = "on: push\njobs: {x: {runs-on: ubuntu-latest, steps: [{run: 'echo planted'}]}}\n"
    work = foreign_cloud_branch(tmp_path, [("chore: sync", {EVIL: flow}),
                                           ("chore: sync\n\nheld for cloud/sweep-protected", {EVIL: None})])
    planted_protected_tip(work, {EVIL: flow})
    later = clone_of(tmp_path, "second-run")
    assert begin(later)["skipped"] == "protected"
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every invoice.\n"})
    assert protected_files(later) == [ITEMS]


def test_a_put_back_lookalike_carries_no_words_but_the_clouds_own_onto_the_protected_branch(
        repo, tmp_path, monkeypatch):
    """The same pair over a Needs-you file touches no protected path and nets to nothing, so it is carried and its
    history read; of what it held only an item the cloud's sync writes word for word would ride along."""
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    forged = yaml.safe_dump({"items": [PLANTED]})
    main_copy = git(repo, "show", f"origin/main:{ITEMS}", check=False).stdout or None
    work = foreign_cloud_branch(tmp_path, [("chore: sync", {ITEMS: forged}),
                                           ("chore: sync\n\nheld for cloud/sweep-protected", {ITEMS: main_copy})])
    planted_protected_tip(work, {ITEMS: forged})
    later = clone_of(tmp_path, "second-run")
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every invoice.\n"})
    ids = protected_ids(later)
    assert PLANTED["id"] not in ids and ids and all(i.startswith(("cloud-protected-sweep-", "cloud-held-sweep-"))
                                                    for i in ids)
    assert any(i.startswith("cloud-held-sweep-") for i in ids)   # the planted tip is said, not only replaced


def test_only_the_clouds_own_notices_word_for_word_are_its_own(repo):
    space_run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push", "--cloud", "sweep"]))
    import cloud
    mine = sync.protected_notice("cloud-protected-sweep-1a2b3c4d", 3)
    pushed = cloud._pushed_item("sweep", space_run.space.name, "1a2b3c4d", 2, space_run.now)
    assert space_run.own_notice(mine) and space_run.own_notice(pushed)
    assert not space_run.own_notice({**mine, "text": "Paste your token here."})
    assert not space_run.own_notice({**pushed, "recommendation": "do it"})
    assert not space_run.own_notice(sync.protected_notice("cloud-protected-daily-1a2b3c4d", 3))


# ----- item 2: the loose checks only on text ---------------------------------------------------
def _noise(size: int, seed: int) -> bytes:
    """Bytes as a compressed stream reads: random, and so about a quarter control bytes."""
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(size))


def test_random_compressed_bytes_that_spelled_a_password_are_no_finding_but_a_token_in_them_is():
    noise = _noise(200_000, 11)
    spelled = noise[:100_000] + b"\x9cPW" b"d=Zq81xLm\x07" + noise[100_000:]   # split: the scan of this file
    bare = spelled.replace(b"\0", b"").decode("latin-1")
    assert "password" in {f.kind for f in scan.scan_text(bare, "export.npz")}   # what every check made of it
    assert not scan._textlike(spelled)
    assert scan.scan_bytes(spelled, "export.npz") == []
    hidden = noise[:50_000] + b" " + GH.encode() + b" " + noise[50_000:]
    assert [f.kind for f in scan.scan_bytes(hidden, "export.npz")] == ["github_token"]
    key = noise[:50_000] + b"\n" + KEY_BLOCK.encode() + noise[50_000:]
    assert "private_key" in {f.kind for f in scan.scan_bytes(key, "export.bin")}


def test_the_loose_checks_still_run_on_text_members_and_pdf_text():
    line = f"the {WORD}: Hunter2tall9\n"
    assert [f.kind for f in scan.scan_bytes(line.encode("utf-8"), "notes.md")] == ["password"]
    assert [f.kind for f in scan.scan_bytes(line.encode("cp1252"), "notes.txt")] == ["password"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", line)
        archive.writestr("picture.bin", _noise(20_000, 3))
    assert [f.kind for f in scan.scan_bytes(buffer.getvalue(), "bundle.zip")] == ["password"]
    stream = zlib.compress(f"BT ({WORD}: Hunter2tall9) Tj ET".encode())
    pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
           + stream + b"\nendstream\nendobj\n%%EOF\n")
    assert "password" in {f.kind for f in scan.scan_bytes(pdf, "invoice.pdf")}


# ----- item 3: a large file is read in chunks; `unscanned` means unreadable or a bomb --------------
@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(scan, "SCAN_MAX_BYTES", 50_000)
    monkeypatch.setattr(scan, "CHUNK_BYTES", 20_000)
    monkeypatch.setattr(scan, "CHUNK_OVERLAP", 2_000)


def test_a_token_across_a_chunk_border_is_found_once(small_chunks, tmp_path):
    noise = _noise(120_000, 5).replace(b"\n", b"")
    data = noise[:19_989] + b" " + GH.encode() + b" " + noise[19_989:]   # across the first border, no lines
    assert [f.kind for f in scan.scan_bytes(data, "dump.bin")] == ["github_token"]
    path = tmp_path / "dump.bin"
    path.write_bytes(data)
    assert [f.kind for f in scan.scan_file(path)] == ["github_token"]   # mapped, never read whole


def test_a_token_wrapped_over_the_line_a_chunk_ends_with_is_found_on_both_lines(small_chunks):
    lines = ["x" * 100 for _ in range(1000)]
    base = "".join(line + "\n" for line in lines).encode()
    stop = scan._cut(base, 0, scan.CHUNK_BYTES, 1)
    last = base[:stop].count(b"\n") - 1   # the line the first chunk ends with
    head, tail = GH[:20], GH[20:]
    lines[last] = "-" * (100 - len(head)) + head
    lines[last + 1] = tail + " " * (100 - len(tail))
    found = scan.scan_bytes("".join(line + "\n" for line in lines).encode(), "log.txt")
    assert {f.kind for f in found} == {"github_token"} and {f.line_no for f in found} == {last + 1, last + 2}


def test_a_large_utf16_file_is_read_in_its_encoding_past_the_first_chunk(small_chunks, tmp_path):
    text = "café notes line\n" * 6000 + f"the key {GH} here\n"
    path = tmp_path / "export.txt"
    path.write_bytes(text.encode("utf-16"))
    assert path.stat().st_size > 4 * scan.CHUNK_BYTES
    assert [(f.kind, f.line_no) for f in scan.scan_file(path)] == [("github_token", 6001)]


def test_a_member_past_the_memory_ceiling_is_read_through_a_temporary_file(small_chunks):
    rng = random.Random(9)
    words = "".join(" ".join(rng.choice(["harbor", "plan", "tide", "north", "wind", "dock"]) for _ in range(12))
                    + "\n" for _ in range(3000))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", words + f"key {GH}\n")
    assert len(words) > scan.SCAN_MAX_BYTES
    assert [f.kind for f in scan.scan_bytes(buffer.getvalue(), "bundle.zip")] == ["github_token"]


def test_an_archive_bomb_and_a_scan_out_of_time_are_unscanned(small_chunks, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros.txt", b"0" * 5_000_000)
    assert [f.kind for f in scan.scan_bytes(buffer.getvalue(), "bomb.zip")] == ["unscanned"]
    monkeypatch.setattr(scan, "SCAN_SECONDS", -1)
    assert [f.kind for f in scan.scan_bytes(b"clean line\n" * 10_000, "big.log")] == ["unscanned"]


# ----- item 5: a Keynote deck is no credential file -------------------------------------------------
def _deck(body: bytes = b"slides") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Index/Document.iwa", b"\x00\x01" + body)
    return buffer.getvalue()


def test_a_keynote_deck_is_no_credential_file_but_a_key_is():
    assert scan.credential_file("work/deck.key", _deck()) is False
    assert scan.credential_file("work/deck.key") is True   # no bytes to tell: the name decides
    assert scan.credential_file("work/server.key", KEY_BLOCK.encode()) is True
    assert scan.credential_file("work/odd.key", b"PK\x03\x04" + KEY_BLOCK.encode()) is True   # a PEM header
    assert scan.credential_file("work/server.pem", _deck()) is True   # only `*.key` has a deck's twin


def test_the_sync_lands_a_keynote_deck_and_leaves_a_key_local(repo):
    (repo / "work" / "deck.key").write_bytes(_deck())
    git(repo, "add", "work/deck.key")
    git(repo, "commit", "-q", "-m", "deck")
    (repo / "work" / "deck.key").write_bytes(_deck(b"slides, second draft"))
    write(repo / "work" / "server.key", "-----BEGIN CERTIFICATE-----\nabc\n", minutes_old=20)
    assert run(repo) == 0
    files = git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert "work/deck.key" in files and "work/server.key" not in files
    assert sync.read_left_local(repo)["work/server.key"]["why"] == ["credential_file"]


# ----- item 4: what stays local is recorded, and asked about after seven days -----------------------
def test_a_file_left_local_is_recorded_asked_about_after_seven_days_and_dropped_once_it_lands(repo, monkeypatch):
    write(repo / "work" / "export.md", f"key {GH}\n", minutes_old=20)
    write(repo / "work" / "server.pem", "not a key at all\n", minutes_old=20)
    assert run(repo) == 0
    record = sync.read_left_local(repo)
    assert record["work/export.md"]["why"] == ["github_token"]
    assert record["work/server.pem"]["why"] == ["credential_file"]
    assert run_lines(repo)[-1]["notes"]["left_local"] == 2
    assert "export.md" not in json.dumps(run_lines(repo))   # the names stay on this computer
    assert not git(repo, "ls-files", sync.LEFT_LOCAL.as_posix()).stdout.strip()   # never staged
    assert sync.left_local_cell(repo).startswith("left local 2 (since ")
    items_path = repo / sync.SYNC_ITEMS
    assert not any(i["id"].startswith(sync.LEFT_LOCAL_ITEM) for i in sync.items_in(items_path))
    since = record["work/export.md"]["since"]

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=8)
    monkeypatch.setenv("SPACE_NOW", later.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert run(repo) == 0
    assert sync.read_left_local(repo)["work/export.md"]["since"] == since   # its clock goes on
    [item] = [i for i in sync.items_in(items_path) if i["id"].startswith(sync.LEFT_LOCAL_ITEM)]
    assert item["kind"] == "request" and "2 file(s)" in item["text"] and "export.md" not in item["text"]
    assert sync.LEFT_LOCAL.as_posix() in item["text"] and item["recommendation"].startswith("yes: an agent")

    write(repo / "work" / "export.md", "key moved to the password manager\n", minutes_old=20)
    (repo / "work" / "server.pem").unlink()
    assert run(repo) == 0
    assert sync.read_left_local(repo) == {} and not (repo / sync.LEFT_LOCAL).exists()
    assert not any(i["id"].startswith(sync.LEFT_LOCAL_ITEM) for i in sync.items_in(items_path))
    assert sync.left_local_cell(repo) == "left local none"


def test_a_file_too_fresh_to_look_at_keeps_its_clock(repo, monkeypatch):
    write(repo / "work" / "export.md", f"key {GH}\n", minutes_old=20)
    assert run(repo) == 0
    since = sync.read_left_local(repo)["work/export.md"]["since"]
    write(repo / "work" / "export.md", f"key {GH}\nmore\n")   # edited just now: this run does not look at it
    assert run(repo) == 0
    assert sync.read_left_local(repo)["work/export.md"]["since"] == since


# ----- round 3, verified: a run's scan stays short and inside its lock -------------------------------
def committed(clone: Path) -> list:
    return git(clone, "show", "--name-only", "--format=", "HEAD").stdout.split()


def counting_scan(monkeypatch) -> list:
    """The labels `scan_bytes` is asked to read, in order."""
    seen, real = [], scan.scan_bytes

    def scan_bytes(data, label):
        seen.append(label)
        return real(data, label)
    monkeypatch.setattr(scan, "scan_bytes", scan_bytes)
    return seen


def test_a_blob_left_local_is_not_read_again_until_it_or_the_scan_changes(repo, monkeypatch):
    write(repo / "work" / "export.md", f"key {GH}\n", minutes_old=20)
    assert run(repo) == 0
    entry = sync.read_left_local(repo)["work/export.md"]
    assert entry["why"] == ["github_token"] and entry["scan"] == sync.scanner_id() and len(entry["blob"]) == 40
    seen = counting_scan(monkeypatch)
    assert run(repo) == 0
    assert "work/export.md" not in seen   # the same blob, the same scan: its verdict stands, unread
    assert sync.read_left_local(repo)["work/export.md"]["why"] == ["github_token"]
    assert "work/export.md" not in committed(repo)
    monkeypatch.setattr(sync, "_SCANNER", ["a-newer-scan"])
    assert run(repo) == 0
    assert "work/export.md" in seen   # a newer scan reads it again


def test_a_file_the_runs_scan_time_did_not_reach_stays_local_and_lands_on_a_later_run(repo, monkeypatch):
    write(repo / "work" / "plan.md", "# Notes\n\nline one\n", minutes_old=20)
    monkeypatch.setattr(sync, "SCAN_RUN_SECONDS", 0)
    assert run(repo) == 0
    assert "work/plan.md" not in committed(repo)
    entry = sync.read_left_local(repo)["work/plan.md"]
    assert entry["why"] == [sync.NOT_YET] and "blob" not in entry   # unjudged: the next run reads it
    monkeypatch.setattr(sync, "SCAN_RUN_SECONDS", 600)
    assert run(repo) == 0
    assert "work/plan.md" in committed(repo) and sync.read_left_local(repo) == {}


def test_a_file_staged_while_the_scan_reads_is_scanned_before_the_commit(repo, monkeypatch):
    write(repo / "work" / "plan.md", "# Notes\n\nline one\n", minutes_old=20)
    real = scan.scan_bytes

    def slow_scan(data, label):   # a hand's `git add` lands while the scan reads
        if label == "work/plan.md" and not (repo / "work" / "late.md").exists():
            write(repo / "work" / "late.md", f"key {GH}\n")
            git(repo, "add", "work/late.md")
        return real(data, label)
    monkeypatch.setattr(scan, "scan_bytes", slow_scan)
    assert run(repo) == 0
    assert "work/plan.md" in committed(repo) and "work/late.md" not in committed(repo)
    assert sync.read_left_local(repo)["work/late.md"]["why"] == ["github_token"]


def test_the_lock_moves_on_while_the_scan_reads(repo, monkeypatch):
    write(repo / "work" / "a.md", "# A\n", minutes_old=20)
    write(repo / "work" / "b.md", "# B\n", minutes_old=20)
    lock = Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()) / "sync.lock"
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen, real = [], scan.scan_bytes

    def scan_bytes(data, label):
        if label in ("work/a.md", "work/b.md"):
            seen.append(json.loads(lock.read_text(encoding="utf-8"))["at"])
            monkeypatch.setenv("SPACE_NOW", later)   # an hour on the clock per file read
        return real(data, label)
    monkeypatch.setattr(scan, "scan_bytes", scan_bytes)
    assert run(repo) == 0
    assert len(seen) == 2 and seen[1] == later   # the second file's read started with a fresh lock


# ----- round 3, verified: text among binary bytes, and a PDF's encoded streams ------------------------
CONFIG = f"[db]\nhost = db.example.test\n{WORD} = Hunter2Harbor9\niban = BE71 0961 " "2345 6769\n".encode()


def _tar(gz: bool) -> bytes:
    import tarfile
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if gz else "w") as archive:
        for name, data in (("config.ini", CONFIG), ("blob.bin", _noise(60_000, 4))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.mark.parametrize("gz", [False, True])
def test_a_config_beside_a_binary_member_in_a_tar_is_still_read_for_a_password(gz):
    assert {f.kind for f in scan.scan_bytes(_tar(gz), "backup.tar.gz")} == {"password", "iban"}


def test_a_sqlite_row_beside_a_blob_and_a_mail_body_beside_its_attachment_are_read_as_text(tmp_path):
    import sqlite3
    path = tmp_path / "notes.db"
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("create table t (a text, b blob)")
        db.execute("insert into t values (?, ?)", ("wacht" "woord: Hunter2Harbor9", _noise(5_000, 6)))
        db.commit()
    assert {f.kind for f in scan.scan_file(path)} == {"password"}
    body = f"Hello Ben,\r\n{WORD}: Hunter2Harbor9\r\n".encode("utf-16-le")
    assert {f.kind for f in scan.scan_bytes(body + _noise(5_000, 7), "mail.msg")} == {"password"}
    cyrillic = ("Привет, коллега. " * 20 + f"\n{WORD}: Hunter2Harbor9\n").encode() + b"\xff"
    assert {f.kind for f in scan.scan_bytes(cyrillic, "letter.txt")} == {"password"}   # one stray byte


def test_a_short_value_behind_its_length_byte_is_read_as_text(tmp_path):
    """A SQLite value or an old .xls cell is a short string behind a length byte below 0x20, among short
    neighbours: too short for a run of text, still read when it is all printable ASCII (`FIELD`)."""
    import sqlite3
    import struct
    path = tmp_path / "vault.db"
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("create table t (k text, v text, b blob)")
        for i in range(30):
            db.execute("insert into t values (?, ?, ?)",
                       (f"k{i}", j("pw", "d=Hunter2Harbor") if i == 13 else f"v{i}", _noise(300, 100 + i)))
        db.commit()
    assert {f.kind for f in scan.scan_file(path)} == {"password"}
    cells = ["Service", "Note", "Mail", f"{WORD}: Hunter2Harbor9", "VPN", "x"]
    sst = b"".join(struct.pack("<HB", len(c), 0) + c.encode() for c in cells)
    book = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504 + _noise(4_000, 8) + sst + _noise(4_000, 9)
    assert {f.kind for f in scan.scan_bytes(book, "accounts.xls")} == {"password"}


def _pdf(body: bytes, filters: bytes) -> bytes:
    return (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(body)).encode() + b" /Filter " + filters
            + b" >>\nstream\n" + body + b"~>\nendstream\nendobj\ntrailer\n%%EOF\n")


def test_an_ascii85_stream_that_spells_a_password_is_no_finding_but_a_token_in_it_is():
    encoded = base64.a85encode(zlib.compress(_noise(20_000, 8)), wrapcol=72)
    spelled = encoded[:9_000] + b"PW" b"d=Zq81lLm" + encoded[9_000:]   # all printable: the old reading took it as text
    assert scan.scan_bytes(_pdf(spelled, b"[/ASCII85Decode /FlateDecode]"), "drawing.pdf") == []
    content = f"BT /F1 12 Tf ({GH}) Tj ET\n".encode() + _noise(3_000, 9)
    for filters, body in ((b"[/ASCII85Decode /FlateDecode]", zlib.compress(content)), (b"/ASCII85Decode", content)):
        found = scan.scan_bytes(_pdf(base64.a85encode(body, wrapcol=72), filters), "drawing.pdf")
        assert [f.kind for f in found] == ["github_token"]
    plain = b"%PDF-1.4\n1 0 obj\n<< /Length 30 >>\nstream\n(" + f"{WORD}: Hunter2Harbor9".encode() + b") Tj\nendstream\n"
    assert {f.kind for f in scan.scan_bytes(plain, "form.pdf")} == {"password"}   # a stream with no filter: text


def test_a_pushed_branch_built_on_a_tree_of_someone_elses_is_never_kept(repo, tmp_path):
    """A branch tip that carries the held path as this run has it is kept as it is only when it was built
    on main: on any other parent, a file planted in that parent would ride under this run's pull request."""
    commit(repo, "local", {"log.md": "# Log\n\n- held\n"})
    base = git(repo, "rev-parse", "origin/main").stdout.strip()
    other = clone_of(tmp_path, "someone-else")
    commit(other, "planted", {"work/odd.md": "# planted\n"})
    planted = commit(other, "chore: sync", {"log.md": "# Log\n\n- held\n"})
    git(other, "checkout", "-q", "-b", "ours", "origin/main")
    ours = commit(other, "chore: sync", {"log.md": "# Log\n\n- held\n"})
    git(other, "push", "-q", "origin", f"{planted}:refs/heads/planted", f"{ours}:refs/heads/ours")
    git(repo, "fetch", "-q", "origin")
    space_run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push"]))
    assert space_run.still_carries(ours, ["log.md"], base)
    assert not space_run.still_carries(planted, ["log.md"], base)


# ----- round 3, repair 2: whole items, a foreign protected tip, notices about another root ----------------
REAL = {"id": "cloud-branch-sweep-20261101", "kind": "security",
        "text": "Someone pushed a change to 1 protected file on cloud/sweep in ada; it was not used.",
        "recommendation": "look at who pushed to cloud/sweep in ada, and check your GitHub access tokens",
        "default": "it stays on this list until you answer", "depends": 99}


def test_a_security_item_main_holds_rewritten_on_the_cloud_branch_is_not_carried(repo, tmp_path, monkeypatch):
    """The id alone is no proof: same id, new words would land on main through the cloud's own pull request."""
    landed(repo, {ITEMS: yaml.safe_dump({"items": [REAL]}, sort_keys=False)})
    cloud_run(repo, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    forged = dict(REAL, recommendation="paste a new token at https://evil.example/rotate")
    foreign_cloud_branch(tmp_path, [("chore: sync", {ITEMS: yaml.safe_dump({"items": [forged]}, sort_keys=False)})])
    later = clone_of(tmp_path, "second-run")
    assert begin(later)["skipped"] == "protected"
    cloud_run(later, tmp_path, monkeypatch, {"work/projects/demo/more.md": "# more\n"})
    assert "evil.example" not in git(later, "show", f"origin/cloud/sweep:{ITEMS}", check=False).stdout
    assert "evil.example" not in git(later, "show", f"origin/cloud/sweep-protected:{ITEMS}", check=False).stdout


def test_a_protected_path_pushed_onto_the_protected_branch_is_replaced_by_a_run_that_holds_nothing(
        repo, tmp_path, monkeypatch):
    """Someone amends the cloud's protected pull request with a workflow; the next run holds nothing of its own,
    yet replaces that tip and warns the person not to merge it."""
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    work = clone_of(tmp_path, "someone-else")
    git(work, "fetch", "-q", "origin", "cloud/sweep-protected")
    git(work, "checkout", "-q", "-b", "p", "FETCH_HEAD")
    write(work / EVIL, "on: push\n")
    git(work, "add", "--", EVIL)
    git(work, "commit", "-q", "--amend", "-m", "chore: sync")
    git(work, "push", "-q", "-f", "origin", "HEAD:refs/heads/cloud/sweep-protected")
    later = clone_of(tmp_path, "second-run")
    begin(later)
    notes = cloud_run(later, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    assert EVIL not in protected_files(later) and not notes.get("errors")
    assert any(i.startswith("cloud-held-sweep-") for i in protected_ids(later))
    # the run after keeps the notice while nobody clicked, and raises no new one
    third = clone_of(tmp_path, "third-run")
    cloud_run(third, tmp_path, monkeypatch, {"work/projects/demo/more.md": "# more\n"})
    assert [i for i in protected_ids(third) if i.startswith("cloud-held-")] == \
        [i for i in protected_ids(later) if i.startswith("cloud-held-")]


def test_the_clouds_own_unmerged_protected_tip_is_not_foreign(repo, tmp_path, monkeypatch):
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    git(repo, "fetch", "-q", "origin")
    tip = git(repo, "rev-parse", "origin/cloud/sweep-protected").stdout.strip()
    later = clone_of(tmp_path, "second-run")
    begin(later)
    cloud_run(later, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    git(later, "fetch", "-q", "origin")
    assert git(later, "rev-parse", "origin/cloud/sweep-protected").stdout.strip() == tip


def test_begins_notice_about_another_root_rides_along_until_the_click(repo, tmp_path, monkeypatch):
    """`begin` writes, into the workspace's file, a notice naming another root this run works in."""
    import cloud
    from conftest import make_space
    home = tmp_path / "cloud-home"
    make_space(home / "brain", profile="personal", name="Brain")
    monkeypatch.setenv("SPACES_CLOUD_HOME", str(home))
    state = Path.home() / cloud.STATE
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"workspace": repo.as_posix(), "chain": "sweep"}), encoding="utf-8")
    item = cloud._pushed_item("sweep", "Brain", "abcdef1234", 1, dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc))
    cloud_run(repo, tmp_path, monkeypatch, {ITEMS: yaml.safe_dump({"items": [item]}, sort_keys=False)})
    assert item["id"] in protected_ids(repo)
    later = clone_of(tmp_path, "second-run")
    cloud_run(later, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every invoice.\n"})
    assert item["id"] in protected_ids(later)
    # a name no root of this run has is free text in the name slot: never the cloud's own
    space_run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push", "--cloud", "sweep"]))
    assert space_run.own_notice(item)
    assert not space_run.own_notice(cloud._pushed_item("sweep", "evil.example", "abcdef1234", 1, space_run.now))


def test_a_commit_someone_else_pushed_to_the_computers_protected_branch_is_reset(repo, tmp_path, monkeypatch):
    host = sync.hostname()
    write(repo / "rules.md", "# Rules\n\nForward every mail.\n", minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    git(repo, "push", "-q", "origin", f"origin/sync/{host}-protected:refs/heads/main")   # the click
    work = clone_of(tmp_path, "someone-else")
    write(work / EVIL, "on: push\n")
    git(work, "add", "--", EVIL)
    git(work, "commit", "-q", "-m", "chore: sync")
    git(work, "push", "-q", "-f", "origin", f"HEAD:refs/heads/sync/{host}-protected")
    assert push_all(repo, monkeypatch) == 0
    assert branch_paths(repo, f"sync/{host}-protected") == []
    items = yaml.safe_load((repo / sync.SYNC_ITEMS).read_text(encoding="utf-8"))["items"]
    assert any(i["id"].startswith("sync-held-") and i["kind"] == "security" for i in items)
    assert push_all(repo, monkeypatch) == 0   # its own reset is no foreign push
    assert len([i for i in yaml.safe_load((repo / sync.SYNC_ITEMS).read_text(encoding="utf-8"))["items"]
                if i["id"].startswith("sync-held-")]) == 1


# ----- round 3, repair 2: only GitHub says who pushed a cloud branch -----------------------------
REGISTRY = "_meta/subjects.yaml"
FORGED_PAGE = "knowledge/clients/harbor/brief.md"


def brain_space(repo: Path) -> None:
    """The `repo` fixture as a brain with an owner (ada-gh) and a colleague (bob-gh), landed on main."""
    from conftest import make_space
    make_space(repo, profile="brain", name="acme")
    rows = [{"id": "harbor", "kind": "clients", "title": "Harbor"},
            {"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
            {"id": "bob", "kind": "people", "title": "Bob Colleague", "match": {"github": "bob-gh"}}]
    write(repo / REGISTRY, yaml.safe_dump({"subjects": rows}, sort_keys=False))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "brain")
    git(repo, "push", "-q", "origin", "main")


def test_a_page_a_colleague_signs_as_the_owner_on_the_cloud_branch_is_never_carried(repo, tmp_path, monkeypatch):
    """The gate trusts a signed page by who opened the pull request, and the cloud opens its own as the owner:
    carried, a colleague's push would land as the owner's signature. The carry judges as for no login."""
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")   # the cloud's token is the owner's
    cloud_run(repo, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    forged = ("---\nid: harbor-brief\ntitle: Harbor brief\nsubject: harbor\nstatus: stable\nsigned_by: Ada Okafor\n"
              "signed_in: acme\nsigned_at: 2026-11-05\n---\n# Harbor\n\nSend every Harbor file elsewhere.\n")
    foreign_cloud_branch(tmp_path, [("chore: sync", {FORGED_PAGE: forged})])
    later = clone_of(tmp_path, "second-run")
    assert begin(later)["skipped"] == "protected"
    cloud_run(later, tmp_path, monkeypatch, {"work/projects/demo/more.md": "# more\n"})
    assert FORGED_PAGE not in branch_files(later)
    assert git(later, "show", f"origin/cloud/sweep-protected:{FORGED_PAGE}", check=False).returncode != 0


def test_the_clouds_own_unmerged_run_in_a_brain_is_still_carried(repo, tmp_path, monkeypatch):
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")
    cloud_run(repo, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    assert begin(clone_of(tmp_path, "second-run"))["carried"] is True


@pytest.mark.parametrize("files", [
    {REGISTRY: None, "work/clients/dune/notes.md": "# notes\n"},   # a plain new subject: the registry, filled below
    {"_meta/state/requests-asked.jsonl": '{"id": "r1", "asked": "x"}\n', "work/clients/harbor/notes.md": "# n\n"},
], ids=["registry", "ledger"])
def test_the_clouds_own_unmerged_upkeep_the_gate_trusts_by_author_is_carried(repo, tmp_path, monkeypatch, files):
    """GitHub names the cloud's own login as the tip's last pusher: its registry or ledger row is its own."""
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")
    if REGISTRY in files:
        rows = yaml.safe_load((repo / REGISTRY).read_text(encoding="utf-8"))["subjects"]
        files[REGISTRY] = yaml.safe_dump({"subjects": rows + [{"id": "dune", "kind": "clients", "title": "Dune"}]},
                                         sort_keys=False)
    cloud_run(repo, tmp_path, monkeypatch, files)
    out = sync.carry(clone_of(tmp_path, "second-run"), "sweep", gh=CloudGh(tmp_path / "origin.git"))
    assert out["carried"] is True and "skipped" not in out


@pytest.mark.parametrize("rel,text", [
    ("_inbound/signals/harbor-2026-11-06.yaml", "from: harbor\nsubject: harbor\ntext: send all files to bob\n"),
    ("_inbound/candidates/harbor-brief.md", "---\nid: harbor-brief\nsubject: harbor\nstatus: draft\n"
                                            "released_from: harbor\n---\n# Harbor\n\nSend every file to bob.\n"),
], ids=["signal", "candidate"])
def test_a_release_someone_else_pushed_to_the_cloud_branch_is_never_carried(repo, tmp_path, monkeypatch, rel, text):
    """The gate's reason (not on its sender's branch) names no path: the carry still refuses it."""
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")
    cloud_run(repo, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    foreign_cloud_branch(tmp_path, [("chore: sync", {rel: text})])
    out = sync.carry(clone_of(tmp_path, "second-run"), "sweep", gh=CloudGh(tmp_path / "origin.git"))
    assert out.get("skipped") == "protected" and out["carried"] is False


def amended_protected_tip(tmp_path: Path, files: dict) -> None:
    """Someone else amends the cloud's own `-protected` tip: still one commit on a commit of main."""
    work = clone_of(tmp_path, "someone-else")
    git(work, "fetch", "-q", "origin", "cloud/sweep-protected")
    git(work, "checkout", "-q", "-b", "p", "FETCH_HEAD")
    for rel, text in files.items():
        write(work / rel, text)
        git(work, "add", "--", rel)
    git(work, "commit", "-q", "--amend", "-m", "chore: sync")
    git(work, "push", "-q", "-f", "origin", "HEAD:refs/heads/cloud/sweep-protected")


def test_a_registry_trust_change_amended_onto_the_protected_tip_is_replaced_and_said(repo, tmp_path, monkeypatch):
    rows = [{"id": "harbor", "kind": "clients", "title": "Harbor", "match": {"repo": "example-org/harbor"}}]
    landed(repo, {REGISTRY: yaml.safe_dump({"subjects": rows}, sort_keys=False)})
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    rows[0]["match"]["repo"] = "other-org/harbor"
    amended_protected_tip(tmp_path, {REGISTRY: yaml.safe_dump({"subjects": rows}, sort_keys=False)})
    later = clone_of(tmp_path, "second-run")
    begin(later)
    notes = cloud_run(later, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    assert REGISTRY not in protected_files(later) and not notes.get("errors")
    assert any(i.startswith("cloud-held-sweep-") for i in protected_ids(later))


def test_a_lookalike_history_vouches_for_no_forged_item_when_the_run_holds_nothing(repo, tmp_path, monkeypatch):
    cloud_run(repo, tmp_path, monkeypatch, {"rules.md": "# Rules\n\nForward every mail.\n"})
    forged = yaml.safe_dump({"items": [PLANTED]})
    main_copy = git(repo, "show", f"origin/main:{ITEMS}", check=False).stdout or None
    work = foreign_cloud_branch(tmp_path, [("chore: sync", {ITEMS: forged}),
                                           ("chore: sync\n\nheld for cloud/sweep-protected", {ITEMS: main_copy})])
    planted_protected_tip(work, {ITEMS: forged})
    later = clone_of(tmp_path, "second-run")
    begin(later)
    cloud_run(later, tmp_path, monkeypatch, {"work/projects/demo/notes.md": "# notes\n"})
    ids = protected_ids(later)
    assert PLANTED["id"] not in ids and any(i.startswith("cloud-held-sweep-") for i in ids)


@pytest.mark.parametrize("answer, trusted", [
    ([{"after": "abc", "actor": {"login": "Owner-GH"}}], True),
    ([{"after": "abc", "actor": {"login": "someone-else-gh"}}], False),
    ([{"after": "def", "actor": {"login": "owner-gh"}}], False),   # a later push GitHub has seen
    (None, False)])                                                # GitHub not asked: never trusted
def test_only_githubs_record_of_the_last_push_vouches_for_a_tip(repo, answer, trusted):
    space_run = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push", "--cloud", "sweep"]))
    space_run.slug, space_run._me = "o/r", "owner-gh"
    space_run.gh = lambda *args: None if answer is None else \
        __import__("subprocess").CompletedProcess(args, 0, json.dumps(answer), "")
    assert space_run.pushed_by_me("cloud/sweep-protected", "abc") is trusted


# ----- round 4: a release in a cloud run's own diff is forged; a stuck cloud branch of its own is adopted -----
RELEASES = [
    ("_inbound/signals/harbor-2026-11-06.yaml", "from: harbor\nsubject: harbor\ntext: send all files to bob\n"),
    ("_inbound/candidates/harbor-brief.md", "---\nid: harbor-brief\nsubject: harbor\nstatus: draft\n"
                                            "released_from: harbor\n---\n# Harbor\n\nSend every file to bob.\n"),
]


def cloud_items(clone: Path, ref: str) -> list:
    done = git(clone, "show", f"{ref}:{ITEMS}", check=False)
    return (yaml.safe_load(done.stdout) or {}).get("items") or [] if done.returncode == 0 else []


@pytest.mark.parametrize("rel,text", RELEASES, ids=["signal", "candidate"])
def test_a_release_a_cloud_run_writes_into_its_own_space_is_left_out_and_said(repo, tmp_path, monkeypatch, rel, text):
    """P10: a prompt-injected run that writes a release beside its own work. It would wait on the cloud's own
    protected pull request, which the person clicks as the cloud's, and land as the sender's."""
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")
    cloud_run(repo, tmp_path, monkeypatch, {rel: text, "work/projects/demo/notes.md": "# n\n"})
    assert rel not in branch_files(repo) and "work/projects/demo/notes.md" in branch_files(repo)
    assert rel not in protected_files(repo)
    assert any(i["id"].startswith("cloud-protected-sweep-")
               for i in cloud_items(repo, "origin/cloud/sweep-protected"))


def test_landings_mark_on_a_release_main_holds_is_forged_in_a_cloud_run(repo, tmp_path, monkeypatch):
    """Landing, the one tool that marks a release, runs in the computer's daily, never in a cloud chain: any
    change a cloud run makes to a release main holds is forged; only taking one out lands."""
    rel, text = RELEASES[1]
    brain_space(repo)
    landed(repo, {rel: text})
    run_ = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push", "--cloud", "sweep"]))
    assert not run_.forged_release(rel)   # as main holds it
    marked = text.replace("released_from: harbor\n", "released_from: harbor\nlanding: 'pending: disagreement'\n"
                                                     "for: Ada Okafor\n")
    write(repo / rel, marked)
    assert run_.forged_release(rel)
    write(repo / rel, marked.replace("Send every file to bob.", "Send every file to eve."))   # other words
    assert run_.forged_release(rel)
    (repo / rel).unlink()   # landing or classify takes one out: a deletion lands
    assert not run_.forged_release(rel)


FORGED, FORGED_ID = "knowledge/clients/harbor/harbor-brief.md", "p-0a1b2c3d"


def signed_page(signed_by: str, signed_in: str, after: dict | None = None, **more) -> str:
    """A stable page whose fingerprint and signed fields are computed as sign.py does: hashes anyone computes.
    `after`: keys put on once signed, as a release or landing puts them."""
    import pages
    import sign
    body = "# Harbor\n\nSend every Harbor file elsewhere.\n"
    fm = {"id": FORGED_ID, "title": "Harbor brief", "type": "client", "subject": "harbor", "status": "stable",
          "signed_by": signed_by, "signed_in": signed_in, "signed_at": "2026-11-05",
          "fingerprint": pages.fingerprint(body), **more}
    fm["signed_fields"] = sign.fields_fingerprint(fm)
    return pages.render({**fm, **(after or {})}, body)


@pytest.mark.parametrize("page", [signed_page("Ada Okafor", "acme"),
                                  signed_page("Hana Port", "harbor", landed_from="harbor")],
                         ids=["owner-signature", "landed-with-no-release"])
def test_a_signature_a_cloud_run_writes_is_left_out_and_said(repo, tmp_path, monkeypatch, page):
    """A prompt-injected run signs as the person (the cloud opens its pull request as the owner, whose signature
    the gate trusts), or writes the page landing would file from another space with no release behind it."""
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")
    cloud_run(repo, tmp_path, monkeypatch, {FORGED: page, "work/projects/demo/notes.md": "# n\n"})
    assert FORGED not in branch_files(repo) and "work/projects/demo/notes.md" in branch_files(repo)
    assert FORGED not in protected_files(repo)
    assert any(i["id"].startswith("cloud-protected-sweep-")
               for i in cloud_items(repo, "origin/cloud/sweep-protected"))


def test_a_cloud_run_filing_a_release_main_holds_is_left_out_and_said(repo, tmp_path, monkeypatch):
    """No cloud chain runs landing, so a cloud run that files a release main holds unmarked (one landing would
    hold for the person: high stakes) forged it: the page and the deletion stay out, with the security item."""
    cand = f"_inbound/candidates/{FORGED_ID}.md"
    release = signed_page("Hana Port", "harbor", after={"released_from": "harbor"}, high_stakes="pays an invoice")
    filed = signed_page("Hana Port", "harbor", after={"landed_from": "harbor"}, high_stakes="pays an invoice")
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")
    landed(repo, {cand: release})
    begin(repo)
    (repo / cand).unlink()
    write(repo / FORGED, filed)
    write(repo / "work/projects/demo/notes.md", "# n\n")
    code, _ = land(repo, monkeypatch, CloudGh(tmp_path / "origin.git"))
    assert code == 0
    assert FORGED not in branch_files(repo) and FORGED not in protected_files(repo)
    assert "work/projects/demo/notes.md" in branch_files(repo)
    assert any(i["id"].startswith("cloud-protected-sweep-")
               for i in cloud_items(repo, "origin/cloud/sweep-protected"))
    run_ = sync.Run(repo, repo / ".git", sync.parse(["--root", str(repo), "--no-push", "--cloud", "sweep"]))
    write(repo / FORGED, filed.replace("status: stable", "status: draft"))
    assert not run_.forged_signature(FORGED)   # not stable: no signature the gate trusts


class OwnerLegGh:
    """The computer's GitHub (`LegGh`) that also names ada-gh as its login and as the last pusher of each tip."""

    def __init__(self, origin: Path, pulls: dict):
        from test_sync_cloud_leg import LegGh
        self.leg = LegGh(origin, pulls)

    def __call__(self, *args):
        import subprocess
        route = next((a for a in args if a.startswith("repos/")), "")
        if args[:3] == ("api", "user", "--jq"):
            return subprocess.CompletedProcess(args, 0, "ada-gh\n", "")
        if "/activity?ref=refs/heads/" in route:
            sha = self.leg.head(route.split("refs/heads/")[1].split("&")[0])
            return subprocess.CompletedProcess(args, 0, json.dumps(
                [{"after": sha, "activity_type": "push", "actor": {"login": "ada-gh"}}] if sha else []), "")
        return self.leg(*args)


def test_the_clouds_own_stuck_upkeep_that_conflicts_is_tried_and_left_to_a_fresh_start(repo, tmp_path, monkeypatch,
                                                                                        capsys):
    """P9: a tip GitHub says the owner's login pushed is the cloud's own upkeep, so the computer's adopt tries it, as
    the carry tries it. One that still conflicts here is no item: the next cloud run sets it aside and starts fresh."""
    import routine
    from test_sync_cloud_leg import in_the_cloud, items, later, sync_run
    from test_sync_merge import cloud_branch, on_main
    brain_space(repo)
    commit(repo, "base", {"work/notes.md": "# Notes\n\nbase\n"})
    git(repo, "push", "-q", "origin", "main")
    in_the_cloud(repo)
    rows = yaml.safe_load((repo / REGISTRY).read_text(encoding="utf-8"))["subjects"]
    cloud_branch(tmp_path, {"work/notes.md": "# Notes\n\nthe cloud's line\n",
                            REGISTRY: yaml.safe_dump({"subjects": rows + [{"id": "dune", "kind": "clients",
                                                                            "title": "Dune"}]}, sort_keys=False)})
    on_main(tmp_path, {"work/notes.md": "# Notes\n\nmain's line\n"})
    assert sync.carry(clone_of(tmp_path, "cloud-next"), "sweep",
                      gh=OwnerLegGh(tmp_path / "origin.git", {}))["conflict"] is True
    monkeypatch.setattr(sync.Run, "cloud_host", lambda self: (self.root, routine.host(self.root)))
    now = later(monkeypatch)
    gh = OwnerLegGh(tmp_path / "origin.git", {7: ("cloud/sweep", now - dt.timedelta(hours=1), "dirty")})
    capsys.readouterr()
    assert sync_run(repo, monkeypatch, gh, "--no-push") == 0
    assert "adopt: pull request #7 conflicts" in capsys.readouterr().out   # tried, not left as someone else's
    assert items(repo) == []


# ----- round 1 repair: the preflight judges what the push carries, with the checks' author -------
def test_an_old_unsigned_local_commit_holds_no_signature_the_pushed_commit_carries_signed(repo, monkeypatch, capsys):
    """The split pushes fresh commits on origin/main, so the computer's own never reach main: an unsigned one to
    the page (before the key, while it could not sign, an adopted cloud head) stays in origin/main..HEAD. The
    preflight judges the commit the push would carry, as the checks will; a real key hold names the key."""
    brain_space(repo)
    monkeypatch.setattr(sync.Run, "me", lambda self: "ada-gh")
    assert sync.make_signing_key(repo) == sync.OK
    landed(repo, {gate.ALLOWED_SIGNERS: (repo / gate.ALLOWED_SIGNERS).read_text(encoding="utf-8")})
    stable = signed_page("Ada Okafor", "acme")
    commit(repo, "an unsigned local draft", {FORGED: stable.replace("status: stable", "status: draft")})
    write(repo / FORGED, stable, minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    host = sync.hostname()
    assert FORGED in branch_paths(repo, f"sync/{host}")
    assert FORGED not in branch_paths(repo, f"sync/{host}-protected")
    verdict, reasons = gate.classify(repo, [FORGED], base_ref="origin/main", head=f"origin/sync/{host}",
                                     author="ada-gh", head_ref=f"sync/{host}")
    assert verdict == gate.AUTO, reasons
    sync.signing_key().write_text("not a key\n", encoding="utf-8")   # it cannot sign now: the hold says why
    capsys.readouterr()
    write(repo / FORGED, stable.replace("2026-11-05", "2026-11-07"), minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    assert FORGED in branch_paths(repo, f"sync/{host}-protected")
    assert any(FORGED in line and "no commit the computer's key signed" in line
               for line in capsys.readouterr().out.splitlines() if line.startswith("split:"))


def test_a_personal_owners_own_signature_lands_on_the_plain_branch(repo, monkeypatch):
    """The checks name the pull request's author in a personal space too: a registry that gives the owner a login
    ties the signature to it, so the preflight names this checkout's login, not none."""
    rows = [{"id": "harbor", "kind": "clients", "title": "Harbor"},
            {"id": "ada-okafor", "kind": "people", "title": "Ada Okafor", "match": {"github": "adaokafor"}}]
    landed(repo, {REGISTRY: yaml.safe_dump({"subjects": rows}, sort_keys=False)})
    monkeypatch.setattr(sync.Run, "me", lambda self: "adaokafor")
    write(repo / FORGED, signed_page("Ada Okafor", "ada"), minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    host = sync.hostname()
    assert FORGED in branch_paths(repo, f"sync/{host}")
    assert branch_paths(repo, f"sync/{host}-protected") == []


def test_the_click_row_names_the_end_of_this_computers_key(tmp_path):
    """Every computer's allowed_signers line reads alike, and a cloud run can open a lookalike pull request: the
    row that asks for the click, read on the computer, names the end of this computer's key to compare."""
    import onboard
    from conftest import make_space
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    for args in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "base")):
        git(root, *args)
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert sync.make_signing_key(root) == sync.OK
    public = Path(f"{sync.signing_key()}.pub").read_text(encoding="utf-8").split()[1]
    step = onboard.signing_key_step(root)
    assert step.needs_owner and f"ends in {public[-16:]}" in step.result


def test_the_key_click_is_the_owners_item_and_its_rendered_text_names_the_keys_end(tmp_path):
    """The click on the key's line is the person's: the daily upkeep shows it as their Needs-you question, whose
    rendered text keeps the key's 16-character end, and writes no agent health row saying the agent handles it."""
    import needs_you
    import onboard
    from conftest import make_space
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    for args in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "base")):
        git(root, *args)
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    onboard.keys_upkeep(root)
    public = Path(f"{sync.signing_key()}.pub").read_text(encoding="utf-8").split()[1]
    assert needs_you.health_rows(root, "signing-key") == []
    shown = needs_you.render(root, items=needs_you.collect(root, online=False))
    assert needs_you.md_inline(public[-16:]) in shown and "your agent handles" not in shown


# ----- merge of main's fresh start (#77): a fresh start keeps the earlier run's hold ------------------------------
def test_a_run_that_started_fresh_keeps_what_the_set_aside_run_held(repo, tmp_path, monkeypatch):
    """A carry that conflicts is set aside and the run starts fresh from main, so the earlier run's put-back commit is
    not in this run's history; its hold on cloud/<chain>-protected must still ride the next held push."""
    from test_sync_merge import on_main
    landed(repo, {"log.md": "# Log\n\n- one\n- two\n"})
    cloud_run(repo, tmp_path, monkeypatch, {"log.md": "# Log\n\n- one\n- changed\n", "work/notes.md": "cloud\n"})
    assert protected_files(repo) == ["log.md"]
    on_main(tmp_path, {"work/notes.md": "computer\n"})   # main now conflicts with cloud/sweep
    later = clone_of(tmp_path, "second-run")
    done = begin(later)
    assert done.get("conflict") and done.get("undone")   # cloud.begin starts fresh here
    write(later / "rules.md", "# Rules\n\nForward every mail.\n")
    land(later, monkeypatch, CloudGh(tmp_path / "origin.git"))   # rules.md is left out: its notice is this run's hold
    held = protected_files(later)
    assert "log.md" in held and len(held) == 2, held
