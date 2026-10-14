"""The fourth wire: a request through the corridor, answered by a standing choice, by the
holder's person, never, or not at all; the way back; both logs; proposals; asker leak tests."""
import datetime as dt
import json

import yaml

import mint
import pages
import release
import request
import sign
import spaceyaml
import tests_run
import validate
from conftest import NOW, make_space, mount_brain, write_page

H = dt.timedelta(hours=1)
SUBJECTS = [{"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor"},
            {"id": "hiring-plan", "kind": "topics", "title": "Hiring plan", "home": "ada"}]


def world(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=SUBJECTS)
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                        audience=["Ada Okafor", "Ben Liu"], left_out=["day rate", "EUR 950"])
    chloe = make_space(tmp_path / "chloe", name="chloe", owner="Chloe Martin", audience=["Chloe Martin"])
    ada = make_space(tmp_path / "ada", name="ada", owner="Ada Okafor")
    ben = make_space(tmp_path / "ben", name="ben", owner="Ben Liu", audience=["Ben Liu"])
    for root in (harbor, chloe, ada, ben):
        mount_brain(root, brain)
    write_page(harbor, "live/subjects/harbor.md", type="snapshot", title="Harbor: status", subject="harbor",
               body="# Harbor: status\n\n## Open\n- none\n\n## Coming up\n- 2026-11-03: go-live (signed, team, 2026-10-02)\n"
                    "\n## Timeline\n- Port schedule arrives Friday (witness, team, 2026-10-04)\n"
                    "- day rate EUR 950 for the extension (witness, team, 2026-10-01)\n")
    mint.channel(chloe, harbor)
    mint.channel(ben, ada)
    roots = {"brain": brain, "harbor": harbor, "chloe": chloe, "ada": ada, "ben": ben,
             "channel-chloe-harbor": tmp_path / "channel-chloe-harbor",
             "channel-ada-ben": tmp_path / "channel-ada-ben"}
    return roots


def test_the_corridor_is_a_channel_both_spaces_mount(tmp_path):
    roots = world(tmp_path)
    channel = spaceyaml.load(roots["channel-chloe-harbor"])
    assert channel.profile == "channel" and channel.data["between"] == ["chloe", "harbor"]
    assert {m["name"] for m in spaceyaml.load(roots["chloe"]).mounts} >= {"channel-chloe-harbor"}
    assert {m["name"] for m in spaceyaml.load(roots["harbor"]).mounts} >= {"channel-chloe-harbor"}
    assert [e for e in validate.validate(roots["channel-chloe-harbor"]) if not e.startswith("warning:")] == []


def test_a_status_request_is_answered_at_once_by_the_standing_choice(tmp_path):
    roots = world(tmp_path)
    assert request.ask(roots["chloe"], "harbor", "status", "Where does Harbor stand?",
                       asked_by="Chloe Martin", now=NOW) is not None
    report = release.ship(roots["chloe"], now=NOW, offline_roots=roots)
    assert report.requests == 1
    held = request.answer(roots["harbor"], now=NOW + H, offline_roots=roots)
    assert len(held.answered) == 1
    release.ship(roots["harbor"], now=NOW + H, offline_roots=roots)
    got = request.collect(roots["chloe"], now=NOW + 2 * H, offline_roots=roots)
    assert len(got.collected) == 1
    rid = got.collected[0]
    witness = next((roots["chloe"] / "knowledge" / "sources").rglob(f"answer-{rid}.md"))
    fm, body = pages.read(witness)
    assert "2026-11-03" in body and "(signed, team, 2026-10-02)" in body
    assert "EUR 950" not in body and "day rate" not in body
    assert fm["approved_by"].startswith("standing choice: status")
    assert (roots["chloe"] / "_inbound" / "answers" / f"{rid}.md").exists()
    log = [json.loads(l) for l in (roots["harbor"] / "_meta/state/requests.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["outcome"] == "answered" and log[-1]["from"] == "chloe"
    asked = (roots["chloe"] / "_meta/state/requests-asked.jsonl").read_text(encoding="utf-8")
    assert '"outcome": "answered"' in asked
    qlog = (roots["chloe"] / "_meta/questions.log").read_text(encoding="utf-8")
    assert '"answered_by_request": true' in qlog
    import owner_email
    (notice,) = owner_email.pending(roots["chloe"])
    assert f"answer-{rid}" in notice.read_text(encoding="utf-8")


def test_correspondence_is_never_answered_and_detail_waits_for_the_holder(tmp_path):
    roots = world(tmp_path)
    request.ask(roots["chloe"], "harbor", "correspondence", "Send me the client's mails", now=NOW)
    request.ask(roots["chloe"], "harbor", "detail", "Who chairs the board?", now=NOW)
    release.ship(roots["chloe"], now=NOW, offline_roots=roots)
    report = request.answer(roots["harbor"], now=NOW + H, offline_roots=roots)
    assert len(report.never) == 1 and len(report.waiting) == 1
    items = yaml.safe_load((roots["harbor"] / "_inbound/needs-you/requests.yaml").read_text(encoding="utf-8"))["items"]
    (item,) = items
    assert item["kind"] == "request" and "Who chairs the board?" in item["text"] and "widens" in item["text"]
    # the holder's person answers with an edit: it ships the next time the channel step runs
    sign.apply(roots["harbor"], {item["id"]: ["edit", "Mira Holt chairs it."]}, by="Ben Liu", now=NOW + 2 * H)
    again = request.answer(roots["harbor"], now=NOW + 3 * H, offline_roots=roots)
    assert again.answered == [item["id"]]
    release.ship(roots["harbor"], now=NOW + 3 * H, offline_roots=roots)
    got = request.collect(roots["chloe"], now=NOW + 4 * H, offline_roots=roots)
    assert got.collected == [item["id"]]


def test_a_personal_space_waits_for_its_person_and_the_request_expires(tmp_path):
    roots = world(tmp_path)
    rid = request.ask(roots["ben"], "hiring-plan", "status", "When does hiring start?", now=NOW).stem
    release.ship(roots["ben"], now=NOW, offline_roots=roots)
    report = request.answer(roots["ada"], now=NOW + H, offline_roots=roots)
    assert report.waiting == [rid], "a personal space ships no standing choices"
    later = NOW + dt.timedelta(hours=30)
    assert request.answer(roots["ada"], now=later, offline_roots=roots).expired == [rid]
    got = request.collect(roots["ben"], now=later, offline_roots=roots)
    assert got.expired == [rid]
    assert "No answer arrived" in (roots["ben"] / "_inbound/answers" / f"{rid}.md").read_text(encoding="utf-8")
    assert request.state_of(roots["ben"], rid)["status"] == "expired"


def test_a_readable_subject_needs_no_request(tmp_path):
    roots = world(tmp_path)
    assert request.ask(roots["ada"], "hiring-plan", "status", "Hiring?", now=NOW) is None


def test_five_of_a_class_become_one_proposal_and_always_writes_the_choice(tmp_path):
    roots = world(tmp_path)
    for n in range(5):
        request.ask(roots["chloe"], "harbor", "detail", f"Detail question {n}?", now=NOW + n * H)
    release.ship(roots["chloe"], now=NOW + 5 * H, offline_roots=roots)
    report = request.answer(roots["harbor"], now=NOW + 6 * H, offline_roots=roots)
    assert report.proposals == ["rq-propose-chloe-detail"]
    with (roots["harbor"] / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"- {pages.iso(NOW + 7 * H)} yes rq-propose-chloe-detail by Ben Liu\n")
    request.answer(roots["harbor"], now=NOW + 7 * H, offline_roots=roots)
    assert {"from": "chloe", "class": "detail", "answer": "at_once"} in spaceyaml.load(roots["harbor"]).request_choices


def test_an_asker_leak_test_renders_what_the_holder_would_send(tmp_path):
    roots = world(tmp_path)
    leaks = tmp_path / "leaks.yaml"
    leaks.write_text(yaml.safe_dump({"leaks": [
        {"audience_root": "chloe", "request": {"asker": "chloe", "holder": "harbor", "subject": "harbor",
                                               "class": "status", "question": "Where does Harbor stand?"},
         "must_not_find": ["EUR 950", "day rate"], "reason": "an asker never gets commercial terms by asking"},
        {"audience_root": "chloe", "request": {"asker": "chloe", "holder": "harbor", "subject": "harbor",
                                               "class": "status", "question": "Where does Harbor stand?"},
         "must_not_find": ["2026-11-03"], "reason": "a canary: the covered part does go out"}]}), encoding="utf-8")
    report = tests_run.leaks(roots["chloe"], roots=roots, path=leaks, now=NOW)
    assert (report.passed, report.failed) == (1, 1)
