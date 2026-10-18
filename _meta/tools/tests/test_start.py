"""Start: one sentence in an agent session sets a person or a company up, offline here."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

import onboard
import spaceyaml
import start
import validate
from conftest import make_space

TEMPLATE = Path(__file__).resolve().parents[3]


def template_copy(tmp_path) -> Path:
    tpl = tmp_path / "tpl"
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
    assert set(names) == {"sweep-chain", "daily-chain"}
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
    # defaults the person may change later return on the brain's Needs-you list
    skipped = yaml.safe_load((brain / "_inbound" / "needs-you" / "onboard.yaml").read_text(encoding="utf-8"))
    assert any("domains" in str(i) for i in skipped["items"])
    assert any(i["id"] == "onboard-identity" for i in skipped["items"])   # the identity draft, to sign
    # and they reach the owner's own list, which the personal instruction shows
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
    assert "Ada Okafor's workspace" in html and 'http-equiv="refresh"' not in html   # the run is over
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
    assert result["failed"] == [] and len(result["routines"]) == 2 and errors(ws) == []


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


def test_a_person_in_an_organisation_is_asked_once_where_the_repositories_go(tmp_path, monkeypatch, capsys):
    calls = _online_dry_run(tmp_path, monkeypatch, orgs="acme\nother-co")
    code = start.main(["--company", "Trial Works", "--template", "acme/Brain", "--home", str(tmp_path / "home"),
                       "--json", "--user-instructions", str(tmp_path / "CLAUDE.md")])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["stopped"] is True
    [row] = result["yours"]
    assert row["name"] == "namespace" and "tess-v" in row["result"] and "acme, other-co" in row["result"]
    assert not any(c[:3] == ["gh", "repo", "create"] for c in calls)


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
