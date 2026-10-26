import datetime as dt
import json
import pytest
import sign_questions as sq
from conftest import NOW, make_space
from test_distill import witness

LEDGER = "_meta/state/sign-questions.jsonl"

def ask(root, text="Has Harbor gone live?", page="p-00000101", **kw):
    args = dict(subject="harbor", page=page, text=text, why="the page cannot say where the launch stands",
                recommendation="add: Harbor went live on 3 November", searched=["mail, Harbor, 1-20 Oct: nothing"],
                by="routine:distill", now=NOW)
    args.update(kw)
    return sq.ask(root, **args)

def events(root):
    return [json.loads(l)["event"] for l in (root / LEDGER).read_text(encoding="utf-8").splitlines()]

def test_ask_writes_one_event_and_lists_the_question_open(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    assert q.id.startswith("q-") and len(q.id) == 10 and q.status == "open"
    assert events(root) == ["asked"]
    assert [x.id for x in sq.for_subject(root, "harbor")] == [q.id]

def test_the_same_question_is_never_asked_twice(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    sq.answer(root, q.id, by="Ada Okafor", text="yes, on 3 November", now=NOW)
    again = ask(root, text="  has HARBOR  gone live? ")
    assert again.id == q.id and again.status == "answered" and events(root) == ["asked", "answered"]

def test_a_reworded_question_keeps_its_id_under_both_texts(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    sq.reword(root, q.id, text="Did Harbor go live on 3 November?", by="routine:distill", now=NOW)
    assert ask(root).id == q.id and ask(root, text="Did Harbor go live on 3 November?").id == q.id
    assert sq.load(root)[q.id].text == "Did Harbor go live on 3 November?"
    assert events(root) == ["asked", "reworded"]

def test_only_a_person_answers(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    for by in ("routine:distill", "", "  "):
        with pytest.raises(sq.QuestionError):
            sq.answer(root, q.id, by=by, text="yes", now=NOW)
    assert events(root) == ["asked"]

def test_a_sensitive_answer_keeps_only_where_it_is_kept(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root, text="Beacon's terms: recorded?", sensitive=True)
    with pytest.raises(sq.QuestionError):
        sq.answer(root, q.id, by="Ada Okafor", text="EUR 2,000 a month", now=NOW)
    a = sq.answer(root, q.id, by="Ada Okafor", kept_in="ada: work/clients/beacon/commercial.md", now=NOW)
    assert a.status == "answered" and a.answer == "" and a.kept_in == "ada: work/clients/beacon/commercial.md"
    assert "EUR" not in (root / LEDGER).read_text(encoding="utf-8")

def test_a_plain_answer_needs_its_text(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    with pytest.raises(sq.QuestionError):
        sq.answer(root, q.id, by="Ada Okafor", now=NOW)

def test_at_most_four_open_questions_per_subject(tmp_path):   # R8
    root = make_space(tmp_path / "ada")
    first = [ask(root, text=f"Question {n}?") for n in range(4)]
    with pytest.raises(sq.QuestionError, match="harbor"):
        ask(root, text="Question 5?")
    sq.park(root, first[0].id, by="Ada Okafor", now=NOW)
    assert ask(root, text="Question 5?").status == "open"

def test_transitions(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    sq.park(root, q.id, by="Ada Okafor", now=NOW)
    s = sq.settle(root, q.id, source="m-7", by="routine:distill", now=NOW)
    assert s.status == "settled" and s.source == "m-7"
    assert sq.reopen(root, q.id, reason="m-7 does not say it", by="routine:check", now=NOW).status == "open"
    with pytest.raises(sq.QuestionError):
        sq.reopen(root, q.id, reason="again", by="routine:check", now=NOW)
    sq.drop(root, q.id, reason="the page no longer says it", by="routine:distill", now=NOW)
    with pytest.raises(sq.QuestionError):
        sq.answer(root, q.id, by="Ada Okafor", text="yes", now=NOW)

def test_states_filter_the_lists(tmp_path):
    root = make_space(tmp_path / "ada")
    a, b = ask(root, text="One?"), ask(root, text="Two?")
    sq.park(root, b.id, by="Ada Okafor", now=NOW)
    assert [q.id for q in sq.for_subject(root, "harbor")] == [a.id]
    assert {q.id for q in sq.for_subject(root, "harbor", states=sq.STATES)} == {a.id, b.id}
    assert [q.id for q in sq.for_page(root, "p-00000101", states=("parked",))] == [b.id]

def test_checked_is_recorded_per_subject(tmp_path):
    root = make_space(tmp_path / "ada")
    assert sq.last_checked(root, "harbor") is None
    sq.mark_checked(root, "harbor", by="routine:distill", now=NOW)
    assert sq.last_checked(root, "harbor") == NOW and sq.last_checked(root, "beacon") is None

def test_a_subject_needs_a_recheck_until_checked_and_again_after_new_records(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor",
                                                    "state": "active", "owner": "Ada Okafor"}])
    witness(root, "m-1", "harbor", "Harbor kickoff.\n", NOW - dt.timedelta(days=2))
    assert sq.needs_recheck(root, "harbor") is True
    sq.mark_checked(root, "harbor", by="routine:distill", now=NOW)
    assert sq.needs_recheck(root, "harbor") is False
    witness(root, "m-2", "harbor", "Harbor went live.\n", NOW + dt.timedelta(hours=1))
    assert sq.needs_recheck(root, "harbor") is True

def test_only_records_of_the_subject_itself_call_for_a_recheck(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor",
                                                    "state": "active", "owner": "Ada Okafor"}])
    witness(root, "m-1", "harbor", "Harbor kickoff.\n", NOW - dt.timedelta(days=2))
    sq.mark_checked(root, "harbor", by="routine:distill", now=NOW)
    witness(root, "m-2", "beacon", "Beacon went live.\n", NOW + dt.timedelta(hours=1))
    witness(root, "m-3", "", "A note that names no subject.\n", NOW + dt.timedelta(hours=1))
    assert sq.needs_recheck(root, "harbor") is False

def test_duplicated_and_reordered_lines_fold_to_the_same_state(tmp_path):   # Review Focus 3
    root = make_space(tmp_path / "ada")
    q = ask(root)
    sq.answer(root, q.id, by="Ada Okafor", text="yes", now=NOW)
    path = root / LEDGER
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines + lines + [lines[0]]) + "\n", encoding="utf-8")
    assert sq.load(root)[q.id].status == "answered"

def test_a_half_written_line_is_skipped(tmp_path):   # Review Focus 4
    root = make_space(tmp_path / "ada")
    q = ask(root)
    with (root / LEDGER).open("a", encoding="utf-8") as handle:
        handle.write('{"event": "ask')
    assert sq.load(root)[q.id].status == "open"

def test_cli_lists_open_questions_as_json(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    assert sq.main([str(root), "list", "harbor", "--json"]) == 0
    assert [row["id"] for row in json.loads(capsys.readouterr().out)] == [q.id]


# ----- beyond the brief: the rulings and the pinned choices it leaves untested ------------------
def test_lines_fold_in_order_of_their_time_not_their_place_in_the_file(tmp_path):   # R1
    root = make_space(tmp_path / "ada")
    q = ask(root)
    sq.answer(root, q.id, by="Ada Okafor", text="yes", now=NOW + dt.timedelta(minutes=1))
    path = root / LEDGER
    path.write_text("\n".join(reversed(path.read_text(encoding="utf-8").splitlines())) + "\n", encoding="utf-8")
    assert sq.load(root)[q.id].status == "answered"

def test_the_cap_follows_the_subject_across_pages(tmp_path):   # R8
    root = make_space(tmp_path / "ada")
    for n in range(4):
        ask(root, text=f"Question {n}?", page=f"p-0000010{n}")
    with pytest.raises(sq.QuestionError, match="harbor"):
        ask(root, text="Question 5?", page="p-00000104")
    assert len(sq.for_subject(root, "harbor")) == 4
    assert ask(root, subject="beacon", text="Question 5?", page="p-00000104").status == "open"

def test_an_answer_in_the_same_second_as_its_question_never_folds_before_it(tmp_path):   # Review Focus 3
    root = make_space(tmp_path / "ada")
    q = ask(root)
    sq.answer(root, q.id, by="Ada Okafor", text="yes", now=NOW)
    path = root / LEDGER
    path.write_text("\n".join(reversed(path.read_text(encoding="utf-8").splitlines())) + "\n", encoding="utf-8")
    assert events(root) == ["answered", "asked"]
    folded = sq.load(root)[q.id]
    assert folded.status == "answered" and folded.answer == "yes"

def test_a_later_asked_line_never_reopens_an_answered_question(tmp_path):   # Review Focus 3, R1
    root = make_space(tmp_path / "ada")
    q = ask(root)
    sq.answer(root, q.id, by="Ada Okafor", text="yes", now=NOW)
    stray = {"at": "2026-10-13T09:00:00Z", "event": "asked", "id": q.id, "by": "routine:distill",
             "subject": "harbor", "page": "p-00000101", "text": q.text, "why": "", "recommendation": "",
             "searched": [], "sensitive": False}
    with (root / LEDGER).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(stray) + "\n")
    folded = sq.load(root)[q.id]
    assert folded.status == "answered" and folded.answer == "yes"

def test_a_write_after_a_half_written_line_still_lands(tmp_path):   # Review Focus 4
    root = make_space(tmp_path / "ada")
    first = ask(root)
    with (root / LEDGER).open("a", encoding="utf-8") as handle:
        handle.write('{"event": "ask')
    second = ask(root, text="Is the contract signed?")
    assert set(sq.load(root)) == {first.id, second.id}

def test_a_write_dated_before_the_last_change_is_refused(tmp_path):
    root = make_space(tmp_path / "ada")
    q = ask(root)
    with pytest.raises(sq.QuestionError):
        sq.park(root, q.id, by="Ada Okafor", now=NOW - dt.timedelta(minutes=1))
    assert events(root) == ["asked"]

def test_every_line_names_who_wrote_it_and_unknown_questions_are_refused(tmp_path):   # R6
    root = make_space(tmp_path / "ada")
    with pytest.raises(sq.QuestionError):
        ask(root, by=" ")
    with pytest.raises(sq.QuestionError):
        sq.mark_checked(root, "harbor", by="", now=NOW)
    with pytest.raises(sq.QuestionError):
        sq.park(root, "q-00000000", by="Ada Okafor", now=NOW)
    assert not (root / LEDGER).exists()
    q = ask(root)
    with pytest.raises(sq.QuestionError):
        sq.park(root, q.id, by="", now=NOW)
    assert events(root) == ["asked"]

def test_cli_writes_use_the_space_clock_and_errors_exit_one(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SPACE_NOW", "2026-10-13T09:00:00Z")
    root = make_space(tmp_path / "ada")
    q = ask(root)
    assert sq.main([str(root), "answer", q.id, "--text", "yes", "--by", "routine:distill"]) == 1
    assert capsys.readouterr().err.startswith("error:")
    assert sq.main([str(root), "answer", q.id, "--text", "yes", "--by", "Ada Okafor"]) == 0
    folded = sq.load(root)[q.id]
    assert folded.status == "answered" and folded.updated_at == "2026-10-13T09:00:00Z"
