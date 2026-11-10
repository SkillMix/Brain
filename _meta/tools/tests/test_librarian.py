"""The librarian: mechanical moves verified, judgment proposed, ownership asked once,
migrations, and one commit per structural change through the sync script."""
import json

import yaml

import librarian
import pages
import registry
import status
import validate
from conftest import NOW, make_space, write_page
from test_sync import git, repo, run, subjects  # noqa: F401  (the repo fixture)

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor"}
BEACON = {"id": "beacon", "kind": "projects", "title": "Beacon"}


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def flat(root):
    return lambda r: (0, 0)


def test_kind_move_and_stray_pages_are_filed_and_logged(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR, BEACON])
    page = write_page(root, "knowledge/clients/harbor/brief.md", id="p-0000a101", type="entity",
                      title="Harbor brief", subject="harbor")
    (root / "work" / "clients" / "harbor" / "kickoff").mkdir(parents=True)
    (root / "work" / "clients" / "harbor" / "kickoff" / "notes.md").write_text("notes\n", encoding="utf-8")
    write_page(root, "knowledge/projects/harbor/beacon-note.md", id="p-0000a102", type="entity",
               title="A Beacon note", subject="beacon")
    write_page(root, "knowledge/projects/loose.md", id="p-0000a103", type="entity", title="Loose",
               subject="harbor")
    report = librarian.run(root, now=NOW, scorer=lambda r: (1, 1))
    assert len(report.applied) == 4
    assert (root / "knowledge/projects/harbor/brief.md").exists() and not page.exists()
    assert (root / "work/projects/harbor/kickoff/notes.md").exists()
    assert (root / "knowledge/projects/beacon/beacon-note.md").exists()
    assert (root / "knowledge/projects/harbor/loose.md").exists()
    log = (root / "log.md").read_text(encoding="utf-8")
    assert log.count("librarian ") >= 4 and "Undo:" in log
    changes = (root / "_meta/state/librarian/changes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(changes) == 4 and all("paths" in json.loads(c) for c in changes)
    assert errors(root) == []


def test_a_change_that_lowers_the_tests_is_reverted(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "knowledge/clients/harbor/brief.md", type="entity", title="Brief", subject="harbor")
    calls = []

    def lower_after(r):  # before a change (2, 1), after it (1, 1): every change lowers the score
        calls.append(r)
        return (2, 1) if len(calls) % 2 else (1, 1)
    report = librarian.run(root, now=NOW, scorer=lower_after)
    assert report.reverted and not report.applied
    assert (root / "knowledge/clients/harbor/brief.md").exists()
    assert "reverted" in (root / "log.md").read_text(encoding="utf-8")


def test_desk_folders_outside_a_kind(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    (root / "work" / "harbor").mkdir(parents=True)
    (root / "work" / "harbor" / "a.md").write_text("a\n", encoding="utf-8")
    (root / "work" / "brain-review").mkdir(parents=True)
    report = librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    assert (root / "work/projects/harbor/a.md").exists()
    assert len(report.proposed) == 1
    props = librarian.proposals(root)
    (data,) = props.values()
    assert data["change"]["kind"] == "file" and "brain-review" in data["change"]["summary"]


def test_split_merge_and_umbrella_are_proposed_not_applied(tmp_path):
    subjects = [HARBOR, dict(BEACON, match={"label": "Beacon"}),
                {"id": "beacon-app", "kind": "projects", "title": "Beacon app", "match": {"label": "Beacon"}},
                {"id": "dock", "kind": "projects", "title": "Dock", "client": "tidewater"},
                {"id": "pier", "kind": "projects", "title": "Pier", "client": "tidewater"},
                {"id": "quay", "kind": "projects", "title": "Quay", "client": "tidewater"}]
    root = make_space(tmp_path / "ada", subjects=subjects)
    for n in range(7):
        for word in ("migration", "ports"):
            (root / "work" / "projects" / "harbor" / f"{word}-{n}").mkdir(parents=True)
    report = librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    kinds = sorted(d["change"]["kind"] for d in librarian.proposals(root).values())
    assert kinds == ["merge", "split", "umbrella"]
    assert len(report.proposed) == 3
    assert registry.Registry(root).get("harbor-migration") is None, "nothing applied without a review"
    again = librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    assert again.proposed == [], "a proposal is written once"


def test_apply_needs_a_reviewer_and_applies_a_split(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    for n in range(7):
        for word in ("migration", "ports"):
            (root / "work" / "projects" / "harbor" / f"{word}-{n}").mkdir(parents=True)
    librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    (key,) = librarian.proposals(root)
    try:
        librarian.apply(root, key, "", now=NOW)
        raise AssertionError("applied without a reviewer")
    except ValueError:
        pass
    assert librarian.apply(root, key, "second agent", now=NOW, scorer=lambda r: (0, 0)) == "applied"
    reg = registry.Registry(root)
    assert reg.get("harbor-migration").part_of == "harbor"
    assert (root / "work/projects/harbor-migration/migration-3").is_dir()
    data = yaml.safe_load((root / "_meta/state/librarian" / f"{key}.yaml").read_text(encoding="utf-8"))
    assert data["status"] == "applied" and data["reviewed_by"] == "second agent"
    assert errors(root) == []


def test_a_change_that_would_move_ownership_is_asked_once(tmp_path):
    subjects = [{"id": "tidewater", "kind": "clients", "title": "Tidewater", "owner": "Chloe Martin"},
                {"id": "dock", "kind": "projects", "title": "Dock", "client": "tidewater"},
                {"id": "pier", "kind": "projects", "title": "Pier", "client": "tidewater"},
                {"id": "quay", "kind": "projects", "title": "Quay", "client": "tidewater"}]
    root = make_space(tmp_path / "ada", subjects=subjects)
    librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    (key,) = librarian.proposals(root)
    assert librarian.apply(root, key, "second agent", now=NOW, scorer=lambda r: (0, 0)) == "asked"
    questions = list((root / "_inbound/questions").glob("librarian-*.md"))
    assert len(questions) == 1
    fm, _ = pages.read(questions[0])
    assert "Chloe Martin" in fm["question"] and fm["default"] == "nothing moves"
    assert registry.Registry(root).get("dock").part_of is None
    report = librarian.Report()
    change = librarian.Change.from_dict(yaml.safe_load(
        (root / "_meta/state/librarian" / f"{key}.yaml").read_text(encoding="utf-8"))["change"])
    assert librarian.apply_change(root, change, key, NOW, scorer=lambda r: (0, 0), report=report) == "asked"
    assert report.asked == [] and len(list((root / "_inbound/questions").glob("librarian-*.md"))) == 1


def test_the_owners_yes_to_an_ownership_question_applies_the_change(tmp_path):
    import sign
    subjects = [{"id": "tidewater", "kind": "clients", "title": "Tidewater", "owner": "Chloe Martin"},
                {"id": "dock", "kind": "projects", "title": "Dock", "client": "tidewater"},
                {"id": "pier", "kind": "projects", "title": "Pier", "client": "tidewater"},
                {"id": "quay", "kind": "projects", "title": "Quay", "client": "tidewater"}]
    root = make_space(tmp_path / "ada", subjects=subjects)
    librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    (key,) = librarian.proposals(root)
    assert librarian.apply(root, key, "second agent", now=NOW, scorer=lambda r: (0, 0)) == "asked"
    (question,) = (root / "_inbound/questions").glob("librarian-*.md")
    qid = pages.read(question)[0]["id"]
    sign.apply(root, {qid: "yes"}, by="Chloe Martin", now=NOW)
    reg = registry.Registry(root)
    assert [reg.get(s).part_of for s in ("dock", "pier", "quay")] == ["tidewater"] * 3
    assert not question.exists()
    assert "approved by Chloe Martin" in (root / "log.md").read_text(encoding="utf-8")
    data = yaml.safe_load((root / "_meta/state/librarian" / f"{key}.yaml").read_text(encoding="utf-8"))
    assert data["status"] == "applied" and data["approved_by"] == "Chloe Martin"
    assert errors(root) == []


def test_a_filing_asked_from_the_run_is_applied_on_the_owners_yes(tmp_path):
    subjects = [dict(HARBOR, owner="Ada Okafor"), dict(BEACON, owner="Ben Achebe")]
    root = make_space(tmp_path / "ada", subjects=subjects)
    page = write_page(root, "knowledge/projects/beacon/note.md", id="p-1a2b3c4d", type="concept", title="Note",
                      subject="harbor")
    report = librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    (key,) = report.asked
    assert page.exists() and (root / "_inbound/questions" / f"librarian-{key}.md").exists()
    assert librarian.answer(root, key, by="Ada Okafor", now=NOW) == "applied"
    assert not page.exists() and (root / "knowledge/projects/harbor/note.md").exists()


def test_a_merge_that_crosses_classifications_names_the_classes(tmp_path):
    subjects = [dict(HARBOR, owner="Ada Okafor", added="2026-01-01",
                     classification={"state": "settled", "class": "company", "home": "brain"}),
                {"id": "harbor-ops", "kind": "projects", "title": "Harbor", "owner": "Ada Okafor",
                 "added": "2026-02-01", "classification": {"state": "settled", "class": "walled",
                                                           "home": "harbor-crew", "crew": ["Ada Okafor"]}}]
    root = make_space(tmp_path / "ada", subjects=subjects)
    librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    (key,) = [k for k, v in librarian.proposals(root).items() if v["change"]["kind"] == "merge"]
    assert librarian.apply(root, key, "second agent", now=NOW, scorer=lambda r: (0, 0)) == "asked"
    fm, _ = pages.read(root / "_inbound/questions" / f"librarian-{key}.md")
    assert "walled (crew Ada Okafor; home harbor-crew) -> company (home brain)" in fm["question"]
    assert "walled" in fm["recommendation"] and "company" in fm["recommendation"]


def test_a_yes_to_an_ownership_question_whose_proposal_is_gone_is_logged(tmp_path):
    import sign
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "_inbound/questions/librarian-lib-0badc0de.md", id="p-0badc0de", type="review",
               title="Ownership would move: gone", question="Make the change?", status="draft")
    sign.apply(root, {"p-0badc0de": "yes"}, by="Ada Okafor", now=NOW)
    assert "could not be applied" in (root / "log.md").read_text(encoding="utf-8")


def test_shape_migrations_run_in_order_and_are_verified(tmp_path):
    root = make_space(tmp_path / "ada")
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "shape_2.py").write_text(
        "from pathlib import Path\n\ndef migrate(root):\n    (Path(root) / 'MIGRATED').write_text('2')\n",
        encoding="utf-8")
    done = librarian.migrate(root, folder=folder, now=NOW, scorer=lambda r: (0, 0))
    assert done == [2] and (root / "MIGRATED").exists()
    assert yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))["shape"] == 2
    assert librarian.migrate(root, folder=folder, now=NOW, scorer=lambda r: (0, 0)) == []


def test_status_runs_the_librarian(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "knowledge/clients/harbor/brief.md", type="entity", title="Brief", subject="harbor")
    report = status.run(root, now=NOW)
    assert report.librarian.applied and (root / "knowledge/projects/harbor/brief.md").exists()
    assert (root / "knowledge/projects/harbor/index.md").exists()


def test_sync_lands_each_structural_change_as_one_commit(repo):  # noqa: F811
    write_page(repo, "knowledge/clients/harbor/brief.md", type="entity", title="Brief", subject="harbor")
    (repo / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [HARBOR]}), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    (repo / "work" / "unrelated.md").write_text("an ordinary edit\n", encoding="utf-8")
    librarian.run(repo, now=NOW, scorer=lambda r: (0, 0))
    assert run(repo, "--min-age-minutes", "0") == 0
    log = subjects(repo)
    assert log[:2] == ["chore: sync", "chore: structure"]
    moved = git(repo, "show", "--name-status", "--format=", "HEAD~1").stdout
    assert "knowledge/projects/harbor/brief.md" in moved and "unrelated" not in moved
    landed = (repo / "_meta/state/librarian/landed.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(landed) == 1
