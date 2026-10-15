import datetime as dt
import json

import ids
import pages
import registry
import spaceyaml
import status
import validate
from conftest import NOW, make_space, mount_brain, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor"}
BEACON = {"id": "beacon", "kind": "projects", "title": "Beacon"}


def witness(root, name, subject, at, title="A mail about Harbor", body="Body.\n", **fm):
    return write_page(root, f"knowledge/sources/2026/10/{name}.md", type="source", title=title,
                      body=body, record=name, source="mail", kind="mail", at=at,
                      subject=subject, mentions=[], audience="owner", identifiers={}, **fm)


def signed(root, rel, pid, title, signed_at, body="Signed truth.\n"):
    return write_page(root, rel, id=pid, type="entity", title=title, status="stable", body=body,
                      signed_by="Ada Okafor", signed_at=signed_at,
                      fingerprint=pages.fingerprint(body))


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def test_desk_folder_registers_subject(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "work" / "projects" / "harbor" / "kickoff").mkdir(parents=True)
    report = status.run(root, now=NOW)
    assert report.registered == ["harbor"]
    subject = registry.Registry(root).get("harbor")
    assert subject.kind == "projects"
    assert subject.title == "Harbor"
    assert subject.added_by == "librarian"
    assert subject.added == "2026-10-12"
    assert (root / "knowledge" / "projects" / "harbor" / "index.md").exists()
    assert "live/subjects/harbor.md" in report.status_pages
    body = pages.read(root / "live" / "subjects" / "harbor.md")[1]
    assert "- work/projects/harbor/kickoff (working, mine, " in body
    assert errors(root) == []
    # a second run registers nothing new
    assert status.run(root, now=NOW).registered == []


def test_desk_folder_does_not_reregister_an_ancestor_subject(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[HARBOR])
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    (ada / "work" / "projects" / "harbor" / "kickoff").mkdir(parents=True)
    report = status.run(ada, now=NOW)
    assert report.registered == []
    assert registry.Registry(ada, spaceyaml.load(ada)).subjects == {}
    # the inherited subject still gets a status page from this space's evidence
    assert (ada / "live" / "subjects" / "harbor.md").exists()
    # but no hub: hubs are for own subjects only
    assert not (ada / "knowledge" / "projects" / "harbor" / "index.md").exists()


def test_status_page_timeline_line(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    witness(root, "m-1", "harbor", "2026-10-02T10:00:00Z", title="Harbor kickoff mail")
    report = status.run(root, now=NOW)
    assert report.status_pages == ["live/subjects/harbor.md"]
    fm, body = pages.read(root / "live" / "subjects" / "harbor.md")
    assert fm["type"] == "snapshot"
    assert fm["title"] == "Harbor: status"
    assert fm["status"] == "draft"
    assert fm["generated"]["by"] == "status"
    assert fm["subject"] == "harbor"
    assert pages.ID_RE.match(fm["id"])
    for heading in ("## Open", "## Recently closed", "## Timeline", "## Coming up"):
        assert heading in body
    assert "- Harbor kickoff mail (witness, mine, 2026-10-02)" in body
    assert body.index("## Timeline") < body.index("- Harbor kickoff mail")
    # the id is permanent across runs
    status.run(root, now=NOW + dt.timedelta(days=1))
    assert pages.read(root / "live" / "subjects" / "harbor.md")[0]["id"] == fm["id"]


def test_scope_follows_the_profile(tmp_path):
    project = make_space(tmp_path / "harbor", profile="project", subjects=[HARBOR])
    witness(project, "m-1", "harbor", "2026-10-02T10:00:00Z", title="Team mail")
    status.run(project, now=NOW)
    assert "- Team mail (witness, team, 2026-10-02)" in pages.read(
        project / "live" / "subjects" / "harbor.md")[1]
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[HARBOR])
    witness(brain, "m-1", "harbor", "2026-10-02T10:00:00Z", title="Company mail")
    status.run(brain, now=NOW)
    assert "- Company mail (witness, company, 2026-10-02)" in pages.read(
        brain / "live" / "subjects" / "harbor.md")[1]


def test_open_and_reported_lines(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "_inbound/candidates/harbor.md", title="Harbor scope", subject="harbor",
               kind="projects", generated=pages.generated("distill", NOW - dt.timedelta(days=1)))
    write_page(root, "_inbound/candidates/p-0000dddd.md", id="p-0000dddd",
               title="Harbor go-live moved", subject="harbor", released_from="harbor-project",
               release_class="window", generated=pages.generated("release", NOW - dt.timedelta(days=2)))
    status.run(root, now=NOW)
    body = pages.read(root / "live" / "subjects" / "harbor.md")[1]
    assert "- Harbor scope (pending, mine, 2026-10-11, " in body   # open items carry their id
    assert "- Harbor go-live moved (pending, team, 2026-10-10, " in body
    assert "- Harbor go-live moved (reported, team, 2026-10-10)" in body
    open_section = body[body.index("## Open"):body.index("## Recently closed")]
    assert "Harbor scope" in open_section and "reported" not in open_section


def test_recently_closed_and_coming_up(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    signed(root, "knowledge/projects/harbor/brief.md", "p-0000bbbb", "Harbor brief",
           "2026-10-05T09:00:00Z")
    signed(root, "knowledge/projects/harbor/old.md", "p-0000bbbc", "Old brief",
           "2026-08-01T09:00:00Z")
    witness(root, "m-2", "harbor", "2026-10-03T10:00:00Z", title="Go-live planning",
            body="Go-live is planned for 2026-10-20; the kickoff was 2026-09-01.\n")
    status.run(root, now=NOW)
    body = pages.read(root / "live" / "subjects" / "harbor.md")[1]
    assert "- Harbor brief (signed, mine, 2026-10-05)" in body
    assert "Old brief" not in body
    assert "- 2026-10-20: Go-live planning (witness, mine, 2026-10-03)" in body
    assert "2026-09-01" not in body


def test_a_record_routed_elsewhere_stays_on_its_owners_status_page(tmp_path):
    """The router sent it to the brain; the person whose record it is still sees it here."""
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    witness(root, "m-1", None, "2026-10-02T10:00:00Z", title="Harbor scope mail",
            routing={"decision": "route", "routes": ["brain/harbor"]})
    status.run(root, now=NOW)
    assert "- Harbor scope mail (witness, mine, 2026-10-02)" in pages.read(
        root / "live" / "subjects" / "harbor.md")[1]


def test_a_record_dated_ahead_is_coming_up_not_history(tmp_path):
    """A calendar event is captured before it happens; it is not part of the timeline yet."""
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    witness(root, "e-1", "harbor", "2026-10-20T13:00:00Z", title="Harbor go-live call")
    status.run(root, now=NOW)
    body = pages.read(root / "live" / "subjects" / "harbor.md")[1]
    assert "Harbor go-live call" not in body[body.index("## Timeline"):body.index("## Coming up")]
    assert "- 2026-10-20: Harbor go-live call (witness, mine, 2026-10-20)" in body[body.index("## Coming up"):]


def test_hub_links_by_id_and_ids_resolve(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[
        {"id": "sales", "kind": "domains", "title": "Sales"},
        {**HARBOR, "part_of": "sales"}])
    signed(root, "knowledge/projects/harbor/brief.md", "p-0000bbbb", "Harbor brief",
           "2026-10-05T09:00:00Z")
    write_page(root, "_inbound/candidates/p-0000cccc.md", id="p-0000cccc", title="Harbor scope",
               subject="harbor", kind="projects")
    witness(root, "m-1", "harbor", "2026-10-02T10:00:00Z")
    report = status.run(root, now=NOW)
    hub = root / "knowledge" / "projects" / "harbor" / "index.md"
    assert "knowledge/projects/harbor/index.md" in report.hubs
    text = hub.read_text(encoding="utf-8")
    assert "- [Harbor brief](id:p-0000bbbb) (stable, entity)" in text
    assert "- Pending: [Harbor scope](id:p-0000cccc)" in text
    assert "- Part of: Sales" in text
    status_id = pages.read(root / "live" / "subjects" / "harbor.md")[0]["id"]
    assert f"- Status: [status](id:{status_id})" in text
    for pid in pages.id_links(text):
        assert ids.resolve(root, pid) is not None, pid
    assert ids.resolve(root, "p-0000bbbb") == root / "knowledge" / "projects" / "harbor" / "brief.md"
    assert (root / "_meta" / "state" / "ids.yaml").exists()
    kind_index = (root / "knowledge" / "projects" / "index.md").read_text(encoding="utf-8")
    assert "[Harbor](harbor/index.md)" in kind_index
    knowledge_index = (root / "knowledge" / "index.md").read_text(encoding="utf-8")
    assert "<!-- kinds -->" in knowledge_index and "[projects](projects/index.md)" in knowledge_index
    # the umbrella has a hub too, with nothing listed yet
    assert (root / "knowledge" / "domains" / "sales" / "index.md").exists()
    assert errors(root) == []


def test_root_index_block_replaced_only_between_markers(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    (root / "index.md").write_text(
        "---\ntitle: Index\n---\n# Index\n\nKeep this line.\n\n<!-- subjects -->\nold content\n"
        "<!-- /subjects -->\n\nAnd this trailing line.\n", encoding="utf-8")
    status.run(root, now=NOW)
    text = (root / "index.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: Index\n---\n# Index\n\nKeep this line.\n\n<!-- subjects -->\n")
    assert text.endswith("<!-- /subjects -->\n\nAnd this trailing line.\n")
    assert "old content" not in text
    assert "[Harbor](knowledge/projects/harbor/index.md)" in text
    assert text.count("<!-- subjects -->") == 1
    # a page without markers gets the block appended, once
    (root / "index.md").write_text("---\ntitle: Index\n---\n# Index\n", encoding="utf-8")
    status.run(root, now=NOW)
    status.run(root, now=NOW)
    text = (root / "index.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: Index\n---\n# Index\n")
    assert text.count("<!-- subjects -->") == 1 and text.count("<!-- /subjects -->") == 1


def test_dormant_after_eight_scaled_weeks(tmp_path):
    root = make_space(tmp_path / "ada", mode="fixture", time_scale=48, subjects=[HARBOR, BEACON])
    quiet = NOW - dt.timedelta(hours=9 * 7 * 24 / 48)
    fresh = NOW - dt.timedelta(hours=7 * 7 * 24 / 48)
    witness(root, "m-old", "harbor", pages.iso(quiet), generated=pages.generated("sweep", quiet))
    witness(root, "m-new", "beacon", pages.iso(fresh), title="Beacon mail",
            generated=pages.generated("sweep", fresh))
    # old content that only ARRIVED now is not dormancy: a sweep may backfill old records
    witness(root, "m-backfill", "beacon", pages.iso(quiet), title="Old Beacon mail")
    report = status.run(root, now=NOW)
    assert report.dormant == ["harbor"]
    reg = registry.Registry(root)
    assert reg.get("harbor").state == "dormant"
    assert reg.get("beacon").state == "active"
    # nothing moves: the evidence and the pages stay where they are
    assert (root / "knowledge" / "sources" / "2026" / "10" / "m-old.md").exists()
    assert (root / "live" / "subjects" / "harbor.md").exists()
    assert "(projects, dormant)" in (root / "knowledge" / "projects" / "harbor" / "index.md").read_text(
        encoding="utf-8")
    # a subject added today with no evidence is not dormant
    root2 = make_space(tmp_path / "ben", subjects=[{**HARBOR, "added": "2026-10-12"}])
    assert status.run(root2, now=NOW).dormant == []


def test_health_lines(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    root = make_space(tmp_path / "ada", name="ada")
    mount_brain(root, brain)
    run = {"routine": "sweep", "at": pages.iso(NOW - dt.timedelta(hours=2)), "space": "ada",
           "cost_tokens": 1234, "notes": ""}
    old = {"routine": "sweep", "at": pages.iso(NOW - dt.timedelta(hours=30)), "space": "ada",
           "cost_tokens": 999, "notes": ""}
    (root / "_meta" / "state" / "runs.jsonl").write_text(
        json.dumps(run) + "\n" + json.dumps(old) + "\n", encoding="utf-8")
    (root / "_meta" / "state" / "check-verdicts.jsonl").write_text(
        json.dumps({"check_id": "a", "verdict": "yes", "by": "Ada Okafor", "at": pages.iso(NOW)}) + "\n"
        + json.dumps({"check_id": "b", "verdict": "no", "by": "Ada Okafor", "at": pages.iso(NOW)}) + "\n",
        encoding="utf-8")
    write_page(root, "_outbound/held/p-0000eee1.md", id="p-0000eee1", title="Held", reason="doubt")
    write_page(root, "_outbound/held/p-0000eee2.md", id="p-0000eee2", title="Held", reason="never")
    (root / "_inbound" / "questions" / "q1.md").write_text("---\nquestion: x\n---\n", encoding="utf-8")
    write_page(root, "_inbound/candidates/c.md", title="C", subject="x")
    report = status.run(root, now=NOW)
    text = (root / "live" / "health.md").read_text(encoding="utf-8")
    line = ("- ada: runs 2 in 24h, cost 1234 tokens, checker hit rate 0.50, held in doubt 1, "
            "open questions 1, candidates 1, probe none yet, capture rate n/a, "
            "requests asked 0 answered 0 expired 0, index none")
    assert line in text
    assert ("- brain: runs 0 in 24h, cost 0 tokens, checker hit rate n/a, held in doubt 0, "
            "open questions 0, candidates 0, probe none yet, requests asked 0 answered 0 expired 0, "
            "index none") in text
    assert report.health_lines[0] == line
    fm = pages.read(root / "live" / "health.md")[0]
    assert fm["type"] == "snapshot" and pages.ID_RE.match(fm["id"])
    runs = [json.loads(l) for l in (root / "_meta" / "state" / "runs.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    assert runs[-1]["routine"] == "status" and runs[-1]["space"] == "ada"
    assert runs[-1]["at"] == pages.iso(NOW) and runs[-1]["cost_tokens"] == 0


def test_now_pin(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR, BEACON])
    write_page(root, "live/now.md", id="p-00000001", type="snapshot", title="Now")
    witness(root, "m-1", "harbor", "2026-10-02T10:00:00Z")
    witness(root, "m-2", "beacon", "2026-10-05T10:00:00Z", title="Beacon mail")
    write_page(root, "_inbound/candidates/c.md", title="C", subject="harbor",
               generated=pages.generated("distill", NOW - dt.timedelta(days=12)))
    write_page(root, "_outbound/queue/p-0000aa01.md", id="p-0000aa01", title="Soon",
               stage_after=pages.iso(NOW + dt.timedelta(hours=2)))
    write_page(root, "_outbound/queue/p-0000aa02.md", id="p-0000aa02", title="Later",
               stage_after=pages.iso(NOW + dt.timedelta(days=3)))
    status.run(root, now=NOW)
    fm, body = pages.read(root / "live" / "now.md")
    assert fm["id"] == "p-00000001"
    assert "As of 2026-10-12: 2 active subjects, 1 candidates waiting, 1 items leaving within a day" in body
    assert body.index("Beacon") < body.index("Harbor")
    assert errors(root) == []


def test_see_also_from_questions_log(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    signed(root, "knowledge/projects/harbor/brief.md", "p-0000bbbb", "Harbor brief",
           "2026-10-05T09:00:00Z")
    steered = {"at": pages.iso(NOW), "question": "Who runs Harbor?", "subject": "harbor",
               "tier": "signed", "pages": ["p-0000bbbb", "knowledge/projects/harbor/brief.md"],
               "needed_steering": True, "answered_by_request": False}
    plain = {**steered, "pages": ["p-0000ffff"], "needed_steering": False}
    (root / "_meta" / "questions.log").write_text(
        "# Question log\n" + json.dumps(steered) + "\n" + json.dumps(plain) + "\n", encoding="utf-8")
    report = status.run(root, now=NOW)
    text = (root / "knowledge" / "projects" / "harbor" / "index.md").read_text(encoding="utf-8")
    assert "- See also: [Harbor brief](id:p-0000bbbb), `knowledge/projects/harbor/brief.md`" in text
    assert "p-0000ffff" not in text
    assert report.see_also == {"harbor": ["p-0000bbbb", "knowledge/projects/harbor/brief.md"]}


def test_check_mode_writes_nothing_and_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_NOW", pages.iso(NOW))
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    witness(root, "m-1", "harbor", "2026-10-02T10:00:00Z")
    report = status.run(root, now=NOW, check=True)
    assert not (root / "live" / "subjects" / "harbor.md").exists()
    assert "live/subjects/harbor.md" in report.changed
    assert "knowledge/projects/harbor/index.md" in report.changed
    assert not (root / "_meta" / "state" / "runs.jsonl").exists()
    assert status.main([str(root), "--check"]) == 1
    assert status.main([str(root)]) == 0
    assert status.run(root, now=NOW, check=True).changed == []
    assert status.main([str(root), "--check"]) == 0
    witness(root, "m-2", "harbor", "2026-10-06T10:00:00Z", title="Newer")
    assert status.main([str(root), "--check"]) == 1


def test_hand_written_pages_are_never_touched(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    page = signed(root, "knowledge/projects/harbor/brief.md", "p-0000bbbb", "Harbor brief",
                  "2026-10-05T09:00:00Z")
    before = page.read_bytes()
    charter = write_page(root, "identity/charter.md", type="policy", title="Charter")
    charter_before = charter.read_bytes()
    status.run(root, now=NOW)
    assert page.read_bytes() == before
    assert charter.read_bytes() == charter_before
    for path in root.rglob("*.md"):
        fm, _ = pages.read(path)
        if fm.get("generated", {}).get("by") == "status":
            assert fm.get("status") != "stable"


# ----- measurement: the weekly probe, the health line, the index and probe proposals ------
def _answers(root, questions):
    import yaml
    path = root / "_meta" / "tests" / "answers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"questions": questions}, sort_keys=False), encoding="utf-8")


def test_weekly_probe_runs_from_status_and_shows_in_health(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    signed(root, "knowledge/projects/harbor/brief.md", "p-0000bbbb", "Harbor brief",
           "2026-10-05T09:00:00Z", body="Harbor goes live on 3 November.\n")
    _answers(root, [{"question": "When?", "subject": "harbor", "expect": ["3 November"], "asker": "owner"}])
    status.run(root, now=NOW)
    probe = (root / "_meta" / "state" / "probe.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(probe) == 1
    status.run(root, now=NOW + dt.timedelta(days=1))
    assert len((root / "_meta" / "state" / "probe.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    status.run(root, now=NOW + dt.timedelta(days=8))
    assert len((root / "_meta" / "state" / "probe.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    text = (root / "live" / "health.md").read_text(encoding="utf-8")
    assert "probe walk 1/1 search 1/1" in text


def test_a_big_project_whose_search_beats_the_walk_gets_one_index_proposal(tmp_path, monkeypatch):
    root = make_space(tmp_path / "harbor", profile="project", subjects=[HARBOR])
    monkeypatch.setattr(status, "INDEX_PAGES", 3)
    for n in range(4):
        write_page(root, f"knowledge/projects/harbor/p{n}.md", type="concept", title=f"P{n}",
                   subject="harbor")
    (root / "_meta" / "state" / "probe.jsonl").write_text(json.dumps(
        {"at": pages.iso(NOW), "kinds": {"walk": {"walk": [1, 4], "search": [3, 4], "decisions": None}}})
        + "\n", encoding="utf-8")
    status.run(root, now=NOW)
    import yaml
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "librarian.yaml").read_text(encoding="utf-8"))
    ids_ = [i["id"] for i in items["items"]]
    assert ids_ == ["index-harbor"]
    status.run(root, now=NOW + dt.timedelta(hours=1))
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "librarian.yaml").read_text(encoding="utf-8"))
    assert [i["id"] for i in items["items"]] == ["index-harbor"]
    with (root / "log.md").open("a", encoding="utf-8") as fh:
        fh.write("- 2026-10-12T10:00:00Z yes index-harbor by Ada Okafor\n")
    status.run(root, now=NOW + dt.timedelta(hours=2))
    cfg = yaml.safe_load((root / "_meta" / "search.yaml").read_text(encoding="utf-8"))
    assert cfg["engine"] == "index"
    assert (root / "_meta" / "state" / "search-index.json").exists()
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "librarian.yaml").read_text(encoding="utf-8"))
    assert items["items"] == []
    assert "index index" in (root / "live" / "health.md").read_text(encoding="utf-8")


def test_a_steered_question_is_proposed_as_a_probe_question(tmp_path):
    import yaml
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    signed(root, "knowledge/projects/harbor/brief.md", "p-0000bbbb", "Harbor brief", "2026-10-05T09:00:00Z")
    steered = {"at": pages.iso(NOW), "question": "Who runs Harbor?", "subject": "harbor", "tier": "signed",
               "pages": ["p-0000bbbb"], "needed_steering": True, "answered_by_request": False}
    (root / "_meta" / "questions.log").write_text(json.dumps(steered) + "\n", encoding="utf-8")
    status.run(root, now=NOW)
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "librarian.yaml").read_text(encoding="utf-8"))
    probe_items = [i for i in items["items"] if i["id"].startswith("probe-q-")]
    assert len(probe_items) == 1 and "Who runs Harbor?" in probe_items[0]["text"]
    pid = probe_items[0]["id"]
    with (root / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"- 2026-10-12T10:00:00Z edit {pid} by Ada Okafor\n"
                 f"- 2026-10-12T10:00:00Z note {pid} by Ada Okafor: Ben Liu; delivery\n")
    status.run(root, now=NOW + dt.timedelta(hours=1))
    answers = yaml.safe_load((root / "_meta" / "tests" / "answers.yaml").read_text(encoding="utf-8"))
    added = [q for q in answers["questions"] if q["question"] == "Who runs Harbor?"]
    assert added and added[0]["expect"] == ["Ben Liu", "delivery"] and added[0]["subject"] == "harbor"
    items = yaml.safe_load((root / "_inbound" / "needs-you" / "librarian.yaml").read_text(encoding="utf-8"))
    assert not [i for i in items["items"] if i["id"] == pid]


def test_status_rewrites_a_stale_agents_file(tmp_path):
    import agents_md
    root = make_space(tmp_path / "ada")
    (root / "AGENTS.md").write_text("stale", encoding="utf-8")
    status.run(root, now=NOW)
    assert agents_md.check(root)
