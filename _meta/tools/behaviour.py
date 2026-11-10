"""Behaviour scenarios: run a tool in a copy of the space and hold it to its invariants.

A scenario is data (`_meta/tests/behaviour/<skill>.yaml`):

    name: sweep writes witness pages only
    module: _meta/tools/sweep.py          # optional; else the first .py in the command
    command: [python, _meta/tools/sweep.py, "{root}"]
    setup: {files: {relpath: content}}    # written into the copy before the run
    env: {SPACE_NOW: "2026-10-05T09:00:00Z"}
    expect:
      exit: 0
      writes_only_under: [knowledge/sources/, _outbound/]   # _meta/state/ and log.md always may
      never_contains: {"knowledge/sources/**": ["status: stable"]}
      must_exist: ["knowledge/sources/**/*.md"]
      must_not_exist: ["_scratch/dest/**/*.md"]

The space is copied to a temporary directory (without `.git`), the setup files are
written, the tree is snapshotted, the command runs with `python` as this interpreter and
`{root}` (`{root_posix}` inside code) as the copy, and the tree is compared. Every created,
changed or deleted file must sit under an allowed prefix; no file matching a
`never_contains` glob may carry one of its substrings (case-insensitive); `must_exist`
and `must_not_exist` take paths or globs; the exit code must match.

A scenario runs code, so it is held to one shape before anything is copied
(`scenario_problem`): the command is `python _meta/tools/<tool>.py ...`, the env sets
only `SPACE_`/`SPACES_` variables to a plain word or a path inside the copy, and the setup
writes plain paths inside the copy (no name Windows reads as another) and no tool. One that does not fit fails. The scenarios are protected paths (gate.py), so a
new or changed one waits for the owner's click.

    python behaviour.py [root] --all
    python behaviour.py [root] <scenario.yaml> [...]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

SCENARIOS = Path("_meta") / "tests" / "behaviour"
TESTS_LOG = Path("_meta") / "state" / "tests.jsonl"
ALWAYS_ALLOWED = ("_meta/state/", "log.md")
COPY_SKIP = (".git", "__pycache__", ".pytest_cache")
TIMEOUT_SECONDS = 300


@dataclass
class Report:
    passed: int = 0
    failed: int = 0
    details: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def ok(self) -> bool:
        return self.failed == 0

    def merge(self, other: "Report") -> None:
        self.passed += other.passed
        self.failed += other.failed
        self.details.extend(other.details)
        self.skipped.extend(other.skipped)


# ----- scenario files ----------------------------------------------------------------

def load_scenario(path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("command"), list):
        raise ValueError(f"{path}: a scenario is a mapping with a `command` list")
    data.setdefault("name", Path(path).stem)
    return data


def module_of(data: dict) -> str | None:
    """The tool file the scenario exercises: `module`, else the first `.py` in the command."""
    module = data.get("module")
    if not module:
        for arg in data.get("command") or []:
            if str(arg).endswith(".py"):
                module = arg
                break
    return str(module).replace("\\", "/") if module else None


# ----- the run -----------------------------------------------------------------------

def _copy(root: Path, temp_root: Path) -> dict:
    """Copy the space and return each copied file's size, mtime and sha1, taken from the bytes
    as they are copied. On Windows the first read of a file just written is the slow part (the
    virus scanner reads it first), so both snapshots reuse these."""
    copied: dict = {}

    def copy(src, dst):
        data = Path(src).read_bytes()
        Path(dst).write_bytes(data)
        shutil.copystat(src, dst)
        stat = os.stat(dst)
        copied[Path(dst).relative_to(temp_root).as_posix()] = (
            stat.st_size, stat.st_mtime_ns, hashlib.sha1(data).hexdigest())
        return dst

    shutil.copytree(root, temp_root, ignore=_copy_ignore(root), copy_function=copy)
    return copied


def _snapshot(root: Path, copied: dict | None = None) -> dict:
    """Path to sha1 of every file. A copied file whose size and mtime are still the copy's is
    not read again: any write since moves its mtime."""
    table: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in COPY_SKIP]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            known = (copied or {}).get(rel)
            if known:
                stat = path.stat()
                if known[:2] == (stat.st_size, stat.st_mtime_ns):
                    table[rel] = known[2]
                    continue
            table[rel] = hashlib.sha1(path.read_bytes()).hexdigest()
    return table


def _substitute(text: str, temp_root: Path) -> str:
    return str(text).replace("{root_posix}", temp_root.as_posix()).replace("{root}", str(temp_root))


TOOL_ARG = re.compile(r"_meta/tools/[A-Za-z0-9_]+\.py")
ENV_KEY = re.compile(r"SPACES?_[A-Z0-9_]+")
# An env value is a plain word (a time, a flag, a prefix) or a path inside the copy: several SPACES_ variables name
# a folder the tools write to (SPACES_HOME, SPACES_CLOUD_HOME), which must never be this machine's own.
SHORT_NAME = re.compile(r"^[^~/]{1,6}~[0-9]{1,6}(\.[^./]{0,3})?$")   # as gate.SHORT_NAME
ENV_WORD = re.compile(r"(?![A-Za-z]:)[A-Za-z0-9_.:+-]*")   # never a drive (`C:x` is a path on Windows)
ENV_IN_COPY = re.compile(r"\{root(_posix)?\}(/[A-Za-z0-9_.-]+)*/?")


def scenario_problem(data: dict) -> str | None:
    """Why a scenario may not run, or None. A scenario is data that runs one tool of this space:
    `python _meta/tools/<tool>.py` and its arguments, never another program, `python -c` or
    `python -m`; its env sets only the space's own `SPACE_`/`SPACES_` variables, never one that
    changes what Python or the shell loads (PYTHONPATH, PATH, LD_PRELOAD, BASH_ENV), and each to a
    plain word or a path inside the copy, never a folder of this machine; and its setup writes
    only inside the copy, under names no system reads as another, and never a tool it would then
    run."""
    command = [str(arg) for arg in data.get("command") or []]
    if len(command) < 2 or command[0] != "python" or not TOOL_ARG.fullmatch(command[1]):
        return f"the command must be `python _meta/tools/<tool>.py ...`, not {ascii(command[:2])}"
    for key, value in (data.get("env") or {}).items():
        if not ENV_KEY.fullmatch(str(key)):
            return f"env {ascii(str(key))} is not a SPACE_ or SPACES_ variable"
        text = str(value)
        if not (ENV_WORD.fullmatch(text) or ENV_IN_COPY.fullmatch(text)) or ".." in text.split("/"):
            return f"env {ascii(str(key))} is neither a plain word nor a path inside the copy ({{root}}/...)"
    for rel in ((data.get("setup") or {}).get("files") or {}):
        text = str(rel)
        parts = text.split("/")
        if (not text or "\\" in text or ":" in text or text.startswith("/")
                or any(p in ("", ".", "..") for p in parts)):
            return f"setup file {ascii(text)} is not a plain path inside the copy"
        # a name Windows reads as another one (`tools.`, `tools `, `TOOLS~1`) would pass the test below
        alias = next((pages.name_problem(p) for p in parts if pages.name_problem(p)), None)
        if alias or any(SHORT_NAME.match(p) for p in parts):
            return f"setup file {ascii(text)} has a name that {alias or 'is an 8.3 short name'}"
        if text.casefold().startswith("_meta/tools/"):
            return f"setup file {ascii(text)} would replace a tool the scenario runs"
    return None


def _command(command: list, temp_root: Path) -> list:
    argv = [_substitute(arg, temp_root) for arg in command]
    if argv and argv[0] == "python":
        argv[0] = sys.executable
    return argv


def _glob_files(root: Path, pattern: str) -> list:
    pattern = pattern.replace("\\", "/").rstrip("/")
    direct = root / pattern
    if any(ch in pattern for ch in "*?["):
        if pattern.endswith("**"):
            pattern += "/*"
        return [p for p in root.glob(pattern) if p.is_file()]
    if direct.is_dir():
        return [p for p in direct.rglob("*") if p.is_file()]
    return [direct] if direct.is_file() else []


def _write_setup(temp_root: Path, files: dict) -> None:
    for rel, content in (files or {}).items():
        target = temp_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(content, str):
            content = yaml.safe_dump(content, sort_keys=False, allow_unicode=True, width=100)
        target.write_text(content, encoding="utf-8", newline="\n")


def _check(name: str, ok: bool, info) -> dict:
    return {"check": name, "ok": bool(ok), "info": info}


def _compare(temp_root: Path, before: dict, after: dict, expect: dict, code, output: str) -> list:
    details: list = []
    want = expect.get("exit", 0)
    tail = output[-1500:] if code != want else ""
    details.append(_check("exit", code == want, {"exit": code, "expected": want, "output": tail}))
    changed = sorted({rel for rel in after if before.get(rel) != after[rel]} | (set(before) - set(after)))
    if "writes_only_under" in expect:
        allowed = tuple(str(p).replace("\\", "/") for p in (expect.get("writes_only_under") or []))
        allowed += ALWAYS_ALLOWED
        outside = [rel for rel in changed
                   if not any(rel == a or rel.startswith(a) for a in allowed)]
        details.append(_check("writes_only_under", not outside,
                              {"outside": outside, "allowed": list(allowed)}))
    never = expect.get("never_contains") or {}
    if never:
        offenders: list = []
        for pattern, needles in never.items():
            lowered = [str(n).lower() for n in (needles or [])]
            for file in _glob_files(temp_root, pattern):
                try:
                    text = file.read_text(encoding="utf-8", errors="replace").lower()
                except OSError:
                    continue
                found = [n for n in lowered if n in text]
                if found:
                    offenders.append({"file": file.relative_to(temp_root).as_posix(),
                                      "pattern": pattern, "found": found})
        details.append(_check("never_contains", not offenders, {"offenders": offenders}))
    missing = [rel for rel in (expect.get("must_exist") or []) if not _glob_files(temp_root, str(rel))]
    if "must_exist" in expect:
        details.append(_check("must_exist", not missing, {"missing": missing}))
    present = [rel for rel in (expect.get("must_not_exist") or []) if _glob_files(temp_root, str(rel))]
    if "must_not_exist" in expect:
        details.append(_check("must_not_exist", not present, {"present": present}))
    return details


HERMETIC_ZONES = ("knowledge", "live", "_inbound", "_outbound", "work", "journal", "outbox", "_meta/state")
# content files outside the zones: a real instance's question log would steer the librarian
HERMETIC_FILES = ("_meta/questions.log",)
HERMETIC_DIRS = ("knowledge/sources", "knowledge/decisions", "live/subjects", "_inbound/candidates",
                 "_inbound/checks", "_inbound/questions", "_inbound/needs-you", "_outbound/queue",
                 "_outbound/held", "_outbound/signals", "_meta/state")
# Never copied: the private zones (rules.md section 1, rule 8), the local feed, and the content
# zones and run state that `_hermetic` empties anyway (a large instance copied slowly).
PRIVATE_TOP = ("personal", "academic", "_scratch")


def _copy_ignore(root: Path):
    root = Path(root).resolve()
    # mounted spaces are other spaces' checkouts: a scenario writes what it needs in its setup
    skip_top = set(PRIVATE_TOP) | {z for z in HERMETIC_ZONES if "/" not in z} | {"brain", "mounts"}

    def ignore(directory, names):
        here = Path(directory).resolve()
        out = {n for n in names if n in COPY_SKIP}
        if here == root:
            out |= {n for n in names if n in skip_top}
        elif here == root / "_meta":
            out |= {n for n in names if n == "state"}
        return out
    return ignore


def _hermetic(temp_root: Path) -> None:
    """A scenario runs on the space's machinery and settings, never on its content or state:
    the content zones and the run state are emptied in the copy so that watermarks, pages and
    hubs of a real instance cannot change what a scenario sees. The setup files then build
    exactly the situation the scenario describes."""
    for zone in HERMETIC_ZONES:
        target = temp_root / zone
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
    for sub in HERMETIC_DIRS:
        (temp_root / sub).mkdir(parents=True, exist_ok=True)
    for rel in HERMETIC_FILES:
        (temp_root / rel).unlink(missing_ok=True)


def run(root, scenario_path) -> Report:
    """Run one scenario against a copy of `root`; the space itself is never touched."""
    root = Path(root).resolve()
    data = load_scenario(scenario_path)
    name = str(data.get("name"))
    problem = scenario_problem(data)
    if problem:   # refused before anything is copied or run: a failure, never a skip
        return Report(failed=1, details=[{"scenario": name, "check": "scenario", "ok": False, "info": problem}])
    with tempfile.TemporaryDirectory(prefix="behaviour-", ignore_cleanup_errors=True) as tmp:
        temp_root = Path(tmp) / "space"
        copied = _copy(root, temp_root)
        _hermetic(temp_root)
        _write_setup(temp_root, (data.get("setup") or {}).get("files") or {})
        before = _snapshot(temp_root, copied)
        env = os.environ.copy()
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
        # a home of the scenario's own, outside the copy: a tool that writes under ~ (the sync's signing key, the
        # machine's list of spaces) never writes this machine's; git keeps its identity, Python its user packages
        import site
        home = Path(tmp) / "home"
        home.mkdir()
        env.setdefault("GIT_CONFIG_GLOBAL", str(Path.home() / ".gitconfig"))
        env.setdefault("PYTHONUSERBASE", site.getuserbase())
        env.update({"HOME": str(home), "USERPROFILE": str(home)})
        env.update({str(k): _substitute(v, temp_root) for k, v in (data.get("env") or {}).items()})
        argv = _command(data["command"], temp_root)
        try:
            proc = subprocess.run(argv, cwd=str(temp_root), env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_SECONDS)
            code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            code, output = None, f"timed out after {TIMEOUT_SECONDS}s"
        except OSError as exc:
            code, output = None, str(exc)
        after = _snapshot(temp_root, copied)
        checks = _compare(temp_root, before, after, data.get("expect") or {}, code, output)
    for check in checks:
        check["scenario"] = name
    ok = all(c["ok"] for c in checks)
    return Report(passed=int(ok), failed=int(not ok), details=checks)


def run_all(root) -> Report:
    """Every scenario under `_meta/tests/behaviour/`; a scenario whose tool is absent is skipped."""
    root = Path(root).resolve()
    report = Report()
    for path in sorted((root / SCENARIOS).glob("*.yaml")):
        try:
            data = load_scenario(path)
        except Exception as exc:
            report.failed += 1
            report.details.append({"scenario": path.stem, "check": "load", "ok": False,
                                   "info": str(exc)})
            continue
        module = module_of(data)
        if module and not (root / module).exists():
            report.skipped.append(f"{data['name']}: skipped: tool not present ({module})")
            continue
        report.merge(run(root, path))
    record(root, report)
    return report


def record(root, report: Report) -> None:
    """Append one line to `_meta/state/tests.jsonl`; a root without space.yaml, or the template,
    records nothing."""
    root = Path(root)
    if not spaceyaml.keeps_run_state(root):
        return
    try:
        now = spaceyaml.load(root).now()
    except (FileNotFoundError, ValueError):
        return
    path = root / TESTS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": pages.iso(now), "kind": "behaviour", "passed": report.passed,
            "failed": report.failed, "skipped": len(report.skipped),
            "details": [d for d in report.details if not d["ok"]]}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


# ----- CLI ---------------------------------------------------------------------------

def _print(report: Report) -> None:
    for detail in report.details:
        if not detail["ok"]:
            print(f"  FAIL {detail['scenario']}: {detail['check']} "
                  f"{json.dumps(detail['info'], default=str)[:600]}")
    for note in report.skipped:
        print(f"  {note}")
    print(f"behaviour: {report.passed} passed, {report.failed} failed, {len(report.skipped)} skipped")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    everything = "--all" in argv
    args = [a for a in argv if a != "--all"]
    scenarios = [Path(a) for a in args if a.endswith((".yaml", ".yml"))]
    rest = [a for a in args if not a.endswith((".yaml", ".yml"))]
    root = Path(rest[0]) if rest else Path(".")
    if not everything and not scenarios:
        print(__doc__)
        return 2
    report = Report()
    if everything:
        report.merge(run_all(root))
    if scenarios:
        single = Report()
        for path in scenarios:
            single.merge(run(root, path))
        record(root, single)
        report.merge(single)
    _print(report)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
