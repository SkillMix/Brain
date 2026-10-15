"""A money amount is a price: wherever `price` is a left-out term, an amount counts as one."""
import datetime as dt

import pytest

import pages
import release
import registry
import route
import spaceyaml
import sweep
from conftest import NOW, make_space
from test_route import captured, decision, queued, route_with, space_set

PRICE = release.Terms(["price", "fee"])
AMOUNTS = ["EUR 600", "€600", "€ 600", "600 EUR", "600€", "$1,200.50", "1.200,- EUR", "€1.200,-",
           "eur 45", "GBP 1,250", "USD 2.5k", "a quote of €5k"]
NOT_AMOUNTS = ["2026-10-15", "15.10.2026", "15/10/2026", "v1.2.3", "release 2.10.1", "Python 3.12",
               "a 12.5% discount", "USD 5% higher", "600 records", "SM-223", "p-0442087a", "#600",
               "[00:01:00] Chloe: forty leads", "Euro 2024 tickets", "EURO 2024"]


@pytest.mark.parametrize("text", AMOUNTS)
def test_an_amount_counts_as_a_price(text):
    assert release.first_term(f"The offer: {text} for the pilot.", PRICE) == "amount"


@pytest.mark.parametrize("text", NOT_AMOUNTS)
def test_dates_versions_percentages_counts_and_ids_are_not_amounts(text):
    assert release.first_term(f"Noted {text} today.", PRICE) is None


def test_an_amount_counts_only_where_price_is_left_out():
    assert release.first_term("The offer: EUR 600.", release.Terms(["transcript", "fee"])) is None
    assert release.first_term("The offer: EUR 600.", release.Terms(["Price"])) == "amount"


def test_an_excepted_phrase_still_hides_only_itself():
    terms = release.Terms(["price", "pricing"], ["transfer pricing"])
    assert release.first_term("The transfer pricing team met.", terms) is None
    assert release.first_term("The transfer pricing team quoted EUR 600.", terms) == "amount"
    assert release.first_term("Plan 600 EUR", release.Terms(["price"], ["Plan 600 EUR"])) is None


def test_an_amount_in_a_title_holds_the_projection_like_price(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root, title="Kickoff, quote EUR 600")
    out = route.record(root, decision(), now=NOW)
    assert out["held"][0][1] == "left_out" and queued(root) == []
    captured(root, rid="c-2", title="Kickoff, quote EUR 600")
    route.record(root, decision(record="c-2", routes=route_with(title="Harbor kickoff")), now=NOW)
    (fm, _), = queued(root)
    assert fm["title"] == "Harbor kickoff"


def test_an_amount_in_a_part_or_a_note_holds_the_projection(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root, text="[00:01:00] Chloe: It comes to €1.200,- a week.\n[00:02:00] Ada: Fine.")
    out = route.record(root, decision(routes=route_with(
        parts=[{"text": "[00:01:00] Chloe: It comes to €1.200,- a week."}])), now=NOW)
    assert out["held"][0][1] == "left_out"
    captured(root, rid="c-2")
    out = route.record(root, decision(record="c-2", routes=route_with(
        parts=[{"text": "[00:02:00] Ada: We send it by Friday 2026-10-23.", "note": "agreed at 600 EUR"}])),
        now=NOW)
    assert out["held"][0][1] == "left_out"


def test_a_line_with_an_amount_is_left_out_and_a_title_with_one_stays_home(tmp_path):
    ada = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor",
                                                    "home": "harbor", "match": {"label": "Harbor"}}],
                     owner_email="ada@lantern.example")
    space = spaceyaml.load(ada)
    subject = registry.Registry(ada, space).get("harbor")
    mixed = {"id": "m1", "kind": "mail", "from": "ops@harbor.example", "at": "2026-10-11T08:00:00Z",
             "subject": "Harbor plan", "text": "Go-live moves to 3 November.\nThe extra week is $1,200.50."}
    verdict = sweep.classify(space, mixed, subject, "harbor", None, "owner", NOW)
    assert not verdict.hold and verdict.extra["trimmed_body"] == "Go-live moves to 3 November."
    titled = dict(mixed, id="m2", subject="Harbor plan, 600 EUR")
    assert sweep.classify(space, titled, subject, "harbor", None, "owner", NOW).hold == "left_out"


def test_the_scrub_at_ship_time_holds_an_amount(tmp_path):
    from test_release import build, held_items, landed, queue_paths
    from test_sweep import record, write_feed
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    path, = queue_paths(ada)
    fm, body = pages.read(path)
    pages.write(path, fm, body + "\nThe extra week is EUR 950.\n")
    report = release.ship(ada, now=NOW + dt.timedelta(hours=1), offline_roots={"harbor": harbor})
    assert report.shipped == 0 and report.held == 1 and landed(harbor) == []
    (held, _), = held_items(ada)
    assert held["reason"] == "left_out" and "amount" in held["why"]


def test_a_term_at_the_edge_of_a_note_or_in_italics_is_caught(tmp_path):
    """A note travels as `_note_`: the underscore is markdown, not part of the word."""
    assert release.first_term("_about the fee_", PRICE) == "fee"
    assert release.first_term("*about the fee*", PRICE) == "fee"
    assert release.first_term("agreed _EUR 600_", PRICE) == "amount"
    root, _, _ = space_set(tmp_path)
    captured(root)
    out = route.record(root, decision(routes=route_with(
        parts=[{"text": "[00:02:00] Ada: We send it by Friday 2026-10-23.", "note": "and the fee"}])), now=NOW)
    assert out["held"][0][1] == "left_out"
