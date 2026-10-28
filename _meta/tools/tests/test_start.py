"""Start: one sentence in an agent session sets a person or a company up, offline here."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest
import yaml

import apps
import doctor
import onboard
import reader
import research
import routine
import setup_journal
import spaceyaml
import start
import validate
from conftest import args, last_report, make_space
from test_sync_task import world

TEMPLATE = Path(__file__).resolve().parents[3]


def template_copy(tmp_path) -> Path:
    tpl = tmp_path / "tpl"
    with pytest.MonkeyPatch.context() as mp:   # a working copy's own notes are not the template (a checkout has none)
        mp.setattr(onboard, "SKIP_DIRS", onboard.SKIP_DIRS | {".superpowers"})
        onboard.copy_template(TEMPLATE, tpl)
    return tpl


def brain_with_invitation(tmp_path, slug="ada", name="Ada Okafor", email="ada@lantern.example") -> Path:
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    inv = brain / "_meta" / "state" / "invitations" / f"{slug}.yaml"
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text(yaml.safe_dump({"name": name, "slug": slug, "email": email, "parent": "brain",
                                   "brain": str(brain), "feeds": "none", "schedule": 15,
                                   "crew_projects": []}), encoding="utf-8")
    return brain


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def test_parse_invitation_takes_owner_repo_urls_and_local_paths():
    assert start.parse_invitation("acme/brain:ada") == ("acme/brain", "ada")
    assert start.parse_invitation("https://github.com/acme/brain") == ("https://github.com/acme/brain", None)
    assert start.parse_invitation("C:/Users/x/brain:ada") == ("C:/Users/x/brain", "ada")
    assert start.parse_invitation("C:\\Users\\x\\brain") == ("C:\\Users\\x\\brain", None)
    assert start.normalize("https://github.com/acme/brain.git/") == "acme/brain"


def test_an_invitation_is_found_by_the_persons_name(tmp_path):
    brain = brain_with_invitation(tmp_path)
    other = brain / "_meta" / "state" / "invitations" / "ben.yaml"
    other.write_text(yaml.safe_dump({"name": "Ben Liu", "slug": "ben"}), encoding="utf-8")
    inv, how = start.find_invitation(str(brain), None, {"name": "ada okafor", "login": "", "email": ""})
    assert inv["slug"] == "ada" and "found by name" in how
    none, how = start.find_invitation(str(brain), None, {"name": "Chloe Martin", "login": "", "email": ""})
    assert none is None and "defaults" in how
    other.write_text(yaml.safe_dump({"name": "Ben Liu", "slug": "ben", "login": "ben-lw"}), encoding="utf-8")
    inv, how = start.find_invitation(str(brain), None, {"name": "B. Liu", "login": "ben-lw", "email": ""})
    assert inv["slug"] == "ben" and "found by name" in how


def test_one_sentence_sets_a_person_up_from_their_invitation(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    tpl, brain = template_copy(tmp_path), brain_with_invitation(tmp_path)
    ui = tmp_path / "home" / ".claude" / "CLAUDE.md"
    report = start.start(tpl, invitation=f"{brain.as_posix()}:ada", offline=True, home=tmp_path / "home",
                         user_instructions=ui)
    assert not report.failed(), [s.row() for s in report.failed()]
    ws = report.workspace
    space = spaceyaml.load(ws)
    assert space.profile == "personal" and space.owner == "Ada Okafor"
    assert space.data.get("owner_email") == "ada@lantern.example"
    assert any(m.get("name") == "brain" for m in space.mounts)
    assert errors(ws) == []
    assert onboard.instruction_present(ui, ws)
    names = {r["name"]: r for r in report.routines}
    assert set(names) == {"sweep-chain", "daily-chain", "backfill-night"}
    # one schedule, from the routine's own prompt, in the machine's local time
    assert all(r["timezone"] == "local" and r["schedule"] for r in report.routines)
    assert "routines" not in spaceyaml.load(ws).data
    text = Path(names["daily-chain"]["prompt_file"]).read_text(encoding="utf-8")
    assert Path(ws).as_posix() in text and "Ada Okafor" in text and "{workspace}" not in text
    result = start.summary(report)
    assert result["failed"] == [] and result["workspace"] == str(ws)


def test_a_named_invitation_that_is_missing_stops_before_anything_is_created(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    tpl, brain = template_copy(tmp_path), brain_with_invitation(tmp_path)
    report = start.start(tpl, invitation=f"{brain.as_posix()}:zoe", offline=True, home=tmp_path / "home",
                         user_instructions=tmp_path / "CLAUDE.md")
    assert [s.name for s in report.failed()] == ["invitation"]
    assert not (tmp_path / "zoe").exists()


def test_one_sentence_starts_a_company_with_its_owners_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    tpl = template_copy(tmp_path)
    report = start.start(tpl, company="Lantern Works", name="Ada Okafor", email="ada@lantern.example",
                         org="lantern", offline=True, home=tmp_path / "home",
                         user_instructions=tmp_path / "home" / ".claude" / "CLAUDE.md")
    assert not report.failed(), [s.row() for s in report.failed()]
    brain = tmp_path / "home" / "brain"   # under the home it was given
    assert spaceyaml.load(brain).profile == "brain"
    ws = report.workspace
    assert spaceyaml.load(ws).profile == "personal" and spaceyaml.load(ws).owner == "Ada Okafor"
    assert (Path(ws) / "_meta" / "state" / "routines" / "sweep-chain.md").exists()
    # the build schedules the routines itself: no row hands the agent a command (offline: recorded, not registered)
    rows = {s.name: s for s in report.steps}
    assert "routine prompts" not in rows and not any("--os-routines" in s.result for s in report.steps)
    for routine_name in ("sweep-chain", "daily-chain"):
        row = rows[f"routine task space-{routine_name}-{Path(ws).name}"]
        assert row.done and "offline" in row.result
    # the defaults the person may want to change are the journal's settings, not questions on Needs you
    skipped = yaml.safe_load((brain / "_inbound" / "needs-you" / "onboard.yaml").read_text(encoding="utf-8"))
    assert [i["id"] for i in skipped["items"]] == ["onboard-identity"]   # the identity draft, to sign
    assert {"domains", "never_leaves", "schedule"} <= {row["key"] for row in report.settings}
    # and the identity draft reaches the owner's own list, which the personal instruction shows
    import needs_you
    assert "onboard-identity" in {i.id for i in needs_you.collect(ws, online=False)}


def test_offline_without_a_home_makes_the_workspace_beside_the_template_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "userhome"))
    tpl, brain = template_copy(tmp_path), brain_with_invitation(tmp_path)
    report = start.start(tpl, invitation=f"{brain.as_posix()}:ada", offline=True,
                         user_instructions=tmp_path / "CLAUDE.md")
    assert not report.failed(), [s.row() for s in report.failed()]
    assert Path(report.workspace) == tmp_path / "ada"
    assert not (tmp_path / "userhome" / "spaces").exists()


def test_start_keeps_the_onboarding_page_outside_every_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    tpl, brain = template_copy(tmp_path), brain_with_invitation(tmp_path)
    report = start.start(tpl, invitation=f"{brain.as_posix()}:ada", offline=True, home=tmp_path / "home",
                         user_instructions=tmp_path / "CLAUDE.md")
    assert not report.failed(), [s.row() for s in report.failed()]
    assert report.page == Path.home() / ".spaces" / "onboarding.html"   # beside the machine's spaces list
    assert start.summary(report)["page"] == str(report.page)
    html = report.page.read_text(encoding="utf-8")
    assert "Welcome, Ada</p>" in html and 'http-equiv="refresh"' not in html   # the run is over
    assert not any(Path(p) in report.page.parents for p in report.spaces.values())


def test_a_dry_run_prints_the_plan_and_stops_before_onboard(tmp_path, monkeypatch, capsys):
    import subprocess
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        calls.append(([str(a) for a in argv], dry_run))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    monkeypatch.setattr(start.doctor, "diagnose", lambda **kw: [])   # the machine's own checks: test_doctor
    monkeypatch.setattr(start, "LOCK", tmp_path / "start.lock")
    tpl, brain = template_copy(tmp_path), brain_with_invitation(tmp_path)
    monkeypatch.setattr(start, "HERE", tpl)
    code = start.main(["--invitation", f"{brain.as_posix()}:ada", "--org", "lantern", "--home",
                       str(tmp_path / "home"), "--template", "lantern/brain-template", "--dry-run",
                       "--user-instructions", str(tmp_path / "CLAUDE.md")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "dry run" in out and "lantern/ada-workspace" in out and "onboard" in out
    assert not (tmp_path / "home" / "ada-workspace").exists() and not (tmp_path / "CLAUDE.md").exists()
    # every outside call after the sign-in check was a dry run
    assert all(dry for argv, dry in calls if argv[:3] != ["gh", "auth", "status"])


def test_the_start_command_onboards_offline_from_a_fixture_invitation(tmp_path):
    """The command line as the person's agent runs it; CI runs it on Windows, macOS and Linux."""
    tpl, brain = template_copy(tmp_path), brain_with_invitation(tmp_path)
    home = tmp_path / "userhome"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    done = subprocess.run([sys.executable, str(tpl / "_meta" / "tools" / "start.py"), "--offline",
                           "--invitation", f"{brain.as_posix()}:ada", "--home", str(tmp_path / "home"),
                           "--user-instructions", str(tmp_path / "CLAUDE.md"), "--json"],
                          capture_output=True, text=True, env=env, cwd=tpl)
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout[done.stdout.index("{"):])
    ws = Path(result["workspace"])
    assert ws.parent == tmp_path / "home" and spaceyaml.load(ws).owner == "Ada Okafor"
    assert result["failed"] == [] and len(result["routines"]) == 3 and errors(ws) == []


def test_identity_drops_a_no_reply_address(monkeypatch):
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "1+x@users.noreply.github.com"
                        if "user.email" in argv else ("Ada Okafor" if "user.name" in argv else ""))
    who = start.identity(offline=True)
    assert who["name"] == "Ada Okafor" and who["email"] == ""


def _online_dry_run(tmp_path, monkeypatch, orgs="", login="tess-v"):
    """start.main online with --dry-run: every outside call recorded, the host faked."""
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 1 if argv[:3] == ["gh", "repo", "view"] else 0, "", "")

    def out(argv, cwd=None):
        if argv[:3] == ["gh", "api", "user"]:
            return json.dumps({"login": login, "name": "Tess Varga"})
        return orgs if "user/memberships/orgs" in " ".join(argv) else ""
    monkeypatch.setattr(onboard, "run_cmd", fake)
    monkeypatch.setattr(start, "_out", out)
    monkeypatch.setattr(start.doctor, "diagnose", lambda **kw: [])
    monkeypatch.setattr(start, "LOCK", tmp_path / "start.lock")
    monkeypatch.setattr(start, "HERE", template_copy(tmp_path))
    return calls


def test_without_org_a_new_company_goes_to_the_persons_own_account(tmp_path, monkeypatch, capsys):
    calls = _online_dry_run(tmp_path, monkeypatch)
    code = start.main(["--company", "Trial Works", "--template", "acme/Brain", "--home", str(tmp_path / "home"),
                       "--dry-run", "--user-instructions", str(tmp_path / "CLAUDE.md")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert ["gh", "repo", "create", "tess-v/trial-works-brain", "--template", "acme/Brain", "--private",
            "--clone"] in calls
    assert "tess-v/trial-works-brain" in out


def _signed_in(monkeypatch):
    """The doctor's rows for gh and claude are fine, and both sign-ins read back."""
    rows = {"gh": doctor.Row("gh", True, "gh 2.101.0", "gh --version"),
            "claude command line": doctor.Row("claude command line", True, "2.1.303 (Claude Code)", "claude --version")}
    monkeypatch.setattr(start.doctor, "tool_row", lambda name, m=None, fix=True: rows[name])
    monkeypatch.setattr(start.signin, "github_signed_in", lambda m: True)
    monkeypatch.setattr(start.signin, "claude_state", lambda m: "account")
    monkeypatch.setattr(start.signin, "claude_identity", lambda m: {})


def _build_args(tmp_path, *more):
    return ["build", "--company", "Trial Works", "--template", "acme/Brain", "--home", str(tmp_path / "home"),
            "--json", "--user-instructions", str(tmp_path / "CLAUDE.md"), *more]


def test_an_ambiguous_namespace_is_a_need_in_the_signins_answer(tmp_path, monkeypatch, capsys):
    calls = _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    _signed_in(monkeypatch)
    code = start.main(["signins", "--company", "Trial Works", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["state"] == "waiting" and result["namespace"] is None
    [need] = result["needs"]
    assert need["kind"] == "namespace" and "tess-v" in need["text"] and "acme, other-co" in need["text"]
    assert result["choices"] == ["acme", "other-co", "tess-v"]
    assert not any(c[:3] == ["gh", "repo", "create"] for c in calls)


def test_the_build_waits_for_the_namespace_and_org_answers_it(tmp_path, monkeypatch, capsys):
    calls = _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    _signed_in(monkeypatch)
    started = []
    monkeypatch.setattr(start, "start", lambda *a, **k: started.append(k) or onboard.Report(case="start"))
    start.main(["signins", "--company", "Trial Works", "--json"])
    capsys.readouterr()
    code = start.main(_build_args(tmp_path))
    refused = json.loads(capsys.readouterr().out)
    assert code == 1 and refused["state"] == "waiting" and refused["stopped"] is True
    assert "files live on GitHub" in refused["why"] and [n["kind"] for n in refused["needs"]] == ["namespace"]
    assert started == [] and setup_journal.load()["build"]["state"] == "waiting"
    code = start.main(_build_args(tmp_path, "--org", "acme"))   # the answer
    built = json.loads(capsys.readouterr().out)
    assert code == 0 and built["state"] == "done" and built["needs"] == []
    assert started[0]["org"] == "acme"
    record = setup_journal.load()["signins"]
    assert record["namespace"] == "acme" and record["state"] == "done"   # the namespace was all it waited for


def test_a_refused_build_says_so_in_words_and_changes_nothing(tmp_path, monkeypatch, capsys):
    _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    _signed_in(monkeypatch)
    start.main(["signins", "--company", "Trial Works", "--json"])
    capsys.readouterr()
    assert start.main(["build", "--company", "Trial Works", "--home", str(tmp_path / "home")]) == 1
    out = capsys.readouterr().out
    assert "build: waiting: Where should the company's files live on GitHub?" in out and "yours: namespace:" in out
    assert not (tmp_path / "home").exists()


def test_a_dry_run_build_answers_nothing_in_the_journal(tmp_path, monkeypatch, capsys):
    _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    _signed_in(monkeypatch)
    start.main(["signins", "--company", "Trial Works", "--json"])
    capsys.readouterr()
    start.main(_build_args(tmp_path, "--org", "acme", "--dry-run"))
    capsys.readouterr()
    assert [n["kind"] for n in setup_journal.open_needs(setup_journal.load())] == ["namespace"]
    assert "namespace" not in setup_journal.load()["signins"] and "settings" not in setup_journal.load()


def test_the_account_the_signins_recorded_is_where_the_build_goes(tmp_path, monkeypatch, capsys):
    _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    _signed_in(monkeypatch)
    started = []
    monkeypatch.setattr(start, "start", lambda *a, **k: started.append(k) or onboard.Report(case="start"))
    start.main(["signins", "--company", "Trial Works", "--org", "other-co", "--json"])
    capsys.readouterr()
    assert start.main(_build_args(tmp_path)) == 0
    assert started[0]["org"] == "other-co"


def test_an_invited_colleague_is_not_asked_where_the_companys_repositories_go(tmp_path, monkeypatch, capsys):
    _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    _signed_in(monkeypatch)
    started = []
    monkeypatch.setattr(start, "start", lambda *a, **k: started.append(k) or onboard.Report(case="start"))
    code = start.main(["signins", "--invitation", "acme/brain:ada", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["state"] == "done" and result["needs"] == [] and "namespace" not in setup_journal.load()["signins"]
    assert start.main(["build", "--invitation", "acme/brain:ada", "--json"]) == 0
    assert started[0]["org"] is None   # the invitation says where the workspace goes


def test_setup_itself_never_stops_at_an_account_that_is_not_clear(tmp_path, monkeypatch, capsys):
    """Asked before, in the signins phase; with nothing asked, the person's own account."""
    calls = _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    code = start.main(["--company", "Trial Works", "--template", "acme/Brain", "--home", str(tmp_path / "home"),
                       "--dry-run", "--user-instructions", str(tmp_path / "CLAUDE.md")])
    assert code == 0, capsys.readouterr().out
    assert ["gh", "repo", "create", "tess-v/trial-works-brain", "--template", "acme/Brain", "--private",
            "--clone"] in calls


def test_setup_without_an_answer_picks_the_organisation_named_like_the_company(tmp_path, monkeypatch, capsys):
    calls = _online_dry_run(tmp_path, monkeypatch, orgs="other-co\ntrial-works")
    code = start.main(["--company", "Trial Works", "--template", "acme/Brain", "--home", str(tmp_path / "home"),
                       "--dry-run", "--user-instructions", str(tmp_path / "CLAUDE.md")])
    assert code == 0, capsys.readouterr().out
    assert ["gh", "repo", "create", "trial-works/trial-works-brain", "--template", "acme/Brain", "--private",
            "--clone"] in calls


def test_a_target_that_is_the_template_itself_is_refused(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: calls.append(argv))
    report = onboard.Report("company")
    dst, full = onboard.create_space(report, tmp_path, "brain", "brain", False, "Acme", tmp_path / "home",
                                     "https://github.com/acme/Brain.git", False)
    assert dst is None and calls == []
    [step] = report.steps
    assert not step.done and step.needs_owner and "template" in step.result


def test_a_rerun_adopts_the_brain_an_earlier_start_named_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    home = tmp_path / "home"
    assert start.brain_name("trial-works", "tess-v", home, offline=False) == "trial-works-brain"
    make_space(home / "brain", profile="brain", name="trial-works")
    assert start.brain_name("trial-works", "tess-v", home, offline=False) == "brain"
    assert start.brain_name("other-co", "tess-v", home, offline=False) == "other-co-brain"
    # on the host only (a new machine): adopted when it is this company's brain, never the template
    shutil.rmtree(home / "brain")
    space = {"tess-v/brain": "profile: brain\nname: trial-works\n", "acme/brain": "profile: template\n"}
    monkeypatch.setattr(start, "read_file", lambda repo, rel: space.get(repo))
    assert start.brain_name("trial-works", "tess-v", home, offline=False) == "brain"
    assert start.brain_name("trial-works", "acme", home, offline=False) == "trial-works-brain"


def test_the_tests_take_the_start_lock_in_their_own_folder(tmp_path, monkeypatch):
    """A killed test run leaves its lock in its own folder, never in the machine's temp folder
    where it would block the person's real start for an hour."""
    assert start.lock_path() == tmp_path / "_home" / "spaces-start.lock"
    lock = start.take_lock()
    assert lock == tmp_path / "_home" / "spaces-start.lock" and lock.exists()
    lock.unlink()
    monkeypatch.delenv("SPACES_START_LOCK")
    import tempfile
    assert start.lock_path() == Path(tempfile.gettempdir()) / "spaces-start.lock"


# ----- the build takes what the earlier phases found, and schedules the routines itself --------
@pytest.fixture(scope="module", autouse=True)
def _slim_template(slim_template):
    """The session's template copy (conftest's `slim_template`), made before any build of this module."""
    yield slim_template


GMAIL = {"app": "Gmail", "kinds": ["mail"], "read": ["mcp__claude_ai_Gmail__search_threads"], "write": [],
         "send": ["mcp__claude_ai_Gmail__send_message"], "account": "ada@lanternworks.example", "verdict": "work",
         "per_month": {"2026-10": 212, "2026-09": 187}, "why": "The mailbox holds Lantern Works mail."}
CALENDAR = {"app": "Google Calendar", "kinds": ["calendar"], "read": ["mcp__claude_ai_Google_Calendar__list_events"],
            "write": [], "send": [], "account": "ada@lanternworks.example", "verdict": "work",
            "per_month": {"2026-10": 40}, "why": "Meetings with clients and the team."}
OUTLOOK = {"app": "Outlook", "kinds": ["mail"], "read": ["mcp__claude_ai_Outlook__search_messages"], "write": [],
           "send": [], "account": "ada.private@example.org", "verdict": "personal",
           "per_month": {"2026-10": 15}, "why": "Family and private matters only."}


def finished_setup(verdicts=None, namespace="lantern-works", company="Lantern Works",
                   claude_email="ada@lanternworks.example") -> list:
    """The journal as the three phases before the build leave it: both sign-ins read back and the
    namespace settled, the apps read, the company researched."""
    verdicts = [GMAIL, CALENDAR, OUTLOOK] if verdicts is None else verdicts
    setup_journal.set_phase("signins", "done", github="ada-okafor", claude="account",
                            claude_email=claude_email, namespace=namespace)
    setup_journal.set_phase("apps", "done", groups=[], verdicts=verdicts, feeds=apps.feeds(verdicts), unfinished=[],
                            card=apps.card(verdicts, start._now()))
    setup_journal.set_phase("research", "done", company={"name": company, "domain": "lanternworks.example",
                                                         "domains": ["lanternworks.example"]})
    return verdicts


def no_outside(monkeypatch, git_email="ada.private@example.org"):
    """Offline, nothing reaches the outside: git's identity is all `start` may ask for."""
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: git_email if "user.email" in argv else (
        "Ada Okafor" if "user.name" in argv else ""))


@pytest.fixture(scope="module")
def built(tmp_path_factory, slim_template):
    """One offline build over a finished setup, for the tests that only read what it left: a fake
    home of its own (undone before the tests run), the creation of each space recorded. Building once
    instead of in each of them."""
    root = tmp_path_factory.mktemp("built")
    home = root / "_home"
    home.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "home", classmethod(lambda cls: home))
        mp.setenv("SPACES_START_LOCK", str(home / "spaces-start.lock"))
        mp.setenv("SPACES_SETUP_JOURNAL", str(home / ".spaces" / "setup.yaml"))
        mp.setattr(start, "HERE", start.HERE)
        no_outside(mp)
        made, create_space = [], onboard.create_space
        mp.setattr(onboard, "create_space", lambda report, r, dirname, repo, offline, org, *a, **k:
                   made.append((dirname, org)) or create_space(report, r, dirname, repo, offline, org, *a, **k))
        finished_setup()
        result = start.phase_build(args(root))
        report, journal = start.LAST_REPORT, setup_journal.load()
    return types.SimpleNamespace(result=result, report=report, journal=journal, made=made,
                                 workspace=Path(result["workspace"]), brain=Path(result["spaces"]["brain"]))


def test_build_registers_the_routines_without_a_hand_step(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup()
    result = start.phase_build(args(tmp_path))
    assert result["state"] == "done", result
    assert {"sweep-chain", "daily-chain"} <= {r["name"] for r in result["routines"]}
    assert not any("--os-routines" in s.result for s in last_report().steps)
    assert not any(s.name == "routine prompts" for s in last_report().steps)
    names = {s.name for s in last_report().steps}
    assert {f"routine task space-{r}-ada-okafor" for r in ("sweep-chain", "daily-chain")} <= names


def test_build_puts_defaults_under_settings_not_needs_you(built):
    for space in (built.workspace, built.brain):
        for path in (space / "_inbound" / "needs-you").glob("*.yaml"):
            for item in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("items") or []:
                # the identity draft is something to sign, not a default
                assert item["id"] == "onboard-identity", f"{path.name}: {item['id']}"
    rows = built.journal["settings"]
    assert {"domains", "never_leaves", "classification_default", "schedule"} <= {r["key"] for r in rows}
    domains = next(r for r in rows if r["key"] == "domains")
    assert domains["default"] == "sales, delivery, operations" and "domains" in domains["ask"]
    assert isinstance(next(r for r in rows if r["key"] == "never_leaves")["default"], list)
    assert "owner_email" not in {r["key"] for r in rows}   # the briefing address was worked out, not defaulted


def test_the_briefing_goes_to_the_work_mailbox(built):
    # git's address is the private one; the work mailbox is the person's own (it is the Claude sign-in's address)
    assert spaceyaml.load(built.workspace).owner_email == "ada@lanternworks.example"
    assert spaceyaml.load(built.brain).owner_email == "ada@lanternworks.example"
    assert built.journal["build"]["owner_email"] == "ada@lanternworks.example"
    assert built.journal["build"]["owner_email_source"] == "work-mailbox"


def test_build_takes_the_company_and_the_feeds_from_the_journal(built):
    assert spaceyaml.load(built.brain).name == "lantern-works"   # the researched company, not a folder's name
    assert [s["name"] for s in spaceyaml.load(built.workspace).data["sources"]] == ["mail", "calendar"]   # Outlook is personal


def test_the_workspace_goes_in_the_account_of_the_brain(built):
    """Decision A: one account holds both, the namespace the signins settled."""
    assert built.made == [("brain", "lantern-works"), ("ada-okafor", "lantern-works")]


def test_the_apps_the_signins_proved_are_not_asked_to_connect_again(built):
    """The apps phase read them: a row asking the person to sign in with /mcp would be wrong, and
    the build would wait on it."""
    assert built.result["yours"] == [] and built.result["state"] == "done"
    rows = {s.name: s for s in built.report.steps}
    assert rows["connect mail"].done and rows["connect calendar"].done and not rows["connect mail"].needs_owner


def test_build_writes_the_apps_grant_to_the_private_zone(built):
    saved = yaml.safe_load((built.workspace / "_meta" / "state" / "private" / "apps.yaml").read_text(encoding="utf-8"))
    assert [v["app"] for v in saved["apps"]] == ["Gmail", "Google Calendar", "Outlook"]
    assert all("per_month" not in v for v in saved["apps"])   # counts are the journal's, not the routines' grant
    assert saved["apps"][0] == {k: v for k, v in GMAIL.items() if k != "per_month"}
    assert "_meta/state/private/" in (built.workspace / ".gitignore").read_text(encoding="utf-8")   # never synced


def test_what_the_person_said_on_the_command_line_wins_and_an_unproven_feed_is_still_theirs(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup(company="Lantern Works")
    given = args(tmp_path, company="Quill Works", feeds="mail: owner, chat: owner", email="ada@desk.example")
    result = start.phase_build(given)
    assert spaceyaml.load(Path(given.home) / "brain").name == "quill-works"
    assert spaceyaml.load(result["workspace"]).owner_email == "ada@desk.example"
    assert [s["name"] for s in spaceyaml.load(result["workspace"]).data["sources"]] == ["mail", "chat"]
    # the apps step read mail, not chat: a row for chat stays the person's, and the build waits for it
    assert [r["name"] for r in result["yours"]] == ["connect chat"] and result["state"] == "waiting"


def test_a_build_without_apps_writes_no_grant_and_keeps_the_offer_to_connect(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    setup_journal.set_phase("signins", "done", namespace="lantern-works")
    result = start.phase_build(args(tmp_path, company="Lantern Works"))
    workspace = Path(result["workspace"])
    assert not (workspace / "_meta" / "state" / "private" / "apps.yaml").exists()
    offers = yaml.safe_load((workspace / "_inbound" / "needs-you" / "connect.yaml").read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in offers] == [f"onboard-connect-{n}" for n in ("mail", "calendar", "chat", "calls")]


def test_a_second_build_changes_nothing_that_is_right(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup()
    first = start.phase_build(args(tmp_path))
    settings, grant = setup_journal.load()["settings"], (Path(first["workspace"]) / "_meta/state/private/apps.yaml").read_bytes()
    second = start.phase_build(args(tmp_path))
    assert second["state"] == "done" and second["workspace"] == first["workspace"] and second["failed"] == []
    assert setup_journal.load()["settings"] == settings
    assert (Path(second["workspace"]) / "_meta/state/private/apps.yaml").read_bytes() == grant
    assert spaceyaml.load(second["workspace"]).owner_email == "ada@lanternworks.example"


def test_a_colleague_gets_the_work_mailbox_their_apps_and_their_settings_too(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup()
    brain = brain_with_invitation(tmp_path, email="")
    result = start.phase_build(args(tmp_path, invitation=f"{brain.as_posix()}:ada"))
    workspace = Path(result["workspace"])
    assert result["state"] == "done", result
    assert spaceyaml.load(workspace).owner_email == "ada@lanternworks.example"
    assert yaml.safe_load((workspace / "_meta" / "state" / "private" / "apps.yaml").read_text(encoding="utf-8"))["apps"]
    # the colleague's defaults are settings too, not questions on their Needs you
    assert {"never_leaves", "org"} == {r["key"] for r in setup_journal.load()["settings"]}   # the invitation answered the rest
    assert {"never_leaves", "org"} == {r["key"] for r in last_report().settings}
    assert not (workspace / "_inbound" / "needs-you" / "onboard.yaml").exists()


def test_offline_the_build_makes_no_call_to_github(tmp_path, monkeypatch):
    seen = []

    def out(argv, cwd=None):
        seen.append(list(argv))
        return "ada.private@example.org" if "user.email" in argv else ""
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", out)
    finished_setup()
    result = start.phase_build(args(tmp_path))
    assert result["state"] == "done"
    assert not [a for a in seen if a and a[0] == "gh"]   # no organisation check, no identity lookup
    assert not (Path(result["workspace"]) / "_inbound" / "needs-you" / "onboard-org.yaml").exists()


# ----- "skip <app>": the person's word is the app's verdict (Ruling R38, I-5) -------------------------------------
def test_skipping_an_app_makes_the_persons_word_its_verdict_for_the_card_the_feeds_and_the_research(tmp_path, monkeypatch, capsys):
    no_outside(monkeypatch)
    finished_setup()
    before = setup_journal.load()["apps"]["card"]["starts_after"]
    monkeypatch.setattr(routine, "probe_tools", lambda *a, **k: pytest.fail("a skip asks Claude nothing"))
    assert start.main(["apps", "--skip", "google calendar", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] == ["Google Calendar"] and out["state"] == "done" and out["feeds"] == "mail: owner"
    record = setup_journal.load()["apps"]
    calendar = next(v for v in record["verdicts"] if v["app"] == "Google Calendar")
    assert calendar["verdict"] == "skip" and calendar["was"] == "work" and record["person_skips"] == ["Google Calendar"]
    assert not any("Google Calendar" in line for line in record["card"]["read"])
    assert any(line.startswith("Google Calendar") and "you asked me to skip it" in line for line in record["card"]["skip"])
    assert record["card"]["starts_after"] == before and record["state"] == "done"
    assert start.history_counts(setup_journal.load()) == {"2026-10": 212, "2026-09": 187}      # its records are not read
    assert start.APPS_RUN not in out.get("why", "") and "mcp__" not in out.get("why", "")


def test_a_skip_of_an_app_that_is_not_there_changes_nothing_and_names_the_apps(tmp_path, monkeypatch, capsys):
    no_outside(monkeypatch)
    finished_setup()
    before = setup_journal.load()["apps"]
    assert start.main(["apps", "--skip", "Dropbox", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] == [] and "Dropbox" in out["why"] and "Gmail" in out["why"] and out["state"] == "done"
    assert setup_journal.load()["apps"] == before


def test_after_the_build_a_skip_takes_the_app_out_of_the_routines_grant(tmp_path, monkeypatch, capsys):
    no_outside(monkeypatch)
    finished_setup()
    ws = make_space(tmp_path / "ada")
    grant_file = ws / "_meta" / "state" / "private" / "apps.yaml"
    onboard.write_yaml(grant_file, {"apps": [{k: v for k, v in a.items() if k != "per_month"} for a in (GMAIL, CALENDAR)]})
    assert start.main(["apps", "--skip", "Gmail", "--workspace", str(ws), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    kept = {v["app"]: v["verdict"] for v in routine._verdicts(ws)}
    assert kept == {"Gmail": "skip", "Google Calendar": "work"} and "routines" in out["why"]
    assert all("per_month" not in v for v in yaml.safe_load(grant_file.read_text(encoding="utf-8"))["apps"])


def test_reading_the_apps_again_keeps_what_the_person_skipped(tmp_path, monkeypatch, capsys):
    no_outside(monkeypatch)
    finished_setup()
    start.main(["apps", "--skip", "Google Calendar", "--json"])
    capsys.readouterr()
    tools = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Google_Calendar__list_events"]
    servers = [{"name": "claude.ai Gmail", "status": "connected"}, {"name": "claude.ai Google Calendar", "status": "connected"}]
    monkeypatch.setattr(routine, "probe_tools", lambda *a, **k: {"tools": tools, "mcp_servers": servers})
    monkeypatch.setattr(routine, "find_claude", lambda: "/bin/claude")
    monkeypatch.setattr(apps, "analyse", lambda groups, *a, **k: [dict(GMAIL), dict(CALENDAR)])
    assert start.main(["apps", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["feeds"] == "mail: owner" and any("Google Calendar" in line for line in out["card"]["skip"])
    assert next(v for v in setup_journal.load()["apps"]["verdicts"] if v["app"] == "Google Calendar")["verdict"] == "skip"



def probed_apps(monkeypatch):
    tools = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Google_Calendar__list_events"]
    servers = [{"name": "claude.ai Gmail", "status": "connected"}, {"name": "claude.ai Google Calendar", "status": "connected"}]
    monkeypatch.setattr(routine, "probe_tools", lambda *a, **k: {"tools": tools, "mcp_servers": servers})
    monkeypatch.setattr(routine, "find_claude", lambda: "/bin/claude")


def test_a_skip_said_while_the_apps_are_read_wins_and_drops_what_was_read_of_it(tmp_path, monkeypatch, capsys):
    """"Skip Google Calendar" while its reader runs: the phase reads the journal again before it writes, so the
    calendar is not a feed, not granted, and what its reader found is not kept (R40, I-5 b; probe P6). The skip
    itself leaves the running phase's state alone."""
    no_outside(monkeypatch)
    probed_apps(monkeypatch)
    assert start.main(["apps", "--probe", "--json"]) == 0
    capsys.readouterr()

    def analyse(groups, *a, **k):
        assert start.main(["apps", "--skip", "Google Calendar", "--json"]) == 0
        said = json.loads(capsys.readouterr().out)
        assert said["skipped"] == ["Google Calendar"] and said["state"] == "running"
        assert setup_journal.load()["apps"]["state"] == "running"
        return [dict(GMAIL), dict(CALENDAR)]
    monkeypatch.setattr(apps, "analyse", analyse)
    assert start.main(["apps", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    record = setup_journal.load()["apps"]
    calendar = next(v for v in record["verdicts"] if v["app"] == "Google Calendar")
    assert out["feeds"] == "mail: owner" and record["person_skips"] == ["Google Calendar"] and record["state"] == "done"
    assert calendar["verdict"] == "skip" and not {"per_month", "was"} & set(calendar) and calendar["account"] is None
    assert start.history_counts(setup_journal.load()) == {"2026-10": 212, "2026-09": 187}
    assert any(line.startswith("Google Calendar") for line in out["card"]["skip"])


def test_an_app_the_person_skipped_is_not_read_again_and_a_skip_before_any_verdict_keeps_the_state(tmp_path, monkeypatch,
                                                                                                  capsys):
    no_outside(monkeypatch)
    probed_apps(monkeypatch)
    assert start.main(["apps", "--probe", "--json"]) == 0
    assert start.main(["apps", "--skip", "Google Calendar", "--json"]) == 0
    capsys.readouterr()
    assert setup_journal.load()["apps"]["state"] == "pending"                  # the probe's state, not "running"
    asked = []

    def analyse(groups, *a, **k):
        asked.extend(g["app"] for g in groups)
        return [dict(GMAIL)]
    monkeypatch.setattr(apps, "analyse", analyse)
    assert start.main(["apps", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert asked == ["Gmail"] and out["unfinished"] == [] and out["state"] == "done" and out["feeds"] == "mail: owner"
    assert next(v for v in setup_journal.load()["apps"]["verdicts"] if v["app"] == "Google Calendar")["verdict"] == "skip"


# ----- Task 13 fix round 3 (Ruling R43): a skip holds wherever it lands -------------------------------------------
def six_apps(n=6) -> list:
    names = ["Gmail", "Google Calendar", "Slack", "Zoom", "Linear", "Notion"][:n]
    kinds = ["mail", "calendar", "chat", "calls", "tracker", "documents"][:n]
    return [{"app": a, "kinds": [k], "read": [f"mcp__claude_ai_{a.replace(' ', '_')}__read"], "write": [], "send": []}
            for a, k in zip(names, kinds)]


WORK = {"account": "ada@lanternworks.example", "verdict": "work", "per_month": {}, "why": "w"}
APPS_ARGS = types.SimpleNamespace(name=None, email="ada@lanternworks.example", company=None)


def say(app: str) -> dict:
    return start.skip_apps(types.SimpleNamespace(skip=[app], workspace=None))


def test_an_app_skipped_while_the_first_four_are_read_is_never_read(monkeypatch):
    """Probe Q2a: six apps, four readers at a time. "Skip Notion" is said while the first readers run, before any of
    them is through: Notion waits behind them, and its reader never starts (R43, N2-3)."""
    no_outside(monkeypatch)
    groups = six_apps()
    setup_journal.set_phase("apps", "running", groups=groups, not_signed_in=[])
    started, said, first = [], [], threading.Lock()

    def read(prompt, tools, schema, cwd, **kw):
        started.append(next(g["app"] for g in groups if g["read"][0] in tools))
        with first:                                                          # the others wait until it is said
            if not said:
                said.append(say("Notion"))
        return dict(WORK)
    monkeypatch.setattr(reader, "read", read)
    got = start._read_apps(APPS_ARGS, groups, [], [], "/bin/claude", None, {"groups": groups, "not_signed_in": []})
    notion = next(v for v in setup_journal.load()["apps"]["verdicts"] if v["app"] == "Notion")
    assert said[0]["skipped"] == ["Notion"] and "Notion" not in started and len(started) == 5
    assert notion["verdict"] == "skip" and got["unfinished"] == [] and "documents" not in got["feeds"]


def test_a_skip_before_the_apps_and_one_after_a_limit_are_never_read_and_never_counted_as_read(monkeypatch):
    """Probe Q2c: Slack skipped before the apps phase, then a limit while the calendar is read. The message counts
    the apps actually read (R43, N2-4); a skip after the limit ends the phase, and the phase run again reads only
    what is left of the person's apps."""
    no_outside(monkeypatch)
    groups = six_apps(3)
    setup_journal.set_phase("apps", "pending", groups=groups, not_signed_in=[])
    assert say("Slack")["state"] == "pending"
    seen = []

    def read(prompt, tools, schema, cwd, **kw):
        seen.append(next(g["app"] for g in groups if g["read"][0] in tools))
        if seen[-1] == "Google Calendar":
            raise reader.ReaderError("limit", "resets 03:00")
        return dict(WORK)
    real = apps.analyse
    monkeypatch.setattr(reader, "read", read)
    monkeypatch.setattr(apps, "analyse", lambda groups, *a, **k: real(groups, *a, **{**k, "workers": 1}))
    got = start._read_apps(APPS_ARGS, groups, [], [], "/bin/claude", None, {"groups": groups, "not_signed_in": []})
    assert got["why"].startswith("Your Claude limit was reached with 1 of 2 apps read (resets 03:00).")
    assert seen == ["Gmail", "Google Calendar"] and got["unfinished"] == ["Google Calendar"] and got["state"] == "waiting"
    said = say("Google Calendar")
    record = setup_journal.load()["apps"]
    assert said["state"] == "done" and record["unfinished"] == [] and record["feeds"] == "mail: owner"
    seen.clear()
    got = start._read_apps(APPS_ARGS, groups, [], [], "/bin/claude", None, {"groups": groups, "not_signed_in": []})
    assert seen == [] and got["feeds"] == "mail: owner" and got["state"] == "done"   # Gmail's verdict is kept (R45, I-2)


def test_a_skip_kept_only_as_the_persons_word_still_holds_wherever_the_verdicts_are_read(tmp_path, monkeypatch):
    """The skip's last race (R43, N2-8): a skip saved while the apps phase wrote its verdicts is in `person_skips` but
    not on the verdict. The feeds, the grant, the history's counts, the documents step and the work mailbox read the
    person's word too, so the app is left out everywhere."""
    no_outside(monkeypatch)
    drive = {"app": "Google Drive", "kinds": ["documents"], "read": ["mcp__claude_ai_Google_Drive__search_files"],
             "write": [], "send": [], "account": "ada@lanternworks.example", "verdict": "work",
             "per_month": {"2026-10": 5}, "why": "w"}
    journal = {"apps": {"verdicts": [dict(GMAIL), dict(CALENDAR), drive], "person_skips": ["Gmail", "Google Drive"],
                        "feeds": "mail: owner, calendar: owner, documents: owner"}}
    assert start.history_counts(journal) == {"2026-10": 40}
    assert start._documents_apps(journal) == []
    assert start.work_mailbox(journal, {"ada@lanternworks.example"}) == ""
    found = start.found_by_setup(types.SimpleNamespace(company=None, feeds=None, email=None, name="Ada Okafor",
                                                       domain=None, offline=True), journal, False)
    assert found["feeds"] == "calendar: owner" and found["connected"] == ["calendar"]
    ws = make_space(tmp_path / "ada", owner="Ada Okafor", owner_email="ada@lanternworks.example")
    start.keep_findings(types.SimpleNamespace(settings=None, workspace=ws), journal)
    kept = {v["app"]: v["verdict"] for v in yaml.safe_load((ws / routine.APPS_FILE).read_text(encoding="utf-8"))["apps"]}
    assert kept == {"Gmail": "skip", "Google Calendar": "work", "Google Drive": "skip"}


def test_a_skip_said_as_the_apps_phase_finishes_never_puts_back_its_old_state(monkeypatch):
    """A skip that read the phase before any verdict, and writes after the readers wrote theirs: it goes onto their
    verdicts, and the phase stays done (R43, N2-8)."""
    no_outside(monkeypatch)
    groups = six_apps(2)
    setup_journal.set_phase("apps", "running", groups=groups, not_signed_in=[])
    real_load, loads = setup_journal.load, []

    def load(*a, **k):
        loads.append(1)
        if len(loads) == 2:                                                   # the readers finish in between
            setup_journal.set_phase("apps", "done", verdicts=[{**g, **WORK} for g in groups], unfinished=[],
                                    feeds="mail: owner, calendar: owner")
        return real_load(*a, **k)
    monkeypatch.setattr(setup_journal, "load", load)
    said = say("Google Calendar")
    monkeypatch.setattr(setup_journal, "load", real_load)
    record = setup_journal.load()["apps"]
    assert said["skipped"] == ["Google Calendar"] and record["state"] == "done" and record["feeds"] == "mail: owner"
    assert next(v for v in record["verdicts"] if v["app"] == "Google Calendar")["verdict"] == "skip"

# ----- the briefing address: one the person is known to have, never a reader's word alone --------
def _person(email=None, domain=None, offline=True):
    return types.SimpleNamespace(email=email, name="Ada Okafor", domain=domain, offline=offline)


def _journal(*verdicts, claude_email="ada@lanternworks.example", card_domain="lanternworks.example", domain_from="address"):
    """The setup journal; the card's domain is the person's address's by default, as the research phase makes it."""
    return {"signins": {"claude_email": claude_email},
            "research": {"company": {"name": "Lantern Works", "domain": card_domain, "domains": [card_domain],
                                     "domain_from": domain_from}},
            "apps": {"verdicts": list(verdicts)}}


@pytest.mark.parametrize("account, taken", [
    ("ada@lanternworks.example", "ada@lanternworks.example"),                  # the person's own address
    ("Ben@LanternWorks.example", "ben@lanternworks.example"),                  # on the company's domain
    ("Ada Okafor <ada@lanternworks.example>", "ada@lanternworks.example"),     # a name and an address
    ("ada@lanternworks.example (work)", "ada@lanternworks.example"),
    ("x@attacker.example", ""),                                                # an outside address a mail could name
    ("ada@lanternworks.example.attacker.example", ""),                         # a look-alike of the domain
    ("ada@lanternworks.example, eve@attacker.example", ""),                    # two addresses: which one?
    ("ada", ""), ("", "")])
def test_the_work_mailbox_is_an_account_the_person_has_or_one_on_the_companys_domain(account, taken):
    known, domains = {"ada@lanternworks.example"}, {"lanternworks.example"}
    assert start.work_mailbox({"apps": {"verdicts": [{**GMAIL, "account": account}]}}, known, domains) == taken


def test_only_a_mail_app_read_as_work_gives_the_work_mailbox():
    known, domains = {"ada@lanternworks.example"}, {"lanternworks.example"}
    journal = {"apps": {"verdicts": [{**CALENDAR, "account": "cal@lanternworks.example"},          # not a mail app
                                     {**GMAIL, "verdict": "mixed"}, {**GMAIL, "verdict": "someone-else"},
                                     {**GMAIL, "app": "Fastmail", "account": " ben@lanternworks.example "}, GMAIL]}}
    assert start.work_mailbox(journal, known, domains) == "ben@lanternworks.example"
    assert start.work_mailbox({}, known, domains) == "" and start.work_mailbox({"apps": "pending"}, known, domains) == ""


def test_a_mail_provider_is_never_a_companys_domain():
    journal = {"apps": {"verdicts": [{**GMAIL, "account": "eve@gmail.com"}]}}
    assert start.work_mailbox(journal, {"ada@gmail.com"}, {"gmail.com"}) == ""        # even when a domain list says so
    journal = {"apps": {"verdicts": [{**GMAIL, "account": "ada@gmail.com"}]}}
    assert start.work_mailbox(journal, {"ada@gmail.com"}, set()) == "ada@gmail.com"   # but the person's own address counts


def test_the_briefing_address_comes_from_the_first_source_that_has_one(monkeypatch):
    git = {"value": "ada.private@example.org"}
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: git["value"] if "user.email" in argv else "")
    work = _journal(GMAIL)
    assert start.briefing_address(_person(email="ada@desk.example"), work) == ("ada@desk.example", "asked")
    assert start.briefing_address(_person(), work) == ("ada@lanternworks.example", "work-mailbox")
    # a mailbox that names an outside address is no briefing address: git's is next, then the sign-in's
    foreign = _journal({**GMAIL, "account": "x@attacker.example"})
    assert start.briefing_address(_person(), foreign) == ("ada.private@example.org", "git")
    git["value"] = ""
    assert start.briefing_address(_person(), foreign) == ("ada@lanternworks.example", "sign-in")
    assert start.briefing_address(_person(), _journal(GMAIL, claude_email="", domain_from="flag")) == (
        "ada@lanternworks.example", "work-mailbox")
    assert start.briefing_address(_person(), _journal({**GMAIL, "account": "x@attacker.example"}, claude_email="")) == ("", "asked")
    git["value"] = "1+x@users.noreply.github.com"   # a host's no-reply address cannot receive anything
    assert start.briefing_address(_person(), _journal(claude_email="")) == ("", "asked")


def test_the_companys_domain_is_not_taken_from_the_mailbox_that_names_it(monkeypatch):
    """The researched card, the --domain flag and the person's own addresses give the domains; the reader's
    account does not: with none of them, a company-looking account is not enough."""
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "")
    lone = _journal({**GMAIL, "account": "ben@lanternworks.example"}, claude_email="", card_domain="")
    assert start.briefing_address(_person(), lone) == ("", "asked")
    assert start.briefing_address(_person(domain="www.lanternworks.example"), lone) == ("ben@lanternworks.example", "work-mailbox")
    card = _journal({**GMAIL, "account": "ben@lanternworks.example"}, claude_email="", domain_from="flag")
    assert start.briefing_address(_person(), card) == ("ben@lanternworks.example", "work-mailbox")


def test_an_address_an_app_names_that_the_person_does_not_have_is_not_the_briefing_address(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup([{**GMAIL, "account": "x@attacker.example"}, CALENDAR, OUTLOOK])
    result = start.phase_build(args(tmp_path))
    assert result["state"] == "done"
    assert spaceyaml.load(result["workspace"]).owner_email == "ada.private@example.org"   # git's
    assert setup_journal.load()["build"]["owner_email_source"] == "git"
    assert not (Path(result["workspace"]) / "_inbound" / "needs-you" / "onboard-briefing.yaml").exists()


def test_without_any_address_one_plain_item_asks_and_the_build_waits_for_it(tmp_path, monkeypatch):
    no_outside(monkeypatch, git_email="")
    finished_setup([{**GMAIL, "account": "x@attacker.example"}, CALENDAR], claude_email="")
    result = start.phase_build(args(tmp_path))
    workspace = Path(result["workspace"])
    assert spaceyaml.load(workspace).owner_email == ""
    [item] = yaml.safe_load((workspace / "_inbound" / "needs-you" / "onboard-briefing.yaml").read_text(encoding="utf-8"))["items"]
    assert item["id"] == "onboard-briefing-address" and "email address" in item["text"] and "morning briefing" in item["text"]
    assert "check" not in item and "--" not in item["text"] and "_meta" not in item["text"]   # plain words, no command
    assert item["default"] == "no morning briefing is sent"
    # the same kind of row as the other things only the person can give: the build waits, and says so in the result
    assert result["state"] == "waiting" and [r["name"] for r in result["yours"]] == ["briefing address"]
    assert setup_journal.load()["build"]["owner_email_source"] == "asked" and setup_journal.load()["build"]["owner_email"] == ""
    # their answer, said again with the sentence: the item is withdrawn and the build is done
    answered = start.phase_build(args(tmp_path, email="ada@lanternworks.example"))
    assert answered["state"] == "done" and spaceyaml.load(workspace).owner_email == "ada@lanternworks.example"
    assert not yaml.safe_load((workspace / "_inbound" / "needs-you" / "onboard-briefing.yaml").read_text(encoding="utf-8"))["items"]
    assert setup_journal.load()["build"]["owner_email_source"] == "asked"


PROVIDERS = ["hotmail.nl", "outlook.nl", "yahoo.co.uk", "msn.com", "ziggo.nl", "fastmail.com", "gmail.com"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_persons_mail_provider_is_not_their_companys_domain(provider, monkeypatch):
    """Whoever has an address at a provider shares the provider with strangers: a reader-named stranger on it
    is not the person. Only the person's exact address counts, unless a card or --domain names a company."""
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: f"ada@{provider}" if "user.email" in argv else "")
    stranger = {**GMAIL, "account": f"eve@{provider}"}
    assert start.briefing_address(_person(), _journal(stranger, claude_email="", card_domain="")) == (f"ada@{provider}", "git")
    # the sign-in's address is the person's too, and just as far from making the provider theirs
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "")
    assert start.briefing_address(_person(), _journal(stranger, claude_email=f"ada@{provider}", card_domain="")) == (
        f"ada@{provider}", "sign-in")
    # a card or --domain that names the provider does not make it a company's either
    assert start.briefing_address(_person(domain=provider), _journal(stranger, claude_email="", card_domain=provider)) == ("", "asked")
    # the person's own address, named by the app, is still the work mailbox: equality
    mine = {**GMAIL, "account": f"ADA@{provider.upper()}"}
    assert start.briefing_address(_person(), _journal(mine, claude_email=f"ada@{provider}", card_domain="")) == (
        f"ada@{provider}", "work-mailbox")


def test_a_known_address_gives_no_company_domain_even_off_the_provider_list(monkeypatch):
    """The domain of git's or the sign-in's address is not a company domain on its own: only --domain and the
    researched card name those (the card is built from the person's own address, never from an app's account)."""
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "ada@lanternworks.example" if "user.email" in argv else "")
    colleague = {**GMAIL, "account": "ben@lanternworks.example"}
    assert start.briefing_address(_person(), _journal(colleague, claude_email="", card_domain="")) == (
        "ada@lanternworks.example", "git")
    assert start.briefing_address(_person(), _journal(colleague, claude_email="", domain_from="flag")) == (
        "ben@lanternworks.example", "work-mailbox")
    assert start.briefing_address(_person(domain="lanternworks.example"), _journal(colleague, claude_email="", card_domain="")) == (
        "ben@lanternworks.example", "work-mailbox")


@pytest.mark.parametrize("provider, domain", [("ziggo.nl", ""), ("hotmail.nl", ""), ("gmail.com", ""), ("fastmail.com", ""),
                                              ("lanternworks.example", "lanternworks.example")])
def test_the_company_card_takes_no_mail_providers_domain(monkeypatch, provider, domain):
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "")
    named = types.SimpleNamespace(name="Ada Okafor", email=f"ada@{provider}", company=None, domain=None)
    assert start._company_card(named, {})["domain"] == domain
    assert start._company_card(types.SimpleNamespace(name="Ada Okafor", email=None, company=None, domain=f"www.{provider}"),
                               {"signins": {}})["domain"] == f"{provider}"   # what the person says is what it is


def test_apps_judge_work_against_no_mail_providers_domain():
    assert apps.company_of({}, "Lantern Works", ["ada@hotmail.nl", "ada@lanternworks.example"])["domains"] == ["lanternworks.example"]
    assert apps.company_of({}, None, ["ada@ziggo.nl"])["domains"] == []


def test_the_journal_says_where_the_address_came_from(tmp_path):
    ws = make_space(tmp_path / "ada", owner_email="ben@lanternworks.example")
    report = onboard.Report("start")
    report.workspace, report.settings = ws, []

    def source(chosen, journal=None, invited=""):
        report.invited = invited
        start.keep_findings(report, journal or {}, chosen)
        return setup_journal.load()["build"]["owner_email_source"]
    # the address this run chose is the one there: its source
    assert source(("ben@lanternworks.example", "git")) == "git"
    # a colleague's invitation had set another one first
    assert source(("ada@lanternworks.example", "work-mailbox"), invited="ben@lanternworks.example") == "invitation"
    # an address an earlier run set stays what that run said it was
    earlier = {"build": {"owner_email": "ben@lanternworks.example", "owner_email_source": "sign-in"}}
    assert source(("ada@lanternworks.example", "work-mailbox"), earlier) == "sign-in"
    # one nothing here set (a hand edit) is said to be kept, not given a source it never had
    assert source(("ada@lanternworks.example", "work-mailbox"), {"build": {"owner_email": "eve@x.example"}}) == "kept"
    # none at all: the person was asked
    (ws / "space.yaml").write_text((ws / "space.yaml").read_text(encoding="utf-8").replace("ben@lanternworks.example", ""), encoding="utf-8")
    assert source(("", "asked")) == "asked" and setup_journal.load()["build"]["owner_email"] == ""


@pytest.mark.parametrize("isp", ["home.nl", "planet.nl", "some-isp.example"])
def test_a_card_domain_taken_from_an_address_never_counts_for_the_briefing(monkeypatch, isp):
    """An internet provider on no list is the card's domain when the person's own address sits on it (the card
    takes the first address that is not a listed provider's), and strangers share it: only a domain the person
    typed (--domain) or the site confirmed is a company's for this check."""
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: f"ada@{isp}" if "user.email" in argv else "")
    card = start._company_card(types.SimpleNamespace(name="Ada Okafor", email=None, company=None, domain=None), {"signins": {}})
    assert card["domain"] == isp and card["domain_from"] == "address"
    journal = {"signins": {}, "research": {"company": card}, "apps": {"verdicts": [{**GMAIL, "account": f"eve@{isp}"}]}}
    assert start.briefing_address(_person(), journal) == (f"ada@{isp}", "git")
    # the person's own exact address, named by the app, is still the work mailbox
    journal["apps"]["verdicts"] = [{**GMAIL, "account": f"Ada <ada@{isp}>"}]
    assert start.briefing_address(_person(), journal) == (f"ada@{isp}", "work-mailbox")


def test_the_sign_in_on_an_internet_provider_makes_no_company_domain_for_the_briefing(monkeypatch):
    """The sign-in's address is tried for the card before git's: on an unlisted provider it becomes the card's
    domain, and a colleague on the company's own domain is not the person either."""
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "ada@lanternworks.example" if "user.email" in argv else "")
    signins = {"claude_email": "ada@planet.nl"}
    card = start._company_card(types.SimpleNamespace(name="Ada Okafor", email=None, company=None, domain=None), {"signins": signins})
    assert card["domain"] == "planet.nl" and card["domain_from"] == "address"
    for stranger in ("eve@planet.nl", "ben@lanternworks.example"):
        journal = {"signins": signins, "research": {"company": card}, "apps": {"verdicts": [{**GMAIL, "account": stranger}]}}
        assert start.briefing_address(_person(), journal) == ("ada@lanternworks.example", "git")


@pytest.mark.parametrize("marker, taken", [("flag", True), ("site", True), ("address", False), ("", False), (None, False),
                                          ("surprise", False), ("missing", False)])
def test_only_a_card_domain_the_person_typed_or_the_site_confirmed_counts_and_an_old_card_does_not(monkeypatch, marker, taken):
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "")
    journal = _journal({**GMAIL, "account": "ben@lanternworks.example"}, claude_email="")
    if marker == "missing":   # a card written before the marker existed
        journal["research"]["company"].pop("domain_from", None)
    else:
        journal["research"]["company"]["domain_from"] = marker
    assert start.briefing_address(_person(), journal) == (("ben@lanternworks.example", "work-mailbox") if taken else ("", "asked"))


def test_the_flag_makes_the_cards_domain_and_a_company_domain_account_is_accepted(monkeypatch):
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "")
    named = types.SimpleNamespace(name="Ada Okafor", email="ada@lanternworks.example", company=None, domain="www.lanternworks.example")
    card = start._company_card(named, {})
    assert card["domain"] == "lanternworks.example" and card["domain_from"] == "flag"
    assert start._company_card(types.SimpleNamespace(name="Ada Okafor", email=None, company=None, domain=None), {})["domain_from"] == ""
    journal = {"signins": {}, "research": {"company": card}, "apps": {"verdicts": [{**GMAIL, "account": "ben@lanternworks.example"}]}}
    assert start.briefing_address(_person(), journal) == ("ben@lanternworks.example", "work-mailbox")
    # and so is the flag given to the build itself, whatever the card says
    journal["research"]["company"] = {**card, "domain": "", "domains": [], "domain_from": ""}
    assert start.briefing_address(_person(domain="lanternworks.example"), journal) == ("ben@lanternworks.example", "work-mailbox")


def test_the_marker_changes_nothing_else_the_card_feeds(monkeypatch):
    card = {"name": "Lantern Works", "domain": "lanternworks.example", "domains": ["lanternworks.example"], "domain_from": "address"}
    assert apps.company_of({"research": {"company": card}}, None, [])["domains"] == ["lanternworks.example"]
    assert research.rivals(card, {}, {"verdicts": [{"account": "ben@beaconlabs.example"}]}) == ["beaconlabs"]


def test_a_capitalised_invitation_address_is_still_the_invitations(tmp_path):
    """The space holds the invitation's address as it was typed; the invitation is compared without case."""
    ws = make_space(tmp_path / "ada", owner_email="Ben@LanternWorks.example")
    report = onboard.Report("start")
    report.workspace, report.settings, report.invited = ws, [], "ben@lanternworks.example"
    start.keep_findings(report, {}, ("ada@lanternworks.example", "work-mailbox"))
    assert setup_journal.load()["build"]["owner_email_source"] == "invitation"
    # likewise the address this run chose, and the one an earlier run recorded
    report.invited = ""
    start.keep_findings(report, {}, ("ben@lanternworks.example", "git"))
    assert setup_journal.load()["build"]["owner_email_source"] == "git"
    earlier = {"build": {"owner_email": "ben@lanternworks.example", "owner_email_source": "sign-in"}}
    start.keep_findings(report, earlier, ("ada@lanternworks.example", "work-mailbox"))
    assert setup_journal.load()["build"]["owner_email_source"] == "sign-in"


def test_a_colleague_whose_invitation_holds_a_capitalised_address_is_recorded_as_invited(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup()
    brain = brain_with_invitation(tmp_path, email="Ben@LanternWorks.example")
    result = start.phase_build(args(tmp_path, invitation=f"{brain.as_posix()}:ada"))
    assert spaceyaml.load(result["workspace"]).owner_email == "Ben@LanternWorks.example"
    assert setup_journal.load()["build"]["owner_email_source"] == "invitation"


@pytest.mark.parametrize("typed", ["ada", "ada@lanternworks", "a@b.example, c@d.example", "no address here"])
def test_a_typed_email_that_is_not_one_address_is_refused_in_one_plain_line(tmp_path, monkeypatch, capsys, typed):
    no_outside(monkeypatch)
    finished_setup()
    result = start.phase_build(args(tmp_path, email=typed, json=False))
    said = capsys.readouterr().out.strip().splitlines()
    assert result["state"] == "failed" and "is not an email address" in result["why"] and typed in result["why"]
    assert said == [f"build: failed: {result['why']}"]   # one line, in plain words
    assert not (tmp_path / "spaces").exists()   # nothing was made


def test_a_typed_email_replaces_an_address_derived_before_and_a_derived_one_never_does(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup([{**GMAIL, "account": "x@attacker.example"}, CALENDAR, OUTLOOK])
    first = start.phase_build(args(tmp_path))
    spaces = [Path(first["workspace"]), Path(first["spaces"]["brain"])]
    assert [spaceyaml.load(s).owner_email for s in spaces] == ["ada.private@example.org"] * 2
    # a better derivation on a later run (the work mailbox is now one the person has) does not replace what is set
    finished_setup()
    again = start.phase_build(args(tmp_path))
    assert [spaceyaml.load(s).owner_email for s in spaces] == ["ada.private@example.org"] * 2 and again["state"] == "done"
    assert setup_journal.load()["build"]["owner_email_source"] == "git"   # where that one came from, not the new one's
    # what the person types is their choice: it replaces it, in both of their spaces, and the file is otherwise as it was
    before = (spaces[0] / "space.yaml").read_text(encoding="utf-8")
    typed = start.phase_build(args(tmp_path, email="Ada <ada@desk.example>"))
    assert [spaceyaml.load(s).owner_email for s in spaces] == ["ada@desk.example"] * 2 and typed["state"] == "done"
    assert (spaces[0] / "space.yaml").read_text(encoding="utf-8") == before.replace("ada.private@example.org", "ada@desk.example")
    assert setup_journal.load()["build"]["owner_email_source"] == "asked"


def test_a_task_prefix_that_is_not_ours_fails_the_build_in_plain_words(tmp_path, monkeypatch):
    no_outside(monkeypatch)
    finished_setup()
    monkeypatch.setenv("SPACES_TASK_PREFIX", "spaces--x-")   # one letter off a person's own tasks
    result = start.phase_build(args(tmp_path))
    assert result["state"] == "failed" and result["why"] == start.TASK_PREFIX_WHY and not (tmp_path / "spaces").exists()
    assert "SPACES_TASK_PREFIX" in result["detail"] and "SPACES_" not in result["why"]   # the setting's name: journal only


def _finishing(tmp_path, monkeypatch, org_answer):
    """A built workspace and an online machine: Windows, claude installed, tasks that read back, and the
    organisation's base permission as GitHub would say it (`org_answer` is its output; "" is a personal
    account, which GitHub answers with an error)."""
    monkeypatch.setattr(onboard, "SYSTEM", "Windows")
    monkeypatch.setattr(routine, "find_claude", lambda: "claude")
    calls = []
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, "".join(onboard.WINDOWS_TASK)),
                                                   "gh api orgs/lantern-works": (0, org_answer) if org_answer else (1, "")}, calls))
    ws = make_space(tmp_path / "ada", name="ada", owner_email="ada@lanternworks.example")
    return ws, onboard.Report("start"), calls


def _gh(calls):
    return [c[0] for c in calls if c[0][:1] == ["gh"]]


def test_online_the_routines_are_os_tasks_registered_by_the_build(tmp_path, monkeypatch):
    ws, report, calls = _finishing(tmp_path, monkeypatch, "none")
    routines = start.finish_workspace(report, ws, "Ada Okafor", "lantern-works", offline=False)
    assert [r["name"] for r in routines] == ["backfill-night", "daily-chain", "sweep-chain"]
    created = [c[0][c[0].index("/TN") + 1] for c in calls if c[0][:2] == ["schtasks", "/Create"]]
    assert created == ["space-backfill-night-ada", "space-daily-chain-ada", "space-sweep-chain-ada"]
    assert [t["name"] for t in onboard.read_yaml(ws / "_meta" / "state" / "onboard.yaml")["routine_tasks"]] == created
    assert all(s.done for s in report.steps) and not any("--os-routines" in s.result for s in report.steps)


def test_an_isolated_run_registers_only_tasks_under_its_own_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACES_TASK_PREFIX", "space-test-1a2b-")
    ws, report, calls = _finishing(tmp_path, monkeypatch, "none")
    start.finish_workspace(report, ws, "Ada Okafor", "lantern-works", offline=False)
    created = [c[0][c[0].index("/TN") + 1] for c in calls if c[0][:2] == ["schtasks", "/Create"]]
    assert created == ["space-test-1a2b-backfill-night-ada", "space-test-1a2b-daily-chain-ada",
                       "space-test-1a2b-sweep-chain-ada"]


@pytest.mark.parametrize("permission", ["read", "write", "admin"])
def test_an_organisation_whose_members_read_each_others_repositories_is_one_owner_item(tmp_path, monkeypatch, permission):
    ws, report, calls = _finishing(tmp_path, monkeypatch, permission)
    start.finish_workspace(report, ws, "Ada Okafor", "lantern-works", offline=False)
    assert _gh(calls) == [["gh", "api", "orgs/lantern-works", "-q", ".default_repository_permission"]]   # a read, nothing else
    [item] = yaml.safe_load((ws / "_inbound" / "needs-you" / "onboard-org.yaml").read_text(encoding="utf-8"))["items"]
    assert item["id"] == "onboard-org-members" and item["kind"] == "question"
    assert "lantern-works" in item["text"] and "Settings > Member privileges > Base permissions: No permission" in item["text"]
    assert item["recommendation"] == "set it to No permission" and "lantern-works" in item["default"]
    assert "check" not in item   # as the other onboard items: no command on the person's list
    assert all(s.done for s in report.steps)   # something to do when there is time, never a stop


@pytest.mark.parametrize("answer", ["none", "", "null", "surprise"])
def test_a_private_base_permission_an_unknown_one_or_a_personal_account_raises_nothing(tmp_path, monkeypatch, answer):
    ws, report, _ = _finishing(tmp_path, monkeypatch, answer)   # "" is what a personal account (no such organisation) gives
    start.finish_workspace(report, ws, "Ada Okafor", "lantern-works", offline=False)
    assert not (ws / "_inbound" / "needs-you" / "onboard-org.yaml").exists()


def test_a_later_run_withdraws_the_item_once_the_permission_is_none(tmp_path, monkeypatch):
    ws, report, _ = _finishing(tmp_path, monkeypatch, "read")
    start.finish_workspace(report, ws, "Ada Okafor", "lantern-works", offline=False)
    assert (ws / "_inbound" / "needs-you" / "onboard-org.yaml").exists()
    monkeypatch.setattr(onboard, "run_cmd", world({"schtasks /Query": (0, "".join(onboard.WINDOWS_TASK)),
                                                   "gh api orgs/lantern-works": (0, "none")}, []))
    start.finish_workspace(onboard.Report("start"), ws, "Ada Okafor", "lantern-works", offline=False)
    assert not (yaml.safe_load((ws / "_inbound" / "needs-you" / "onboard-org.yaml").read_text(encoding="utf-8")) or {}).get("items")


def test_offline_the_organisation_is_not_read(tmp_path, monkeypatch):
    ws, report, calls = _finishing(tmp_path, monkeypatch, "read")
    start.finish_workspace(report, ws, "Ada Okafor", "lantern-works", offline=True)
    assert calls == [] and not (ws / "_inbound" / "needs-you" / "onboard-org.yaml").exists()


def test_the_organisation_read_is_for_the_account_the_workspace_landed_in(tmp_path, monkeypatch):
    """The person's setup falls back to their own account when the organisation refuses the workspace:
    the base permission to look at is that account's."""
    brain = brain_with_invitation(tmp_path)
    made, finished = [], []

    def create(report, root, dirname, repo, offline, org, home, tpl, dry_run):
        made.append(org)
        if org == "acme":   # no right to create in the organisation
            return None, f"{org}/{repo}"
        dst = tmp_path / "ada-workspace"
        dst.mkdir()
        return dst, f"{org}/{repo}"
    monkeypatch.setattr(onboard, "create_space", create)
    monkeypatch.setattr(onboard, "run", lambda dst, answers, **k: onboard.Report("person"))
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: json.dumps({"login": "tess-v"}) if argv[:3] == ["gh", "api", "user"] else "")
    monkeypatch.setattr(start, "finish_workspace", lambda report, workspace, person, org, offline: finished.append(org) or [])
    report = onboard.Report("start")
    start.setup(report, TEMPLATE, f"{brain.as_posix()}:ada", None, None, "Ada Okafor", "ada@lanternworks.example", None, "acme",
                tmp_path / "home", "acme/Brain", False, False, None)
    assert made == ["acme", "tess-v"] and finished == ["tess-v"]


# ----- one computer, one runner (Task 14) ---------------------------------------------------------------
def test_a_second_computers_build_schedules_and_proves_no_routines_and_says_where_they_run(tmp_path, monkeypatch):
    """The build goes through the routines' registration: on a second computer it registers nothing, proves
    nothing, and the step that says where the routines run is on the progress page; the build is done."""
    no_outside(monkeypatch)
    finished_setup()
    real = start.start
    monkeypatch.setattr(start, "start", lambda *a, **k: real(*a, **{**k, "offline": True}))   # the spaces offline; the proof online
    proved = []
    monkeypatch.setattr(start, "prove_routines", lambda report, ws: proved.append(ws) or {"ok": True, "checks": [], "notes": [], "why": None})
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    first = start.phase_build(args(tmp_path, offline=False))
    assert first["state"] == "done" and len(proved) == 1   # the computer that runs them proves them
    routine.claim_host(Path(first["workspace"]), "os", dt.datetime.now().astimezone())
    monkeypatch.setattr(routine, "this_machine", lambda: "m-laptop")
    second = start.phase_build(args(tmp_path, offline=False))
    assert second["state"] == "done", second
    assert len(proved) == 1 and second["probe"] is None and "routines_where" not in first
    where = "Your routines run on ADA-DESKTOP; say 'run my routines here' to move them"
    assert second["routines_where"] == where   # the build's answer carries it, for the built message
    step = next(s for s in last_report().steps if s.name == "routines on this computer")
    assert step.done and step.result == where
    page = (Path.home() / ".spaces" / "onboarding.html").read_text(encoding="utf-8")
    stages = dict((title, state) for state, title in
                  re.findall(r'<li class="step state-([a-z-]+)"[^>]*>.*?<span class="name">([^<]+)', page, re.S))
    assert stages["Routines"] == "done"   # the step files under Routines on the progress page, and fails nothing
    assert "Your routines run on ADA-DESKTOP. Say &#x27;run my routines here&#x27; to run them on this computer." in page
    assert routine.host(Path(second["workspace"]))["machine"] == "m-desktop"


def test_the_history_phase_while_the_routines_run_in_the_cloud_says_a_computer_reads_it(tmp_path, monkeypatch):
    """The cloud is no computer the history is planned, measured or read on: the phase writes nothing and says, in
    the history's own sentence, that the routines come back to a computer first."""
    import backfill
    no_outside(monkeypatch)
    finished_setup()
    ws = make_space(tmp_path / "ada", owner="Ada Okafor")
    start.render_routines(ws, "Ada Okafor")
    routine.claim_cloud(ws, {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}, "Europe/Amsterdam", ["ada/ada"],
                        {"read": [], "write": [], "send": []}, dt.datetime.now().astimezone())
    monkeypatch.setattr(onboard, "run_now", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a part started")))
    given = args(tmp_path, reading="five_hour=5,week=26,five_hour_resets=2026-10-25T05:00:00+02:00,"
                                   "week_resets=2026-10-29T06:00:00+02:00,extra=0")
    given.phase, given.workspace = "history", str(ws)
    result = start.phase_history(given)
    assert result["why"] == backfill.CLOUD_WHY and "on the cloud" not in result["why"]
    assert not backfill.load(ws).get("parts") and not backfill.load(ws).get("reading")


def test_the_history_phase_while_the_routines_run_in_the_cloud_says_a_history_read_before_the_move_as_it_is(
        tmp_path, monkeypatch):
    """The routines move to the cloud once the history is read, so a plan made on the computer before the move is
    said as for any record: read, with its parts. The phase still writes nothing and starts no part."""
    import backfill
    no_outside(monkeypatch)
    finished_setup()
    ws = make_space(tmp_path / "ada", owner="Ada Okafor")
    start.render_routines(ws, "Ada Okafor")
    backfill.plan(ws, {"2026-09": 30, "2026-10": 40}, dt.datetime.now().astimezone())
    state = backfill.load(ws)
    state["parts"] = [{**part, "status": "done"} for part in state["parts"]]
    backfill.save(ws, state)
    routine.claim_cloud(ws, {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}, "Europe/Amsterdam", ["ada/ada"],
                        {"read": [], "write": [], "send": []}, dt.datetime.now().astimezone())
    before = (ws / backfill.PATH).read_bytes()
    monkeypatch.setattr(onboard, "run_now", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a part started")))
    given = args(tmp_path, reading="five_hour=5,week=26,five_hour_resets=2026-10-25T05:00:00+02:00,"
                                   "week_resets=2026-10-29T06:00:00+02:00,extra=0")
    given.phase, given.workspace = "history", str(ws)
    result = start.phase_history(given)
    assert result["state"] == "done" and result["why"] == "The history is read.", result
    assert result["parts"] == state["parts"] and backfill.CLOUD_WHY not in result.values()
    assert (ws / backfill.PATH).read_bytes() == before
    (ws / backfill.PATH).write_text("parts: [\n", encoding="utf-8")   # notes on a plan that cannot be read
    result = start.phase_history(given)
    assert (result["state"], result["why"]) == ("waiting", backfill.BROKEN_WHY)
    assert (ws / backfill.PATH).read_text(encoding="utf-8") == "parts: [\n"


def test_the_history_phase_while_the_routines_run_in_the_cloud_says_a_computer_reads_the_rest(tmp_path, monkeypatch):
    """A plan made before the move with parts still to read: the cloud reads none of them, so the phase says, in the
    history's own sentence, that the routines come back to a computer first. It writes nothing and starts no part."""
    import backfill
    no_outside(monkeypatch)
    finished_setup()
    ws = make_space(tmp_path / "ada", owner="Ada Okafor")
    start.render_routines(ws, "Ada Okafor")
    backfill.plan(ws, {"2026-09": 30, "2026-10": 40}, dt.datetime.now().astimezone())   # no part read yet
    state = backfill.load(ws)
    routine.claim_cloud(ws, {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}, "Europe/Amsterdam", ["ada/ada"],
                        {"read": [], "write": [], "send": []}, dt.datetime.now().astimezone())
    before = (ws / backfill.PATH).read_bytes()
    monkeypatch.setattr(onboard, "run_now", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a part started")))
    given = args(tmp_path)
    given.phase, given.workspace = "history", str(ws)
    result = start.phase_history(given)
    assert (result["state"], result["why"]) == ("running", backfill.CLOUD_WHY), result
    assert result["parts"] == state["parts"] and (ws / backfill.PATH).read_bytes() == before


def test_the_probe_holds_the_start_lock_and_waits_for_no_second_start(tmp_path, monkeypatch):
    ws = make_space(tmp_path / "ada", name="ada")
    monkeypatch.setattr(onboard, "register_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError("registered")))
    lock = start.take_lock()   # another start runs on this computer
    report = onboard.Report(case="routines")
    got = start.prove_routines(report, ws)
    assert got["ok"] is False and got["why"] == start.PROBE_STARTING and not report.steps[-1].done
    assert "setup" in got["why"] and "start.py" not in got["why"]
    lock.unlink()
    seen = []

    def register(report, label, task, root, argv, **kw):
        seen.append(start.lock_path().exists())   # held while the probe task exists
        report.add(label, False, "schtasks /Query", "refused")
        return {"name": task}
    monkeypatch.setattr(onboard, "register_task", register)
    import offboard
    monkeypatch.setattr(offboard, "remove_sync_task", lambda report, *a, **k: report.add("removed", True, "", ""))
    start.prove_routines(onboard.Report(case="routines"), ws)
    assert seen == [True] and not start.lock_path().exists()   # and let go of after


def test_a_paused_run_ends_the_wait_for_a_part_and_is_no_reading_of_the_account(tmp_path):
    """A run the pause held started nothing: waiting on it is no use, and it says nothing of the routines' account."""
    ws = make_space(tmp_path / "ada", name="ada")
    since = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.timezone.utc)
    paused = {**routine._stopped("paused until 2026-10-25 06:00"), "outcome": "paused"}
    routine.write_outcome(ws, "sweep-chain", paused, since + dt.timedelta(minutes=2))
    assert start._stopped_since(ws, since)
    reading = {"at": "2026-10-24T12:05:00Z", "five_hour_resets": "2026-10-24T16:00:00Z", "week_resets": "2026-10-30T09:00:00Z"}
    assert start.usage_account(ws, reading, {"started": since.isoformat()}) is None   # no run that started to compare



# ===== Fix wave F2 (Rulings R45 and R47) =========================================================================
def offline_build(tmp_path, monkeypatch):
    """`start.py build --offline` as the agent runs it: through `run_phase`, which journals it."""
    no_outside(monkeypatch)
    finished_setup()
    result = start.run_phase(args(tmp_path))
    assert result["state"] == "done", result
    return result


def test_the_builds_record_keeps_the_workspace_and_the_history_finds_it_there(tmp_path, monkeypatch):
    """After an offline build in another process, the history reads the workspace from the build's record (R45, F-3)."""
    result = offline_build(tmp_path, monkeypatch)
    record = setup_journal.load()["build"]
    assert record["workspace"] == result["workspace"]
    (Path.home() / onboard.MACHINE_SPACES).unlink(missing_ok=True)               # nothing else names it
    monkeypatch.setattr(start, "LAST_REPORT", None)                            # another process
    found = start._history_workspace(types.SimpleNamespace(workspace=None, name=None, email=None), setup_journal.load())
    assert found == Path(result["workspace"])


def test_the_page_after_a_finished_build_says_nothing_is_building(tmp_path, monkeypatch):
    """The last page the build writes is written once the build is marked done (R45, F-4)."""
    offline_build(tmp_path, monkeypatch)
    page = (Path.home() / ".spaces" / "onboarding.html").read_text(encoding="utf-8")
    assert "Building your spaces" not in page


def test_the_journal_keeps_a_plain_why_and_whether_the_run_was_offline(tmp_path, monkeypatch):
    """Beside each phase's state the journal keeps a plain `why` and an offline marker (R45, F-6); a phase that
    raised keeps a plain why, and the fault's class as its detail."""
    offline_build(tmp_path, monkeypatch)
    record = setup_journal.load()["build"]
    assert record["offline"] is True and record["state"] == "done"
    monkeypatch.setattr(start, "phase_apps", lambda a: {"state": "waiting", "why": "Your Claude limit was reached."})
    start.run_phase(start.parser().parse_args(["apps", "--json"]))
    record = setup_journal.load()["apps"]
    assert record["why"] == "Your Claude limit was reached." and record["offline"] is False

    def broken(a):
        raise KeyError("verdicts")
    monkeypatch.setattr(start, "phase_research", broken)
    with pytest.raises(KeyError):
        start.run_phase(start.parser().parse_args(["research", "--json"]))
    record = setup_journal.load()["research"]
    assert record["state"] == "failed" and record["why"] == start.FAULT_WHY and record["detail"] == "KeyError"


def test_a_build_that_did_not_get_the_lock_leaves_the_running_builds_state_alone(tmp_path, monkeypatch):
    """A second start while the first one builds: nothing is written to the journal, and it says so plainly
    (R45, F-5)."""
    no_outside(monkeypatch)
    finished_setup()
    setup_journal.set_phase("build", "running")
    lock = start.take_lock()                                                   # the first start, alive
    try:
        result = start.run_phase(args(tmp_path))
    finally:
        lock.unlink()
    assert result["state"] == "running" and result.get("busy") is True and result["why"] == start.BUSY_WHY
    assert setup_journal.load()["build"]["state"] == "running" and not (tmp_path / "spaces").exists()
    assert "lock" not in result["why"] and "start.py" not in result["why"]


def test_the_build_holds_the_start_lock_until_its_state_is_written(tmp_path, monkeypatch):
    """No second start can slip in between the spaces and the routines' proof, and the lock goes once the build's
    state is in the journal (R45, F-5 and F-9)."""
    no_outside(monkeypatch)
    finished_setup()
    held = []
    real = start.phase_build

    def build(a):
        held.append(start.take_lock() is None)                                 # a second start finds it taken
        return real(a)
    monkeypatch.setattr(start, "phase_build", build)
    assert start.run_phase(args(tmp_path))["state"] == "done"
    assert held == [True] and not start.lock_path().exists()


def test_resume_says_a_build_whose_process_died_stopped_and_can_be_run_again(tmp_path, monkeypatch, capsys):
    """The journal says `running`, but no live start holds the lock: the build stopped (R45, F-9)."""
    setup_journal.set_phase("signins", "done")
    setup_journal.set_phase("apps", "done")
    setup_journal.set_phase("research", "done")
    setup_journal.set_phase("build", "running")
    assert start.main(["resume", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["next"] == "build" and out["phases"]["build"]["stopped"] is True
    assert out["phases"]["build"]["why"] == start.STOPPED_WHY and not start.lock_path().exists()
    assert setup_journal.load()["build"]["state"] == "running"                 # resume writes nothing
    assert start.main(["resume"]) == 0
    assert "build: stopped before it finished" in capsys.readouterr().out
    lock = start.take_lock()                                                   # a live start: it is running
    try:
        assert start.main(["resume", "--json"]) == 0
        assert "stopped" not in json.loads(capsys.readouterr().out)["phases"]["build"]
    finally:
        lock.unlink()


def test_the_builds_why_is_plain_and_the_failed_checks_go_to_the_journal(tmp_path, monkeypatch):
    """A routine check that failed: the build answers a plain line; the checks' own names and words are journaled
    (R45, Minor 3)."""
    no_outside(monkeypatch)
    finished_setup()
    real = start.start
    monkeypatch.setattr(start, "start", lambda *a, **k: real(*a, **{**k, "offline": True}))
    failed = {"ok": False, "checks": [{"name": "send_guard", "ok": False, "why": "hook exited 2 at C:/x/send_guard.py"}],
              "notes": [], "why": "send_guard: hook exited 2 at C:/x/send_guard.py.", "detail": None}
    monkeypatch.setattr(start, "prove_routines", lambda report, ws: dict(failed))
    monkeypatch.setattr(onboard, "routines_elsewhere", lambda ws: None)
    result = start.run_phase(args(tmp_path, offline=False))
    assert result["why"] == start.PROBE_FAILED and "send_guard" not in result["why"] and ".py" not in result["why"]
    assert "send_guard" in setup_journal.load()["build"]["detail"]


def test_a_skip_on_a_damaged_journal_ends_in_plain_words(monkeypatch):
    """Probe R6: `verdicts` that hold no verdict never change, so the skip asks again once, not for ever (R47, N3-3)."""
    setup_journal.set_phase("apps", "running", groups=[{"app": "Gmail", "kinds": ["mail"], "read": ["x"]}],
                            verdicts=["not a verdict"])
    got = start.skip_apps(types.SimpleNamespace(skip=["Gmail"], workspace=None))
    assert got["skipped"] == ["Gmail"] and setup_journal.load()["apps"]["person_skips"] == ["Gmail"]


def test_a_skip_said_as_the_apps_are_written_is_on_the_card_the_feeds_and_the_answer(monkeypatch):
    """Probe R7: the skip is saved between the phase's reload and its write. The card, the stored feeds and the answer
    list the app as skipped too, not only `apps_verdicts` (R47, N3-2)."""
    no_outside(monkeypatch)
    groups = six_apps(2)
    setup_journal.set_phase("apps", "running", groups=groups, not_signed_in=[])
    monkeypatch.setattr(reader, "read", lambda prompt, tools, *a, **k: dict(WORK))
    real, fired = setup_journal.set_phase, []

    def set_phase(phase, state, **facts):
        if phase == "apps" and "verdicts" in facts and not fired:
            fired.append(say("Google Calendar"))
        return real(phase, state, **facts)
    monkeypatch.setattr(setup_journal, "set_phase", set_phase)
    got = start._read_apps(APPS_ARGS, groups, [], [], "/bin/claude", None, {"groups": groups, "not_signed_in": []})
    monkeypatch.setattr(setup_journal, "set_phase", real)
    record = setup_journal.load()["apps"]
    assert fired and record["feeds"] == got["feeds"] == "mail: owner"
    assert not any(line.startswith("Google Calendar") for line in record["card"]["read"] + got["card"]["read"])
    assert any(line.startswith("Google Calendar") for line in record["card"]["skip"])
