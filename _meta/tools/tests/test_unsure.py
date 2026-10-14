"""unsure.py: records the reader could not classify wait privately until the owner says."""
import datetime as dt
import json

import unsure
from conftest import NOW, make_space

REC = {"id": "m-77", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z", "from": "x@y.example",
       "to": ["ada@lantern.example"], "subject": "Dinner — Friday? https://example.com/x", "text": "Body."}


def store(root, *records):
    path = root / unsure.STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_the_store_is_in_the_private_zone():
    assert unsure.STORE.as_posix().startswith("_meta/state/private/")


def test_admit_moves_the_record_into_its_feed_once(tmp_path):
    root = make_space(tmp_path / "ada")
    store(root, REC)
    key = unsure.key_of(REC)
    assert [r["key"] for r in unsure.waiting(root)] == [key]
    assert unsure.admit(root, key) is True and unsure.admit(root, key) is False
    feed = (root / "_meta" / "state" / "feed" / "mail.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(feed) == 1
    written = json.loads(feed[0])
    assert written["id"] == "m-77" and not {"feed", "key", "first_listed"} & set(written)
    assert unsure.waiting(root) == []


def test_admit_never_doubles_a_record_already_in_the_feed(tmp_path):
    root = make_space(tmp_path / "ada")
    feed = root / "_meta" / "state" / "feed" / "mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"id": "m-77"}) + "\n", encoding="utf-8")
    store(root, REC)
    assert unsure.admit(root, unsure.key_of(REC)) is True
    assert len(feed.read_text(encoding="utf-8").splitlines()) == 1


def test_drop_and_expire(tmp_path):
    root = make_space(tmp_path / "ada")
    other = dict(REC, id="m-78")
    store(root, REC, other)
    assert unsure.drop(root, unsure.key_of(REC)) is True and unsure.drop(root, unsure.key_of(REC)) is False
    assert unsure.expire(root, NOW) == 0, "never listed: it waits for its first listing"
    unsure.mark_listed(root, [unsure.key_of(other)], NOW)
    unsure.mark_listed(root, [unsure.key_of(other)], NOW + dt.timedelta(days=3))
    assert unsure.waiting(root)[0]["first_listed"] == "2026-10-12T09:00:00Z"
    assert unsure.expire(root, NOW + dt.timedelta(days=13)) == 0
    assert unsure.expire(root, NOW + dt.timedelta(days=15)) == 1 and unsure.waiting(root) == []


def test_a_line_is_plain_and_short():
    line = unsure.line_for(REC)
    assert "—" not in line and "http" not in line and len(line) <= 80
    assert line.startswith("Dinner") and line.endswith("(mail, 11 Oct)")
    long = unsure.line_for(dict(REC, subject="word " * 40))
    assert len(long) <= 80


def test_a_broken_line_in_the_store_is_skipped(tmp_path):
    root = make_space(tmp_path / "ada")
    path = root / unsure.STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n" + json.dumps(REC) + "\n", encoding="utf-8")
    assert [r["id"] for r in unsure.waiting(root)] == ["m-77"]


def test_a_code_is_short_and_stable():
    code = unsure.code_of(REC)
    assert code == unsure.code_of(dict(REC)) and len(code) == 5 and unsure.key_of(REC).startswith(code)
