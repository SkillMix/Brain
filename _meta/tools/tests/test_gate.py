"""gate.py: protected paths and signatures at the landing gate."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

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


# ----- an approved template release lands on green; anything else waits ---------------------
import yaml  # noqa: E402

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


def _released(tmp_path, monkeypatch, tag="v0.2.0", base_files=None, template_extra=None, base_release=None):
    """A template tagged `tag`, a space whose base names it, and a pull request syncing it."""
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
    for change in ({"approved": None}, {"approved": dict(approved, by="Bea Lund")},
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
    (bin_dir / "sleep").write_bytes(b'#!/bin/sh\necho "sleep $1" >> "$FAKE_LOG"\n')
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
    done = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
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
