"""Security regressions for the gate, the workflows, template sync, the release archive and the
behaviour scenarios (findings 9, 11, 21, 24, 25, 27, 28, 45, 47-52, 57, 64), with the bypasses a re-test found."""
from __future__ import annotations

import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

import behaviour
import gate
import template_archive
import spaceyaml
import start
import template_sync
from conftest import make_space, write_page

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
# Every space runs these tests in its checks, but template-sync never carries the requirements files or
# .github/CODEOWNERS into one: a test that reads them runs in the template only.
template_only = pytest.mark.skipif(not spaceyaml.is_template_repo(REPO), reason="reads a file only the template has")


def _wf(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    return subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8").stdout


@pytest.fixture
def quiet_git(tmp_path, monkeypatch):
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


# ----- the gate's minimum: control state, what pytest reads, what runs (21, 24, 45) -----------------------------
NEWLY_HELD = ("_meta/state/invitations/dana.yaml", "_meta/access/resources.yaml", "_meta/state/template-origin.yaml",
              "_meta/tests/behaviour/evil.yaml", "docs/shape-1.md", ".pytest.ini", "pytest.toml", ".pytest.toml",
              "requirements.txt", "requirements-dev.txt",
              "sub/.pytest.ini", "sub/pytest.toml", "work/x/AGENTS.md", "work/x/.gitattributes",
              "yaml.py", "pytest.py", "sitecustomize.py", "pkg/__init__.py")


@pytest.mark.parametrize("rel", NEWLY_HELD)
def test_control_state_and_what_runs_wait_for_the_click(tmp_path, rel):
    root = make_space(tmp_path / "ada", protected=[])
    cls, reasons = gate.classify(root, [rel, "knowledge/topics/a/one.md"])
    assert cls == "protected", reasons
    assert any(r.startswith(f"{rel}: protected") for r in reasons), reasons


@pytest.mark.parametrize("rel", ["_meta/tests/answers.yaml", "_meta/tests/leaks.yaml", "work/clients/a/b/script.py",
                                 "work/a/v1.2/notes.md", "knowledge/clients/acme/a..b.md", "_meta/subjects.yaml",
                                 "_meta/state/classify.jsonl", "knowledge/topics/a/agents.md-notes.md"])
def test_what_the_routines_land_every_day_stays_auto_mergeable(tmp_path, rel):
    root = make_space(tmp_path / "ada", protected=[])
    cls, reasons = gate.classify(root, [rel])
    assert cls == "auto-mergeable", reasons


# ----- names another system reads as another path (11) ------------------------------------------------------
ALIASES = ("_meta/tools./sync.py", "_meta/tools /sync.py", "rules.md.", "rules.md ", ".claude./settings.json",
           ".claude /settings.json", "CLAUDE~1/settings.local.json", "CLAUDE~1/skills/x/SKILL.md",
           "work/a/CLAUDE~1/settings.json", "GITHUB~1/workflows/x.yml", ".claude::$INDEX_ALLOCATION/x.json",
           "CLAUDE.md:stream", "work/CON/x.md", "work/nul.txt", "work/a\\b.md", "work/x/CLAUDE.md.",
           "work/a\x01b.md")


@pytest.mark.parametrize("rel", ALIASES)
def test_a_windows_alias_of_a_path_is_protected(tmp_path, rel):
    root = make_space(tmp_path / "ada", protected=[])
    cls, reasons = gate.classify(root, [rel])
    assert cls == "protected", (rel, reasons)
    assert gate.alias_reason(rel) or gate.protected_reason(rel, gate.MINIMUM_PROTECTED)


def test_a_tilde_in_an_ordinary_name_is_no_short_name():
    for rel in ("knowledge/sources/2026/10/report~1.pdf.md", "work/a/notes~draft.md"):
        assert gate.alias_reason(rel) is None, rel


# ----- the template: machinery lands on green, the release tag is the human gate (28) -------------------------
def test_the_template_lands_its_machinery_and_holds_only_odd_names(tmp_path):
    root = make_space(tmp_path / "t", profile="template")
    for rel in (".github/workflows/checks.yml", "_meta/tools/gate.py", ".claude/settings.json", "rules.md"):
        cls, reasons = gate.classify(root, [rel, "docs/decisions.md"])
        assert cls == "auto-mergeable", (rel, reasons)
    # what no legitimate change carries still waits for a maintainer's merge
    for rel in ("CLAUDE~1/settings.json", "yaml.py", "_meta/tools/__pycache__/gate.cpython-312.pyc", "rules.md."):
        cls, reasons = gate.classify(root, [rel, "docs/decisions.md"])
        assert cls == "protected", (rel, reasons)
        assert "maintainer" in reasons[0]
    assert gate.classify(root, ["docs/decisions.md", "CHANGELOG.md"])[0] == "auto-mergeable"


# ----- the provenance cannot freeze or rewind a space (9) -----------------------------------------------------
PROV = "_meta/state/template-version.yaml"


def _space_repo(tmp_path, prov: dict) -> tuple[Path, str]:
    space = make_space(tmp_path / "ada")
    (space / PROV).write_text(yaml.safe_dump(prov, sort_keys=False), encoding="utf-8")
    _git(space, "init", "-q", "-b", "main")
    _git(space, "add", "-A")
    _git(space, "commit", "-q", "-m", "base")
    return space, _git(space, "rev-parse", "HEAD").strip()


def test_a_release_named_in_the_provenance_alone_needs_the_click(tmp_path, quiet_git):
    space, base = _space_repo(tmp_path, {"repo": str(tmp_path / "no-template"), "release": "v0.1.0",
                                         "commit": "a" * 40, "files": {}})
    for change in ({"release": "v99.0.0"}, {"release": "v0.0.1"}, {"commit": "b" * 40}):
        data = yaml.safe_load((space / PROV).read_text(encoding="utf-8"))
        data.update(change)
        (space / PROV).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        _git(space, "commit", "-q", "-am", f"edit {change}")
        cls, reasons = gate.classify(space, gate.changed_files(space, base), base_ref=base)
        assert cls == "protected", (change, reasons)
        assert any("the template release it records changed" in r for r in reasons), reasons
        _git(space, "reset", "-q", "--hard", base)
    # the time of a sync alone is no release change
    data = yaml.safe_load((space / PROV).read_text(encoding="utf-8"))
    data["synced_at"] = "2026-11-01T00:00:00Z"
    (space / PROV).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _git(space, "commit", "-q", "-am", "synced again")
    assert gate.classify(space, gate.changed_files(space, base), base_ref=base)[0] == "auto-mergeable"


# ----- signatures: zones and suffix ignore case (64) ------------------------------------------------------------
def test_a_case_variant_zone_or_suffix_is_still_checked(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "Knowledge" / "topics" / "a").mkdir(parents=True, exist_ok=True)
    (root / "Knowledge" / "topics" / "a" / "x.md").write_text("---\nstatus: stable\n---\nNo signature.\n",
                                                               encoding="utf-8")
    write_page(root, "knowledge/topics/a/y.MD", status="stable", body="No signature.\n")
    for rel in ("Knowledge/topics/a/x.md", "knowledge/topics/a/y.MD"):
        errs = gate.check_signatures(root, [rel])
        assert any("spelled unlike" in e for e in errs), (rel, errs)
        assert any("signed_by" in e for e in errs), (rel, errs)
    (root / "Live").mkdir(exist_ok=True)   # on a system that ignores case it is live/ itself
    (root / "Live" / "s.md").write_text("---\nstatus: stable\n---\nState.\n", encoding="utf-8")
    assert any("never signed" in e for e in gate.check_signatures(root, ["Live/s.md"]))


# ----- the workflows ------------------------------------------------------------------------------------------
PR_CODE = re.compile(r"python(3)? (-I )?(-m pytest|_meta/tools/)")


def _steps(job: dict) -> list:
    return job.get("steps") or []


def test_no_job_that_runs_the_pull_requests_code_names_a_secret():
    """GitHub hands a job every secret it names, and a hosted runner gives its steps sudo: a job that runs the pull
    request's code holds no secret but the template's list of name hashes (25)."""
    for name, job in _wf("checks.yml")["jobs"].items():
        steps = _steps(job)
        runs_pr_code = any(PR_CODE.search(s.get("run", "")) for s in steps)
        named = {m for s in steps for m in re.findall(r"secrets\.([A-Z0-9_]+)", json.dumps(s))}
        if runs_pr_code:
            assert named <= {"FORBIDDEN_NAMES_SHA256"}, (name, named)
    mounts = _wf("checks.yml")["jobs"]["mounts"]
    assert not any(PR_CODE.search(s.get("run", "")) for s in _steps(mounts))
    assert "MOUNT_READ_TOKEN" in json.dumps(mounts)


def test_mounts_follow_the_default_branchs_gitmodules():
    mounts = _wf("checks.yml")["jobs"]["mounts"]
    step = next(s for s in _steps(mounts) if s.get("name") == "Check out mounts")
    run = step["run"]
    assert step["env"]["TOKEN"].startswith("${{ secrets.MOUNT_READ_TOKEN ||")
    assert 'git show "$ref:.gitmodules"' in run and 'cp "$RUNNER_TEMP/base.gitmodules" .gitmodules' in run
    # every plain https://github.com/<owner>/<name> of the base copy, pinned in that form (round 3: a workspace in
    # a person's own account mounts the organisation's brain); the behaviour is test_security_round3_onboarding's
    assert 'git config "submodule.$sub.url" "https://github.com/$owner/$repo"' in run and "--recursive" not in run
    assert "^[A-Za-z0-9][A-Za-z0-9-]{0,38}$" in run and "protocol.allow=never" in run and "$OWNER" not in run
    assert step["env"]["DEPLOY_KEYS"] == "${{ secrets.MOUNT_DEPLOY_KEYS }}"
    names = [s.get("name") for s in _steps(mounts)]
    assert names.index("Fetch the default branch") < names.index("Check out mounts")
    validate = next(s for s in _steps(mounts) if s.get("name") == "Validate and test with the mounts")
    assert 'python -I "$RUNNER_TEMP/base/_meta/tools/validate.py" .' in validate["run"]
    # the required check fails, never skips, when the mounts job did not pass
    checks = _wf("checks.yml")["jobs"]["checks"]
    assert checks["needs"] == "mounts" and checks["if"].startswith("!cancelled()")
    first = _steps(checks)[0]
    assert "needs.mounts.result" in first["env"]["MOUNTS"] and "exit 1" in first["run"]


def test_the_secrets_scan_is_the_base_branchs():
    step = next(s for s in _steps(_wf("checks.yml")["jobs"]["checks"]) if s.get("name") == "Secrets scan on what changed")
    assert 'python -I "$RUNNER_TEMP/base/_meta/tools/scan.py"' in step["run"] and "--root ." in step["run"]
    assert not re.search(r"(^|\s)python( -I)? _meta/tools/scan\.py", step["run"])
    names = [s.get("name") for s in _steps(_wf("checks.yml")["jobs"]["checks"])]
    assert names.index("Gate (signatures)") < names.index("Secrets scan on what changed")


def test_only_a_pull_request_into_the_default_branch_is_judged_and_armed():
    jobs = _wf("checks.yml")["jobs"]
    for name in ("gate", "arm"):
        assert "github.base_ref == github.event.repository.default_branch" in jobs[name]["if"], name
        text = json.dumps(jobs[name]["steps"])
        assert "BASE_REF" not in text and "base_ref" not in text, name
    assert jobs["gate"]["steps"][0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"


def test_the_merge_is_pinned_to_the_head_that_was_judged():
    arm = _wf("checks.yml")["jobs"]["arm"]
    assert arm["concurrency"]["group"] == "arm-${{ github.event.pull_request.number }}"
    assert arm["concurrency"]["cancel-in-progress"] is True
    step = arm["steps"][0]
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    merges = [line for line in step["run"].splitlines() if "gh pr merge --auto" in line]
    assert merges and all('--match-head-commit "$HEAD_SHA"' in line for line in merges)
    assert step["env"]["VERDICT"] == "${{ needs.gate.outputs.verdict }}"


def _requirement_blocks(text: str) -> list[str]:
    """Each requirement of a requirements text, with its continuation lines joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [line.strip() for line in joined.splitlines()
            if line.strip() and not line.strip().startswith(("#", "-r "))]


def test_every_install_is_pinned_by_hash():
    for path in WORKFLOWS.glob("*.yml"):
        for job in _wf(path.name)["jobs"].values():
            for step in _steps(job):
                run = step.get("run", "")
                if "pip install" not in run:
                    continue
                assert "--require-hashes" in run, (path.name, run)
                for heredoc in re.findall(r"<<'EOF'\n(.*?)\nEOF", run, re.S):
                    for req in _requirement_blocks(heredoc):
                        assert re.match(r"^[A-Za-z0-9._-]+==[0-9][^ ;]*( |;)", req) and "--hash=sha256:" in req, req


@template_only
def test_the_requirements_files_are_pinned_by_hash():
    for name in ("requirements.txt", "requirements-dev.txt"):
        for req in _requirement_blocks((REPO / name).read_text(encoding="utf-8")):
            assert re.match(r"^[A-Za-z0-9._-]+==[0-9]", req) and re.search(r"--hash=sha256:[0-9a-f]{64}", req), req


def test_pytest_never_runs_with_the_checkout_on_the_path():
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"python3? -m pytest", text), path.name


def test_a_release_is_a_release_name_on_the_default_branch():
    release = _wf("release.yml")
    tags = release[True]["push"]["tags"]
    assert "v*" not in tags and all(t.startswith("v[0-9]") for t in tags)
    steps = release["jobs"]["release"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    check = next(s for s in steps if s.get("name") == "The tag is a release name on the default branch")
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" "origin/${DEFAULT_BRANCH}"' in check["run"]
    names = [s.get("name") for s in steps]
    assert names.index("The tag is a release name on the default branch") < names.index("Build the archive and its checksum")


@template_only
def test_codeowners_names_every_path_the_gate_holds():
    lines = [line.split()[0] for line in (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    covered = {p.strip("/").casefold() for p in lines}
    for entry in gate.MINIMUM_PROTECTED:
        assert entry.strip("/").casefold() in covered, entry


# ----- template sync takes its source from a protected place (9, 57) ------------------------------------------
def _template(root: Path) -> Path:
    (root / "_meta" / "tools").mkdir(parents=True)
    (root / "_meta" / "tools" / "sweep.py").write_text("print('evil')\n", encoding="utf-8")
    return root


def test_the_provenance_cannot_name_the_source(tmp_path):
    evil = _template(tmp_path / "evil")
    space = make_space(tmp_path / "ada")
    (space / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (space / "_meta" / "tools" / "sweep.py").write_text("print('ours')\n", encoding="utf-8")
    prov = space / PROV
    for repo in (str(evil), evil.as_uri(), "attacker/evil-template", "https://evil.example/o/n"):
        prov.write_text(yaml.safe_dump({"repo": repo, "release": "v1.0.0", "commit": "a" * 40, "files": {}}),
                        encoding="utf-8")
        with pytest.raises(template_sync.TemplateRefused):
            template_sync.guarded(space)
        assert (space / "_meta" / "tools" / "sweep.py").read_text(encoding="utf-8") == "print('ours')\n", repo
    assert template_sync.main([str(space)]) == 1


def test_the_source_is_space_yaml_or_the_official_template(tmp_path):
    space = make_space(tmp_path / "ada")
    assert template_sync.template_source(space, None) == template_sync.OFFICIAL_TEMPLATE == start.OFFICIAL_TEMPLATE != ""
    other = make_space(tmp_path / "bea", template="https://github.com/acme/tpl.git")
    assert template_sync.template_source(other, None) == "acme/tpl"
    (other / PROV).write_text(yaml.safe_dump({"repo": "acme/tpl", "files": {}}), encoding="utf-8")
    assert template_sync.template_source(other, None) == "acme/tpl"
    for bad in ("../x", "-oProxyCommand=x/y", "file:///tmp/x", "acme/tpl/extra", "https://evil.example/a/b"):
        broken = make_space(tmp_path / f"c{abs(hash(bad))}", template=bad)
        with pytest.raises(template_sync.TemplateRefused):
            template_sync.template_source(broken, None)


def test_a_local_folder_needs_local(tmp_path):
    tpl = _template(tmp_path / "tpl")
    space = make_space(tmp_path / "ada")
    with pytest.raises(template_sync.TemplateRefused):
        template_sync.run(space, source=str(tpl))
    assert not (space / "_meta" / "tools" / "sweep.py").exists()
    assert template_sync.main([str(space), "--from", str(tpl)]) == 1
    assert not (space / "_meta" / "tools" / "sweep.py").exists()
    assert template_sync.main([str(space), "--from", str(tpl), "--local"]) == 0
    assert (space / "_meta" / "tools" / "sweep.py").exists()
    # a local copy is a one-off: the space still follows its pinned template
    assert yaml.safe_load((space / PROV).read_text(encoding="utf-8"))["repo"] == template_sync.OFFICIAL_TEMPLATE


def test_a_yes_names_the_full_commit(tmp_path):
    space = make_space(tmp_path / "ada")
    commit = "abcdef01" + "2" * 32
    with (space / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n- 2026-10-18T09:00:00Z yes template-v0.1.0-{commit[:8]} by Ada Okafor\n")
    import spaceyaml
    loaded = spaceyaml.load(space)
    assert template_sync.approval(loaded, "v0.1.0", commit) is None          # a prefix is no yes
    with (space / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n- 2026-10-18T09:00:00Z yes template-v0.1.0-{commit} by Ada Okafor\n")
    assert template_sync.approval(loaded, "v0.1.0", commit)["commit"] == commit
    assert template_sync.approval(loaded, "v0.1.0", commit[:8] + "f" * 32) is None   # same prefix, other commit
    assert template_sync.item_id("v0.1.0", commit) == f"template-v0.1.0-{commit}"


def test_staying_on_a_release_needs_its_recorded_approval(tmp_path):
    from test_template_sync import _release, _yes, git_template
    tpl = git_template(tmp_path / "tpl")
    first = _release(tpl, "v0.1.0", "print('from the tag')\n", "2026-10-01T09:00:00Z")
    space = make_space(tmp_path / "ada")
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "release": "v0.1.0", "commit": first,
                                               "files": {}}), encoding="utf-8")
    report = template_sync.run(space, source=tpl.as_uri())
    assert not report.added and "has no recorded yes" in report.note
    assert not (space / "_meta" / "tools" / "sweep.py").exists()
    # with the approval recorded and the owner's yes, it stays (and re-syncs) on its release
    _yes(space, "v0.1.0", first)
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "release": "v0.1.0", "commit": first,
                                               "approved": {"release": "v0.1.0", "commit": first,
                                                            "by": "Ada Okafor"}, "files": {}}), encoding="utf-8")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.release == "v0.1.0" and "_meta/tools/sweep.py" in report.added


def test_a_release_the_template_never_had_does_not_freeze_the_space(tmp_path):
    from test_template_sync import _release, git_template
    tpl = git_template(tmp_path / "tpl")
    _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    space = make_space(tmp_path / "ada")
    (space / PROV).write_text(yaml.safe_dump({"repo": tpl.as_uri(), "release": "v99.0.0", "commit": "f" * 40,
                                               "files": {}}), encoding="utf-8")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.note.startswith("v0.1.0 waits for a yes"), report.note


# ----- the release archive (51, 57) ------------------------------------------------------------------------------
def test_a_tracked_link_fails_the_build(tmp_path, quiet_git):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "a.txt")
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(repo), input="../outside",
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},docs/x.txt")
    assert template_archive.main(["build", str(repo), str(tmp_path / "out"), "v1"]) == 1
    assert not (tmp_path / "out" / "brain-template-v1.zip").exists()


def test_verify_refuses_an_archive_that_unpacks_outside_its_folder(tmp_path):
    zip_path = tmp_path / "brain-template-v1.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("brain-template-v1/ok.txt", "ok")
        archive.writestr("brain-template-v1/../../evil.txt", "evil")
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(f"{template_archive.digest(zip_path)}  {zip_path.name}\n", encoding="utf-8")
    assert template_archive.verify(zip_path, sums) is False
    assert template_archive.unsafe_entries(zip_path) == ["brain-template-v1/../../evil.txt"]


def test_verify_fails_closed_on_a_short_or_doubled_sum(tmp_path, quiet_git):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "a.txt")
    zip_path, sums = template_archive.build(repo, tmp_path / "out", "v1")
    assert template_archive.verify(zip_path, sums)
    real = sums.read_text(encoding="utf-8")
    sums.write_text(real + f"{'0' * 64}  {zip_path.name}\n", encoding="utf-8")
    assert template_archive.verify(zip_path, sums) is False
    sums.write_text(f"  {zip_path.name}\n", encoding="utf-8")
    assert template_archive.verify(zip_path, sums) is False


# ----- behaviour scenarios run one tool, nothing else (45) ------------------------------------------------------
def _scenario(path: Path, **data) -> Path:
    data.setdefault("name", path.stem)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("change", [
    {"command": ["python", "-c", "open(r'{marker}', 'w').write('ran')"]},
    {"command": ["sh", "-c", "echo ran > '{marker}'"]},
    {"command": ["python", "-m", "pip"]},
    {"command": ["python", "_meta/tools/../../x.py"]},
    {"command": ["python", "_meta/tools/ok.py"], "env": {"PYTHONPATH": "{root}"}},
    {"command": ["python", "_meta/tools/ok.py"], "env": {"BASH_ENV": "x"}},
    {"command": ["python", "_meta/tools/ok.py"], "setup": {"files": {"../outside.txt": "x"}}},
    {"command": ["python", "_meta/tools/ok.py"], "setup": {"files": {"_meta/tools/ok.py": "print('swapped')"}}},
    {"command": ["python", "_meta/tools/ok.py"], "setup": {"files": {"C:/x.txt": "x"}}},
])
def test_a_scenario_outside_its_shape_fails_and_never_runs(tmp_path, change):
    root = make_space(tmp_path / "ada")
    (root / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "tools" / "ok.py").write_text("print('ok')\n", encoding="utf-8")
    marker = tmp_path / "marker.txt"
    data = json.loads(json.dumps(change).replace("{marker}", marker.as_posix().replace("\\", "/")))
    path = _scenario(tmp_path / "s.yaml", expect={"exit": 0}, **data)
    report = behaviour.run(root, path)
    assert report.failed == 1 and report.details[0]["check"] == "scenario", report.details
    assert not marker.exists() and not (tmp_path / "outside.txt").exists()


def test_the_shipped_scenarios_keep_the_shape():
    for path in sorted((REPO / "_meta" / "tests" / "behaviour").glob("*.yaml")):
        assert behaviour.scenario_problem(behaviour.load_scenario(path)) is None, path.name



# ----- re-test: bypasses of the fixes above ---------------------------------------------------------------------
# What Python imports beside a source: a sourceless `.pyc` loads as the module, an extension too, and an unchecked
# hash-based `.pyc` in `__pycache__` replaces its source unseen (45).
COMPILED = ("yaml.pyc", "yaml.pyw", "yaml.pyd", "yaml.cpython-312-x86_64-linux-gnu.so", "yaml.abi3.so",
            "pytest/__init__.pyc", "pkg/__init__.cpython-312-x86_64-linux-gnu.so", "_meta/__init__.pyc",
            "work/a/__pycache__/x.cpython-312.pyc", "work/a/b.pyc", "knowledge/topics/a/x.pyo")


@pytest.mark.parametrize("rel", COMPILED)
def test_a_compiled_or_root_module_of_any_suffix_waits_for_the_click(tmp_path, rel):
    root = make_space(tmp_path / "ada", protected=[])
    cls, reasons = gate.classify(root, [rel, "knowledge/topics/a/one.md"])
    assert cls == "protected", reasons
    assert any(r.startswith(f"{rel}: protected") for r in reasons), reasons


@pytest.mark.parametrize("rel", ["work/a/notes.pyc.md", "knowledge/sources/2026/10/lib.so.md", "work/a/yaml.py",
                                 "work/a/b/__init__.py", "knowledge/topics/a/so.md"])
def test_a_module_below_the_first_level_or_a_page_named_after_one_still_lands(tmp_path, rel):
    root = make_space(tmp_path / "ada", protected=[])
    assert gate.classify(root, [rel])[0] == "auto-mergeable", rel


def test_a_name_git_could_not_read_as_utf8_waits_for_the_click(tmp_path):
    root = make_space(tmp_path / "ada", protected=[])
    cls, reasons = gate.classify(root, ["rules.md\ufffd", "work/a/\ufffd.md"])
    assert cls == "protected" and sum("not UTF-8" in r for r in reasons) == 2, reasons


def test_the_provenance_in_another_case_waits_for_the_click(tmp_path):
    root = make_space(tmp_path / "ada", protected=[])
    for rel in ("_meta/state/Template-Version.yaml", "_META/state/template-version.yaml"):
        cls, reasons = gate.classify(root, [rel])
        assert cls == "protected" and any("another case" in r for r in reasons), (rel, reasons)
    assert gate.protected_reason(PROV, gate.MINIMUM_PROTECTED) is None   # the provenance itself is judged apart


def _blob(space: Path, rel: str, text: str) -> None:
    """Add `rel` to the index as a blob, never through the folder: this checkout may ignore case itself."""
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(space), input=text,
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(space, "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}")


def test_a_path_that_is_another_where_case_is_ignored_waits_for_the_click(tmp_path, quiet_git):
    space = make_space(tmp_path / "ada", protected=[])
    write_page(space, "knowledge/clients/acme/overview.md", "# Overview\n", title="Overview")
    _git(space, "init", "-q", "-b", "main")
    _git(space, "add", "-A")
    _git(space, "commit", "-q", "-m", "base")
    base = _git(space, "rev-parse", "HEAD").strip()
    for n, rel in enumerate(("knowledge/clients/acme/Overview.md", "Knowledge/clients/acme/new.md",
                             "knowledge/clients/ACME/other.md")):
        _git(space, "branch", "-q", f"twin-{n}", base)
        _git(space, "symbolic-ref", "HEAD", f"refs/heads/twin-{n}")
        _git(space, "read-tree", base)
        _blob(space, rel, "---\ntitle: x\n---\n")
        _git(space, "commit", "-q", "-m", f"add {rel}")
        changed = gate.changed_files(space, base)
        assert rel in changed
        cls, reasons = gate.classify(space, changed, base_ref=base)
        assert cls == "protected", (rel, reasons)
        assert any("where case is ignored" in r for r in reasons), reasons
    # an ordinary page beside them still lands
    _git(space, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(space, "read-tree", base)
    _blob(space, "knowledge/clients/acme/plan.md", "---\ntitle: Plan\nstatus: draft\n---\n# Plan\n")
    _git(space, "commit", "-q", "-m", "plan")
    assert gate.classify(space, gate.changed_files(space, base), base_ref=base)[0] == "auto-mergeable"


def test_a_case_only_rename_is_no_twin(tmp_path, quiet_git):
    space = make_space(tmp_path / "ada", protected=[])
    (space / "work" / "a").mkdir(parents=True, exist_ok=True)
    (space / "work" / "a" / "notes.md").write_text("x\n", encoding="utf-8")
    _git(space, "init", "-q", "-b", "main")
    _git(space, "add", "-A")
    _git(space, "commit", "-q", "-m", "base")
    base = _git(space, "rev-parse", "HEAD").strip()
    _git(space, "rm", "-q", "--cached", "work/a/notes.md")
    _blob(space, "work/a/Notes.md", "x\n")
    _git(space, "commit", "-q", "-m", "rename")
    changed = gate.changed_files(space, base)
    assert set(changed) == {"work/a/notes.md", "work/a/Notes.md"}
    assert gate.case_twins(space, "HEAD", changed) == {}


def test_the_template_lands_every_file_template_sync_installs(tmp_path):
    root = make_space(tmp_path / "t", profile="template")
    for rel in ("README.md", "LICENSE", "_meta/tests/README.md", "docs/shape-1.md", ".claude/skills/x/SKILL.md"):
        cls, reasons = gate.classify(root, [rel])
        assert cls == "auto-mergeable", (rel, reasons)
    assert gate.classify(root, ["docs/decisions.md", "CHANGELOG.md", "docs/README.md"])[0] == "auto-mergeable"


def test_the_release_fetch_never_reads_an_address_as_an_option(tmp_path):
    root = make_space(tmp_path / "ada")
    for repo in ("--upload-pack=touch x;://", "-uevil://x"):
        assert gate.fetch_release(root, repo, "v0.1.0") is None


def _off_main_template(tmp_path) -> tuple[Path, str, str]:
    from test_template_sync import _git as tgit, _release, git_template
    tpl = git_template(tmp_path / "tpl")
    on_main = _release(tpl, "v0.1.0", "print('v1')\n", "2026-10-01T09:00:00Z")
    tgit(tpl, "checkout", "-q", "-b", "side")
    side = _release(tpl, "v0.2.0", "print('not merged')\n", "2026-10-02T09:00:00Z")
    tgit(tpl, "checkout", "-q", "main")
    return tpl, on_main, side


def test_a_tag_off_the_templates_default_branch_is_no_release(tmp_path):
    tpl, on_main, _ = _off_main_template(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--no-checkout", "--", tpl.as_uri(), str(clone)], check=True,
                   capture_output=True)
    assert template_sync._releases(clone) == {"v0.1.0": on_main}
    # the newest release a space is asked about is the one on main, never the side branch's
    from test_template_sync import _items
    space = make_space(tmp_path / "ada")
    report = template_sync.run(space, source=tpl.as_uri())
    assert report.note.startswith("v0.1.0 waits for a yes"), report.note
    assert [i["id"] for i in _items(space)] == [template_sync.item_id("v0.1.0", on_main)]


def test_the_gate_lands_no_release_off_the_templates_default_branch(tmp_path, quiet_git):
    tpl, _, side = _off_main_template(tmp_path)
    space = make_space(tmp_path / "ada")
    (space / PROV).write_text(yaml.safe_dump({"repo": str(tpl), "commit": "0" * 40, "files": {}}), encoding="utf-8")
    _git(space, "init", "-q", "-b", "main")
    _git(space, "add", "-A")
    _git(space, "commit", "-q", "-m", "base")
    base = _git(space, "rev-parse", "HEAD").strip()
    (space / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (space / "_meta" / "tools" / "sweep.py").write_text("print('not merged')\n", encoding="utf-8")
    (space / PROV).write_text(yaml.safe_dump({"repo": str(tpl), "release": "v0.2.0", "commit": side,
                                               "approved": {"release": "v0.2.0", "commit": side, "by": "Ada Okafor"},
                                               "files": {}}), encoding="utf-8")
    _git(space, "add", "-A")
    _git(space, "commit", "-q", "-m", "sync v0.2.0")
    cls, reasons = gate.classify(space, gate.changed_files(space, base), base_ref=base)
    assert cls == "protected", reasons
    assert any("not on the template's default branch" in r for r in reasons), reasons


def _link(link: Path, target: Path, directory: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a symbolic link without privileges")


def test_template_sync_never_copies_a_link_nor_writes_through_one(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("the machine's own file\n", encoding="utf-8")
    outside = tmp_path / "outside"
    (outside / "x").mkdir(parents=True)
    (outside / "x" / "SKILL.md").write_text("# not the template's\n", encoding="utf-8")
    tpl = _template(tmp_path / "tpl")
    _link(tpl / "_meta" / "tools" / "leak.txt", secret)
    _link(tpl / ".claude" / "skills", outside, directory=True)
    files = template_sync.machinery_files(tpl)
    assert "_meta/tools/sweep.py" in files
    assert "_meta/tools/leak.txt" not in files and not any(f.startswith(".claude/skills/") for f in files)
    # a space path that is a link is kept, never written through
    space = make_space(tmp_path / "ada")
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched\n", encoding="utf-8")
    _link(space / "_meta" / "tools" / "sweep.py", victim)
    report = template_sync.run(space, source=str(tpl), local=True)
    assert "_meta/tools/sweep.py" in report.kept
    assert victim.read_text(encoding="utf-8") == "untouched\n"


@pytest.mark.parametrize("change", [
    {"setup": {"files": {"_meta/tools./ok.py": "print('swapped')"}}},
    {"setup": {"files": {"_meta/tools /ok.py": "print('swapped')"}}},
    {"setup": {"files": {"_META/TOOLS~1/ok.py": "print('swapped')"}}},
    {"env": {"SPACES_HOME": "/home/someone/.spaces"}},
    {"env": {"SPACES_HOME": "C:relative"}},
    {"env": {"SPACES_CLOUD_HOME": "{root}/../outside"}},
])
def test_a_scenario_cannot_reach_a_tool_or_this_machine_by_another_name(tmp_path, change):
    root = make_space(tmp_path / "ada")
    (root / "_meta" / "tools").mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "tools" / "ok.py").write_text("print('ok')\n", encoding="utf-8")
    path = _scenario(tmp_path / "s.yaml", command=["python", "_meta/tools/ok.py"], expect={"exit": 0}, **change)
    report = behaviour.run(root, path)
    assert report.failed == 1 and report.details[0]["check"] == "scenario", report.details


def test_a_scenario_may_still_name_a_word_or_a_folder_in_its_copy():
    for env in ({"SPACE_NOW": "2026-10-05T09:00:00Z"}, {"SPACES_HOME": "{root}/home"}, {"SPACES_LOCAL_CLONES": "1"},
                {"SPACES_TASK_PREFIX": "space-test-1-"}):
        assert behaviour.scenario_problem({"command": ["python", "_meta/tools/ok.py"], "env": env}) is None, env


def test_a_push_switches_off_an_earlier_arming_before_the_checks_can_pass():
    """GitHub keeps auto-merge armed through a writer's push, so without `disarm` an arming from an earlier push
    would merge new commits the moment `checks` passes, before the gate judged them (50)."""
    jobs = _wf("checks.yml")["jobs"]
    disarm = jobs["disarm"]
    assert "github.event.action == 'synchronize'" in disarm["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in disarm["if"]
    run = json.dumps(disarm["steps"])
    assert "gh pr merge --disable-auto" in run and "actions/checkout" not in run and "python" not in run
    assert jobs["mounts"]["needs"] == "disarm" and jobs["mounts"]["if"].startswith("!cancelled()")
    first = _steps(jobs["mounts"])[0]
    assert "needs.disarm.result" in first["env"]["DISARM"] and "exit 1" in first["run"]
    assert jobs["checks"]["needs"] == "mounts"


@template_only
def test_codeowners_names_compiled_and_root_modules():
    lines = {line.split()[0] for line in (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")}
    for pattern in ("/*.py", "/*.pyw", "/*.pyd", "/*.so", "/*/__init__.*", "*.pyc", "*.pyo", "__pycache__/"):
        assert pattern in lines, pattern
