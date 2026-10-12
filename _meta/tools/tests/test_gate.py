"""gate.py: protected paths and signatures at the landing gate."""
import subprocess
from pathlib import Path

import gate
import pages
from conftest import make_space, write_page

SIGNED = {"signed_by": "Ada Okafor", "signed_at": "2026-10-12T09:00:00Z"}


def test_change_under_claude_skills_is_protected_in_an_instance(tmp_path):
    root = make_space(tmp_path / "ada")
    cls, reasons = gate.classify(root, [".claude/skills/x.md"])
    assert cls == "protected"
    assert any(".claude/skills/x.md" in r and ".claude/" in r for r in reasons)


def test_other_changes_are_auto_mergeable(tmp_path):
    root = make_space(tmp_path / "ada")
    cls, reasons = gate.classify(root, ["knowledge/topics/a/one.md", "journal/2026/10/2026-10-12.md",
                                        "_meta/state/runs.jsonl", "rules-of-thumb.md"])
    assert cls == "auto-mergeable"
    assert reasons
    assert gate.classify(root, [])[0] == "auto-mergeable"


def test_every_default_protected_prefix_counts(tmp_path):
    root = make_space(tmp_path / "ada")
    for rel in ("identity/charter.md", "rules.md", "space.yaml", ".claude/settings.json",
                "_meta/tools/sync.py", ".github/workflows/checks.yml"):
        assert gate.classify(root, [rel])[0] == "protected", rel


def test_custom_protected_list_is_read_from_space_yaml(tmp_path):
    root = make_space(tmp_path / "ada", protected=["knowledge/decisions/"])
    assert gate.classify(root, [".claude/skills/x.md"])[0] == "auto-mergeable"
    assert gate.classify(root, ["knowledge/decisions/2026-10-12-x.md"])[0] == "protected"


def test_template_repository_is_always_auto_mergeable(tmp_path):
    root = make_space(tmp_path / "t", profile="template")
    cls, reasons = gate.classify(root, [".claude/skills/x.md", "rules.md", "_meta/tools/gate.py"])
    assert cls == "auto-mergeable"
    assert any("template" in r for r in reasons)


def test_check_signatures_fails_a_mismatch_and_passes_a_match(tmp_path):
    root = make_space(tmp_path / "ada")
    body = "Signed truth.\n"
    write_page(root, "knowledge/topics/a/good.md", status="stable", body=body,
               fingerprint=pages.fingerprint(body), **SIGNED)
    write_page(root, "knowledge/topics/a/bad.md", status="stable", body="Edited later.\n",
               fingerprint=pages.fingerprint(body), **SIGNED)
    write_page(root, "knowledge/topics/a/draft.md", status="draft", body="Anything.\n")
    assert gate.check_signatures(root, ["knowledge/topics/a/good.md", "knowledge/topics/a/draft.md"]) == []
    errs = gate.check_signatures(root, ["knowledge/topics/a/bad.md"])
    assert len(errs) == 1 and "fingerprint" in errs[0] and "bad.md" in errs[0]


def test_stable_page_under_live_or_sources_is_an_error(tmp_path):
    root = make_space(tmp_path / "ada")
    body = "Operational state.\n"
    write_page(root, "live/subjects/harbor.md", type="status", status="stable", body=body,
               fingerprint=pages.fingerprint(body), **SIGNED)
    write_page(root, "knowledge/sources/2026/10/m-1.md", type="source", status="stable", body=body,
               fingerprint=pages.fingerprint(body), **SIGNED)
    errs = gate.check_signatures(root, ["live/subjects/harbor.md", "knowledge/sources/2026/10/m-1.md"])
    assert len(errs) == 2
    assert all("never signed" in e for e in errs)


def test_unsigned_stable_page_deleted_pages_and_other_zones(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/topics/a/unsigned.md", status="stable", body="No signature.\n")
    errs = gate.check_signatures(root, ["knowledge/topics/a/unsigned.md"])
    assert any("signed_by" in e for e in errs) and any("fingerprint" in e for e in errs)
    assert gate.check_signatures(root, ["knowledge/topics/a/gone.md"]) == []
    (root / "work").mkdir(exist_ok=True)
    (root / "work" / "note.md").write_text("---\nstatus: stable\n---\nDesk note.\n", encoding="utf-8")
    assert gate.check_signatures(root, ["work/note.md"]) == []


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    done = subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def test_cli_prints_class_and_exits_on_signature_errors_only(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = make_space(tmp_path / "ada")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD").strip()
    (root / ".claude" / "skills").mkdir(parents=True)
    (root / ".claude" / "skills" / "x.md").write_text("skill\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "skill")
    assert gate.main([base, str(root)]) == 0
    out = capsys.readouterr().out
    assert "GATE: protected" in out and ".claude/skills/x.md" in out
    body = "Signed.\n"
    write_page(root, "knowledge/topics/a/edited.md", status="stable", body="Edited after signing.\n",
               fingerprint=pages.fingerprint(body), **SIGNED)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "page")
    assert gate.main([base, str(root)]) == 1
    out = capsys.readouterr().out
    assert "GATE: protected" in out and "fingerprint" in out
