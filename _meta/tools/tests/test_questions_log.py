import datetime as dt
import json

import questions_log
from conftest import NOW, make_space


def test_append_then_lookup_finds_a_near_duplicate_in_the_same_subject(tmp_path):
    root = make_space(tmp_path / "ada")
    entry = questions_log.append(root, "When does Harbor go live?", "harbor", "evidence",
                                 ["knowledge/sources/2026/10/m-1.md"], now=NOW)
    questions_log.append(root, "What does the prospect want?", "sales", "evidence", [], now=NOW)
    assert entry == {"at": "2026-10-12T09:00:00Z", "question": "When does Harbor go live?",
                     "subject": "harbor", "tier": "evidence",
                     "pages": ["knowledge/sources/2026/10/m-1.md"],
                     "needed_steering": False, "answered_by_request": False}
    hits = questions_log.lookup(root, "harbor", "When will the Harbor project go live?")
    assert [h["question"] for h in hits] == ["When does Harbor go live?"]
    assert hits[0]["pages"] == ["knowledge/sources/2026/10/m-1.md"]
    assert questions_log.lookup(root, "harbor", "Who owns the Beacon budget?") == []
    assert questions_log.lookup(root, "sales", "When does Harbor go live?") == []
    assert questions_log.lookup(root, None, "When does Harbor go live?")[0]["subject"] == "harbor"
    lines = (root / "_meta" / "questions.log").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#") and len(lines) == 3
    assert json.loads(lines[1])["question"] == "When does Harbor go live?"


def test_overlap_is_on_words_of_three_letters_or_more():
    assert questions_log.tokens("Is it on? Go to the Harbor kick-off") == {"the", "harbor", "kick", "off"}
    assert questions_log.overlap("When does Harbor go live?", "when will harbor go LIVE") == 0.75
    assert questions_log.overlap("", "anything") == 0.0


def test_unanswered_lists_questions_asked_three_times_from_evidence_only(tmp_path):
    root = make_space(tmp_path / "ada")
    for n, text in enumerate(["When does Harbor go live?", "When will Harbor go live?",
                              "Harbor go-live: when?"]):
        questions_log.append(root, text, "harbor", "evidence", ["m-1"],
                             now=NOW + dt.timedelta(hours=n))
    for n in range(2):
        questions_log.append(root, "Who runs Beacon?", "beacon", "evidence", [], now=NOW)
    for tier in ("evidence", "evidence", "evidence", "signed"):
        questions_log.append(root, "What is the pilot length?", "beacon", tier, [], now=NOW)
    assert questions_log.unanswered(root) == ["Harbor go-live: when?"]
    assert questions_log.unanswered(root, times=2) == ["Harbor go-live: when?", "Who runs Beacon?"]


def test_cli_append_and_lookup(tmp_path, monkeypatch, capsys):
    root = make_space(tmp_path / "ada")
    monkeypatch.setenv("SPACE_NOW", "2026-10-12T09:00:00Z")
    assert questions_log.main([str(root), "append", "--question", "When does Harbor go live?",
                               "--subject", "harbor", "--tier", "evidence", "--page", "m-1.md",
                               "--steering"]) == 0
    capsys.readouterr()
    assert questions_log.main([str(root), "lookup", "--subject", "harbor", "--question",
                               "when does harbor go live"]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits[0]["needed_steering"] is True and hits[0]["at"] == "2026-10-12T09:00:00Z"
    assert questions_log.main([str(root), "lookup", "--question", "nothing like it"]) == 1
