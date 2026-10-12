import json
import re

import pytest

import owner_email as oe
from conftest import make_space


def security():
    return {
        "kind": "security", "sender": "sweep",
        "title": "Change one leaked form secret (booking form to the scheduler)",
        "summary": "A handover document says the secret that protects the **booking form hook** "
                   "was saved inside an old automation run. Nobody has changed it since.",
        "steps": ["Open the saved run the document names and delete it if it still exists.",
                  "Create a new signing secret for the form and paste it into the scheduler.",
                  "Fill in the two empty secret settings the document lists."],
        "link": {"label": "Open the handover document", "url": "https://example.test/doc"},
        "reassure": "This is a precaution. There is no sign anyone misused the secret, and I did "
                    "not open or copy it.",
        "also": [{"title": "Not urgent:", "text": "every automation for this client uses a login "
                  "stored in one person's account. Move it to the shared one."}],
    }


def briefing():
    return {
        "kind": "briefing", "sender": "morning-view", "title": "12 things waiting for you",
        "summary": "Here is what needs you today. Full details are in your morning view.",
        "stats": [
            {"count": 1, "label": "update about to be shared with the company",
             "hint": "It leaves automatically within a day. To stop it, say no."},
            {"count": 8, "label": "notes waiting for your sign-off", "hint": "Answer with the number."},
            {"count": 1, "label": "question only you can answer"},
            {"count": 2, "label": "drafts older than two weeks, not sent yet"},
        ],
        "also": [{"title": "Needs a look:", "text": "a scheduled job did not finish normally."}],
    }


def test_examples_pass_the_check():
    assert oe.check(security()) == []
    assert oe.check(briefing()) == []


@pytest.mark.parametrize("text, needle", [
    ("Three candidates are waiting.", "jargon"),
    ("The brain has a new page.", "jargon"),
    ("See _inbound/questions/x.md for more.", "file paths"),
    ("The file notes.md explains it.", "file paths"),
    ("Rotate it — soon.", "dashes"),
    ("Open https://example.test now.", "link field"),
    ("The key is sk-abcdefghijklmnop.", "secrets"),
])
def test_rejects_unclear_or_unsafe_text(text, needle):
    notice = security()
    notice["summary"] = text
    problems = oe.check(notice)
    assert any(needle in p for p in problems), problems


def test_allow_terms_overrides_one_jargon_word():
    notice = security()
    notice["summary"] = "The Brain dashboard vendor flagged a login."
    assert oe.check(notice)
    notice["allow_terms"] = ["brain"]
    assert oe.check(notice) == []


def test_limits_and_required_fields():
    notice = security()
    notice["steps"] = ["Do it."] * 6
    notice["title"] = "x" * 80
    problems = oe.check(notice)
    assert any("at most 5 steps" in p for p in problems)
    assert any("title is 80" in p for p in problems)
    assert any("steps" in p for p in oe.check({"kind": "action", "title": "t", "summary": "s"}))
    assert any("kind" in p for p in oe.check({"kind": "shout", "title": "t", "summary": "s"}))


def test_all_quiet_briefing_needs_a_reassure_or_also_line():
    notice = {"kind": "briefing", "title": "All quiet today", "summary": "Nothing needs you today.",
              "also": [{"title": "Needs a look:", "text": "a scheduled job did not finish normally."}]}
    assert oe.check(notice) == []
    assert oe.check({"kind": "briefing", "title": "All quiet today", "summary": "Nothing."})


def test_render_fills_every_placeholder_and_escapes():
    notice = security()
    notice["summary"] = "A <script> tag & **bold** word @@blocks@@."
    payload = oe.render(notice, ["owner@example.test"], today="2026-10-12")
    assert set(re.findall(r"@@(\w+)@@", payload["htmlBody"])) == {"blocks"}
    assert payload["htmlBody"].count("What to do") == 1
    assert "&lt;script&gt;" in payload["htmlBody"]
    assert "<strong>bold</strong>" in payload["htmlBody"]
    assert payload["subject"] == "Security check: Change one leaked form secret (booking form to the scheduler)"
    assert payload["to"] == ["owner@example.test"]
    assert "**" not in payload["body"]
    assert "1. Open the saved run" in payload["body"]
    assert "2026-10-12" in payload["body"]


def test_briefing_renders_counts():
    payload = oe.render(briefing(), ["owner@example.test"], today="2026-10-12")
    assert payload["subject"] == "Morning briefing: 12 things waiting for you"
    assert "notes waiting for your sign-off" in payload["htmlBody"]
    assert "8  notes waiting" in payload["body"]


def test_render_refuses_a_notice_with_a_path_in_it():
    notice = briefing()
    notice["summary"] = "Open _inbound/candidates/harbor.md and sign it."
    with pytest.raises(oe.NoticeError) as info:
        oe.render(notice, ["owner@example.test"])
    assert any("file paths" in p for p in info.value.problems)


def test_recipient_comes_from_space_yaml(tmp_path):
    root = make_space(tmp_path / "ada")
    assert oe.recipient(root) == ["owner@example.test"]
    payload = oe.render(briefing(), oe.recipient(root), today="2026-10-12")
    assert payload["to"] == ["owner@example.test"]
    blank = make_space(tmp_path / "blank", owner_email="")
    with pytest.raises(SystemExit):
        oe.recipient(blank)
    nested = root / "knowledge" / "projects"
    nested.mkdir(parents=True, exist_ok=True)
    assert oe.recipient(nested) == ["owner@example.test"]


def test_cli_render_writes_payload_and_preview(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    notice = tmp_path / "notice.json"
    notice.write_text(json.dumps(briefing()), encoding="utf-8")
    out, page = tmp_path / "p.json", tmp_path / "p.html"
    code = oe.main(["render", str(notice), "--root", str(root), "--out", str(out),
                    "--preview", str(page), "--date", "2026-10-12"])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload) == {"to", "subject", "htmlBody", "body"}
    assert payload["to"] == ["owner@example.test"]
    assert page.read_text(encoding="utf-8") == payload["htmlBody"]
    capsys.readouterr()
    code = oe.main(["render", str(notice), "--to", "ben@example.test", "--date", "2026-10-12"])
    assert code == 0 and json.loads(capsys.readouterr().out)["to"] == ["ben@example.test"]


def test_cli_check_exit_code(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"kind": "fyi", "title": "Sweep update", "summary": "x"}), encoding="utf-8")
    assert oe.main(["check", str(bad)]) == 2
    good = tmp_path / "good.json"
    good.write_text(json.dumps(briefing()), encoding="utf-8")
    assert oe.main(["check", str(good)]) == 0
    capsys.readouterr()
