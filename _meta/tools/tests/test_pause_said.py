"""A no that pauses a release path for four weeks says so before the verdict and after it."""
import datetime as dt

import landing
import needs_you
import sign
from conftest import NOW, make_space
from test_needs_you import queue_item


def test_a_no_that_pauses_a_path_says_so_before_and_after_the_verdict(tmp_path):
    # Seen live: a no on a queue item paused the path that let it through for four weeks, and
    # neither the list, the read-back nor the apply report said so.
    root = make_space(tmp_path / "ada")
    queue_item(root, "p-00000231", "Kickoff", NOW + dt.timedelta(hours=10), path="router:harbor")
    queue_item(root, "p-00000232", "Signed", NOW + dt.timedelta(hours=10), path="approved:Ada Okafor")
    items = {i.id: i for i in needs_you.collect(root, now=NOW) if i.kind == "leaving"}
    assert items["p-00000231"].text.endswith("; a no pauses router:harbor for four weeks")
    assert "pauses" not in items["p-00000232"].text
    assert "a no pauses router:harbor for four weeks" in sign.read_back(root, ["p-00000231"])
    report = sign.apply(root, {"p-00000231": "no", "p-00000232": "no"}, by="Ada Okafor", now=NOW)
    assert report.paused == [{"id": "p-00000231", "path": "router:harbor"}]


def test_a_high_stakes_item_says_it_too(tmp_path):
    root = make_space(tmp_path / "ada")
    queue_item(root, "p-00000233", "Contract", NOW + dt.timedelta(hours=10), path="router:harbor", high_stakes="money")
    (item,) = [i for i in needs_you.collect(root, now=NOW) if i.id == "p-00000233"]
    assert item.text.endswith("; a no pauses router:harbor for four weeks")


def test_the_weekly_sample_says_a_no_pauses_the_senders_path(tmp_path, monkeypatch):
    from test_landing import harbor_space, released, vouch
    root = harbor_space(tmp_path)
    released(root, "p-0000b0a1", "Fact.\n")
    vouch(monkeypatch, root)
    landing.land(root, now=NOW)
    item = landing.sample_item(root, now=NOW + dt.timedelta(hours=1), given={})
    assert item["text"].endswith("; a no pauses, for four weeks, the path that let each failed one through")
