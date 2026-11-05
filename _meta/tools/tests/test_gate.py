"""gate.py: protected paths and signatures at the landing gate."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import agents_md
import gate
import pages
import spaceyaml
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
    assert gate.classify(root, ["knowledge/decisions/2026-10-12-x.md"])[0] == "protected"
    assert gate.classify(root, ["knowledge/topics/a/one.md"])[0] == "auto-mergeable"
    # a list that leaves the machinery out does not take it out of the gate
    assert gate.classify(root, [".claude/skills/x.md"])[0] == "protected"


# ----- the minimum: what the gate guards whatever space.yaml says -------------------------------
REPO = Path(__file__).resolve().parents[3]
# Template sync installs these tests in every space, where space.yaml is the space's own list:
# a test that reads the template's own space.yaml runs in the template only.
template_only = pytest.mark.skipif(not (REPO / ".github" / "template-marker").exists(),
                                   reason="reads the template's own space.yaml")
NEW_IN_THE_MINIMUM = ("CLAUDE.md", "AGENTS.md", ".gitignore", ".gitattributes", ".gitmodules",
                      "_meta/templates/space-personal.yaml", "_meta/plugins/spaces-guard/guard.py",
                      ".claude-plugin/marketplace.json")
# What instructs or runs code on the person's computer: a cloud run has their token and a shell, and a branch
# name proves nothing, so these wait for the click from any branch.
RUNS_ON_THE_COMPUTER = ("_meta/state/routines/sweep-chain.md", "_meta/state/onboard.yaml",
                        "_meta/migrations/0007-move-pages.py", ".mcp.json", "CLAUDE.local.md", "conftest.py",
                        "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
NEW_IN_THE_MINIMUM += RUNS_ON_THE_COMPUTER


def test_a_short_protected_list_still_guards_the_minimum(tmp_path):
    for name, own in (("ada", ["rules.md"]), ("ada-short", [])):
        prefixes, note = gate.protected_prefixes(make_space(tmp_path / name, protected=own))
        assert note is None
        assert set(gate.MINIMUM_PROTECTED) <= set(prefixes), own
    # a space.yaml that cannot be read still guards it, and says why
    broken = make_space(tmp_path / "ada-broken")
    (broken / "space.yaml").write_text("protected: [unclosed\n", encoding="utf-8")
    prefixes, note = gate.protected_prefixes(broken)
    assert set(gate.MINIMUM_PROTECTED) <= set(prefixes) and "unreadable" in note


def test_the_spaces_own_entries_come_first(tmp_path):
    own = ["knowledge/decisions/", "rules.md", ".Claude", "knowledge/decisions/"]
    prefixes, _ = gate.protected_prefixes(make_space(tmp_path / "ada", protected=own))
    assert prefixes[:3] == ["knowledge/decisions/", "rules.md", ".Claude"]
    # then the minimum in its order, each entry once: `.Claude` is `.claude/`, `rules.md` is there
    rest = [p for p in gate.MINIMUM_PROTECTED if p not in ("rules.md", ".claude/")]
    assert prefixes[3:] == rest
    assert len(prefixes) == len(set(p.casefold().rstrip("/") for p in prefixes))


def test_a_change_to_the_gitignore_is_protected(tmp_path):
    default = make_space(tmp_path / "ada")
    short = make_space(tmp_path / "ada-short", protected=["rules.md"])
    for root in (default, short):
        for rel in NEW_IN_THE_MINIMUM:
            cls, reasons = gate.classify(root, [rel])
            assert cls == "protected", (root.name, rel)
            assert any(r.startswith(f"{rel}: protected by `") for r in reasons), (root.name, rel)


def test_the_base_branchs_short_list_still_guards_the_minimum(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    space = make_space(tmp_path / "ada", protected=[])
    _git(space, "init", "-q", "-b", "main")
    _git(space, "add", "-A")
    _git(space, "commit", "-q", "-m", "base")
    base = _git(space, "rev-parse", "HEAD").strip()
    assert set(gate.MINIMUM_PROTECTED) <= set(gate.rules_at(space, base)["protected"])
    (space / ".gitignore").write_text("personal/\n", encoding="utf-8")
    (space / "AGENTS.md").write_text("# Operating contract\n", encoding="utf-8")
    _git(space, "add", "-A")
    _git(space, "commit", "-q", "-m", "machinery")
    cls, reasons = gate.classify(space, gate.changed_files(space, base), base_ref=base)
    assert cls == "protected"
    for rel in (".gitignore", "AGENTS.md"):
        assert any(r.startswith(f"{rel}: protected by `") for r in reasons), rel


def test_a_new_space_is_written_with_the_minimum():
    assert spaceyaml.DEFAULTS["protected"] == list(gate.MINIMUM_PROTECTED)
    # the widened minimum by name, so a new space never starts without what runs on the computer
    for rel in RUNS_ON_THE_COMPUTER:
        assert any(gate._under(rel, p) for p in spaceyaml.DEFAULTS["protected"]), rel


@template_only
def test_the_template_space_yaml_lists_the_minimum():
    own = yaml.safe_load((REPO / "space.yaml").read_text(encoding="utf-8"))["protected"]
    assert own == list(gate.MINIMUM_PROTECTED)
    for rel in RUNS_ON_THE_COMPUTER:
        assert any(gate._under(rel, p) for p in own), rel


def test_template_repository_is_always_auto_mergeable(tmp_path):
    root = make_space(tmp_path / "t", profile="template")
    cls, reasons = gate.classify(root, [".claude/skills/x.md", "rules.md", "_meta/tools/gate.py"])
    assert cls == "auto-mergeable"
    assert any("template" in r for r in reasons)
    # the widened minimum changes nothing in the template: it is a code repository, not a space
    cls, reasons = gate.classify(root, list(RUNS_ON_THE_COMPUTER))
    assert cls == "auto-mergeable" and any("template" in r for r in reasons)


# ----- what runs on the computer: protected by its path, whatever the branch ---------------------
@pytest.mark.parametrize("rel", RUNS_ON_THE_COMPUTER)
def test_what_runs_on_the_computer_is_protected_from_any_branch(tmp_path, monkeypatch, capsys, rel):
    """A cloud run has the person's token and a shell, so it can push a branch of any name and open a pull request
    of its own: the gate reads paths, never the branch. Each new entry of the minimum waits for the click from a
    `cloud/` branch and an ordinary one alike, beside a page that alone would land."""
    root = make_space(tmp_path / "ada")
    cls, reasons = gate.classify(root, [rel, "knowledge/topics/a/one.md"])
    assert cls == "protected"
    assert any(r.startswith(f"{rel}: protected by `") for r in reasons), reasons
    # a space whose own list leaves it out is still guarded
    assert gate.classify(make_space(tmp_path / "ada-short", protected=[]), [rel])[0] == "protected"
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(root, "init", "-q", "-b", "main")
    base = _commit(root, "base")
    _write(root, {rel: "# from a run\n", "knowledge/topics/a/one.md": "# one\n"})
    _commit(root, "a run's change")
    for branch in ("cloud/sweep", "sync/m-desktop", "feature/x", None):
        if branch is None:
            monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
        else:
            monkeypatch.setenv("GITHUB_HEAD_REF", branch)
        assert gate.main([base, str(root)]) == 0
        out = capsys.readouterr().out
        assert "GATE: protected" in out and f"{rel}: protected by `" in out, (branch, out)


def test_pages_alone_from_a_cloud_branch_stay_auto_mergeable(tmp_path, monkeypatch, capsys):
    """The rule is about paths, not branches: a cloud run's landing of pages and state lands on green from
    `cloud/sweep` as from any other branch."""
    pages_only = ["knowledge/sources/2026/10/r1.md", "knowledge/topics/a/one.md", "_meta/state/runs.jsonl",
                  "_meta/state/sweep-watermark.yaml", "_inbound/needs-you/sync-cloud.yaml",
                  "_meta/state/cloud-fresh.yaml"]   # a fresh start's count rides its push
    root = make_space(tmp_path / "ada")
    assert gate.classify(root, pages_only)[0] == "auto-mergeable"
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    _git(root, "init", "-q", "-b", "main")
    base = _commit(root, "base")
    _write(root, {rel: "a: 1\n" for rel in pages_only})
    _commit(root, "a cloud run's pages")
    for branch in ("cloud/sweep", "sync/m-desktop"):
        monkeypatch.setenv("GITHUB_HEAD_REF", branch)
        assert gate.main([base, str(root)]) == 0
        out = capsys.readouterr().out
        assert "GATE: auto-mergeable" in out and "no protected paths touched" in out, (branch, out)


# ----- the gate and what a routine may not edit: two lists that must not drift apart -------------
# Of what a routine may not edit (`routine.PROTECTED_EDITS`), these are written on the computer by a tool and landed
# by its own sync pull request on green, so the gate cannot hold them without holding every landing: a branch name
# proves nothing, so a cloud run's pull request lands them on green too. Each is named here with its writer; a new
# entry of PROTECTED_EDITS the gate does not hold fails the test below until it is held or named here.
LANDED_BY_THE_COMPUTER = {
    "_meta/state/backfill.yaml": "backfill.py, in the sweep chain",
    "_meta/state/routine-host.yaml": "a session's move or pause, which a cloud run's `begin` reads from main",
    "_meta/state/routine-limits.jsonl": "routine.py after each run, landed as housekeeping",
    "_meta/tests/**": "the sync's own repair of a failing answer test (`set_aside_tests`, `hold_failed`)",
}
# Loaded wherever they sit, not only at the root: Claude Code reads CLAUDE.md and CLAUDE.local.md beside any file it
# opens, and pytest a conftest.py in any folder it collects (gate.ANY_DEPTH holds these and more).
ANY_DEPTH = ("CLAUDE.md", "CLAUDE.local.md", "conftest.py")


def _git_ignores(rel: str) -> bool:
    done = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode in (0, 1), done.stderr
    return done.returncode == 0


def test_every_tracked_path_a_routine_may_not_edit_is_held_by_the_gate_or_named_as_the_computers(tmp_path):
    """What `sync.py --cloud` keeps out of a cloud run's landing (`routine.PROTECTED_EDITS` and
    `routine.PROTECTED_IN_ROOT`) a cloud run can still push on a branch of its own: each such path git tracks waits
    for the click in a space whose own list is empty, unless the computer's own sync lands it (named, with its writer).
    Git's own folder and what git ignores never reach a pull request."""
    import routine
    root = make_space(tmp_path / "ada", protected=[])
    open_to_a_run = set()
    for entry in (*routine.PROTECTED_EDITS, *routine.PROTECTED_IN_ROOT):
        if entry in (".git", ".git/**"):
            continue
        rel = entry[:-len("/**")] + "/example.md" if entry.endswith("/**") else entry
        if _git_ignores(rel):
            continue
        cls, reasons = gate.classify(root, [rel])
        if cls != "protected":
            open_to_a_run.add(entry)
        else:
            assert any(r.startswith(f"{rel}: protected by `") for r in reasons), (entry, reasons)
    assert open_to_a_run == set(LANDED_BY_THE_COMPUTER), (
        "held by neither the gate nor a computer's landing: "
        f"{sorted(open_to_a_run - set(LANDED_BY_THE_COMPUTER))}; "
        f"named but now held or gone: {sorted(set(LANDED_BY_THE_COMPUTER) - open_to_a_run)}")
    # the files loaded at any depth are among what a routine may not edit, so the nested check below covers the list
    assert set(ANY_DEPTH) <= set(routine.PROTECTED_EDITS)


@pytest.mark.parametrize("rel", ["work/clients/a/CLAUDE.local.md", "work/clients/a/CLAUDE.md",
                                 "_meta/conftest.py", "knowledge/topics/a/conftest.py", "_meta/pytest.ini",
                                 "work/clients/a/.claude/settings.json", "work/clients/a/.mcp.json",
                                 "work/clients/a/Claude.md"])
def test_an_instruction_file_below_the_root_is_protected_from_any_branch(tmp_path, rel):
    """A cloud run that pushes a CLAUDE.md beside a subject's desk, or a conftest.py in a folder pytest collects,
    instructs or runs code on the computer as surely as the root's: the gate holds it at any depth, in a space whose
    own list is empty, beside a page that alone would land."""
    assert any(n.casefold() in rel.casefold().split("/") for n in gate.ANY_DEPTH)
    root = make_space(tmp_path / "ada", protected=[])
    cls, reasons = gate.classify(root, [rel, "knowledge/topics/a/one.md"])
    assert cls == "protected", reasons
    assert any(r.startswith(f"{rel}: protected by `") for r in reasons), reasons


def test_a_name_that_only_contains_an_instruction_files_name_stays_auto_mergeable(tmp_path):
    """The gate holds a path segment that is the name, not a page whose name holds it."""
    root = make_space(tmp_path / "ada", protected=[])
    cls, reasons = gate.classify(root, ["knowledge/topics/a/claude.md-notes.md", "work/clients/a/my-conftest.py.md"])
    assert cls == "auto-mergeable", reasons


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


# ----- an approved template release lands on green; anything else waits ---------------------
PROV = "_meta/state/template-version.yaml"
RELEASE_FILES = {".claude/skills/sweep/SKILL.md": "# sweep v2\n", "_meta/tools/sweep.py": "print('v2')\n",
                 "docs/shape-1.md": "shape\n"}


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", message)
    return _git(root, "rev-parse", "HEAD").strip()


def _provenance(root: Path, **data) -> None:
    (root / PROV).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _edit_provenance(root: Path, **changes) -> None:
    data = yaml.safe_load((root / PROV).read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    _provenance(root, **data)


def _released(tmp_path, monkeypatch, tag="v0.2.0", base_files=None, template_extra=None, base_release=None,
              prepare=None):
    """A template tagged `tag`, a space whose base names it, and a pull request syncing it.
    `prepare(space)` runs before the base commit."""
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    _write(tpl, dict(RELEASE_FILES, **(template_extra or {})))
    _git(tpl, "init", "-q", "-b", "main")
    commit = _commit(tpl, "release")
    _git(tpl, "tag", "-a", tag, "-m", tag)
    space = make_space(tmp_path / "ada")
    _provenance(space, repo=str(tpl), commit="0" * 40, files={},
                **({"release": base_release} if base_release else {}))
    _write(space, base_files or {})
    if prepare:
        prepare(space)
    _git(space, "init", "-q", "-b", "main")
    base = _commit(space, "base")
    _write(space, RELEASE_FILES)
    _provenance(space, repo=str(tpl), release=tag, commit=commit,
                approved={"release": tag, "commit": commit, "by": "Ada Okafor",
                          "at": "2026-10-18T09:00:00Z", "in": "ada"}, files={})
    _commit(space, "sync")
    return tpl, space, base, commit


def _gate(space: Path, base: str):
    return gate.classify(space, gate.changed_files(space, base), base_ref=base)


def test_a_release_matched_sync_lands_on_green(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch)
    cls, reasons = _gate(space, base)
    assert cls == "auto-mergeable", reasons
    assert any("release v0.2.0 approved by Ada Okafor" in r for r in reasons)


def test_a_release_never_carries_what_runs_on_the_computer(tmp_path, monkeypatch):
    """An approved release lands only machinery the template installs: a rendered routine prompt, the task records,
    an MCP config or a file pytest reads at its start, byte for byte the template's own, still waits for the click,
    from whatever branch the pull request comes."""
    outside = [rel for rel in RUNS_ON_THE_COMPUTER if not gate._machinery(rel)]
    assert "_meta/migrations/0007-move-pages.py" not in outside   # migrations are machinery a release installs
    assert len(outside) == len(RUNS_ON_THE_COMPUTER) - 1
    extra = {rel: "# the template's own\n" for rel in outside}
    _, space, base, _ = _released(tmp_path, monkeypatch, template_extra=extra)
    for rel in outside:
        _write(space, {rel: extra[rel]})
        _commit(space, f"take {rel}")
        for branch in ("cloud/sweep", "sync/m-desktop"):
            monkeypatch.setenv("GITHUB_HEAD_REF", branch)
            cls, reasons = _gate(space, base)
            assert cls == "protected", (rel, branch, reasons)
            assert any(r.startswith(f"{rel}: not machinery") for r in reasons), (rel, reasons)
        (space / rel).unlink()
        _commit(space, "back")
    # without them, the same release still lands on green
    cls, reasons = _gate(space, base)
    assert cls == "auto-mergeable", reasons


AGENTS_TPL = "_meta/templates/AGENTS.md.tpl"
OLDER_AGENTS_MD = {"AGENTS.md": "# Operating contract, as an older template rendered it\n"}


def _agents_md(space: Path, text: str | None = None) -> str:
    """AGENTS.md written into the space: `text`, else what agents_md.py renders from its files."""
    text = agents_md.render(space) if text is None else text
    (space / "AGENTS.md").write_text(text, encoding="utf-8", newline="\n")
    return text


def test_agents_md_rendered_from_the_release_lands_with_it(tmp_path, monkeypatch):
    # the release changes the template AGENTS.md is rendered from; the space regenerates it
    tpl_text = (agents_md.TEMPLATES / agents_md.TEMPLATE_NAME).read_text(encoding="utf-8") \
        + "\nA line release v0.2.0 adds.\n"
    _, space, base, _ = _released(tmp_path, monkeypatch, base_files=OLDER_AGENTS_MD,
                                  template_extra={AGENTS_TPL: tpl_text})
    _write(space, {AGENTS_TPL: tpl_text})
    _agents_md(space)
    _commit(space, "the release's template, and AGENTS.md from it")
    assert "A line release v0.2.0 adds." in (space / "AGENTS.md").read_text(encoding="utf-8")
    cls, reasons = _gate(space, base)
    assert cls == "auto-mergeable", reasons
    assert any("release v0.2.0 approved by Ada Okafor" in r and "AGENTS.md as agents_md.py renders it" in r
               for r in reasons)


def test_any_other_agents_md_still_needs_the_click(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch, base_files=OLDER_AGENTS_MD)
    rendered = _agents_md(space)
    _commit(space, "AGENTS.md as rendered")
    assert _gate(space, base)[0] == "auto-mergeable"
    # an edit by hand
    _agents_md(space, rendered + "\nAn instruction nobody rendered.\n")
    _commit(space, "edited")
    cls, reasons = _gate(space, base)
    assert cls == "protected"
    assert any(r.startswith("AGENTS.md: not what agents_md.py renders") for r in reasons), reasons
    # rendered from a space.yaml the pull request does not carry
    original = (space / "space.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(original)
    (space / "space.yaml").write_text(yaml.safe_dump(dict(data, models=["claude"]), sort_keys=False),
                                      encoding="utf-8")
    _agents_md(space)
    (space / "space.yaml").write_text(original, encoding="utf-8")
    _commit(space, "rendered elsewhere")
    cls, reasons = _gate(space, base)
    assert cls == "protected"
    assert any(r.startswith("AGENTS.md: not what agents_md.py renders") for r in reasons), reasons
    # rendered from a space.yaml the pull request changes: space.yaml is no release file
    (space / "space.yaml").write_text(yaml.safe_dump(dict(data, models=["claude"]), sort_keys=False),
                                      encoding="utf-8")
    _commit(space, "space.yaml too")
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any(r.startswith("space.yaml: not machinery") for r in reasons), reasons
    # gone
    (space / "space.yaml").write_text(original, encoding="utf-8")
    (space / "AGENTS.md").unlink()
    _commit(space, "dropped")
    cls, reasons = _gate(space, base)
    assert cls == "protected"
    assert any(r.startswith("AGENTS.md: not what agents_md.py renders") for r in reasons), reasons


def test_agents_md_alone_lands_on_a_space_already_at_the_release(tmp_path, monkeypatch):
    # the release is on main; a later status run regenerates AGENTS.md, which lands on its own
    _, space, _, _ = _released(tmp_path, monkeypatch, base_files=OLDER_AGENTS_MD)
    base = _git(space, "rev-parse", "HEAD").strip()
    rendered = _agents_md(space)
    _commit(space, "AGENTS.md as the status run writes it")
    assert gate.changed_files(space, base) == ["AGENTS.md"]
    cls, reasons = _gate(space, base)
    assert cls == "auto-mergeable", reasons
    assert "release v0.2.0 approved by Ada Okafor: AGENTS.md as agents_md.py renders it" in reasons
    _agents_md(space, rendered + "\nAn instruction nobody rendered.\n")
    _commit(space, "edited")
    assert _gate(space, base)[0] == "protected"


def _brain_declaring(*extra):
    """A `prepare` for `_released`: a brain mount whose kinds add `extra` to the defaults, out of
    the space's own commits as a mount's files are, and AGENTS.md as the space's status run writes
    it with that mount in place."""
    def prepare(space: Path) -> None:
        make_space(space / "brain", profile="brain", kinds=[*spaceyaml.DEFAULTS["kinds"], *extra])
        data = yaml.safe_load((space / "space.yaml").read_text(encoding="utf-8"))
        data["mounts"] = [{"name": "brain", "path": "brain"}]
        (space / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _write(space, {".gitignore": "brain/\n"})
        _agents_md(space)
    return prepare


def test_agents_md_with_the_brains_kinds_lands_with_a_release(tmp_path, monkeypatch):
    # the gate's checkout has no mount, so a space that inherits its kinds is rendered with the
    # kinds its AGENTS.md on the base branch lists
    tpl_text = (agents_md.TEMPLATES / agents_md.TEMPLATE_NAME).read_text(encoding="utf-8") \
        + "\nA line release v0.2.0 adds.\n"
    _, space, base, _ = _released(tmp_path, monkeypatch, template_extra={AGENTS_TPL: tpl_text},
                                  prepare=_brain_declaring("deals"))
    assert "- `deals`\n" in _git(space, "show", f"{base}:AGENTS.md")
    _write(space, {AGENTS_TPL: tpl_text})
    _agents_md(space)
    _commit(space, "the release's template, and AGENTS.md from it")
    assert "brain/space.yaml" not in _git(space, "ls-files")
    cls, reasons = _gate(space, base)
    assert cls == "auto-mergeable", reasons
    # a kind the brain adds later is on no base branch yet: that AGENTS.md waits for the click
    brain = yaml.safe_load((space / "brain/space.yaml").read_text(encoding="utf-8"))
    (space / "brain/space.yaml").write_text(
        yaml.safe_dump(dict(brain, kinds=[*brain["kinds"], "grants"]), sort_keys=False), encoding="utf-8")
    _agents_md(space)
    _commit(space, "a kind the brain added")
    cls, reasons = _gate(space, base)
    assert cls == "protected"
    assert any(r.startswith("AGENTS.md: not what agents_md.py renders") for r in reasons), reasons


def test_a_space_yaml_without_a_name_never_matches(tmp_path, monkeypatch):
    # its AGENTS.md is named after its folder, which the gate's checkout does not share
    _, space, _, _ = _released(tmp_path, monkeypatch)
    _agents_md(space)
    _commit(space, "rendered")
    assert gate.rendered_at(space, "HEAD")
    data = yaml.safe_load((space / "space.yaml").read_text(encoding="utf-8"))
    data.pop("name")
    (space / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _agents_md(space)
    _commit(space, "nameless")
    assert (space / "AGENTS.md").read_text(encoding="utf-8").startswith("# Operating contract: ada\n")
    assert not gate.rendered_at(space, "HEAD")


def test_a_tampered_protected_file_still_needs_the_click(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch)
    (space / ".claude/skills/sweep/SKILL.md").write_text("# sweep v2!\n", encoding="utf-8")
    _commit(space, "tamper")
    cls, reasons = _gate(space, base)
    assert cls == "protected"
    assert any(".claude/skills/sweep/SKILL.md: not byte-identical to release v0.2.0" in r for r in reasons)


def test_no_approval_or_someone_elses_or_another_commits_needs_the_click(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch)
    approved = yaml.safe_load((space / PROV).read_text(encoding="utf-8"))["approved"]
    for change in ({"approved": None}, {"approved": dict(approved, by="Ben Liu")},
                   {"approved": dict(approved, commit="1" * 40)},
                   {"approved": dict(approved, release="v0.3.0")}):
        _edit_provenance(space, **change)
        _commit(space, "edit")
        assert _gate(space, base)[0] == "protected", change
        _edit_provenance(space, approved=approved)
    _commit(space, "restored")
    assert _gate(space, base)[0] == "auto-mergeable"


def test_the_owner_is_matched_ignoring_case_and_spacing(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch)
    approved = yaml.safe_load((space / PROV).read_text(encoding="utf-8"))["approved"]
    _edit_provenance(space, approved=dict(approved, by="ada  okafor"))
    _commit(space, "name")
    assert _gate(space, base)[0] == "auto-mergeable"


def test_a_moved_tag_needs_the_click(tmp_path, monkeypatch):
    tpl, space, base, _ = _released(tmp_path, monkeypatch)
    (tpl / "_meta/tools/sweep.py").write_text("print('v2, moved')\n", encoding="utf-8")
    _commit(tpl, "later")
    _git(tpl, "tag", "-f", "-a", "v0.2.0", "-m", "moved")
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any("now points at" in r for r in reasons)


def test_a_protected_file_of_its_own_or_a_deletion_needs_the_click(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch, base_files={".claude/settings.json": "{}\n"})
    assert _gate(space, base)[0] == "auto-mergeable"
    _write(space, {".claude/skills/own/SKILL.md": "# ours\n"})
    _commit(space, "own skill")
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any(r.startswith(".claude/skills/own/SKILL.md: not byte") for r in reasons)
    (space / ".claude/skills/own/SKILL.md").unlink()
    (space / ".claude/settings.json").unlink()
    _commit(space, "drop settings")
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any(r.startswith(".claude/settings.json: not byte") for r in reasons)


def test_the_rules_come_from_the_base_not_the_pull_request(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch)
    data = yaml.safe_load((space / "space.yaml").read_text(encoding="utf-8"))
    (space / "space.yaml").write_text(yaml.safe_dump(dict(data, protected=[]), sort_keys=False), encoding="utf-8")
    _write(space, {".github/template-marker": "", "_meta/tools/gate.py": "print('GATE: auto-mergeable')\n"})
    _commit(space, "loosen the gate")
    cls, reasons = _gate(space, base)
    assert cls == "protected"
    for rel in ("space.yaml", ".github/template-marker", "_meta/tools/gate.py"):
        assert any(r.startswith(f"{rel}: ") for r in reasons), rel


def test_a_changed_template_name_needs_the_click(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch)
    # alone: nothing else protected changes, and the name is protected by itself
    _git(space, "checkout", "-q", "-b", "rename", base)
    _edit_provenance(space, repo="someone/else")
    _commit(space, "rename")
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any("the template's name changed" in r for r in reasons)
    # with a "template" of an agent's own making, whose release holds the changed file
    _git(space, "checkout", "-q", "main")
    fake = tmp_path / "fake"
    _write(fake, dict(RELEASE_FILES, **{"_meta/tools/sweep.py": "print('mine')\n"}))
    _git(fake, "init", "-q", "-b", "main")
    fake_commit = _commit(fake, "fake")
    _git(fake, "tag", "-a", "v0.2.0", "-m", "v0.2.0")
    _write(space, {"_meta/tools/sweep.py": "print('mine')\n"})
    _edit_provenance(space, repo=str(fake), commit=fake_commit,
                     approved={"release": "v0.2.0", "commit": fake_commit, "by": "Ada Okafor"})
    _commit(space, "fake release")
    assert _gate(space, base)[0] == "protected"


def test_a_failed_fetch_says_why_and_needs_the_click(tmp_path, monkeypatch):
    import shutil
    tpl, space, base, _ = _released(tmp_path, monkeypatch)
    shutil.rmtree(tpl, onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any("could not be fetched" in r for r in reasons)


def test_only_machinery_the_template_installs_can_match(tmp_path, monkeypatch):
    # the template's own copies of the rules, the settings and the marker are in its tree too
    extra = {"rules.md": "# Rules of the template\n", ".github/template-marker": "",
             "identity/charter.md": "# Charter\n"}
    _, space, base, _ = _released(tmp_path, monkeypatch, template_extra=extra)
    for rel, text in extra.items():
        _write(space, {rel: text})
        _commit(space, f"take {rel}")
        cls, reasons = _gate(space, base)
        assert cls == "protected" and any(r.startswith(f"{rel}: not machinery") for r in reasons), rel
        (space / rel).unlink()
        _commit(space, "back")


def test_an_older_release_than_the_base_needs_the_click(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch, base_release="v0.3.0")
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any("older than v0.3.0" in r for r in reasons)


def test_a_release_files_id_under_another_mode_needs_the_click(tmp_path, monkeypatch):
    _, space, base, _ = _released(tmp_path, monkeypatch)
    rel = ".claude/skills/sweep/SKILL.md"
    sha = _git(space, "rev-parse", f"HEAD:{rel}").strip()
    _git(space, "rm", "-q", "--cached", rel)
    _git(space, "update-index", "--add", "--cacheinfo", f"160000,{sha},{rel}")
    _git(space, "commit", "-q", "-m", "a submodule entry with the file's id")
    cls, reasons = _gate(space, base)
    assert cls == "protected" and any(r.startswith(f"{rel}: not byte-identical") for r in reasons)


def test_a_moved_or_renamed_protected_path_needs_the_click(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    space = make_space(tmp_path / "ada")
    _write(space, {"_meta/tools/guard.py": "print('guard')\n"})
    _git(space, "init", "-q", "-b", "main")
    base = _commit(space, "base")
    _git(space, "mv", "_meta/tools/guard.py", "docs-guard.py")
    _commit(space, "move")
    assert "_meta/tools/guard.py" in gate.changed_files(space, base)
    assert _gate(space, base)[0] == "protected"
    # a path that is the protected folder itself (a link in its place), or differs only in case
    for rel in (".claude", ".Claude/skills/x/SKILL.md", "Space.yaml"):
        assert gate.classify(space, [rel])[0] == "protected", rel


def test_the_template_name_is_never_a_folder_in_the_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "o" / "n").mkdir(parents=True)
    assert gate.template_url("o/n") == "https://github.com/o/n"
    assert gate.template_url(str(tmp_path / "o" / "n")) == str(tmp_path / "o" / "n")


def test_the_fetch_drops_the_checkout_token_and_adds_the_template_token():
    env = gate._fetch_env("https://github.com/o/n", "t0k")
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull   # the job's url rewrite carries a mount token
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == env["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"] == ""
    assert env["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic ") and "t0k" not in env["GIT_CONFIG_VALUE_1"]
    assert gate._fetch_env("https://github.com/o/n", "")["GIT_CONFIG_COUNT"] == "1"
    assert "GIT_CONFIG_COUNT" not in gate._fetch_env("/some/path", "t0k")
    assert gate.template_url("o/n") == "https://github.com/o/n"
    assert gate.template_url("file:///x/y") == "file:///x/y"


CHECKS = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "checks.yml"


def _jobs() -> dict:
    return yaml.safe_load(CHECKS.read_text(encoding="utf-8"))["jobs"]


def _step(job: str, name: str) -> dict:
    return next(s for s in _jobs()[job]["steps"] if s.get("name") == name)


def test_checks_runs_the_gate_from_the_base_branch():
    for job, name, tree in (("arm", "Gate (protected paths and signatures)", '"$RUNNER_TEMP/pr"'),
                            ("checks", "Gate (signatures)", ".")):
        step = _step(job, name)
        assert step["env"]["BASE_REF"] == "${{ github.base_ref }}"
        assert 'git archive "origin/${BASE_REF}" _meta/tools | tar -x -C "$RUNNER_TEMP/base"' in step["run"]
        assert f'python "$RUNNER_TEMP/base/_meta/tools/gate.py" "origin/${{BASE_REF}}" {tree}' in step["run"]
    # only the arming job, which runs no code of the pull request, holds the template token
    token = _step("arm", "Gate (protected paths and signatures)")["env"]["TEMPLATE_TOKEN"]
    assert "secrets.TEMPLATE_READ_TOKEN || secrets.BRAIN_READ_TOKEN || secrets.MOUNT_READ_TOKEN" in token
    assert "TEMPLATE_TOKEN" not in _step("checks", "Gate (signatures)").get("env", {})


def test_the_gate_step_fails_on_the_gates_exit_code():
    # gate.py exits 1 on a signature error; piped into tee, only pipefail lets that fail the job.
    for job, name in (("arm", "Gate (protected paths and signatures)"), ("checks", "Gate (signatures)")):
        lines = [line.strip() for line in _step(job, name)["run"].splitlines()]
        tee = next(i for i, line in enumerate(lines) if "gate.py" in line and '| tee "$RUNNER_TEMP/gate.txt"' in line)
        assert "set -o pipefail" in lines[:tee]


def test_the_pull_requests_code_never_runs_with_the_write_token():
    jobs = _jobs()
    assert jobs["checks"]["permissions"] == {"contents": "read"}
    assert all(not str(v).endswith("write") for v in (jobs["checks"].get("permissions") or {}).values())
    checkout = next(s for s in jobs["checks"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    arm = jobs["arm"]
    assert arm["needs"] == "checks"
    assert "github.event.pull_request.head.repo.full_name == github.repository" in arm["if"]
    first = arm["steps"][0]
    assert first["uses"].startswith("actions/checkout@")
    assert first["with"]["ref"] == "${{ github.base_ref }}" and first["with"]["persist-credentials"] is False
    # the arming job runs the base branch's tools only: no step runs a file from the checkout
    runs = "\n".join(s.get("run", "") for s in arm["steps"])
    assert "python _meta/" not in runs and "python -m pytest" not in runs
    gate_index = next(i for i, s in enumerate(arm["steps"]) if s.get("name") == "Gate (protected paths and signatures)")
    arming = next(i for i, s in enumerate(arm["steps"]) if s.get("name") == "Arm auto-merge, or leave it for the owner")
    assert gate_index < arming


def test_no_token_is_written_to_a_config_file_and_no_expression_runs_in_a_shell():
    text = CHECKS.read_text(encoding="utf-8")
    assert "git config --global" not in text
    for job in _jobs().values():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "${{ github.base_ref" not in run and "${{ github.event." not in run, step.get("name")
            uses = step.get("uses", "")
            if uses:
                ref = uses.split("@", 1)[1]
                assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses


# A stand-in for gh: the repository allows FAKE_ALLOWS (what the api call prints), the first
# FAKE_REFUSALS merge calls are refused the way GitHub refuses while the base branch moves, and a
# method outside FAKE_METHODS is refused for good. sleep only records how long it was asked to wait.
FAKE_GH = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
if [ "$1" = api ]; then
  [ "$FAKE_ALLOWS" = fail ] && exit 1
  echo "$FAKE_ALLOWS"
  exit 0
fi
case " $* " in *" --disable-auto "*) exit 0 ;; esac
n=$(cat "$FAKE_COUNT" 2>/dev/null || echo 0)
echo $((n + 1)) > "$FAKE_COUNT"
if [ "$n" -lt "$FAKE_REFUSALS" ]; then
  echo "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)" >&2
  exit 1
fi
for m in $FAKE_METHODS; do case " $* " in *" --$m "*) exit 0 ;; esac; done
echo "Merge method not allowed on this repository" >&2
exit 1
"""


def _arm(tmp_path, gate_line, allows, methods, refusals=0):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_bytes(FAKE_GH.encode())
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    (tmp_path / "gate.txt").write_text(gate_line + "\n", encoding="utf-8")
    log = tmp_path / "log.txt"
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], RUNNER_TEMP=str(tmp_path),
               GITHUB_REPOSITORY="owner/space", PR_URL="https://github.com/owner/space/pull/7",
               FAKE_LOG=str(log), FAKE_COUNT=str(tmp_path / "count"), FAKE_ALLOWS=allows,
               FAKE_METHODS=methods, FAKE_REFUSALS=str(refusals))
    script = _step("arm", "Arm auto-merge, or leave it for the owner")["run"]
    # Actions runs a step's script as bash -eo pipefail
    # sleep is a function: it shadows a sleep program or builtin on every platform (Windows runners)
    fake_sleep = 'sleep() { echo "sleep $1" >> "$FAKE_LOG"; }\n'
    done = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", fake_sleep + script],
                          env=env, capture_output=True, text=True)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, [c for c in calls if c.startswith("pr merge")], [c for c in calls if c.startswith("sleep")]


def test_arming_retries_a_moving_base_with_a_method_the_repository_allows(tmp_path):
    # Seen live: five pull requests went green at once, the direct merge of one was refused because
    # the base had just moved, and the fallbacks used methods the repository does not allow.
    done, merges, sleeps = _arm(tmp_path, "GATE: auto-mergeable", "true false false", "merge", refusals=2)
    assert done.returncode == 0, done.stderr
    assert merges == ["pr merge --auto --merge https://github.com/owner/space/pull/7"] * 3
    assert len(sleeps) >= 2 and any(s != "sleep 0" for s in sleeps)


@pytest.mark.parametrize("allows, methods, used", [
    ("false true false", "squash", ["--squash"]),
    ("false false true", "rebase", ["--rebase"]),
    # the allowed methods cannot be read: each is tried in turn, a merge commit first
    ("fail", "rebase", ["--merge", "--squash", "--rebase"]),
])
def test_arming_asks_the_repository_which_method_to_use(tmp_path, allows, methods, used):
    done, merges, _ = _arm(tmp_path, "GATE: auto-mergeable", allows, methods)
    assert done.returncode == 0, done.stderr
    assert [m.split()[3] for m in merges] == used


def test_arming_gives_up_after_a_few_tries_and_says_so(tmp_path):
    done, merges, sleeps = _arm(tmp_path, "GATE: auto-mergeable", "true false false", "merge", refusals=99)
    assert done.returncode != 0
    assert 2 <= len(merges) <= 6 and all("--merge" in m for m in merges)
    assert "::error::" in done.stdout


def test_a_protected_pull_request_is_never_merged(tmp_path):
    done, merges, _ = _arm(tmp_path, "GATE: needs the owner", "true true true", "merge squash rebase")
    assert done.returncode == 0, done.stderr
    assert merges == ["pr merge --disable-auto https://github.com/owner/space/pull/7"]
