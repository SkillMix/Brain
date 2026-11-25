"""Many plain drafts fold into one line of Needs you, so what needs the person is seen."""
import datetime as dt

import needs_you
import sign
from conftest import NOW, make_space
from test_needs_you import candidate, queue_item


def drafts(root, n, **fm):
    for k in range(n):
        candidate(root, stem=f"d{k}", id=f"p-0000f{k:03d}", title=f"Draft {k}", subject=f"s{k}", kind="topics",
                  target=f"knowledge/topics/s{k}/s{k}.md", **fm)


def test_more_plain_drafts_than_a_list_shows_fold_into_one_line(tmp_path):
    # Seen live: one space listed over 200 drafts of pages and decisions, so the few items that
    # needed the person were lost among them.
    root = make_space(tmp_path / "ada")
    drafts(root, 12)
    candidate(root, stem="urgent", id="p-0000f900", title="Urgent", subject="u", kind="topics",
              target="knowledge/topics/u/u.md", deadline="2026-10-13T09:00:00Z")
    candidate(root, stem="stakes", id="p-0000f901", title="Contract", subject="c", kind="topics",
              target="knowledge/topics/c/c.md", high_stakes="money")
    queue_item(root, "p-00000241", "Kickoff", NOW + dt.timedelta(hours=10))
    items = {i.id: i for i in needs_you.collect(root, now=NOW)}
    assert {"p-0000f900", "p-0000f901", "p-00000241", "drafts"} <= set(items)
    assert not any(i.startswith("p-0000f0") for i in items)
    assert items["drafts"].text.startswith("drafts to sign when you have time: 12")
    opened = needs_you.draft_items(root)
    assert sorted(i.id for i in opened) == [f"p-0000f{k:03d}" for k in range(12)]
    assert "not found" not in sign.read_back(root, ["p-0000f004"])   # each is still signed by its id


def test_a_few_plain_drafts_stay_one_line_each(tmp_path):
    root = make_space(tmp_path / "ada")
    drafts(root, needs_you.FOLD_DRAFTS_OVER)
    items = needs_you.collect(root, now=NOW)
    assert len(items) == needs_you.FOLD_DRAFTS_OVER and "drafts" not in {i.id for i in items}


def test_the_morning_mail_counts_each_folded_draft(tmp_path):
    import morning_view
    root = make_space(tmp_path / "ada")
    drafts(root, 12)
    report = morning_view.run(root, "Ada Okafor", now=NOW)
    assert report.counts["candidate"] == 12
