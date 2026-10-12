import json

import yaml

import pages
import tests_run
from conftest import NOW, make_space, mount_brain, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
          "match": {"label": "Harbor"}}


def signed(root, rel, body, **fm):
    fm.setdefault("status", "stable")
    fm.setdefault("signed_by", "Ada Okafor")
    fm.setdefault("signed_at", "2026-10-05T09:00:00Z")
    fm.setdefault("fingerprint", pages.fingerprint(body))
    return write_page(root, rel, body=body, **fm)


def questions_file(path, *questions):
    path.write_text(yaml.safe_dump({"questions": list(questions)}, sort_keys=False),
                    encoding="utf-8")
    return path


def leaks_file(path, *leaks):
    path.write_text(yaml.safe_dump({"leaks": list(leaks)}, sort_keys=False), encoding="utf-8")
    return path


def recorded_lines(root):
    path = root / "_meta" / "state" / "tests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ----- answers ---------------------------------------------------------------

def test_answers_walk_passes_with_signed_page_and_records_a_line(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    page = signed(root, "knowledge/projects/harbor/harbor.md",
                  "Go-live moves to 3 November after the client's request.\n",
                  type="entity", title="Harbor")
    path = questions_file(tmp_path / "answers.yaml", {
        "question": "When does Harbor go live?", "subject": "harbor",
        "expect": ["3 November"], "asker": "ada", "via": "walk"})
    report = tests_run.answers(root, path=path, now=NOW)
    assert (report.passed, report.failed) == (1, 0)
    detail = report.details[0]
    assert detail["ok"] is True
    assert "knowledge/projects/harbor/harbor.md" in detail["found_in"]
    assert page.exists()
    lines = recorded_lines(root)
    assert lines and lines[-1]["kind"] == "answers"
    assert lines[-1]["passed"] == 1 and lines[-1]["failed"] == 0
    assert lines[-1]["at"] == "2026-10-12T09:00:00Z"


def test_answers_fails_when_the_fact_is_missing(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    signed(root, "knowledge/projects/harbor/harbor.md", "Nothing dated here.\n",
           type="entity", title="Harbor")
    path = questions_file(tmp_path / "answers.yaml", {
        "question": "When does Harbor go live?", "subject": "harbor",
        "expect": ["3 November"], "asker": "ada", "via": "both"})
    report = tests_run.answers(root, path=path, now=NOW)
    assert (report.passed, report.failed) == (0, 1)
    assert report.details[0]["ok"] is False
    assert "3 November" in report.details[0]["missing"]


def test_answers_walk_follows_status_page_and_hub_links(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "knowledge/projects/harbor/index.md", type="entity", title="Harbor hub",
               body="- [kickoff notes](id:p-0000cafe)\n")
    write_page(root, "knowledge/topics/notes/kickoff.md", id="p-0000cafe", title="Kickoff",
               body="Kickoff set for the second week.\n")
    write_page(root, "live/subjects/harbor.md", type="snapshot", title="Harbor status",
               body="## Open\n- go-live 3 November (witness, mine, 2026-10-02)\n")
    path = questions_file(tmp_path / "answers.yaml", {
        "question": "What is open on Harbor?", "subject": "harbor",
        "expect": ["3 November", "second week"], "asker": "ada", "via": "walk"})
    report = tests_run.answers(root, path=path, now=NOW)
    assert (report.passed, report.failed) == (1, 0)
    found = report.details[0]["found_in"]
    assert "live/subjects/harbor.md" in found
    assert "knowledge/topics/notes/kickoff.md" in found


def test_answers_search_covers_work_and_mounted_brain(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain")
    write_page(brain, "knowledge/domains/sales/pilot.md", type="entity", title="Pilot",
               body="The prospect wants a Q1 pilot.\n")
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    (ada / "work" / "projects").mkdir(parents=True)
    (ada / "work" / "projects" / "note.md").write_text("Budget up to 40k.\n", encoding="utf-8")
    path = questions_file(tmp_path / "answers.yaml", {
        "question": "What does the prospect want?", "subject": "sales",
        "expect": ["Q1 pilot", "40k"], "asker": "ada", "via": "search"})
    report = tests_run.answers(ada, path=path, now=NOW)
    assert (report.passed, report.failed) == (1, 0)


def test_answers_run_where_the_asker_is(tmp_path):
    ada = make_space(tmp_path / "ada")
    chloe = make_space(tmp_path / "chloe", owner="Chloe Martin")
    write_page(chloe, "knowledge/domains/sales/pilot.md", title="Pilot",
               body="The prospect wants a Q1 pilot.\n")
    path = questions_file(tmp_path / "answers.yaml", {
        "question": "What does the prospect want?", "subject": "sales",
        "expect": ["Q1 pilot"], "asker": "chloe", "via": "search"})
    # Asked in Ada's space: Chloe is not here, so the question is skipped, not failed.
    report = tests_run.answers(ada, path=path, now=NOW)
    assert (report.passed, report.failed) == (0, 0)
    assert report.skipped and "chloe" in report.skipped[0]
    # With the roots given, the question runs against Chloe's space.
    report = tests_run.answers(ada, path=path, now=NOW, roots={"chloe": chloe})
    assert (report.passed, report.failed) == (1, 0)


def test_answers_without_a_file_is_an_empty_report(tmp_path):
    root = make_space(tmp_path / "ada")
    report = tests_run.answers(root, now=NOW)
    assert (report.passed, report.failed) == (0, 0)
    assert recorded_lines(root) == []


# ----- leaks -----------------------------------------------------------------

def test_leaks_fails_on_a_hit_and_names_the_file(tmp_path):
    ada = make_space(tmp_path / "ada")
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor")
    write_page(harbor, "knowledge/projects/harbor/rates.md", title="Rates",
               body="Their Day Rate is 950 per day.\n")
    path = leaks_file(tmp_path / "leaks.yaml", {
        "audience_root": "harbor", "must_not_find": ["950", "day rate"],
        "reason": "left-out list"})
    report = tests_run.leaks(ada, roots={"harbor": harbor}, path=path, now=NOW)
    assert (report.passed, report.failed) == (0, 1)
    detail = report.details[0]
    assert detail["ok"] is False
    assert any("knowledge/projects/harbor/rates.md" in hit["file"] for hit in detail["hits"])
    assert sorted(detail["hits"][0]["found"]) == ["950", "day rate"]
    assert recorded_lines(ada)[-1]["kind"] == "leaks"


def test_leaks_passes_when_clean_and_ignores_git_and_tests(tmp_path):
    ada = make_space(tmp_path / "ada")
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor")
    # a fixed id: a random one carries "950" about once in seven hundred runs
    write_page(harbor, "knowledge/projects/harbor/plan.md", id="p-0000ab01", title="Plan",
               body="Go-live 3 November.\n")
    (harbor / ".git").mkdir()
    (harbor / ".git" / "COMMIT_EDITMSG").write_text("day rate 950\n", encoding="utf-8")
    (harbor / "_meta" / "tests").mkdir(parents=True)
    (harbor / "_meta" / "tests" / "leaks.yaml").write_text("day rate\n", encoding="utf-8")
    (harbor / "_meta" / "state" / "feed").mkdir(parents=True)
    (harbor / "_meta" / "state" / "feed" / "mail.jsonl").write_text("day rate\n", encoding="utf-8")
    path = leaks_file(tmp_path / "leaks.yaml", {
        "audience_root": "harbor", "must_not_find": ["950", "day rate"],
        "reason": "left-out list"})
    report = tests_run.leaks(ada, roots={"harbor": harbor}, path=path, now=NOW)
    assert (report.passed, report.failed) == (1, 0)


def test_leaks_reads_content_not_machinery_settings_or_its_own_log(tmp_path):
    ada = make_space(tmp_path / "ada")
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor",
                        left_out=["day rate", "950"])
    for rel in ("_meta/tools/sweep.py", "_meta/tools/tests/test_x.py", "_meta/templates/page.md",
                "docs/shape-1.md", ".claude/skills/release/SKILL.md", "rules.md", "AGENTS.md",
                "_meta/state/tests.jsonl", "_meta/state/runs.jsonl"):
        target = harbor / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("the day rate is 950\n", encoding="utf-8")
    path = leaks_file(tmp_path / "leaks.yaml", {
        "audience_root": "harbor", "must_not_find": ["950", "day rate"], "reason": "left-out"})
    report = tests_run.leaks(ada, roots={"harbor": harbor}, path=path, now=NOW)
    assert (report.passed, report.failed) == (1, 0), report.details
    # Content anywhere else still counts: a mail payload under state, a queue item, a page.
    (harbor / "_meta" / "state" / "morning-payload.json").write_text(
        '{"body": "their day rate is 950"}\n', encoding="utf-8")
    report = tests_run.leaks(ada, roots={"harbor": harbor}, path=path, now=NOW)
    assert report.failed == 1
    assert [h["file"] for h in report.details[0]["hits"]] == ["harbor:_meta/state/morning-payload.json"]


def test_leaks_resolves_mounts_and_relative_paths_and_skips_unknown(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain")
    write_page(brain, "knowledge/topics/ads/note.md", title="Ads",
               body="We stop paid search ads next month.\n")
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    (ada / "work" / "ads").mkdir(parents=True)
    (ada / "work" / "ads" / "draft.md").write_text("paid search ads\n", encoding="utf-8")
    path = leaks_file(tmp_path / "leaks.yaml",
                      {"audience_root": "brain", "must_not_find": ["paid search ads"],
                       "reason": "stays in the owner's space"},
                      {"audience_root": "work/ads", "must_not_find": ["nothing here"],
                       "reason": "relative path resolves"},
                      {"audience_root": "beacon", "must_not_find": ["3 November"],
                       "reason": "not mounted here"})
    report = tests_run.leaks(ada, path=path, now=NOW)
    assert (report.passed, report.failed) == (1, 1)
    assert report.skipped and "beacon" in report.skipped[0]
    failed = [d for d in report.details if not d["ok"]]
    assert failed[0]["audience_root"] == "brain"


# ----- CLI ---------------------------------------------------------------------

def test_cli_exit_codes(tmp_path):
    ada = make_space(tmp_path / "ada", subjects=[HARBOR])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor")
    (ada / "_meta" / "tests").mkdir(parents=True)
    questions_file(ada / "_meta" / "tests" / "answers.yaml", {
        "question": "When does Harbor go live?", "subject": "harbor",
        "expect": ["3 November"], "asker": "ada", "via": "both"})
    leaks_file(ada / "_meta" / "tests" / "leaks.yaml", {
        "audience_root": "harbor", "must_not_find": ["day rate"], "reason": "left-out"})
    assert tests_run.main([str(ada), "answers"]) == 1
    signed(ada, "knowledge/projects/harbor/harbor.md", "Go-live 3 November.\n",
           type="entity", title="Harbor")
    assert tests_run.main([str(ada), "answers"]) == 0
    assert tests_run.main([str(ada), "all", "--root", f"harbor={harbor}"]) == 0
    write_page(harbor, "knowledge/projects/harbor/rates.md", title="Rates",
               body="day rate 950\n")
    assert tests_run.main([str(ada), "leaks", "--root", f"harbor={harbor}"]) == 1
