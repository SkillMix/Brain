"""The computer's commit-signing key: a cloud run pushes with its person's token, as its person, to any branch,
so the gate trusts a new signature on a stable page only from a commit the computer's own key signed."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import gate
import pages
import sync
from conftest import make_space, write_page
from test_sync import repo  # noqa: F401  (a fixture)

PAGE = "knowledge/topics/pilots/p-1a2b3c4d.md"


def _git(root: Path, *args: str, signed: bool = False) -> str:
    flags = [] if signed else ["-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *flags, "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
                          cwd=str(root), check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _commit(root: Path, message: str, signed: bool = False) -> str:
    _git(root, "add", "-A")
    if signed:
        with sync.signing_on():
            _git(root, "commit", "-q", "-m", message, signed=True)
    else:
        _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _signed(root: Path, text: str) -> None:
    write_page(root, PAGE, text, id="p-1a2b3c4d", subject="pilots", status="stable", signed_by="Ada Okafor",
               signed_in=root.name, signed_at="2026-11-05", fingerprint="f" * 64)


def test_the_computers_key_is_made_once_and_listed_once(tmp_path):
    root = tmp_path / "space"
    root.mkdir()
    assert sync.make_signing_key(root) == sync.OK
    assert sync.signing_key().is_file()   # under the test's own home folder (conftest)
    listed = (root / gate.ALLOWED_SIGNERS).read_text(encoding="utf-8")
    assert listed.startswith("sync ssh-ed25519 ") and listed.count("\n") == 1
    assert sync.make_signing_key(root) == sync.OK
    assert (root / gate.ALLOWED_SIGNERS).read_text(encoding="utf-8") == listed


def test_a_signature_lands_on_green_only_from_a_commit_the_computers_key_signed(tmp_path):
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    _git(root, "init", "-q", "-b", "main")
    no_key = _commit(root, "base")
    _signed(root, "Signed on the computer.\n")
    _commit(root, "chore: sync", signed=True)
    assert gate._key_signed(root, no_key, "HEAD", PAGE)   # no allowed_signers on the base: the tie is opt-in
    _signed(root, "Rewritten, unsigned.\n")
    _commit(root, "chore: sync")
    assert gate._key_signed(root, no_key, "HEAD", PAGE)   # even unsigned, until a key is listed

    _git(root, "reset", "-q", "--hard", no_key)
    assert sync.make_signing_key(root) == sync.OK
    base = _commit(root, "the person's click landed the key")
    _signed(root, "Signed on the computer.\n")
    _commit(root, "chore: sync", signed=True)
    assert gate._key_signed(root, base, "HEAD", PAGE)
    paths = gate.changed_files(root, base)
    assert gate.classify(root, paths, base_ref=base, author="adaokafor", head_ref="sync/m-1a2b3c4d")[0] == gate.AUTO

    _signed(root, "Rewritten in a cloud run, pushed as the owner.\n")   # a later commit no key signed
    _commit(root, "chore: sync")
    assert not gate._key_signed(root, base, "HEAD", PAGE)
    verdict, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base, author="adaokafor",
                                     head_ref="cloud/sweep")
    assert verdict == gate.PROTECTED and any(PAGE in r for r in reasons)

    _git(root, "reset", "-q", "--hard", base)   # a key the base does not list
    os.remove(sync.signing_key())
    os.remove(f"{sync.signing_key()}.pub")
    _signed(root, "Signed with another key.\n")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(sync.signing_key())], check=True)
    _commit(root, "chore: sync", signed=True)
    assert not gate._key_signed(root, base, "HEAD", PAGE)


def test_setup_and_the_daily_make_and_list_the_computers_key(tmp_path, monkeypatch):
    """Setup's sync task makes the computer's key and lists it in the space (it rides the first fill, else the one
    click); the daily upkeep and the read-back name it until main lists it, and never make one in a cloud run."""
    import needs_you
    import onboard
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "base")
    report = onboard.Report(case="sync")
    onboard.register_sync(report, root, "pilot", 15, True, False)
    assert not report.failed() and sync.signing_key().is_file()
    public = " ".join(Path(f"{sync.signing_key()}.pub").read_text(encoding="utf-8").split()[:2])
    assert public in (root / gate.ALLOWED_SIGNERS).read_text(encoding="utf-8")
    waiting = onboard.signing_key_step(root)   # main does not list it yet: the person's one click
    assert not waiting.done and waiting.needs_owner and "click" in waiting.result
    onboard.keys_upkeep(root)   # the click is the person's: their question, no agent health row
    assert needs_you.health_rows(root, "signing-key") == []
    tail = public.split()[1][-16:].lower()
    assert [i.id for i in needs_you.routine_items(root)] == [
        "mount-keys-signing-key-" + "-".join(w for w in __import__("re").split(r"[^a-z0-9]+", tail) if w)]
    _commit(root, "the person's click landed the key")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert onboard.signing_key_step(root).done
    onboard.keys_upkeep(root)
    assert needs_you.health_rows(root, "signing-key") == []
    os.remove(sync.signing_key())
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    assert onboard.make_signing_key(root) and not sync.signing_key().is_file()


def test_the_sync_signs_the_commit_it_pushes_so_a_signature_lands_on_green(repo, monkeypatch, capsys):
    """commit-tree ignores commit.gpgsign: the split commit the sync pushes must carry the key itself."""
    from test_security_round3_sync import landed, push_all
    from test_sync import git, write
    assert sync.make_signing_key(repo) == sync.OK
    landed(repo, {gate.ALLOWED_SIGNERS: (repo / gate.ALLOWED_SIGNERS).read_text(encoding="utf-8")})
    body = "Pilots go ahead.\n"
    write(repo / PAGE, "---\nid: p-1a2b3c4d\ntitle: Pilots\ntype: concept\nsubject: pilots\nstatus: stable\n"
                       "signed_by: Ada Okafor\nsigned_in: ada\nsigned_at: '2026-11-05T09:00:00Z'\nfingerprint: "
                       + pages.fingerprint(body) + "\n---\n" + body, minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    assert "waits on the protected branch" not in capsys.readouterr().out
    git(repo, "fetch", "-q", "origin")
    branch = f"origin/sync/{sync.hostname()}"
    assert gate._key_signed(repo, "origin/main", branch, PAGE)
    assert gate.classify(repo, [PAGE], base_ref="origin/main", head=branch, author="adaokafor",
                         head_ref=branch.removeprefix("origin/"))[0] == gate.AUTO


def test_a_key_git_cannot_sign_with_still_lands_unsigned_and_is_named(repo, monkeypatch, capsys):
    """A broken key (or no ssh-keygen on the task's PATH) must not stop every landing: it commits unsigned and
    stale.yaml names why, for the health line and the morning view."""
    import yaml
    from test_security_round3_sync import push_all
    from test_sync import git, write
    key = sync.signing_key()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("not a key\n", encoding="utf-8")
    write(repo / "journal/2026/11/2026-11-06.md", "## 09:00Z note\n", minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    assert sync.CANNOT_SIGN in capsys.readouterr().out
    assert git(repo, "rev-list", "--count", "origin/main..HEAD").stdout.strip() != "0"   # it committed
    stale = yaml.safe_load((repo / "_meta/state/stale.yaml").read_text(encoding="utf-8"))
    named = [k for k in stale if k.startswith(sync.CANNOT_SIGN)]
    assert len(named) == 1 and named[0].count(key.name) == named[0].count("~/.spaces/signing_key")
    assert str(key.parent.parent.name) not in named[0]   # the home folder's name never lands
    os.remove(key)   # the key gone (or fixed): the entry goes
    write(repo / "journal/2026/11/2026-11-06.md", "## 09:00Z note\n## 09:10Z more\n", minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    stale = yaml.safe_load((repo / "_meta/state/stale.yaml").read_text(encoding="utf-8")) or {}
    assert not [k for k in stale if k.startswith(sync.CANNOT_SIGN)]


def test_a_persons_answer_lands_on_green_only_from_a_commit_the_computers_key_signed(tmp_path):
    """A cloud run pushes with its person's token: a verdict row it appends is never the person's answer,
    in any profile, on any branch; the computer's own signed commit lands."""
    import json
    store = "_meta/state/verdicts.jsonl"
    row = json.dumps({"id": "classify-decide-tide", "verb": "edit", "note": "company", "by": "Ada Okafor",
                      "at": "2026-11-06T09:00:00Z"}) + "\n"
    for profile, name in (("personal", "pilot"), ("brain", "northwind")):
        root = make_space(tmp_path / profile, profile=profile, name=name, owner="Ada Okafor",
                          subjects=[{"id": "ada-okafor", "kind": "people", "title": "Ada Okafor",
                                     "match": {"github": "adaokafor"}}])
        _git(root, "init", "-q", "-b", "main")
        assert sync.make_signing_key(root) == sync.OK
        base = _commit(root, "the key landed")
        (root / store).parent.mkdir(parents=True, exist_ok=True)
        (root / store).write_text(row, encoding="utf-8")
        _commit(root, "an answer, unsigned")
        for ref in ("cloud/daily", "sync/m-1"):
            verdict, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base,
                                             author="adaokafor", head_ref=ref)
            assert verdict == gate.PROTECTED and any(r.startswith(store) for r in reasons), (profile, ref, reasons)
        _git(root, "reset", "-q", "--hard", base)
        (root / store).parent.mkdir(parents=True, exist_ok=True)
        (root / store).write_text(row, encoding="utf-8")
        _commit(root, "chore: sync", signed=True)
        verdict, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base, author="adaokafor",
                                         head_ref="sync/m-1")
        assert verdict == gate.AUTO, (profile, reasons)


def test_once_main_lists_keys_a_computer_without_one_makes_and_lists_its_own(repo, monkeypatch, capsys):
    """A second computer, or one set up before the tie: its sync makes the key and lists it (the line waits for
    the click), so its signatures stop waiting unseen; a key it cannot make is named in stale.yaml."""
    import yaml
    from test_security_round3_sync import landed, push_all
    from test_sync import git, write
    landed(repo, {gate.ALLOWED_SIGNERS: "other ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherComputerKey\n"})
    assert not sync.signing_key().is_file()
    write(repo / "journal/2026/11/2026-11-06.md", "## 09:00Z note\n", minutes_old=20)
    assert push_all(repo, monkeypatch) == 0   # the line goes onto main's own file after the merge
    assert push_all(repo, monkeypatch) == 0   # and the next run commits it
    public = " ".join(Path(f"{sync.signing_key()}.pub").read_text(encoding="utf-8").split()[:2])
    listed = git(repo, "show", f"HEAD:{gate.ALLOWED_SIGNERS}").stdout
    assert listed.startswith("other ssh-ed25519 ") and f"sync {public}\n" in listed   # committed for the click
    assert sync.keep_signing_key(repo) is None   # listed now: nothing more to do
    os.remove(f"{sync.signing_key()}.pub")   # only the public half gone: the row's command reads it back
    assert sync.main(["--root", str(repo), "--signing-key"]) == sync.OK
    assert " ".join(Path(f"{sync.signing_key()}.pub").read_text(encoding="utf-8").split()[:2]) == public
    sync.signing_key().write_text("not a key\n", encoding="utf-8")   # a key it cannot read back: named, no crash
    os.remove(f"{sync.signing_key()}.pub")
    write(repo / "journal/2026/11/2026-11-06.md", "## 09:00Z note\n## 09:10Z more\n", minutes_old=20)
    assert push_all(repo, monkeypatch) == 0
    stale = yaml.safe_load((repo / "_meta/state/stale.yaml").read_text(encoding="utf-8"))
    assert [k for k in stale if k.startswith(sync.CANNOT_SIGN)]
    why = sync.keep_signing_key(repo)   # the key it cannot read back: named as the person's step, not a rerun
    assert why and "OpenSSH" in why and "--signing-key" not in why


ADA_ROW = [{"id": "ada-okafor", "kind": "people", "title": "Ada Okafor", "match": {"github": "adaokafor"}}]


def _keyed_space(tmp_path, profile, name):
    root = make_space(tmp_path / profile, profile=profile, name=name, owner="Ada Okafor", subjects=ADA_ROW)
    _git(root, "init", "-q", "-b", "main")
    assert sync.make_signing_key(root) == sync.OK
    (root / "log.md").write_text("# Log\n", encoding="utf-8")
    return root


def test_deleting_the_answer_store_waits_unless_the_computers_key_signed_it(tmp_path):
    """With no store, the computer's next status run pins every log.md line as an answer: a cloud run that
    deletes the store and appends lines would answer as its person."""
    import status
    for profile, name in (("personal", "pilot"), ("brain", "northwind")):
        root = _keyed_space(tmp_path, profile, name)
        assert status.pin_cutover(root)
        base = _commit(root, "the key landed, the store begun")
        (root / status.VERDICTS).unlink()
        with open(root / "log.md", "a", encoding="utf-8", newline="\n") as f:
            f.write("- 2026-11-06T09:00:00Z edit classify-decide-tide by Ada Okafor\n")
        _commit(root, "a cloud run, unsigned")
        verdict, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base,
                                         author="adaokafor", head_ref="cloud/daily")
        assert verdict == gate.PROTECTED and any(r.startswith(gate.VERDICTS) for r in reasons), (profile, reasons)
        _git(root, "reset", "-q", "--hard", base)
        (root / status.VERDICTS).unlink()
        _commit(root, "chore: sync", signed=True)
        verdict, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base,
                                         author="adaokafor", head_ref="sync/m-1")
        assert verdict == gate.AUTO, (profile, reasons)


def test_the_owners_login_changes_what_a_signed_page_supersedes_only_from_a_keyed_commit(tmp_path):
    """`supersedes` is outside the signature: a cloud run pushing as the owner would change which of the
    owner's decisions is in force; the computer's own signed commit lands."""
    import decisions
    a, b = "knowledge/decisions/d-0000aa01.md", "knowledge/decisions/d-0000bb02.md"

    def decision(root, rel, pid, **extra):
        write_page(root, rel, "A choice.\n", id=pid, type="decision", subject="pilots", status="stable",
                   signed_by="Ada Okafor", signed_in=root.name, signed_at="2026-11-05", fingerprint="f" * 64, **extra)
    for profile, name in (("personal", "pilot"), ("brain", "northwind")):
        root = _keyed_space(tmp_path, profile, name)
        decision(root, a, "d-0000aa01")
        decision(root, b, "d-0000bb02")
        base = _commit(root, "two signed decisions")
        for signed, want in ((False, gate.PROTECTED), (True, gate.AUTO)):
            _git(root, "reset", "-q", "--hard", base)
            decision(root, a, "d-0000aa01", supersedes="d-0000bb02")
            _commit(root, "chore: sync", signed=signed)
            assert {r["id"]: r for r in decisions.scan(root)}["d-0000bb02"].get("superseded_by") == "d-0000aa01"
            verdict, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base,
                                             author="adaokafor", head_ref="cloud/daily")
            assert verdict == want and (signed or any(a in r for r in reasons)), (profile, signed, reasons)


def test_a_signature_line_in_the_history_lands_only_from_a_keyed_commit(tmp_path):
    """Another space's landing reads `fingerprint` and `fields` lines of log.md as the signature
    (landing.signer_problem): only sign.py on the computer writes them. Other appended lines stay on green."""
    f64, e64 = "f" * 64, "e" * 64
    lines = (f"- 2026-11-06T09:00:00Z fingerprint p-1a2b3c4d {f64} by Ada Okafor\n"
             f"- 2026-11-06T09:00:00Z fields p-1a2b3c4d {e64} by Ada Okafor\n")
    root = _keyed_space(tmp_path, "personal", "pilot")
    base = _commit(root, "the key landed")
    for text, signed, want in ((lines, False, gate.PROTECTED), (lines, True, gate.AUTO),
                               ("- 2026-11-06T09:00:00Z swept 3 records\n", False, gate.AUTO)):
        _git(root, "reset", "-q", "--hard", base)
        with open(root / "log.md", "a", encoding="utf-8", newline="\n") as f:
            f.write(text)
        _commit(root, "chore: sync", signed=signed)
        for ref in ("cloud/daily", "sync/m-1"):
            verdict, reasons = gate.classify(root, gate.changed_files(root, base), base_ref=base,
                                             author="adaokafor", head_ref=ref)
            assert verdict == want, (text, signed, ref, reasons)


def test_a_key_hidden_after_another_keys_end_verifies_nothing(tmp_path):
    """allowed_signers ignores what follows a key, so a line `sync <another key> <this key's end>`, or one with this
    whole key as its comment, ends in what the click row names: the gate reads only three-field lines, so such a
    line lists no key, and the sync does not take it for this computer's own line."""
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    _git(root, "init", "-q", "-b", "main")
    first = _commit(root, "base")
    assert sync.make_signing_key(root) == sync.OK
    legit = sync.own_signer()
    other = tmp_path / "other_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
    forged = " ".join(Path(f"{other}.pub").read_text(encoding="utf-8").split()[:2])
    os.replace(other, sync.signing_key())   # the commits below are signed with the other key
    os.replace(f"{other}.pub", f"{sync.signing_key()}.pub")
    for comment in (legit[-16:], legit[len("sync "):]):
        line = f"sync {forged} {comment}\n"
        assert line.rstrip().endswith(legit[-16:]) and not sync.lists(line, legit)
        _git(root, "reset", "-q", "--hard", first)
        (root / ".github").mkdir(exist_ok=True)
        (root / gate.ALLOWED_SIGNERS).write_text(line, encoding="utf-8")
        base = _commit(root, "a click on the look-alike line")
        _signed(root, "Signed with the other key.\n")
        _commit(root, "chore: sync", signed=True)
        assert not gate._key_signed(root, base, "HEAD", PAGE)
    _git(root, "reset", "-q", "--hard", first)
    (root / ".github").mkdir(exist_ok=True)
    (root / gate.ALLOWED_SIGNERS).write_text(f"sync {forged}\n", encoding="utf-8")   # its own plain line verifies
    base = _commit(root, "a plain line")
    _signed(root, "Signed with the other key.\n")
    _commit(root, "chore: sync", signed=True)
    assert gate._key_signed(root, base, "HEAD", PAGE)


def _clone(tmp_path, name: str) -> Path:
    from test_sync import git
    path = tmp_path / name
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(path))
    for key, value in (("user.name", "t"), ("user.email", "t@example.test"), ("commit.gpgsign", "false")):
        git(path, "config", key, value)
    return path


def test_a_key_listed_on_a_main_fetched_but_not_merged_still_merges_and_waits_for_the_click(repo, tmp_path,
                                                                                            monkeypatch, capsys):
    """An editor's autofetch moved origin/main to one that lists a key: the sync merges main first and adds this
    computer's line to main's own file, so no merge conflicts and the line waits on the protected branch."""
    from test_security_round3_sync import landed, push_all
    from test_sync import git, write
    landed(repo, {"README.md": "# space\n"})
    other = _clone(tmp_path, "other")
    landed(other, {gate.ALLOWED_SIGNERS: "sync ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherComputerKey\n"})
    git(repo, "fetch", "-q", "origin")
    for i in range(2):
        write(repo / "journal/2026/11/2026-11-06.md", "## 09:00Z note\n" * (i + 1), minutes_old=20)
        assert push_all(repo, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "conflict" not in out
    git(repo, "fetch", "-q", "origin")
    held = git(repo, "show", f"origin/sync/{sync.hostname()}-protected:{gate.ALLOWED_SIGNERS}").stdout
    assert held == f"sync ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherComputerKey\n{sync.own_signer()}\n"


def test_two_computers_listing_their_keys_both_land_on_the_owners_clicks(repo, tmp_path, monkeypatch, capsys):
    """Two keyless computers each add their line at the end of main's file; once the first one's click lands, the
    second's merge takes main's copy plus its own line, so it keeps taking in main and its click merges."""
    from test_security_round3_sync import landed, push_all
    from test_sync import git, write
    landed(repo, {gate.ALLOWED_SIGNERS: "other ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherComputerKey\n"})
    second = _clone(tmp_path, "second")
    lines = {}
    for name, clone in (("m-one", repo), ("m-two", second)):
        home = tmp_path / f"home-{name}"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls, home=home: home))
        monkeypatch.setattr(sync, "hostname", lambda name=name: name)
        for i in range(2):
            write(clone / f"journal/2026/11/{name}.md", "## 09:00Z note\n" * (i + 1), minutes_old=20)
            assert push_all(clone, monkeypatch) == 0
        lines[name] = sync.own_signer()
    owner = _clone(tmp_path, "owner")
    git(owner, "merge", "-q", "--no-edit", "origin/sync/m-one-protected")   # the owner's first click
    git(owner, "push", "-q", "origin", "main")
    capsys.readouterr()
    write(second / "journal/2026/11/m-two.md", "## 09:00Z note\n## 10:00Z more\n", minutes_old=20)
    assert push_all(second, monkeypatch) == 0
    assert "conflict" not in capsys.readouterr().out
    assert git(second, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode == 0
    git(owner, "fetch", "-q", "origin")
    git(owner, "merge", "-q", "--no-edit", "origin/sync/m-two-protected")   # the second click merges
    assert git(owner, "show", f"HEAD:{gate.ALLOWED_SIGNERS}").stdout == \
        f"other ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherComputerKey\n{lines['m-one']}\n{lines['m-two']}\n"


def test_two_computers_health_rows_never_stop_a_merge(repo, tmp_path, monkeypatch, capsys):
    """Each computer writes its own health rows (one has its key on main, the other waits for the click): the
    store keeps this checkout's side in a merge, so the second computer keeps taking in main."""
    import needs_you
    from test_security_round3_sync import landed, push_all
    rel = "_meta/state/health/signing-key.yaml"
    landed(repo, {rel: "items: []\n"})
    second = _clone(tmp_path, "second")
    landed(repo, {rel: "items:\n- id: signing-key\n  text: one computer's row\n"})
    needs_you.set_health(second, "signing-key", [{"id": "signing-key", "text": "the other computer's row"}])
    assert push_all(second, monkeypatch) == 0
    assert "conflict" not in capsys.readouterr().out
    from test_sync import git
    assert git(second, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False).returncode == 0


def test_an_answered_key_question_does_not_hide_a_new_key(tmp_path):
    """The owner's answer to the key question stays with that key: a new key on this computer (or another computer
    of theirs, whose verdicts are shared) is a new question, shown on Needs you with its own end to compare."""
    import needs_you
    import onboard
    import sign
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "base")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")

    def tail():
        return Path(f"{sync.signing_key()}.pub").read_text(encoding="utf-8").split()[1][-16:]

    onboard.keys_upkeep(root)
    first, = [i.id for i in needs_you.collect(root, online=False) if i.id.startswith("mount-keys-signing-key")]
    sign.apply(root, {first: "yes"}, by="Ada Okafor")
    assert first not in [i.id for i in needs_you.collect(root, online=False)]
    old = tail()
    for p in (sync.signing_key(), Path(f"{sync.signing_key()}.pub")):
        p.unlink()
    onboard.keys_upkeep(root)
    assert tail() != old
    shown = needs_you.render(root, items=needs_you.collect(root, online=False))
    assert needs_you.md_inline(tail()) in shown


def test_no_ssh_keygen_is_said_never_raised(tmp_path, monkeypatch):
    """Registering the sync task and the daily key upkeep get onboarding's 'install OpenSSH' answer back."""
    import onboard
    real = subprocess.run

    def run(args, *a, **kw):
        if args and args[0] == "ssh-keygen":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return real(args, *a, **kw)
    monkeypatch.setattr(subprocess, "run", run)
    root = tmp_path / "space"
    root.mkdir()
    assert sync.make_signing_key(root) == sync.ERROR
    assert "install OpenSSH" in onboard.make_signing_key(root)


def test_githubs_merge_of_a_signed_pull_request_is_no_unsigned_commit(tmp_path, monkeypatch):
    """CI judges GitHub's own merge of the pull request into main, which no key signed: with the pull
    request's head in PR_HEAD_SHA the tie checks the pull request's commits, and an unsigned one still fails."""
    root = make_space(tmp_path / "pilot", profile="personal", name="pilot", owner="Ada Okafor")
    log = root / "log.md"

    def merged(pr_signed: bool) -> tuple[str, str, str]:
        _git(root, "checkout", "-q", "-b", "pr")
        log.write_text(log.read_text(encoding="utf-8") + "- 2026-11-06T10:00:00Z fingerprint p-1 abc by Ada\n",
                       encoding="utf-8", newline="\n")
        head = _commit(root, "chore: sync", signed=pr_signed)
        _git(root, "checkout", "-q", "main")
        text = log.read_text(encoding="utf-8").replace("# Log\n", "# Log\n- 2026-11-06T09:00:00Z main moved\n")
        log.write_text(text, encoding="utf-8", newline="\n")
        base = _commit(root, "main moved")
        _git(root, "merge", "-q", "--no-ff", "-m", "Merge pull request", "pr")   # clean: both sides kept
        return base, _git(root, "rev-parse", "HEAD"), head

    _git(root, "init", "-q", "-b", "main")
    log.write_text("# Log\n- 2026-11-01T09:00:00Z a\n- 2026-11-02T09:00:00Z b\n", encoding="utf-8", newline="\n")
    assert sync.make_signing_key(root) == sync.OK
    start = _commit(root, "the key landed")
    base, merge, head = merged(pr_signed=True)
    assert not gate._key_signed(root, base, merge, "log.md")   # the merge itself is unsigned
    monkeypatch.setenv("PR_HEAD_SHA", head)
    assert gate._key_signed(root, base, merge, "log.md")
    _git(root, "reset", "-q", "--hard", start)
    _git(root, "branch", "-q", "-D", "pr")
    base, merge, head = merged(pr_signed=False)                 # an unsigned pull request: its merge is no cover
    monkeypatch.setenv("PR_HEAD_SHA", head)
    assert not gate._key_signed(root, base, merge, "log.md")
    monkeypatch.setenv("PR_HEAD_SHA", "0" * 40)                 # a head that is not the merge's: today's check
    assert not gate._key_signed(root, base, merge, "log.md")


def test_a_doctor_machine_takes_the_home_a_test_gives():
    import doctor
    assert doctor.Machine().home == Path.home()   # conftest's home, never the real one
