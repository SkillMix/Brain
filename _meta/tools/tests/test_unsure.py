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


# ----- the ids a cloud run keeps ------------------------------------------------------------------
def id_lines(root):
    path = root / unsure.IDS
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_an_unsure_record_leaves_only_its_id_in_the_repository(tmp_path):
    """A cloud run's private zone goes with its clone: what stays is the id, the source and when
    it was kept, never what the record says or who sent it."""
    root = make_space(tmp_path / "ada")
    secret = {"id": "gmail-1a2b", "source": "mail", "from": "x@y.example", "subject": "Secret deal",
              "text": "The numbers."}
    assert unsure.keep(root, secret, now=NOW) is True
    assert unsure.keep(root, secret, now=NOW + dt.timedelta(hours=1)) is False, "once per id"
    assert unsure.IDS.as_posix() == "_meta/state/unsure-ids.jsonl"
    assert id_lines(root) == [{"id": "gmail-1a2b", "source": "mail", "at": "2026-10-12T09:00:00Z"}]
    text = (root / unsure.IDS).read_text(encoding="utf-8")
    assert "Secret" not in text and "numbers" not in text and "x@y.example" not in text
    [kept] = unsure.waiting(root)
    assert kept["subject"] == "Secret deal" and kept["feed"] == "mail", "the record itself stays private"


def test_a_record_the_export_parked_by_hand_is_noted_once(tmp_path):
    root = make_space(tmp_path / "ada")
    store(root, REC, dict(REC, id="m-78", feed="calls"))
    assert unsure.note_ids(root, NOW) == 2
    assert unsure.note_ids(root, NOW + dt.timedelta(hours=1)) == 0
    assert [(l["id"], l["source"]) for l in id_lines(root)] == [("m-77", "mail"), ("m-78", "calls")]
    assert unsure.drop(root, unsure.key_of(REC)) is True
    assert len(id_lines(root)) == 2, "an answer never takes the trace back"


def test_the_records_kept_since_a_moment_are_counted_once_each(tmp_path):
    root = make_space(tmp_path / "ada")
    unsure.keep(root, dict(REC, id="m-1"), now=NOW - dt.timedelta(days=2))
    unsure.keep(root, dict(REC, id="m-2"), now=NOW - dt.timedelta(hours=3))
    unsure.keep(root, dict(REC, id="m-3"), now=NOW + dt.timedelta(hours=3))
    with (root / unsure.IDS).open("a", encoding="utf-8") as handle:   # a union merge may repeat a line
        handle.write(json.dumps({"id": "m-2", "source": "mail", "at": "2026-10-12T07:00:00Z"}) + "\nnot json\n")
    assert unsure.kept_since(root, NOW - dt.timedelta(days=1), NOW) == 1
    assert unsure.kept_since(root, None, NOW) == 2, "without a briefing yet, every one so far"
