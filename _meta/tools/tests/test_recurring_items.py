"""An answered item that comes back is shown again. Needs you hides every id that has an answer, and
the routines' own items had one fixed id each: after one yes to "sign in again", a second loss of
the sign-in was never shown. An item raised again after it was away, or still true after the person
said it was done, gets an id of its own; one that only persists unanswered keeps the id it has."""
import yaml

import needs_you
import pages
import routine
import status
from conftest import NOW, make_space


def shown(root) -> list:
    return [i.id for i in needs_you.collect(root, now=NOW, online=False) if i.id.startswith("routine-")]


def kept(root) -> list:
    return [i["id"] for i in yaml.safe_load((root / routine.ITEMS).read_text(encoding="utf-8"))["items"]]


def yes(root, item_id: str, verb: str = "yes") -> None:
    status.record_verdict(root, item_id, "Ada Okafor", verb=verb, at=pages.iso(NOW))


def test_an_id_with_an_answer_gets_a_new_one_when_its_item_is_raised_again(tmp_path):
    root = make_space(tmp_path / "ada")
    assert needs_you.recurring_id(root, "routine-claude-signin", NOW) == "routine-claude-signin"
    yes(root, "routine-claude-signin")
    again = needs_you.recurring_id(root, "routine-claude-signin", NOW)
    assert again == "routine-claude-signin-20261012T0900"
    assert needs_you.same_item(again, "routine-claude-signin") and needs_you.same_item("routine-claude-signin", "routine-claude-signin")
    assert not needs_you.same_item("routine-claude-signin-in-two-steps", "routine-claude-signin")
    assert not needs_you.same_item("routine-reconnect-mail", "routine-claude-signin")


def test_a_sign_in_lost_again_after_the_person_answered_is_asked_again(tmp_path):
    ws = make_space(tmp_path / "ada")
    routine.write_items(ws, claude_signin=True)
    assert shown(ws) == [routine.SIGNIN_ID]
    before = (ws / routine.ITEMS).read_bytes()
    routine.write_items(ws, claude_signin=True)   # the next run, still signed out: the same item, not written again
    assert (ws / routine.ITEMS).read_bytes() == before
    yes(ws, routine.SIGNIN_ID)
    assert shown(ws) == []
    routine.write_items(ws, claude_signin=False)   # a run found the sign-in back
    assert kept(ws) == []
    routine.write_items(ws, claude_signin=True)    # weeks later it is lost again
    (again,) = shown(ws)
    assert again != routine.SIGNIN_ID and needs_you.same_item(again, routine.SIGNIN_ID)
    routine.write_items(ws, claude_signin=False)
    assert kept(ws) == [] and shown(ws) == []


def test_a_yes_that_did_not_bring_the_sign_in_back_is_asked_again(tmp_path):
    ws = make_space(tmp_path / "ada")
    routine.write_items(ws, claude_signin=True)
    yes(ws, routine.SIGNIN_ID)
    routine.write_items(ws, claude_signin=True)   # the next run still finds Claude signed out
    (again,) = shown(ws)
    assert again != routine.SIGNIN_ID and kept(ws) == [again]   # one item, not a pile
    before = (ws / routine.ITEMS).read_bytes()
    routine.write_items(ws, claude_signin=True)   # unanswered, it stays as it is
    assert (ws / routine.ITEMS).read_bytes() == before


def test_an_app_to_reconnect_comes_back_the_same_way(tmp_path):
    ws = make_space(tmp_path / "ada")
    routine.write_items(ws, reconnect=["Mail"])
    (first,) = shown(ws)
    yes(ws, first)
    routine.write_items(ws, reconnect=[])          # connected again
    assert kept(ws) == []
    routine.write_items(ws, reconnect=["Mail", "Calendar"])   # and lost again, with a second app
    asked = shown(ws)
    (mail,) = [i for i in asked if needs_you.same_item(i, first)]
    assert mail != first and sorted(asked) == sorted([mail, "routine-reconnect-calendar"])
    before = (ws / routine.ITEMS).read_bytes()
    routine.write_items(ws, reconnect=["Mail", "Calendar"])
    assert (ws / routine.ITEMS).read_bytes() == before


def test_a_second_yes_in_the_same_minute_still_gets_an_id_of_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_NOW", pages.iso(NOW))   # a clock that stands still, as in a fixture
    ws = make_space(tmp_path / "ada")
    seen = []
    for _ in range(3):   # each time: asked, answered yes, and the next run still finds it signed out
        routine.write_items(ws, claude_signin=True)
        (asked,) = shown(ws)
        assert asked not in seen and kept(ws) == [asked]
        seen.append(asked)
        yes(ws, asked)
    assert seen[0] == routine.SIGNIN_ID and all(needs_you.same_item(i, routine.SIGNIN_ID) for i in seen)


def test_a_no_or_a_later_stands_while_the_item_stays_and_nothing_is_touched_when_nothing_is_asked(tmp_path):
    ws = make_space(tmp_path / "ada")
    routine.write_items(ws, claude_signin=True, reconnect=["Mail"])
    yes(ws, routine.SIGNIN_ID, verb="no")              # "not now": their word, not a claim that it is done
    yes(ws, "routine-reconnect-mail", verb="later")
    before = (ws / routine.ITEMS).read_bytes()
    routine.write_items(ws, claude_signin=True, reconnect=["Mail"])   # the next run finds both still true
    assert (ws / routine.ITEMS).read_bytes() == before
    assert shown(ws) == ["routine-reconnect-mail"]   # later is still asked; no is hidden, as answered
    routine.write_items(ws)                           # None for both: left as they are
    assert (ws / routine.ITEMS).read_bytes() == before
    routine.write_items(ws, claude_signin=False)
    routine.write_items(ws, claude_signin=True)       # lost again after it came back: asked again
    assert [i for i in shown(ws) if needs_you.same_item(i, routine.SIGNIN_ID)] != [routine.SIGNIN_ID]
    assert len([i for i in kept(ws) if needs_you.same_item(i, routine.SIGNIN_ID)]) == 1
