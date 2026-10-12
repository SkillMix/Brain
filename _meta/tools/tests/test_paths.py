"""The path pause (vision section 10): a pulled-back or failed at-once release pauses the
standing choice or source audience that let it through, for four scaled weeks."""
import datetime as dt
import json

import yaml

import pages
import release
import sign
import status
import sweep
from conftest import NOW
from test_release import build, landed, queue_paths
from test_sweep import record, write_feed

CHOICE = [{"destination": "harbor", "kind": "mail"}]


def _shipped(tmp_path):
    _, harbor, ada = build(tmp_path, standing_choices=CHOICE)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (path,) = queue_paths(ada)
    fm, _ = pages.read(path)
    assert fm["path"] == "standing_choice:harbor:mail"
    release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    (shipped, _), = landed(harbor)
    row = json.loads((ada / "_meta/state/released.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["path"] == "standing_choice:harbor:mail"
    return harbor, ada, shipped["id"]


def test_a_retraction_pauses_the_path_and_the_next_record_waits(tmp_path):
    harbor, ada, item_id = _shipped(tmp_path)
    release.retract(ada, item_id, now=NOW + dt.timedelta(hours=1))
    assert release.is_paused(ada, "standing_choice:harbor:mail", NOW + dt.timedelta(days=27))
    assert not release.is_paused(ada, "standing_choice:harbor:mail", NOW + dt.timedelta(days=29))
    write_feed(ada, [record(), record("m-0002", at="2026-10-12T09:30:00Z")])
    sweep.run(ada, now=NOW + dt.timedelta(hours=2))
    newest = [pages.read(p)[0] for p in queue_paths(ada) if pages.read(p)[0].get("evidence") == ["m-0002"]]
    assert newest and newest[0]["class"] == "window"


def test_no_on_an_item_that_already_left_retracts_it_and_pauses(tmp_path):
    harbor, ada, item_id = _shipped(tmp_path)
    report = sign.apply(ada, {item_id: "no"}, by="Ada Okafor", now=NOW + dt.timedelta(hours=1))
    assert item_id in report.retracted
    assert any(pages.read(p)[0].get("retracts") == item_id for p in queue_paths(ada))
    assert release.is_paused(ada, "standing_choice:harbor:mail", NOW + dt.timedelta(days=1))


def test_a_failed_sample_signal_pauses_the_path_in_the_releasing_space(tmp_path):
    harbor, ada, item_id = _shipped(tmp_path)
    signal = {"id": "p-0000cc01", "destination": "ada", "at": pages.iso(NOW), "kind": "sample_failed",
              "item": item_id, "subject": "harbor"}
    target = ada / "_inbound" / "signals" / "p-0000cc01.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(signal), encoding="utf-8")
    status.run(ada, now=NOW + dt.timedelta(hours=1))
    assert release.is_paused(ada, "standing_choice:harbor:mail", NOW + dt.timedelta(days=2))
    rows = [json.loads(l) for l in (ada / "_meta/state/paused.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["reason"] == "failed sample" and rows[-1]["item"] == item_id
    status.run(ada, now=NOW + dt.timedelta(hours=2))
    rows2 = (ada / "_meta/state/paused.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows2) == len(rows), "a signal is read once"
