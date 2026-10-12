import json
from pathlib import Path

import yaml

import behaviour
from conftest import make_space

TEMPLATE = Path(__file__).resolve().parents[3]


def scenario(path, **data):
    data.setdefault("name", path.stem)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def py(code):
    return ["python", "-c", code]


def test_run_fails_a_command_that_writes_outside_the_allowed_prefixes(tmp_path):
    root = make_space(tmp_path / "ada")
    path = scenario(tmp_path / "bad.yaml",
                    command=py("open('{root_posix}/knowledge/x.md', 'w').write('status: stable')"),
                    expect={"exit": 0, "writes_only_under": ["_outbound/"],
                            "never_contains": {"knowledge/**": ["status: stable"]}})
    report = behaviour.run(root, path)
    assert (report.passed, report.failed) == (0, 1)
    text = json.dumps(report.details)
    assert "knowledge/x.md" in text
    checks = {d["check"]: d["ok"] for d in report.details}
    assert checks["writes_only_under"] is False
    assert checks["never_contains"] is False
    assert checks["exit"] is True
    # The scenario ran in a copy: the real space is untouched.
    assert not (root / "knowledge" / "x.md").exists()


def test_run_passes_a_clean_scenario_with_setup_env_and_must_exist(tmp_path):
    root = make_space(tmp_path / "ada")
    code = ("import os, pathlib; "
            "text = pathlib.Path('_inbound/candidates/seed.md').read_text(); "
            "pathlib.Path('_outbound/queue').mkdir(parents=True, exist_ok=True); "
            "pathlib.Path('_outbound/queue/q-1.md').write_text(text + os.environ['SPACE_NOW']); "
            "pathlib.Path('log.md').open('a').write('one line'); "
            "pathlib.Path('_meta/state/runs.jsonl').open('a').write('{}')")
    path = scenario(tmp_path / "good.yaml", command=py(code),
                    setup={"files": {"_inbound/candidates/seed.md": "status: draft\n"}},
                    env={"SPACE_NOW": "2026-10-05T09:00:00Z"},
                    expect={"exit": 0, "writes_only_under": ["_outbound/"],
                            "never_contains": {"_outbound/**": ["status: stable"]},
                            "must_exist": ["_outbound/queue/q-1.md", "_outbound/**/*.md"]})
    report = behaviour.run(root, path)
    assert (report.passed, report.failed) == (1, 0), report.details
    assert all(d["ok"] for d in report.details)


def test_run_checks_the_exit_code_and_must_not_exist(tmp_path):
    root = make_space(tmp_path / "ada")
    refusing = scenario(tmp_path / "refuse.yaml", command=py("import sys; sys.exit(1)"),
                        expect={"exit": 1, "writes_only_under": [],
                                "must_not_exist": ["knowledge/**/*.md"]})
    assert behaviour.run(root, refusing).failed == 0
    wrong = scenario(tmp_path / "wrong.yaml", command=py("import sys; sys.exit(1)"),
                     expect={"exit": 0})
    report = behaviour.run(root, wrong)
    assert report.failed == 1
    assert [d for d in report.details if d["check"] == "exit"][0]["ok"] is False
    present = scenario(tmp_path / "present.yaml", command=py("pass"),
                       setup={"files": {"knowledge/topics/x/page.md": "hello\n"}},
                       expect={"exit": 0, "must_not_exist": ["knowledge/**/*.md"]})
    assert behaviour.run(root, present).failed == 1


def test_run_all_skips_scenarios_whose_tool_is_absent_and_records_a_line(tmp_path):
    root = make_space(tmp_path / "ada")
    folder = root / "_meta" / "tests" / "behaviour"
    folder.mkdir(parents=True)
    scenario(folder / "missing.yaml", command=["python", "_meta/tools/not_here.py", "{root}"],
             expect={"exit": 0})
    scenario(folder / "missing-module.yaml", module="_meta/tools/also_not_here.py",
             command=py("pass"), expect={"exit": 0})
    scenario(folder / "ok.yaml", command=py("pass"), expect={"exit": 0})
    report = behaviour.run_all(root)
    assert (report.passed, report.failed) == (1, 0)
    assert len(report.skipped) == 2
    assert all("tool not present" in note for note in report.skipped)
    lines = (root / "_meta" / "state" / "tests.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    assert last["kind"] == "behaviour" and last["passed"] == 1 and last["failed"] == 0


def test_cli_exit_codes(tmp_path):
    root = make_space(tmp_path / "ada")
    good = scenario(tmp_path / "good.yaml", command=py("pass"), expect={"exit": 0})
    bad = scenario(tmp_path / "bad.yaml", command=py("pass"), expect={"exit": 3})
    assert behaviour.main([str(root), str(good)]) == 0
    assert behaviour.main([str(root), str(good), str(bad)]) == 1


def test_shipped_scenarios_are_well_formed():
    folder = TEMPLATE / "_meta" / "tests" / "behaviour"
    files = sorted(folder.glob("*.yaml"))
    names = {p.stem for p in files}
    assert {"sweep", "distill", "check", "status", "release", "sign", "morning-view",
            "sync"} <= names
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["name"], path
        assert isinstance(data["command"], list) and data["command"][0] == "python", path
        expect = data["expect"]
        assert "exit" in expect, path
        assert isinstance(expect.get("writes_only_under", []), list), path
        for pattern, needles in (expect.get("never_contains") or {}).items():
            assert isinstance(needles, list) and needles, (path, pattern)
        module = behaviour.module_of(data)
        assert module is not None and module.startswith("_meta/tools/"), path



def test_run_never_copies_private_zones_or_the_feed(tmp_path):
    root = tmp_path / "space"
    for rel in ("personal/diary.txt", "academic/thesis.txt", "_scratch/tmp.txt",
                "_meta/state/feed/mail.jsonl", "knowledge/topics/x/page.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("private\n", encoding="utf-8")
    (root / "space.yaml").write_text("shape: 1\nprofile: personal\nname: space\n", encoding="utf-8")
    path = scenario(tmp_path / "s.yaml", name="private zones", command=["python", "-c", "print(1)"],
                    expect={"exit": 0, "must_not_exist": ["personal/diary.txt", "academic/thesis.txt",
                                                         "_scratch/tmp.txt", "_meta/state/feed/mail.jsonl",
                                                         "knowledge/topics/x/page.md"]})
    report = behaviour.run(root, path)
    assert report.failed == 0, report.details
