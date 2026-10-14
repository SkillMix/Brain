import datetime as dt
import json
from pathlib import Path

import morning_view
import pages
import validate
from conftest import NOW, make_space, write_page

HEALTH_LINE = "ada: 3 runs in the last day, 1200 tokens, checker hit rate 0.5, 2 held in doubt, 1 open question"


def setup(tmp_path, **extra):
    root = make_space(tmp_path / "ada", **extra)
    write_page(root, "_inbound/candidates/harbor.md", id="p-00000101", type="entity", title="Harbor",
               subject="harbor", kind="projects", target="knowledge/projects/harbor/harbor.md",
               evidence=["m-1"], body="Harbor goes live on 3 November.\n")
    write_page(root, "_outbound/queue/p-00000201.md", id="p-00000201", type="source", title="Go-live date",
               **{"class": "window"}, destination="harbor", subject="harbor",
               stage_after=pages.iso(NOW + dt.timedelta(hours=10)), listed=False, from_space="ada",
               kind="mail", evidence=["m-1"], body="Go-live date body.\n")
    write_page(root, "live/health.md", id="p-00000002", type="snapshot", title="Health",
               body=f"# Health\n\n{HEALTH_LINE}\n")
    (root / "log.md").write_text(
        "---\ntitle: Log\n---\n# Log\n\n"
        "- 2026-10-10T02:00:00Z released p-00000998 to harbor (at_once)\n"
        "- 2026-10-12T02:00:00Z released p-00000999 to harbor (at_once)\n"
        "- 2026-10-12T03:00:00Z released p-00000997 to brain (window)\n", encoding="utf-8")
    return root


def test_run_lists_the_leaving_item_and_marks_the_shadow(tmp_path):
    root = setup(tmp_path)
    report = morning_view.run(root, "Ada Okafor", now=NOW, shadow=True)
    qfm, _ = pages.read(root / "_outbound/queue/p-00000201.md")
    assert qfm["listed"] is True and qfm["listed_at"] == "2026-10-12T09:00:00Z"
    assert report.listed == ["p-00000201"]
    assert report.counts == {"leaving": 1, "candidate": 1}
    payload = json.loads(Path(report.payload_path).read_text(encoding="utf-8"))
    assert payload["subject"].startswith("[shadow]")
    assert payload["subject"].count("[shadow]") == 1
    assert payload["to"] == ["owner@example.test"]
    notice = json.loads(Path(report.notice_path).read_text(encoding="utf-8"))
    assert notice["kind"] == "briefing" and notice["sender"] == "morning-view"
    assert notice["title"] == "[shadow] 2 items need you"
    assert "link" not in notice
    assert sorted((s["count"], s["label"]) for s in notice["stats"]) == sorted(
        [(1, morning_view.LABELS["leaving"]), (1, morning_view.LABELS["candidate"])])
    assert HEALTH_LINE in notice["summary"]
    md = Path(report.markdown_path).read_text(encoding="utf-8")
    assert Path(report.markdown_path) == root / "live" / "needs-you.md"
    assert "# Needs you (2 items)" in md and "Ada Okafor" in md
    assert "## Left yesterday" in md and "p-00000999" in md
    assert "p-00000998" not in md and "p-00000997" not in md
    assert "## Health" in md and HEALTH_LINE in md
    runs = [json.loads(l) for l in (root / "_meta/state/runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert runs[-1]["routine"] == "morning-view" and runs[-1]["at"] == "2026-10-12T09:00:00Z"
    assert runs[-1]["space"] == "ada" and "cost_tokens" in runs[-1]
    assert [e for e in validate.validate(root) if not e.startswith("warning:")] == []


def test_run_without_shadow_has_no_prefix_and_keeps_the_page_id(tmp_path):
    root = setup(tmp_path)
    first = morning_view.run(root, "Ada Okafor", now=NOW)
    payload = json.loads(Path(first.payload_path).read_text(encoding="utf-8"))
    notice = json.loads(Path(first.notice_path).read_text(encoding="utf-8"))
    assert not payload["subject"].startswith("[shadow]") and "[shadow]" not in notice["title"]
    assert payload["subject"] == "Morning briefing: 2 items need you"
    page_id = pages.read(root / "live/needs-you.md")[0]["id"]
    second = morning_view.run(root, "Ada Okafor", now=NOW + dt.timedelta(hours=1))
    assert pages.read(root / "live/needs-you.md")[0]["id"] == page_id
    assert second.listed == ["p-00000201"]


def test_a_quiet_day_still_renders(tmp_path):
    root = make_space(tmp_path / "ben", owner="Ben Liu")
    report = morning_view.run(root, "Ben Liu", now=NOW)
    notice = json.loads(Path(report.notice_path).read_text(encoding="utf-8"))
    assert notice["title"] == "Nothing needs you today" and notice["stats"] == []
    assert notice.get("reassure")
    payload = json.loads(Path(report.payload_path).read_text(encoding="utf-8"))
    assert payload["subject"] == "Morning briefing: Nothing needs you today"
    assert report.counts == {} and report.listed == []


def test_a_health_line_that_fails_the_plain_language_check_is_left_out(tmp_path):
    root = setup(tmp_path)
    write_page(root, "live/health.md", id="p-00000002", type="snapshot", title="Health",
               body="# Health\n\nsee _meta/state/runs.jsonl for the raw lines\n")
    report = morning_view.run(root, "Ada Okafor", now=NOW)
    notice = json.loads(Path(report.notice_path).read_text(encoding="utf-8"))
    assert "runs.jsonl" not in notice["summary"]
    assert Path(report.payload_path).exists()


def test_cli(tmp_path, monkeypatch, capsys):
    root = setup(tmp_path)
    monkeypatch.setenv("SPACE_NOW", "2026-10-12T09:00:00Z")
    assert morning_view.main([str(root), "--person", "Ada Okafor", "--shadow"]) == 0
    out = capsys.readouterr().out
    assert "live/needs-you.md" in out
    payload = json.loads((root / "_meta/state/private/morning-payload.json").read_text(encoding="utf-8"))
    assert payload["subject"].startswith("[shadow]")


# ----- the doubt count, the high-stakes listing, the once-a-day guard ---------------------
def test_doubt_count_is_the_number_of_held_pages(tmp_path):
    root = setup(tmp_path)
    for n in range(3):
        write_page(root, f"_outbound/held/p-0000030{n}.md", id=f"p-0000030{n}", type="source",
                   title=f"held {n}", reason="doubt", why="mixed subjects", body="x\n")
    report = morning_view.run(root, "Ada Okafor", now=NOW)
    assert report.counts["doubt"] == 3
    notice = json.loads(Path(report.notice_path).read_text(encoding="utf-8"))
    assert (3, morning_view.LABELS["doubt"]) in [(s["count"], s["label"]) for s in notice["stats"]]


def test_high_stakes_window_item_is_listed_so_it_can_leave(tmp_path):
    root = setup(tmp_path)
    write_page(root, "_outbound/queue/p-00000301.md", id="p-00000301", type="source", title="Pricing rule",
               **{"class": "window"}, destination="brain", subject="sales", high_stakes=True,
               stage_after=pages.iso(NOW + dt.timedelta(hours=5)), listed=False, from_space="ada",
               kind="mail", evidence=["m-9"], body="A pricing rule.\n")
    report = morning_view.run(root, "Ada Okafor", now=NOW)
    fm, _ = pages.read(root / "_outbound/queue/p-00000301.md")
    assert fm["listed"] is True
    assert "p-00000301" in report.listed


def test_sent_today_reads_any_send_line_of_this_day(tmp_path):
    root = setup(tmp_path)
    assert morning_view.sent_today(root, now=NOW) is False
    runs = root / "_meta/state/runs.jsonl"
    lines = [{"routine": "daily-chain", "at": "2026-10-11T11:00:00Z", "space": "ada",
              "cost_tokens": 1, "notes": {"step": "agent", "message_id": "old"}},
             {"routine": "morning-view", "at": "2026-10-12T08:00:00Z", "space": "ada",
              "cost_tokens": 0, "notes": {"items": 3}}]
    runs.write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")
    assert morning_view.sent_today(root, now=NOW) is False
    with runs.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"routine": "daily-chain", "at": "2026-10-12T08:30:00Z", "space": "ada",
                             "cost_tokens": 1, "notes": {"step": "agent", "message_id": "abc"}}) + "\n")
    assert morning_view.sent_today(root, now=NOW) is True


def test_record_send_writes_the_canonical_line_and_the_cli_guard(tmp_path, monkeypatch):
    root = setup(tmp_path)
    monkeypatch.setenv("SPACE_NOW", "2026-10-12T09:00:00Z")
    assert morning_view.main([str(root), "sent-today"]) == 1
    assert morning_view.main([str(root), "record-send", "--id", "msg-1"]) == 0
    line = json.loads((root / "_meta/state/runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["routine"] == "morning-view" and line["notes"] == {"step": "send", "email": "msg-1"}
    assert morning_view.main([str(root), "sent-today"]) == 0
    assert morning_view.main([str(root), "record-send", "--failed", "no connector"]) == 0
    line = json.loads((root / "_meta/state/runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["notes"] == {"step": "send", "failed": "no connector"}


def test_silent_landings_count_without_a_doubt_item_and_include_the_homes_you_own(tmp_path):
    """The count is on the briefing every day, and it counts what landed in the spaces this
    person owns (their workspace and the mounted homes they own), not only the workspace."""
    import yaml
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor")
    other = make_space(tmp_path / "ferry", profile="project", name="ferry", owner="Ben Liu")
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] = [{"name": "harbor", "path": str(harbor)}, {"name": "ferry", "path": str(other)}]
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    for space_root in (harbor, other):
        path = space_root / "_meta/state/landed.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": pages.iso(NOW - dt.timedelta(days=1)), "id": "p-1"}) + "\n",
                        encoding="utf-8")
    report = morning_view.run(root, "Ada Okafor", now=NOW)
    assert report.counts.get("landed_silently") == 1


def test_a_channel_writes_no_page_where_its_profile_has_no_live_folder(tmp_path):
    """A channel has no `live/`: its briefing still counts and queues, but leaves no page
    behind that would fail the channel's own validation."""
    root = make_space(tmp_path / "corridor", profile="channel", name="corridor")
    morning_view.run(root, "Ada Okafor", now=NOW)
    assert not (root / "live").exists()
    assert (root / "_meta" / "state" / "private" / "morning-notice.json").exists()


def test_unsure_records_are_listed_in_the_email_only(tmp_path):
    import unsure
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    path = root / unsure.STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"id": "m-77", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z",
           "subject": "Dinner on Friday", "text": "Body."}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    report = morning_view.run(root, person="Ada Okafor", now=NOW)
    payload = json.loads(Path(report.payload_path).read_text(encoding="utf-8"))
    assert "Dinner on Friday" in payload["body"]
    assert Path(report.payload_path).relative_to(root).as_posix().startswith("_meta/state/private/")
    assert Path(report.notice_path).relative_to(root).as_posix().startswith("_meta/state/private/")
    day = json.loads((root / "_meta" / "state" / "morning" / "2026-10-12.json").read_text(encoding="utf-8"))
    assert "Dinner" not in json.dumps(day)
    assert "Dinner" not in (root / "live" / "needs-you.md").read_text(encoding="utf-8")
    assert unsure.waiting(root)[0]["first_listed"] == "2026-10-12T09:00:00Z"


def test_unsure_records_are_asked_by_code_and_survive_a_failing_health_line(tmp_path):
    import unsure
    root = setup(tmp_path, owner_email="ada@lantern.example")
    write_page(root, "live/health.md", id="p-00000002", type="snapshot", title="Health",
               body="# Health\n\nsee _meta/state/runs.jsonl for the raw lines\n")
    rec = {"id": "m-80", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z", "subject": "Gym plan",
           "text": "B."}
    (root / unsure.STORE).parent.mkdir(parents=True, exist_ok=True)
    (root / unsure.STORE).write_text(json.dumps(rec) + "\n", encoding="utf-8")
    report = morning_view.run(root, person="Ada Okafor", now=NOW)
    payload = json.loads(Path(report.payload_path).read_text(encoding="utf-8"))
    assert f"{unsure.code_of(rec)}: Gym plan" in payload["body"]
    day = json.loads((root / "_meta" / "state" / "morning" / "2026-10-12.json").read_text(encoding="utf-8"))
    assert all(i["kind"] != "unsure" for i in day["items"]), "unsure records are never numbered"
    assert unsure.waiting(root)[0]["first_listed"]


def test_the_old_tracked_briefing_files_are_removed(tmp_path):
    root = setup(tmp_path)
    for name in ("morning-payload.json", "morning-notice.json"):
        (root / "_meta" / "state" / name).write_text("{}", encoding="utf-8")
    morning_view.run(root, person="Ada Okafor", now=NOW)
    assert not (root / "_meta" / "state" / "morning-payload.json").exists()
    assert not (root / "_meta" / "state" / "morning-notice.json").exists()
