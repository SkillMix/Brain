"""Start: one sentence in an agent session sets a person or a company up, offline here."""
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


def test_a_dry_run_prints_the_plan_and_stops_before_onboard(tmp_path, monkeypatch, capsys):
    import subprocess
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        calls.append(([str(a) for a in argv], dry_run))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(onboard, "run_cmd", fake)
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
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


def test_identity_drops_a_no_reply_address(monkeypatch):
    monkeypatch.setattr(start, "_out", lambda argv, cwd=None: "1+x@users.noreply.github.com"
                        if "user.email" in argv else ("Ada Okafor" if "user.name" in argv else ""))
    who = start.identity(offline=True)
    assert who["name"] == "Ada Okafor" and who["email"] == ""
