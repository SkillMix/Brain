"""Security regressions for record text and how it is rendered (cluster records-render).

Record text is written by anyone who can send the person a mail or an invite, and a mounted
space's files by anyone who can push to it. None of it may answer the briefing for the person,
sign for them, widen who reads a record, forge a line on their Needs you list, stall the sweep,
or write outside the space.
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import chain
import distill
import morning_view
import needs_you
import owner_email
import pages
import reply
import spaceyaml
import sweep
import unsure
from conftest import NOW, make_space, mount_brain, write_page

TOOLS = Path(__file__).resolve().parents[1]
OWNER = "owner@example.test"


# ----- helpers -------------------------------------------------------------------------------
def feed(root, records, source="mail"):
    path = root / "_meta" / "state" / "feed" / f"{source}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def briefed(root):
    """Run the morning view over three candidates; the reference the mail carries."""
    for n in range(3):
        write_page(root, f"_inbound/candidates/c{n}.md", id=f"p-0000c00{n}", title=f"Candidate {n}",
                   subject="harbor")
    morning_view.run(root, "Ada Okafor", now=NOW)
    payload = json.loads((root / morning_view.PAYLOAD).read_text(encoding="utf-8"))
    found = morning_view.REFERENCE_RE.search(payload["body"])
    assert found and found.group(1) in payload["htmlBody"], "the reference is in both parts of the mail"
    return found.group(1)


def mail(rid, sender, text, to=(OWNER,), subject="Re: Morning briefing: 3 items need you", **more):
    return {"id": rid, "kind": "mail", "at": "2026-10-12T11:30:00Z", "from": sender, "to": list(to),
            "subject": subject, "text": text, **more}


def answers(root):
    return sorted((root / "_inbound" / "answers").glob("*.md"))


def seen_rows(root):
    path = root / reply.SEEN
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ----- 8: an outsider cannot answer the briefing ---------------------------------------------
@pytest.mark.parametrize("sender", [
    f'"{OWNER}" <mallory@evil.example>',          # the owner's address as a display name
    "owner@example.test.evil.example",            # a lookalike domain that holds the address
    f"mallory@evil.example, {OWNER}",             # two senders
    "Owner",                                       # a name alone
])
def test_a_mail_not_from_the_owners_own_address_answers_nothing(tmp_path, sender):
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    feed(root, [mail("m-x", sender, f"all yes\n\n> reference {ref}")])
    assert reply.run(root, now=NOW) == []
    assert answers(root) == []
    assert seen_rows(root)[-1]["rejected"] == "sender", "read once and recorded as refused"


def test_a_mail_to_someone_else_too_answers_nothing(tmp_path):
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    feed(root, [mail("m-cc", OWNER, f"all yes\n> reference {ref}", cc=["eve@evil.example"])])
    assert reply.run(root, now=NOW) == [] and answers(root) == []
    assert seen_rows(root)[-1]["rejected"] == "recipient"


def test_a_reply_without_the_briefings_reference_answers_nothing(tmp_path):
    """The From line is the exported field and can be forged; the reference is printed only in
    the mail the person sent themselves, and the repository keeps only its hash."""
    root = make_space(tmp_path / "ada")
    briefed(root)
    listing = (root / "_meta/state/morning/2026-10-12.json").read_text(encoding="utf-8")
    feed(root, [mail("m-1", OWNER, "all yes"),
                mail("m-2", OWNER, "all yes\n> reference abcdefghjkmn"),
                mail("m-3", OWNER, f"all yes\n> reference {json.loads(listing)['references'][0][:12]}")])
    assert reply.run(root, now=NOW) == [] and answers(root) == []
    assert [r["rejected"] for r in seen_rows(root)] == ["reference"] * 3


def test_the_owners_real_reply_is_still_read(tmp_path):
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    feed(root, [mail("m-ok", f"Ada Okafor <{OWNER.upper()}>", f"1 yes, 2 later\n\nOn Monday Ada wrote:\n> ref {ref}",
                     to=[f"Ada <{OWNER}>"], identifiers={"labels": ["SENT", "INBOX"]})])
    [written] = reply.run(root, now=NOW)
    fm, _ = pages.read(root / written)
    assert fm["verdicts"] == {"p-0000c000": "yes", "p-0000c001": "later"}


def test_a_reference_older_than_a_week_is_not_read(tmp_path):
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    late = mail("m-late", OWNER, f"1 yes\n> reference {ref}", at="2026-10-25T08:00:00Z")
    feed(root, [late])
    assert reply.run(root, now=NOW + dt.timedelta(days=13)) == []


def test_the_listing_keeps_only_a_hash_of_the_reference(tmp_path):
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    tracked = (root / "_meta/state/morning/2026-10-12.json").read_text(encoding="utf-8")
    assert ref not in tracked and morning_view.reference_hash(ref) in tracked


def _unsure_store(root, *records):
    path = root / unsure.STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


PRIVATE = {"id": "m-private-777", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z",
           "subject": "Your test results", "from": "results@clinic.example", "text": "Private."}


def test_guessed_unsure_codes_admit_nothing_even_from_the_owner(tmp_path):
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    _unsure_store(root, PRIVATE)
    [waiting] = unsure.waiting(root)
    real = unsure.code_of(waiting)
    guesses = "\n".join(f"{n:08x} yes" for n in range(1000)) + f"\n{real} yes"
    feed(root, [mail("m-brute", OWNER, f"{guesses}\n> reference {ref}")])
    reply.run(root, now=NOW)
    assert [r["id"] for r in unsure.waiting(root)] == ["m-private-777"], "one unknown code: none applied"
    assert "m-private-777" not in (root / "_meta/state/feed/mail.jsonl").read_text(encoding="utf-8")
    assert seen_rows(root)[-1]["unsure"].get("refused") == 1001


def test_an_unsure_code_is_random_not_the_hash_of_its_id(tmp_path):
    import hashlib
    root = make_space(tmp_path / "ada")
    _unsure_store(root, PRIVATE)
    [waiting] = unsure.waiting(root)
    code = unsure.code_of(waiting)
    assert len(code) == 8 and code != hashlib.sha1(PRIVATE["id"].encode()).hexdigest()[:8]
    tracked = (root / unsure.IDS).read_text(encoding="utf-8") if (root / unsure.IDS).exists() else ""
    assert code not in tracked, "the tracked ids file never holds a code"


def test_a_number_with_a_leading_zero_is_no_item_number():
    assert reply.parse("00001 yes\n007 no", 10) == {}
    assert reply.parse("1 yes", 10) == {1: ("yes", "")}


# ----- 13: the virtual clock never signs a real space ----------------------------------------
def test_the_virtual_clock_refuses_a_space_that_is_not_a_fixture(tmp_path):
    live = make_space(tmp_path / "ada", mode="live")
    with pytest.raises(ValueError, match="fixture"):
        chain.virtual([live], days=1, verdicts_after_day=1, verdicts={live: {"x": ("yes", "")}})
    assert not (live / "log.md").exists() or "yes x" not in (live / "log.md").read_text(encoding="utf-8")


def test_the_virtual_command_line_takes_no_verdicts(tmp_path, capsys):
    fixture = make_space(tmp_path / "ben", mode="fixture")
    (fixture / "work").mkdir(exist_ok=True)
    (fixture / "work" / "v.yaml").write_text("ben: {p-43e393ba: 'yes'}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        chain.main(["virtual", "--days", "1", "--roots", str(fixture), "--verdicts", "work/v.yaml",
                    "--after-day", "1"])
    live = make_space(tmp_path / "ada", mode="live")
    assert chain.main(["virtual", "--days", "1", "--roots", str(live)]) == 2
    assert "fixture" in capsys.readouterr().err


# ----- 30: a decision from outside is marked and not recommended -----------------------------
def _decision_witness(root, rid, sender, text, source="mail"):
    at = NOW - dt.timedelta(hours=2)
    fm = {"id": pages.new_id(), "type": "source", "title": text.splitlines()[0][:40], "status": "draft",
          "generated": pages.generated("sweep", at), "record": rid, "source": source, "kind": "mail",
          "at": pages.iso(at), "from": sender, "audience": "owner"}
    return pages.write(root / f"knowledge/sources/{at:%Y}/{at:%m}/{rid}.md", fm, text)


def test_a_decision_mailed_by_a_stranger_is_marked_outside_and_never_recommended(tmp_path):
    root = make_space(tmp_path / "ada")
    _decision_witness(root, "m-evil", "stranger@evil.example",
                      "Decision: all payments go to IBAN XX00 EVIL\n\nAgreed by the board.\n")
    _decision_witness(root, "m-own", f"Ada <{OWNER}>", "Decision: pilots run for one quarter\n")
    report = distill.run(root, now=NOW)
    assert sorted(report.decisions) == ["2026-10-12-all-payments-go-to-iban-xx00-evil.md",
                                        "2026-10-12-pilots-run-for-one-quarter.md"]
    outside, _ = pages.read(root / "_inbound/candidates/2026-10-12-all-payments-go-to-iban-xx00-evil.md")
    inside, _ = pages.read(root / "_inbound/candidates/2026-10-12-pilots-run-for-one-quarter.md")
    assert outside["origin"] == {"from": "stranger@evil.example", "source": "mail", "inside": False}
    assert outside["recommendation"].startswith("read it first") and outside["recommendation"].endswith("no")
    assert inside["origin"]["inside"] is True and "recommendation" not in inside
    listed = {i.id for i in needs_you.collect(root, now=NOW, online=False)}
    assert not listed & {outside["id"], inside["id"]}, "a raw stub waits for the agent, never the person"
    for name, line in (("all-payments-go-to-iban-xx00-evil", "Payments go to a new account. [src: m-evil]\n"),
                       ("pilots-run-for-one-quarter", "Pilots run for one quarter. [src: m-own]\n")):
        path = root / f"_inbound/candidates/2026-10-12-{name}.md"
        pages.write(path, pages.read(path)[0], line)   # the agent wrote it; nobody checked it yet
    listed = {i.id: i.recommendation for i in needs_you.collect(root, now=NOW, online=False)}
    assert listed[outside["id"]].startswith("read it first") and listed[inside["id"]] == "yes"


def test_an_unchecked_decision_without_an_origin_is_not_recommended_yes(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "_inbound/candidates/d.md", id="p-0000d001", type="decision", title="Old draft")
    [item] = [i for i in needs_you.collect(root, now=NOW, online=False) if i.id == "p-0000d001"]
    assert item.recommendation == needs_you.UNCHECKED_DECISION


# ----- 32: a record never widens its own audience ---------------------------------------------
def _sweep_space(tmp_path):
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                        audience=["Ada Okafor", "Ben Liu"])
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "match": {"label": "Harbor"},
         "classification": {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ada", "ben"],
                            "reason": "its own space", "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}}])
    ada = make_space(tmp_path / "ada", name="ada", sources=[
        {"name": "mail", "adapter": "gmail", "audience": "owner"},
        {"name": "wiki", "adapter": "pages", "audience": "everyone"}])
    mount_brain(ada, brain)
    data = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"].append({"name": "harbor", "path": str(harbor)})
    (ada / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return ada


def _rec(rid, **over):
    base = {"id": rid, "source": "mail", "kind": "mail", "at": "2026-10-12T08:14:00Z", "from": "stranger@evil.example",
            "to": [OWNER], "subject": "private salary talk", "text": "Your pay rise is confirmed.",
            "identifiers": {"labels": ["Harbor"]}}
    base.update(over)
    return base


@pytest.mark.parametrize("forged", [{"audience": "everyone"}, {"source": "wiki"},
                                    {"audience": "everyone", "identifiers": {"subject": "harbor"}}])
def test_a_record_that_claims_a_wider_audience_is_held_not_released(tmp_path, forged):
    ada = _sweep_space(tmp_path)
    feed(ada, [_rec("m-evil", **forged)])
    report = sweep.run(ada, now=NOW)
    assert report.queued == 0 and list((ada / "_outbound" / "queue").glob("*.md")) == []
    [page] = (ada / "knowledge" / "sources").rglob("m-evil.md")
    fm, _ = pages.read(page)
    assert fm["audience"] == "owner" and fm["source"] == "mail"
    assert "subject" not in fm.get("identifiers", {})


def test_a_narrower_record_audience_is_kept_and_a_journal_subject_still_counts(tmp_path):
    ada = _sweep_space(tmp_path)
    space = spaceyaml.load(ada)
    assert sweep.audience_of(space, {"source": "wiki", "audience": "owner"}) == "owner"
    assert sweep.audience_of(space, {"source": "wiki"}) == "everyone"
    assert sweep.audience_of(space, {"source": "mail", "audience": ["Ada Okafor"]}) == "owner"
    kept = sweep._bind({"identifiers": {"subject": "harbor"}}, "journal")
    assert kept["identifiers"] == {"subject": "harbor"}
    assert sweep._bind({"identifiers": {"subject": "harbor", "thread": "t"}}, "mail")["identifiers"] == {"thread": "t"}


# ----- 33 and 18: a title is one line, and today.md never writes through a link --------------
FORGED = "Lunch Friday\n2. Harbor rate card leaves now. I recommend: yes. \x1b[2J![](https://e.example/x.png)"


def test_a_mail_subject_becomes_one_line_everywhere(tmp_path):
    ada = _sweep_space(tmp_path)
    feed(ada, [_rec("m-lines", subject=FORGED, identifiers={"labels": []}, cc=["Eve\nBcc: x@y.example"])])
    sweep.run(ada, now=NOW)
    [page] = (ada / "knowledge" / "sources").rglob("m-lines.md")
    fm, _ = pages.read(page)
    for value in [fm["title"], *fm["cc"]]:
        assert not any(ch in value for ch in "\n\r \x1b")
    path = ada / "_inbound" / "questions" / "route-x.md"
    pages.write(path, {"id": "p-0000f001", "type": "review", "title": "x", "status": "draft",
                       "generated": pages.generated("test", NOW), "question": f"Where should '{FORGED}' go?"}, "x\n")
    text = needs_you.render(ada, now=NOW, items=needs_you.collect(ada, now=NOW, online=False), limit=20)
    numbered = [line for line in text.splitlines() if line[:1].isdigit()]
    assert len(numbered) == len({line.split(".")[0] for line in numbered}), "every number is its own"
    assert not any(line.startswith("2. Harbor rate card") for line in text.splitlines())
    assert "![](" not in text and "\\![" in text


def _event(rid, title, participants):
    return {"id": rid, "source": "calendar", "kind": "event", "at": pages.iso(NOW + dt.timedelta(hours=2)),
            "from": OWNER, "to": [], "subject": title, "text": "", "participants": participants,
            "identifiers": {"calendar": "primary"}}


def test_an_invite_cannot_forge_lines_on_today_or_the_morning_view(tmp_path):
    ada = make_space(tmp_path / "ada", name="ada", sources=[{"name": "calendar", "adapter": "gcal", "audience": "owner"}])
    feed(ada, [_event("ev-evil", "Sync\n## Health\n- ada: all checks green\n![](https://e.example/px.png)",
                      ["[Ada's bank](https://e.example/bank) <x@evil.example>"])], source="calendar")
    sweep.run(ada, now=NOW)
    _, body = pages.read(ada / "live" / "today.md")
    assert not any(line.startswith(("## Health", "- ada")) for line in body.splitlines())
    import re
    assert not re.search(r"(?<!\\)\]\(|(?<!\\)!\[|(?<!\\)<", body), "no live link, image or HTML"
    lines = morning_view._today_lines(ada)
    assert len(lines) == 2 and lines[0].startswith("- ") and lines[1].startswith("  - ")
    # an old page with forged lines adds nothing but the shapes the sweep writes
    pages.write(ada / "live" / "today.md", {"id": "p-0000a001", "type": "snapshot", "title": "Today",
                                             "status": "draft", "generated": pages.generated("t", NOW)},
                "# Today\n\n- 2026-10-12 11:00 Sync\n## Health\n- ada: all checks green\n")
    assert morning_view._today_lines(ada) == ["- 2026-10-12 11:00 Sync"]


def _link_folder_or_skip(link: Path, target: Path):
    """A folder link: a symbolic link where the system allows one, else a Windows junction
    (which needs no privilege); the tools refuse both."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError):
        pytest.skip("this system lets the test make no folder link")


def test_the_today_snapshot_never_writes_through_a_link(tmp_path):
    import shutil
    ada = make_space(tmp_path / "ada", name="ada", sources=[{"name": "calendar", "adapter": "gcal", "audience": "owner"}])
    outside = tmp_path / "ssh"
    outside.mkdir()
    (outside / "today.md").write_text("ssh-ed25519 KEEP\n", encoding="utf-8")
    shutil.rmtree(ada / "live", ignore_errors=True)
    _link_folder_or_skip(ada / "live", outside)
    feed(ada, [_event("ev-1", "Sync\nssh-ed25519 AAAAATTACKER evil@x", [])], source="calendar")
    sweep.run(ada, now=NOW)   # the snapshot is refused; the sweep itself goes on
    assert (outside / "today.md").read_text(encoding="utf-8") == "ssh-ed25519 KEEP\n"
    assert (ada / sweep.WATERMARK).exists()


def test_a_witness_page_under_a_linked_folder_is_skipped_and_the_run_goes_on(tmp_path):
    ada = make_space(tmp_path / "ada", name="ada", sources=[{"name": "mail", "adapter": "gmail", "audience": "owner"}])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ada / "knowledge" / "sources" / "2026").mkdir(parents=True, exist_ok=True)
    _link_folder_or_skip(ada / "knowledge" / "sources" / "2026" / "10", elsewhere)
    feed(ada, [_rec("m-1", identifiers={"labels": []})])
    report = sweep.run(ada, now=NOW)
    assert list(elsewhere.iterdir()) == [] and report.unwritable == 1
    assert any("unwritable page" in line for line in report.plan)
    assert (ada / sweep.WATERMARK).exists(), "the run still ends with its watermark"


# ----- 34: any character prints on a cp1252 pipe ---------------------------------------------
def test_needs_you_prints_cyrillic_and_emoji_on_a_cp1252_pipe(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "_inbound/questions/q-cyr.md", id="q-cyr", question="Route Счёт за ноябрь 🎉 to which subject?")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    for extra in ([], ["--json"]):
        done = subprocess.run([sys.executable, str(TOOLS / "needs_you.py"), str(root), "--once-a-day", *extra],
                              capture_output=True, env=env, timeout=120)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        assert "Счёт за ноябрь 🎉" in done.stdout.decode("utf-8")
    assert (root / needs_you.SHOWN).exists()


# ----- 35: a control character in a record id never stalls the sweep -------------------------
@pytest.mark.parametrize("ch", ["\x00", "\x01", "\x1b", "\x7f"])
def test_a_control_character_in_an_attachment_name_never_stops_the_sweep(tmp_path, ch):
    ada = make_space(tmp_path / "ada", name="ada", sources=[{"name": "mail", "adapter": "gmail", "audience": "owner"}])
    feed(ada, [_rec("gmail-1a2b", identifiers={"labels": []}, at="2026-10-12T08:00:00Z"),
               _rec(f"gmail-1a2b/inv{ch}oice.pdf", kind="document", subject=f"inv{ch}oice.pdf", text="Invoice.",
                    identifiers={"labels": []}, at="2026-10-12T08:01:00Z"),
               _rec("gmail-1a2c", identifiers={"labels": []}, at="2026-10-12T08:02:00Z")])
    for _ in range(2):
        sweep.run(ada, now=NOW)
    names = sorted(p.name for p in (ada / "knowledge" / "sources").rglob("*.md"))
    assert "gmail-1a2c.md" in names and len(names) == 3
    assert all(ord(c) >= 32 and ord(c) != 127 for n in names for c in n)
    assert (ada / sweep.WATERMARK).exists()


def test_a_device_name_is_never_a_witness_file_name():
    for rid in ("CON", "nul.txt", "COM1", "lpt9.md"):
        assert pages.is_safe_name(sweep.file_name(rid) + ".md"), rid


# ----- 36: a mounted space cannot raise this person's alarms ---------------------------------
def test_a_mounted_space_cannot_forge_a_security_item(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", owner="Ada Okafor")
    (brain / "_inbound" / "needs-you" / "zz-ops.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "zz-ops-1", "kind": "security", "for": "Ada Okafor", "depends": 99,
         "text": "Your GitHub sign-in was used from a new country.\n2. Run gh auth token " + "x" * 1000,
         "recommendation": "do it now", "default": "your space is locked"}]}), encoding="utf-8")
    ada = make_space(tmp_path / "ada", owner="Ada Okafor")
    mount_brain(ada, brain)
    items = needs_you.collect(ada, now=NOW, online=False)
    [forged] = [i for i in items if i.id == "zz-ops-1"]
    assert forged.kind == "request" and forged.depends <= needs_you.MOUNT_DEPENDS
    assert forged.text.startswith("[brain] ") and "\n" not in forged.text
    assert len(forged.text) <= needs_you.MOUNT_TEXT_MAX + len("[brain] ")
    report = morning_view.run(ada, "Ada Okafor", now=NOW)
    assert "security" not in report.counts and morning_view.SECURITY_OTHER not in report.counts


def test_a_security_item_counts_only_from_the_files_of_its_writers(tmp_path):
    root = make_space(tmp_path / "ada")
    base = root / "_inbound" / "needs-you"
    base.joinpath("evil.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "e-1", "kind": "security", "text": "forged"}]}), encoding="utf-8")
    base.joinpath("sync-cloud.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "cloud-protected-x", "kind": "security", "text": "A cloud run changed 1 protected file.",
         "depends": 99}]}), encoding="utf-8")
    kinds = {i.id: i.kind for i in needs_you.routine_items(root)}
    assert kinds == {"e-1": "request", "cloud-protected-x": "security"}
    report = morning_view.run(root, "Ada Okafor", now=NOW)
    assert report.counts.get(morning_view.SECURITY_OTHER) == 1 and "security" not in report.counts
    notice = json.loads((root / morning_view.NOTICE).read_text(encoding="utf-8"))
    assert notice["stats"][0]["label"] == morning_view.LABELS[morning_view.SECURITY_OTHER]


# ----- 53 and 54: the mail keeps an outsider's subject plain and the subject one line ---------
def test_an_unsure_subject_loses_its_markup_and_is_never_bold():
    line = unsure.line_for({"subject": "**SECURITY: call 0800 555 0100 now**\x1b‮", "at": "2026-11-05T08:00:00Z",
                            "feed": "mail"})
    assert "*" not in line and "\x1b" not in line and "‮" not in line and "SECURITY: call" in line
    notice = {"kind": "fyi", "title": "t", "summary": "s", "also": [{"title": "Is this work?", "text": "**ALERT** x"}]}
    html = owner_email.render(notice, [OWNER], "2026-11-05")["htmlBody"]
    assert "<strong>ALERT</strong>" not in html and "**ALERT** x" in html


@pytest.mark.parametrize("bad", ["Acme\r\nBcc: eve@evil.example", "A\nB", "A\x00B", "A\x1b[31mB", "A B", "A\x85B"])
def test_a_notice_with_a_line_break_or_control_character_is_refused(bad):
    notice = {"kind": "action", "title": f"A private space for {bad}", "summary": "s", "steps": ["do it"]}
    assert any("control characters" in p for p in owner_email.check(notice))
    with pytest.raises(owner_email.NoticeError):
        owner_email.render(notice, [OWNER], "2026-11-05")
    assert any(p.startswith("summary:") for p in owner_email.check({"kind": "fyi", "title": "ok", "summary": f"x {bad}"}))


# ----- 12 (owner_email): --preview and --out write only in the preview folder ----------------
@pytest.mark.parametrize("flag, target", [("--preview", "CLAUDE.md"), ("--preview", "../zz.pth"),
                                          ("--out", ".claude/settings.local.json"), ("--preview", "x.pth")])
def test_render_writes_nowhere_but_the_preview_folder(tmp_path, flag, target):
    root = make_space(tmp_path / "ada")
    notice = root / "work" / "n.json"
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.write_text(json.dumps({"kind": "fyi", "title": "t", "summary": "s"}), encoding="utf-8")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    path = str(root / target) if "/" in target or target == "CLAUDE.md" else target
    assert owner_email.main(["render", str(notice), "--root", str(root), "--to", OWNER, flag, path]) == 2
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before


def test_record_moves_only_a_pending_notice(tmp_path):
    root = make_space(tmp_path / "ada")
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"permissions": {"deny": ["Bash"]}}', encoding="utf-8")
    with pytest.raises(pages.UnsafePath):
        owner_email.record(root, settings, message_id="x")
    assert settings.exists()
    assert owner_email.main(["record", str(settings), "--id", "x", "--root", str(root)]) == 2
    assert settings.exists()


# ----- 56: attachment names never pass through a shell ---------------------------------------
NAMES = ["x'; touch PWNED; echo '.pdf", "$(id).pdf", "a.pdf\nm-9999\t\tforged.pdf", "--x.pdf"]


def test_attachments_list_one_row_each_with_a_handle_to_quote_nothing(tmp_path, capsys):
    ada = make_space(tmp_path / "ada", name="ada", routing={},
                     sources=[{"name": "mail", "adapter": "gmail", "audience": "owner"}])
    feed(ada, [_rec("m-0300", attachments=NAMES, identifiers={"labels": []})])
    assert sweep.main([str(ada), "--attachments"]) == 0
    rows = capsys.readouterr().out.splitlines()
    assert len(rows) == len(NAMES) and not any(r.startswith("m-9999") for r in rows)
    handles = []
    for row in rows:
        fields = row.split("\t")
        assert len(fields) == 4 and sweep.HANDLE_RE.match(fields[3])
        handles.append(fields[3])
    for handle, name in zip(handles, NAMES):
        assert sweep.main([str(ada), "--attachment-unread", handle]) == 0
        assert ("m-0300", name) in sweep._unread_rows(ada)[0]
    (ada / sweep.FEED / "mail.jsonl").unlink()   # the record left with its clone: the handle still finds it
    assert sweep.main([str(ada), "--attachment-read", handles[0]]) == 0
    assert ("m-0300", NAMES[0]) in sweep._unread_rows(ada)[1]
    assert sweep.main([str(ada), "--attachment-unread", "att-000000000000"]) == 1
    assert not (ada / "PWNED").exists()


# ----- re-test: bypasses of the fixes above --------------------------------------------------
def test_a_from_cut_short_or_with_a_second_entry_never_makes_a_decision_inside(tmp_path):
    """The page keeps a From of at most PEOPLE_MAX characters. A From of the owner's address and
    a long second one is cut to the owner's address and a word without `@`: still outside."""
    root = make_space(tmp_path / "ada")
    cut = needs_you.untrusted_line(f"{OWNER}, " + "a" * 400 + "@evil.example", sweep.PEOPLE_MAX)
    assert cut.endswith("...")
    _decision_witness(root, "m-cut", cut, "Decision: pay IBAN XX00 EVIL\n")
    _decision_witness(root, "m-two", f"{OWNER}, not-an-address", "Decision: pay IBAN XX11 EVIL\n")
    distill.run(root, now=NOW)
    for name in ("2026-10-12-pay-iban-xx00-evil.md", "2026-10-12-pay-iban-xx11-evil.md"):
        fm, _ = pages.read(root / "_inbound" / "candidates" / name)
        assert fm["origin"]["inside"] is False and fm["recommendation"].startswith("read it first"), name


def test_only_people_registered_here_or_in_the_brain_make_a_decision_inside(tmp_path):
    """A project or channel mount is written by whoever can push to it: a person it registers
    never turns a stranger's address into a colleague's. The brain's people still count."""
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "ben", "kind": "people", "title": "Ben Liu", "email": "ben@example.test"}])
    project = make_space(tmp_path / "harbor", profile="project", name="harbor", subjects=[
        {"id": "mallory", "kind": "people", "title": "Mallory", "email": "mallory@evil.example"}])
    root = make_space(tmp_path / "ada")
    mount_brain(root, brain)
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"].append({"name": "harbor", "path": str(project)})
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _decision_witness(root, "m-ben", "Ben Liu <ben@example.test>", "Decision: pilots run for a quarter\n")
    _decision_witness(root, "m-mal", "mallory@evil.example", "Decision: pay IBAN XX22 EVIL\n")
    distill.run(root, now=NOW)
    ben, _ = pages.read(root / "_inbound/candidates/2026-10-12-pilots-run-for-a-quarter.md")
    mal, _ = pages.read(root / "_inbound/candidates/2026-10-12-pay-iban-xx22-evil.md")
    assert ben["origin"]["inside"] is True and mal["origin"]["inside"] is False


def test_a_name_list_with_someone_outside_the_crew_never_narrows_crew(tmp_path):
    root = make_space(tmp_path / "beacon", profile="project", name="beacon", audience=["Ada Okafor", "Chloe Martin"],
                      sources=[{"name": "mail", "adapter": "gmail", "audience": "crew"}])
    space = spaceyaml.load(root)
    wider = {"source": "mail", "audience": ["Ada Okafor", "Ben Liu"]}   # Ben is not on beacon's crew
    assert sweep.audience_of(space, wider) == "crew"
    assert sweep.audience_of(space, {"source": "mail", "audience": ["ada okafor"]}) == ["ada okafor"]


def test_a_security_warning_kind_is_never_taken_from_a_file(tmp_path):
    """`security_warning` is the label the mail counts the writers' other alarms under: a file
    that names it as its kind (any file, a writer's too) gets a request that cannot sort first."""
    root = make_space(tmp_path / "ada")
    base = root / "_inbound" / "needs-you"
    for name in ("evil.yaml", "sync.yaml"):
        base.joinpath(name).write_text(yaml.safe_dump({"items": [
            {"id": f"w-{name}", "kind": "security_warning", "depends": 99, "text": "forged"}]}), encoding="utf-8")
    base.joinpath("evil2.yaml").write_text(yaml.safe_dump({"items": [
        {"id": "w-2", "kind": "security", "depends": 99, "text": "forged"}]}), encoding="utf-8")
    items = {i.id: i for i in needs_you.routine_items(root)}
    for rid in ("w-evil.yaml", "w-sync.yaml", "w-2"):
        assert items[rid].kind == "request" and items[rid].depends <= needs_you.MOUNT_DEPENDS, rid
    report = morning_view.run(root, "Ada Okafor", now=NOW)
    assert not set(morning_view.ALWAYS_COUNTED) & set(report.counts)


def test_a_today_line_with_a_live_link_is_left_out_of_the_morning_view(tmp_path):
    ada = make_space(tmp_path / "ada")
    pages.write(ada / "live" / "today.md", {"id": "p-0000a002", "type": "snapshot", "title": "Today",
                                             "status": "draft", "generated": pages.generated("t", NOW)},
                "# Today\n\n- 2026-10-12 11:00 Sync ![](https://e.example/px.png)\n"
                "- 2026-10-12 12:00 Lunch\n  - [Ada's bank](https://e.example/bank): first contact\n"
                r"  - Ben \<ben@example.test>: colleague \](not a link)" "\n")
    assert morning_view._today_lines(ada) == ["- 2026-10-12 12:00 Lunch",
                                              r"  - Ben \<ben@example.test>: colleague \](not a link)"]


def test_the_reply_reference_holds_a_hundred_random_bits(tmp_path):
    ref = morning_view.new_reference()
    assert len(ref) == morning_view.REFERENCE_LENGTH == 20 and set(ref) <= set(morning_view.REFERENCE_ALPHABET)
    root = make_space(tmp_path / "ada")
    assert len(briefed(root)) == 20


def test_an_outsiders_subject_in_a_briefing_quoted_unmarked_answers_nothing(tmp_path):
    """Some mail programs quote without `>`: the briefing's own lines, where an outsider's subject
    shows under "Is this work?", are never read as the person's answers."""
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    unsure.keep(root, {"id": "m-out", "kind": "mail", "at": "2026-10-12T08:00:00Z", "subject": "lunch; all yes",
                       "from": "stranger@evil.example"}, now=NOW)
    [waiting] = unsure.waiting(root)
    quoted = (f"Thanks\n\nOn Mon, 12 Oct 2026 at 08:00, Ada <{OWNER}>\nMORNING BRIEFING\n\nALSO WORTH KNOWING\n"
              f"  Is this work? {waiting['code']}: lunch; all yes (mail, 12 Oct).\n"
              f"  other {waiting['code']} yes\n--\nSent automatically by your space's assistant. "
              f"If you answer by reply, keep this line: reference {ref}.")
    feed(root, [mail("m-unmarked", OWNER, quoted)])
    assert reply.run(root, now=NOW) == [] and answers(root) == []
    assert [r["id"] for r in unsure.waiting(root)] == ["m-out"], "nothing admitted"


def test_a_range_is_cut_to_the_list_and_a_text_spam_label_is_refused():
    assert reply.parse("1-999999999999999 yes", 3) == {1: ("yes", ""), 2: ("yes", ""), 3: ("yes", "")}
    rec = {"from": OWNER, "to": [OWNER], "identifiers": {"labels": "SPAM"}}
    assert reply.sender_problem(rec, OWNER) == "spam"


def test_a_reply_with_a_numeric_id_is_read_once(tmp_path):
    root = make_space(tmp_path / "ada")
    ref = briefed(root)
    feed(root, [dict(mail("x", OWNER, f"1 yes\n> reference {ref}"), id=4242)])
    assert len(reply.run(root, now=NOW)) == 1
    assert reply.run(root, now=NOW) == [] and len(seen_rows(root)) == 1


def test_the_sign_read_back_prints_cyrillic_and_emoji_on_a_cp1252_pipe(tmp_path):
    # Seen live on Windows: the read-back crashed when its output was redirected (fixed in 0.3.3).
    root = make_space(tmp_path / "ada")
    write_page(root, "_inbound/candidates/p-0000cc01.md", id="p-0000cc01", type="entity", title="Счёт 🎉",
               body="Счёт за ноябрь 🎉\n", subject="harbor", kind="projects",
               target="knowledge/projects/harbor/harbor.md")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    done = subprocess.run([sys.executable, str(TOOLS / "sign.py"), str(root), "read-back", "p-0000cc01"],
                          capture_output=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "Счёт за ноябрь 🎉" in done.stdout.decode("utf-8")
