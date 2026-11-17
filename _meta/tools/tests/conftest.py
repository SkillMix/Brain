"""Shared fixtures: build minimal shape-1 spaces in a temp directory."""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import pages  # noqa: E402
import spaceyaml  # noqa: E402

NOW = dt.datetime(2026, 10, 12, 9, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _machine_home(tmp_path, monkeypatch):
    """No test touches the real `~/.spaces/` (the machine's list of spaces, logs, tools): the
    home folder is a temporary one; git keeps the real global config for its identity. The start
    lock lives in the test's folder too, so a killed run never blocks a real start, and so does
    the setup journal, so no test can write the person's real one (a command a test starts
    inherits both)."""
    import os
    real = Path.home()
    gitconfig = os.environ.get("GIT_CONFIG_GLOBAL") or str(real / ".gitconfig")
    fake = tmp_path / "_home"
    fake.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", gitconfig)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)   # a suite run inside a cloud session is not a cloud run
    monkeypatch.setenv("SPACES_START_LOCK", str(fake / "spaces-start.lock"))
    monkeypatch.setenv("SPACES_SETUP_JOURNAL", str(fake / ".spaces" / "setup.yaml"))
    # What the tools set for what they start (doctor.use_tools), and the checks' word on whether this
    # repository is the template (read by the test files' own marks, before any test runs): each test
    # starts without them, and what one sets is gone after it.
    for name in ("GH_CONFIG_DIR", "SPACES_TEMPLATE_REPO", "GCM_INTERACTIVE", "GIT_CONFIG_COUNT", "SPACES_LOCAL_CLONES",
                 *(f"GIT_CONFIG_{part}_{i}" for part in ("KEY", "VALUE") for i in range(4))):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.fixture
def model_free_runs():
    """Asked for by a test of the sync's own run of the daily chain (`sync.Run.model_free_daily`)."""
    return True


@pytest.fixture(autouse=True)
def _no_daily_by_the_way(request, monkeypatch):
    """A sync run in a test runs the daily chain itself only in a test that asks for it (`model_free_runs`).
    The run lines the fixtures write are dated in the past, so to the real clock each is a daily that did not
    start, and a test of something else would run a whole daily chain, the real tools included."""
    if "model_free_runs" not in request.fixturenames:
        import sync
        monkeypatch.setattr(sync.Run, "model_free_daily", lambda self, now: False)


def write_page(root: Path, rel: str, body: str = "Body.\n", **fm) -> Path:
    data = {"id": fm.pop("id", pages.new_id()), "type": fm.pop("type", "concept"),
            "title": fm.pop("title", rel), "status": fm.pop("status", "draft"),
            "generated": fm.pop("generated", pages.generated("test", NOW))}
    data.update(fm)
    return pages.write(root / rel, data, body)


def make_space(root: Path, profile: str = "personal", mode: str = "live",
               name: str | None = None, owner: str = "Ada Okafor",
               subjects: list | None = None, **extra) -> Path:
    """Create the required tree for a profile plus a minimal space.yaml."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    table = spaceyaml.PROFILE_TABLE[profile]
    for entry in table["required"]:
        target = root / entry
        if "." in Path(entry).name:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                if entry.endswith("subjects.yaml"):
                    target.write_text("subjects: []\n", encoding="utf-8")
                elif entry.endswith("space.yaml"):
                    pass
                else:
                    target.write_text(f"---\ntitle: {Path(entry).stem}\n---\n# {Path(entry).stem}\n",
                                      encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
    for sub in ("_inbound/candidates", "_inbound/checks", "_inbound/questions", "_inbound/needs-you",
                "_outbound/queue", "_outbound/held", "_outbound/signals", "_meta/state",
                "knowledge/sources", "knowledge/decisions", "live/subjects"):
        if profile != "channel":
            (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "_meta" / "state").mkdir(parents=True, exist_ok=True)
    data = {"shape": 1, "profile": profile, "mode": mode, "name": name or root.name,
            "owner": owner, "owner_email": "owner@example.test",
            "audience": "everyone" if profile == "brain" else [owner],
            "parent": None if profile == "brain" else "brain"}
    if profile == "template":
        (root / ".github").mkdir(exist_ok=True)
        (root / ".github" / "template-marker").write_text("", encoding="utf-8")
    data.update(extra)
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if subjects is not None:
        (root / "_meta" / "subjects.yaml").write_text(
            yaml.safe_dump({"subjects": subjects}, sort_keys=False), encoding="utf-8")
    if profile == "brain":
        write_page(root, "identity/charter.md", type="policy", title="Charter")
    return root


def fake_run(seen: dict | None = None, stdout: str = "", stderr: str = "", code: int = 0):
    """A `subprocess.run` stand-in for a test: records what it was called with (`argv`, and the
    keywords: `env`, `cwd`, `input`, `timeout`) in `seen`, then returns a finished process with
    this output. No test starts the real claude."""
    def run(argv, **kw):
        if seen is not None:
            seen.update(argv=list(argv), **kw)
        return subprocess.CompletedProcess(argv, code, stdout, stderr)
    return run


def mount_brain(space_root: Path, brain_root: Path) -> None:
    """Point a space's `brain` mount at a brain root (absolute path, read-only by convention)."""
    data = yaml.safe_load((space_root / "space.yaml").read_text(encoding="utf-8"))
    mounts = [m for m in (data.get("mounts") or []) if m.get("name") != "brain"]
    mounts.append({"name": "brain", "path": str(brain_root)})
    data["mounts"] = mounts
    (space_root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def space_factory(tmp_path):
    def _make(name="ada", **kwargs):
        return make_space(tmp_path / name, **kwargs)
    return _make


# ----- the build's arguments, shared by the tests of start.py and of the routines (Task 8, moved here) ---
TEMPLATE = Path(__file__).resolve().parents[3]
SLIM = None   # set by the slim_template fixture, once a session


@pytest.fixture(scope="session")
def slim_template(tmp_path_factory):
    """One template copy for every build of the session, without what only a working copy has (the
    session's notes under .superpowers, the tool tests): a build copies from it twice (the brain and the
    workspace) and never writes into it. A copy per test cost most of the suite's time."""
    global SLIM
    import onboard
    tpl = tmp_path_factory.mktemp("template") / "tpl"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(onboard, "SKIP_DIRS", onboard.SKIP_DIRS | {".superpowers"})
        mp.setattr(onboard, "SKIP_PATHS", onboard.SKIP_PATHS + ("_meta/tools/tests",))
        onboard.copy_template(TEMPLATE, tpl)
    SLIM = tpl
    yield tpl


@pytest.fixture(autouse=True)
def _here_is_restored(monkeypatch):
    """`args` points start.HERE at the session's template copy; the end of the test puts it back."""
    import start
    monkeypatch.setattr(start, "HERE", start.HERE)


def args(tmp_path, **more):
    """The build's arguments as the command line gives them, offline: the spaces go under
    `tmp_path/spaces`, made from the session's template copy that start.HERE is pointed at (a test
    that calls this asks for the `slim_template` fixture)."""
    import start
    assert SLIM is not None, "ask for the slim_template fixture"
    start.HERE = SLIM
    given = start.parser().parse_args(["build", "--offline", "--home", str(tmp_path / "spaces"), "--template", str(SLIM),
                                       "--name", "Ada Okafor", "--user-instructions", str(tmp_path / "CLAUDE.md"),
                                       "--json"])
    for key, value in more.items():
        setattr(given, key, value)
    return given


def last_report():
    """The report the build kept on start.LAST_REPORT."""
    import start
    return start.LAST_REPORT
