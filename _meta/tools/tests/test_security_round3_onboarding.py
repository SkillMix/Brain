"""Round 3, onboarding: the agents get back the upkeep the security fixes took from them, and each guard keeps
its property. The checks read every plain GitHub mount of the base branch's .gitmodules, with read-only deploy
keys the agents make and keep up daily (a token only as the fallback); an invitation carries the colleague's
login, found only by their verified address; the brain question is asked even of a member, a yes to a brain
accepts its organisation's invitation, and the brain's own template is followed; a crew project the brain's
registry pins mounts; and the brain owner's agent removes a leaver's access itself, after the owner's one yes,
never in a routine."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import doctor
import gate
import offboard
import onboard
import registry
import setup_journal
import spaceyaml
import start
from conftest import make_space, mount_brain
from test_security_onboarding import Host, _signed_in

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO


def done(argv, code=0, out=""):
    return subprocess.CompletedProcess(argv, code, out, "")


def public_key() -> str:
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + os.urandom(32)
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


PRIVATE = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA{n}\n-----END OPENSSH PRIVATE KEY-----\n"
REAL_MERGED_NOTE = onboard.merged_note


@pytest.fixture(autouse=True)
def _notes_came_through_git(monkeypatch):
    """The key notes these tests hand the brain stand for notes its main branch holds; the tests of
    `merged_note` itself put the real one back."""
    monkeypatch.setattr(onboard, "merged_note", lambda root, signal: True)


# ----- 1 and 2: the mount checkout, run as the workflow runs it ---------------------------------------
def _mount_step() -> str:
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8"))["jobs"]
    return next(s for s in jobs["mounts"]["steps"] if s.get("name") == "Check out mounts")


def _bash() -> str | None:
    git = shutil.which("git")
    if os.name != "nt":
        return shutil.which("bash")
    if not git:
        return None
    # Git for Windows' own bash, never another one on the path (WSL's would run in another machine)
    folders = list(Path(git).resolve().parents)[:3]
    found = [f / sub / "bash.exe" for f in folders for sub in ("usr/bin", "bin") if (f / sub / "bash.exe").is_file()]
    return str(found[0]) if found else None


STUB = """#!/bin/sh
# git for the test: `submodule update` is recorded and faked; everything else is the real git
for arg in "$@"; do
  if [ "$arg" = submodule ]; then
    eval last=\\${$#}
    url=$("$REAL_GIT" config --get "submodule.$last.url")
    key=$(printf '%s' "${GIT_SSH_COMMAND:-}" | sed -n "s/.*-i '\\([^']*\\)'.*/\\1/p")
    printf '%s|%s|%s|%s\\n' "$last" "$url" "$( [ -n "$key" ] && head -n 1 "$key" | tr -d '\\r')" "$*" >> "$STUB_LOG"
    case " $STUB_FAIL " in *" $url "*) exit 1 ;; esac
    mkdir -p "$last" && : > "$last/.git"
    exit 0
  fi
done
exec "$REAL_GIT" "$@"
"""


def _checkout(tmp_path, gitmodules: str, token="", keys="", fail=()):
    bash = _bash()
    if not bash or not shutil.which("git"):
        pytest.skip("no bash or git here")
    repo = tmp_path / "ws"
    repo.mkdir(parents=True)
    quiet = tmp_path / "gitconfig"
    quiet.write_text("", encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(quiet), "GIT_CONFIG_NOSYSTEM": "1"}
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True, env=env)
    (repo / ".gitmodules").write_bytes(gitmodules.encode())
    subprocess.run([*git, "add", ".gitmodules"], cwd=repo, check=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", "mounts"], cwd=repo, check=True, env=env)
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "git").write_bytes(STUB.encode())
    (stub / "git").chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_bytes(_mount_step()["run"].encode())
    log, temp = tmp_path / "log.txt", tmp_path / "runner"
    temp.mkdir()
    env.update({"TOKEN": token, "DEPLOY_KEYS": keys, "EVENT": "push", "DEFAULT_BRANCH": "main",
                "RUNNER_TEMP": temp.as_posix(), "REAL_GIT": Path(shutil.which("git")).as_posix(),
                "STUB_LOG": log.as_posix(), "STUB_FAIL": " ".join(fail),
                "PATH": f"{stub}{os.pathsep}{env['PATH']}"})
    res = subprocess.run([bash, script.as_posix()], cwd=repo, env=env, capture_output=True, text=True)
    calls = [line.split("|", 3) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return res, calls, temp


MODULES = """[submodule "brain"]
\tpath = brain
\turl = https://github.com/northwind-co/brain
[submodule "mounts/harbor"]
\tpath = mounts/harbor
\turl = https://github.com/harbor-client/harbor.git
[submodule "mounts/evil"]
\tpath = mounts/evil
\turl = git@github.com:northwind-co/x.git
[submodule "mounts/local"]
\tpath = mounts/local
\turl = file:///etc
[submodule "mounts/deep"]
\tpath = mounts/deep
\turl = https://github.com/northwind-co/a/../../b
[submodule "up"]
\tpath = ../up
\turl = https://github.com/northwind-co/up
"""


def test_a_personal_account_space_reads_the_organisations_brain_from_the_base_gitmodules(tmp_path):
    # the blocker: a workspace in a person's own account mounts the brain from the organisation
    res, calls, _ = _checkout(tmp_path, MODULES, token="t0ken")
    assert res.returncode == 0, res.stdout + res.stderr
    read = {c[0]: c for c in calls}
    assert set(read) == {"brain", "mounts/harbor"}   # every plain https GitHub address, whoever owns it
    assert read["brain"][1] == "https://github.com/northwind-co/brain"
    assert read["mounts/harbor"][1] == "https://github.com/harbor-client/harbor"   # pinned in its plain form
    assert all("protocol.allow=never" in c[3] and "protocol.https.allow=always" in c[3] and "extraheader" in c[3]
               for c in calls)
    for skipped in ("mounts/evil", "mounts/local", "mounts/deep"):
        assert f"mount {skipped} is not an https://github.com/<owner>/<name> address" in res.stdout
    assert "mount path ../up is not a plain folder" in res.stdout
    step = _mount_step()
    assert "$OWNER" not in step["run"] and "OWNER" not in step["env"]
    assert 'git show "$ref:.gitmodules"' in step["run"] and 'cp "$RUNNER_TEMP/base.gitmodules" .gitmodules' in step["run"]


def test_a_mount_with_a_deploy_key_is_read_over_ssh_with_that_key_and_no_token(tmp_path):
    keys = onboard.key_bundle({"northwind-co/brain": PRIVATE.format(n="brain"),
                               "harbor-client/harbor": PRIVATE.format(n="harbor")})
    res, calls, runner = _checkout(tmp_path, MODULES, token="t0ken", keys=keys)
    assert res.returncode == 0, res.stdout + res.stderr
    read = {c[0]: c for c in calls}
    assert read["brain"][1] == "ssh://git@github.com/northwind-co/brain.git"
    assert read["mounts/harbor"][1] == "ssh://git@github.com/harbor-client/harbor.git"
    assert all(c[2] == "-----BEGIN OPENSSH PRIVATE KEY-----" for c in calls)   # each read with its own key file
    assert all("protocol.ssh.allow=always" in c[3] and "extraheader" not in c[3] for c in calls)
    assert "mount brain read with its deploy key" in res.stdout
    assert not (runner / "mount-keys").exists()   # the keys leave with the step
    step = _mount_step()
    assert step["env"]["DEPLOY_KEYS"] == "${{ secrets.MOUNT_DEPLOY_KEYS }}"
    assert "StrictHostKeyChecking=yes" in step["run"] and "IdentitiesOnly=yes" in step["run"]
    assert "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" in step["run"]


def test_a_key_that_fails_falls_back_to_the_token_and_a_brain_neither_reads_fails(tmp_path):
    keys = onboard.key_bundle({"northwind-co/brain": PRIVATE.format(n="brain")})
    res, calls, _ = _checkout(tmp_path, MODULES, token="t0ken", keys=keys,
                              fail=("ssh://git@github.com/northwind-co/brain.git",))
    assert res.returncode == 0, res.stdout + res.stderr
    brain = [c for c in calls if c[0] == "brain"]
    assert [c[1] for c in brain] == ["ssh://git@github.com/northwind-co/brain.git", "https://github.com/northwind-co/brain"]
    assert "mount brain read with its token" in res.stdout
    res, _, _ = _checkout(tmp_path / "again", MODULES, keys=keys, fail=("ssh://git@github.com/northwind-co/brain.git",))
    assert res.returncode == 1 and "neither the mount key nor the mount token can read the brain" in res.stdout


def test_without_a_key_or_a_token_the_checks_run_without_mounts(tmp_path):
    res, calls, _ = _checkout(tmp_path, MODULES)
    assert res.returncode == 0 and calls == [] and "continuing without mounts" in res.stdout


# ----- 2: the agent makes the deploy keys ---------------------------------------------------------------
class FakeGH:
    """gh over a fake GitHub: who administers what, who can push and read, the deploy keys and the secrets."""

    def __init__(self, admin=(), readers=None, pushers=("ada-lw",), private=True, secrets=(), keys=None):
        self.admin, self.readers, self.pushers, self.private = set(admin), readers or {}, list(pushers), private
        self.secrets, self.keys, self.calls, self.inputs = set(secrets), keys or {}, [], []
        self.next_id = 100

    def __call__(self, args, input=None):
        args = [str(a) for a in args]
        self.calls.append(args)
        if input is not None:
            self.inputs.append((args, input))
        if args[:2] == ["secret", "list"]:
            return done(args, 0, "".join(f"{n}\t2026-11-05\n" for n in sorted(self.secrets)))
        if args[:2] == ["secret", "set"]:
            self.secrets.add(args[2])
            return done(args, 0)
        if args[:1] != ["api"]:
            return done(args, 1)
        if args[1:3] == ["-X", "POST"] and args[3].endswith("/keys"):
            repo = args[3][len("repos/"):-len("/keys")]
            title = next(a[6:] for a in args if a.startswith("title="))
            self.next_id += 1
            self.keys.setdefault(repo, {})[str(self.next_id)] = title
            return done(args, 0, "true\n")
        if args[1:3] == ["-X", "DELETE"]:
            path = args[3]
            if "/keys/" in path:
                repo, _, kid = path[len("repos/"):].partition("/keys/")
                return done(args, 0 if self.keys.get(repo, {}).pop(kid, None) else 1)
            return done(args, 0)
        path = args[1]
        if path.endswith("/keys"):
            repo = path[len("repos/"):-len("/keys")]
            return done(args, 0, "".join(f"{k} {t}\n" for k, t in self.keys.get(repo, {}).items()))
        if "/collaborators?" in path:
            return done(args, 0, "".join(f"{p}\n" for p in self.pushers))
        if "/collaborators/" in path and path.endswith("/permission"):
            repo, _, rest = path[len("repos/"):].partition("/collaborators/")
            return done(args, 0, self.readers.get((repo, rest.split("/")[0]), "none") + "\n")
        if args[-1] == ".permissions.admin":
            return done(args, 0, "true\n" if path[len("repos/"):] in self.admin else "false\n")
        if args[-1] == ".private":
            return done(args, 0, "true\n" if self.private else "false\n")
        return done(args, 1)


def _consumer(tmp_path, brain="https://github.com/northwind-co/brain", more=""):
    ws = make_space(tmp_path / "ada", name="ada")
    (ws / ".gitmodules").write_text(f'[submodule "brain"]\n\tpath = brain\n\turl = {brain}\n{more}', encoding="utf-8")
    return ws


def _keygen(made):
    def keygen(title):
        pair = (PRIVATE.format(n=len(made)), public_key())
        made.append((title, pair))
        return pair
    return keygen


def test_the_agent_keys_every_mount_it_administers_and_stores_the_private_halves_by_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(argv, 1))   # no organisation secret
    ws = _consumer(tmp_path, more='[submodule "mounts/harbor"]\n\tpath = mounts/harbor\n'
                                  '\turl = https://github.com/northwind-co/harbor\n')
    gh = FakeGH(admin={"northwind-co/ada-workspace", "northwind-co/brain", "northwind-co/harbor"},
                readers={("northwind-co/brain", "ada-lw"): "read", ("northwind-co/harbor", "ada-lw"): "write"},
                keys={"northwind-co/brain": {"7": "spaces checks of northwind-co/ada-workspace", "8": "spaces checks of x/y"}})
    made = []
    report = onboard.Report("person")
    onboard.mount_access(report, "northwind-co/ada-workspace", ws, gh=gh, keygen=_keygen(made))
    row = next(s for s in report.steps if s.name == "mount keys northwind-co/ada-workspace")
    assert row.done and "northwind-co/brain" in row.result and "northwind-co/harbor" in row.result
    assert not any(s.name.startswith("mount token") for s in report.steps)   # no person is asked for a token
    assert {t for t, _ in made} == {"spaces checks of northwind-co/ada-workspace"}
    assert set(gh.keys["northwind-co/brain"].values()) == {"spaces checks of northwind-co/ada-workspace",
                                                            "spaces checks of x/y"}   # the old key replaced, others kept
    assert "7" not in gh.keys["northwind-co/brain"]
    posts = [c for c in gh.calls if c[1:3] == ["-X", "POST"]]
    assert len(posts) == 2 and all("read_only=true" in c for c in posts)
    [(args, bundle)] = gh.inputs
    assert args == ["secret", "set", "MOUNT_DEPLOY_KEYS", "--repo", "northwind-co/ada-workspace"]
    assert "mount northwind-co/brain\n-----BEGIN OPENSSH PRIVATE KEY-----" in bundle
    assert not any("PRIVATE KEY" in " ".join(c) for c in gh.calls)   # never on a command line
    state = yaml.safe_load((ws / onboard.MOUNT_KEYS_STATE).read_text(encoding="utf-8"))
    assert state["repo"] == "northwind-co/ada-workspace" and state["keys"]["northwind-co/brain"]["attached"] is True
    assert "PRIVATE" not in (ws / onboard.MOUNT_KEYS_STATE).read_text(encoding="utf-8")
    # run again: everything is settled, no new key
    again = onboard.Report("person")
    onboard.mount_access(again, "northwind-co/ada-workspace", ws, gh=gh, keygen=_keygen(made))
    assert len(made) == 2 and again.steps[0].done


def test_a_key_is_never_made_for_a_mount_a_writer_cannot_read_and_the_token_is_then_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(argv, 1))
    ws = _consumer(tmp_path)
    gh = FakeGH(admin={"northwind-co/ada-workspace", "northwind-co/brain"}, pushers=("ada-lw", "stranger"),
                readers={("northwind-co/brain", "ada-lw"): "read"})
    made = []
    report = onboard.Report("person")
    onboard.mount_access(report, "northwind-co/ada-workspace", ws, gh=gh, keygen=_keygen(made))
    assert made == [] and not gh.keys and gh.inputs == []
    keys = next(s for s in report.steps if s.name.startswith("mount keys"))
    assert keys.done and "cannot read it" in keys.result
    token = next(s for s in report.steps if s.name == "mount token northwind-co/ada-workspace")
    assert not token.done and token.needs_owner   # neither works: only now is the person told


def test_a_colleagues_own_account_workspace_asks_the_brain_by_note(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(argv, 1))
    asked = []
    monkeypatch.setattr(onboard, "request_key", lambda root, mount, consumer, public: asked.append(
        (mount, consumer, public)) and None)
    ws = _consumer(tmp_path)
    gh = FakeGH(admin={"ada-lw/ada-workspace"})   # the brain is not theirs to administer
    report = onboard.Report("person")
    onboard.mount_access(report, "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]))
    [(mount, consumer, public)] = asked
    assert (mount, consumer) == ("northwind-co/brain", "ada-lw/ada-workspace") and onboard.PUBLIC_KEY.fullmatch(public)
    assert not gh.keys and "MOUNT_DEPLOY_KEYS" in gh.secrets
    row = report.steps[0]
    assert row.done and "the brain's agent attaches the one for northwind-co/brain" in row.result
    assert not any(s.needs_owner for s in report.steps)
    # a brain whose tools take no key note: the fallback token is asked for, and only then
    monkeypatch.setattr(onboard, "request_key", lambda *a: "a note is one of owner, workspace")
    (ws / onboard.MOUNT_KEYS_STATE).unlink()
    later = onboard.Report("person")
    onboard.mount_access(later, "ada-lw/ada-workspace", ws, gh=FakeGH(admin={"ada-lw/ada-workspace"}),
                         keygen=_keygen([]))
    assert any(s.name == "mount token ada-lw/ada-workspace" and s.needs_owner for s in later.steps)


def test_without_the_right_to_the_secrets_no_key_is_made(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(argv, 1))
    ws = _consumer(tmp_path)
    made = []
    report = onboard.Report("person")
    onboard.mount_access(report, "ada-lw/ada-workspace", ws, gh=FakeGH(), keygen=_keygen(made))
    assert made == [] and any(s.name == "mount token ada-lw/ada-workspace" for s in report.steps)


def test_make_keypair_gives_an_ed25519_pair_and_leaves_nothing_behind(tmp_path, monkeypatch):
    if not shutil.which("ssh-keygen"):
        pytest.skip("no ssh-keygen here")
    import tempfile
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    pair = onboard.make_keypair("spaces checks of ada-lw/ada-workspace")
    assert pair and "PRIVATE KEY" in pair[0] and onboard.PUBLIC_KEY.fullmatch(pair[1])
    assert list(temp.iterdir()) == []
    assert onboard.key_fingerprint(pair[1]).startswith("SHA256:")


def _git_init(root):
    if not shutil.which("git"):
        pytest.skip("no git here")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


def _brain_with_ben(tmp_path, people=(), **ben):
    people = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}, "workspace": "ada"},
              {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-lw"}, "workspace": "ben",
               **ben}, *people]
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=people)
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {"case": "company", "repo": "northwind-co/brain"})
    return _git_init(brain)


def _note(**data):
    return {"note": "key", "by": "Ben Liu", "from": "ben", "at": "2026-11-05T09:00:00Z",
            "data": {"consumer": "ben-lw/ben-workspace", "mount": "northwind-co/brain", "key": public_key(), **data}}


def _record(brain):
    return onboard.attached_keys(brain)


def test_the_brain_queues_a_key_only_from_the_persons_own_workspace(tmp_path):
    brain = _brain_with_ben(tmp_path)
    assert onboard.queue_mount_key(brain, _note()) == ("applied", "")   # their own account: theirs
    for bad in ({**_note(), "from": "ada"}, {**_note(), "by": "Ada Okafor"}, _note(key="ssh-rsa AAAA"),
                _note(mount="--upload-pack=x")):
        assert onboard.queue_mount_key(brain, bad)[0] == "refused", bad
    pinned = _brain_with_ben(tmp_path / "two", workspace_repo="https://github.com/northwind-co/ben-workspace")
    assert onboard.queue_mount_key(pinned, _note())[0] == "refused"   # not their registered repository
    assert onboard.queue_mount_key(pinned, _note(consumer="northwind-co/ben-workspace"))[0] == "applied"
    # the requests are kept where the sync never stages them
    assert onboard.KEY_REQUESTS.as_posix().startswith("_meta/state/private/")


def test_the_brains_agent_attaches_a_queued_key_once_the_host_agrees(tmp_path):
    brain = _brain_with_ben(tmp_path)
    onboard.queue_mount_key(brain, _note())
    cannot = FakeGH(admin={"northwind-co/brain"})   # Ben cannot read the brain
    report = onboard.attach_mount_keys(brain, gh=cannot)
    assert not cannot.keys and "refused" in report.steps[0].result
    assert not list((brain / onboard.KEY_REQUESTS).glob("*.yaml"))   # a refusal is not tried again
    assert _record(brain)["ben-lw/ben-workspace northwind-co/brain"]["outcome"] == "refused"   # and is recorded
    onboard.queue_mount_key(brain, _note())
    gh = FakeGH(admin={"northwind-co/brain"}, readers={("northwind-co/brain", "ben-lw"): "read"})
    report = onboard.attach_mount_keys(brain, gh=gh)
    assert report.steps[0].done and list(gh.keys["northwind-co/brain"].values()) == ["spaces checks of ben-lw/ben-workspace"]
    assert any("read_only=true" in c for c in gh.calls)
    assert _record(brain)["ben-lw/ben-workspace northwind-co/brain"]["outcome"] == "attached"
    assert onboard.attached_mounts(brain, "ben") == ["northwind-co/brain"]
    # a workspace in the organisation: everyone who can push there must read the brain too
    org = _brain_with_ben(tmp_path / "org", workspace_repo="https://github.com/northwind-co/ben-workspace")
    assert onboard.queue_mount_key(org, _note(consumer="northwind-co/ben-workspace"))[0] == "applied"
    shared = FakeGH(admin={"northwind-co/brain"}, pushers=("ben-lw", "outsider"),
                    readers={("northwind-co/brain", "ben-lw"): "read"})
    report = onboard.attach_mount_keys(org, gh=shared)
    assert not shared.keys and "someone who can push" in report.steps[0].result
    # an archived person gets nothing
    onboard.queue_mount_key(brain, _note())
    reg = registry.Registry(brain)
    reg.subjects["ben"].state = "archived"
    reg.save()
    left = FakeGH(admin={"northwind-co/brain"}, readers={("northwind-co/brain", "ben-lw"): "read"})
    assert "refused" in onboard.attach_mount_keys(brain, gh=left).steps[0].result and not left.keys


# ----- 3: the invitation carries the colleague's login ---------------------------------------------------
def _inviting_brain(tmp_path, **ben):
    people = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
              {"id": "ben", "kind": "people", "title": "Ben Liu", "email": "ben@northwind.example", **ben}]
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=people)
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {"case": "company", "repo": "northwind-co/brain"})
    onboard.write_yaml(brain / "_meta" / "state" / "template-version.yaml", {"repo": "SkillMix/Brain"})
    return brain


def _members(profiles, kind="Organization"):
    def fake(argv, *a, **k):
        argv = [str(x) for x in argv]
        if argv[:3] == ["gh", "api", "users/northwind-co"]:
            return done(argv, 0, kind + "\n")
        if argv[2].startswith("orgs/northwind-co/members"):
            return done(argv, 0, "".join(f"{login}\n" for login in profiles))
        if argv[2].startswith("users/") and argv[2][6:] in profiles:
            return done(argv, 0, json.dumps(profiles[argv[2][6:]]))
        if argv[2].endswith("/permission"):
            return done(argv, 0, "read\n")
        return done(argv, 0, "false\n" if argv[-1] == ".private" else "")
    return fake


def test_an_invitation_writes_the_login_the_person_gave_into_the_file_and_the_registry(tmp_path, monkeypatch):
    brain = _inviting_brain(tmp_path)
    monkeypatch.setattr(onboard, "run_cmd", _members({}))
    target = onboard.invite(brain, "ben", login="@ben-lw")
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["login"] == "ben-lw"
    assert registry.Registry(brain).get("ben").match["github"] == "ben-lw"
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "invitations.yaml").read_text(encoding="utf-8"))["items"]
    invite_item = next(i for i in items if i["id"] == "invite-ben")
    assert "Their GitHub login is ben-lw (as you gave it)" in invite_item["text"]
    with pytest.raises(ValueError):
        onboard.invite(brain, "ben", login="someone-else")   # a registered login is never replaced
    with pytest.raises(ValueError):
        onboard.invite(_inviting_brain(tmp_path / "b"), "ben", login="ada-gh")   # another person's
    with pytest.raises(ValueError):
        onboard.invite(_inviting_brain(tmp_path / "c"), "ben", login="ben lw; rm")


def test_an_invitation_reads_the_login_from_the_member_list_only_by_one_members_address(tmp_path, monkeypatch):
    brain = _inviting_brain(tmp_path)
    monkeypatch.setattr(onboard, "run_cmd", _members({"ada-gh": {"email": "ben@northwind.example"},   # Ada's: never Ben's
                                                      "ben-lw": {"name": "Ben Liu", "email": "ben@northwind.example"},
                                                      "carol": {"name": "Ben Liu"}}))   # a name anyone can set: never
    onboard.invite(brain, "ben")
    assert registry.Registry(brain).get("ben").match["github"] == "ben-lw"
    item = next(i for i in yaml.safe_load((brain / "_inbound" / "needs-you" / "invitations.yaml")
                                          .read_text(encoding="utf-8"))["items"] if i["id"] == "invite-ben")
    assert "from northwind-co's member list, by their address" in item["text"]
    named = _inviting_brain(tmp_path / "named")
    monkeypatch.setattr(onboard, "run_cmd", _members({"impostor": {"name": "Ben Liu", "email": None}}))
    onboard.invite(named, "ben")
    assert "github" not in registry.Registry(named).get("ben").match   # a display name alone: the agent asks in chat
    two = _inviting_brain(tmp_path / "two")
    monkeypatch.setattr(onboard, "run_cmd", _members({"ben-lw": {"email": "ben@northwind.example"},
                                                      "ben-2": {"email": "ben@northwind.example"}}))
    onboard.invite(two, "ben")
    assert "github" not in registry.Registry(two).get("ben").match   # two match: the agent asks in chat
    mail = _inviting_brain(tmp_path / "mail")
    monkeypatch.setattr(onboard, "run_cmd", _members({"b-l": {"name": "B.", "email": "Ben@Northwind.example"}}))
    onboard.invite(mail, "ben")
    assert registry.Registry(mail).get("ben").match["github"] == "b-l"
    own = _inviting_brain(tmp_path / "own")
    monkeypatch.setattr(onboard, "run_cmd", _members({"ben-lw": {"email": "ben@northwind.example"}}, kind="User"))
    onboard.invite(own, "ben")
    assert "github" not in registry.Registry(own).get("ben").match   # a personal account has no member list


# ----- 4: joining -------------------------------------------------------------------------------------
@pytest.mark.parametrize("member,file", [
    ("active", "name: Ada Okafor\nlogin: ada-lw\n"),   # a member, and the invitation names their login
    ("pending", "login: ada-lw\n"),                    # a membership they have not taken
    ("active", "name: Ada Okafor\n"),                  # an invitation that names no login
])
def test_the_brain_question_is_asked_even_of_a_member_its_invitation_names(monkeypatch, capsys, member, file):
    # a member of any organisation (a client's, a hackathon's) may face a look-alike brain made there
    host = Host(member=member, files={"_meta/state/invitations/ada.yaml": file,
                                      "_meta/state/template-version.yaml": "repo: evil-org/brain-template\n"})
    monkeypatch.setattr(start, "_out", host)
    _signed_in(monkeypatch)
    start.main(["signins", "--invitation", "evil-org/brain:ada", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "waiting" and result["brain"]["confirmed"] is False and "by" not in result["brain"]
    assert not any("invitations" in " ".join(c) or "template-version" in " ".join(c) for c in host.calls)   # not read
    if member == "pending":
        assert "a yes here accepts that invitation too" in result["brain"]["question"]


def test_setup_never_takes_a_membership_as_the_yes(tmp_path, monkeypatch):
    host = Host(member="active", files={"_meta/state/invitations/ada.yaml": "name: Ada Okafor\nlogin: ada-lw\n",
                                        "_meta/state/template-version.yaml": "repo: evil-org/brain-template\n"})
    monkeypatch.setattr(start, "_out", host)
    made = []
    monkeypatch.setattr(onboard, "create_space", lambda *a, **k: made.append(a) or (None, None))
    report = onboard.Report("start")
    start.setup(report, TEMPLATE, "evil-org/brain:ada", None, None, "Ada Okafor", None, None, None,
                tmp_path / "home", "SkillMix/Brain", False, False, None)
    row = next(s for s in report.steps if s.name == "invitation brain")
    assert not row.done and row.needs_owner and report.stopped
    assert made == [] and not any(s.name == "invitation template" for s in report.steps)   # nothing made, no swap


def test_the_doctor_accepts_the_invitation_of_the_confirmed_brains_organisation_only(tmp_path, monkeypatch):
    class M:
        def __init__(self):
            self.state, self.calls = "pending", []

        def out(self, *argv):
            self.calls.append(list(argv))
            return {"users/northwind-co": "Organization", "users/evil-org": "Organization"}.get(argv[2]) or (
                self.state if argv[2].startswith("user/memberships/orgs/") else "")

        def ok(self, *argv):
            self.calls.append(list(argv))
            if "PATCH" in argv:
                self.state = "active"
            return True
    m = M()
    row = doctor._organization(m, "northwind-co", fix=True, accept=True)
    assert row.ok and "accepted the invitation to northwind-co" in row.fixed
    other = M()
    assert not doctor._organization(other, "evil-org", fix=True, accept=False).ok
    assert not [c for c in other.calls if "PATCH" in c]
    dry = M()
    assert not doctor._organization(dry, "northwind-co", fix=False, accept=True).ok   # a dry run changes nothing
    seen = []
    monkeypatch.setattr(start.doctor, "diagnose", lambda **kw: seen.append(kw) or [])
    start.checkup(onboard.Report("start"), "northwind-co/brain", "SkillMix/Brain", False, True, tmp_path,
                  "northwind-co/brain")
    start.checkup(onboard.Report("start"), "northwind-co/brain", "SkillMix/Brain", False, True, tmp_path, None)
    assert seen[0]["accept_orgs"] == ["northwind-co"] and seen[1]["accept_orgs"] == [] and seen[1]["orgs"] == []


# ----- 5: a crew project the brain's registry pins ------------------------------------------------------
HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
          "match": {"repo": "https://github.com/harbor-client/harbor"}}


def test_a_crew_project_the_brains_registry_pins_mounts_from_the_pinned_address(tmp_path):
    pinned = lambda name: onboard.registry_pin([registry.Subject.from_dict(HARBOR)], name)   # noqa: E731
    mount, _ = onboard.crew_mount({"name": "harbor", "repo": "https://github.com/Harbor-Client/harbor.git"}, tmp_path,
                                  "ada", "https://github.com/northwind-co/brain", False, pinned=pinned)
    assert mount == {"name": "harbor", "repo": "https://github.com/harbor-client/harbor", "path": "mounts/harbor",
                     "branch": "main"}
    for project in ({"name": "harbor", "repo": "https://github.com/evil-org/harbor"},     # another repository
                    {"name": "beacon", "repo": "https://github.com/harbor-client/harbor"}):  # pinned for another project
        left, why = onboard.crew_mount(project, tmp_path, "ada", "https://github.com/northwind-co/brain", False,
                                       pinned=pinned)
        assert left is None and "does not pin it" in why
    person = registry.Subject.from_dict({"id": "ben", "kind": "people", "home": "ben",
                                         "match": {"repo": "https://github.com/ben-lw/ben-workspace"}})
    assert onboard.registry_pin([person], "ben") is None   # a person's workspace is never a crew project
    assert onboard.registry_pin([registry.Subject.from_dict(dict(HARBOR, home="brain"))], "harbor") is None


def test_online_the_pins_come_from_the_brains_default_branch(monkeypatch):
    calls = []

    def fake(argv, *a, **k):
        calls.append([str(x) for x in argv])
        return done(argv, 0, yaml.safe_dump({"subjects": [HARBOR]}))
    monkeypatch.setattr(onboard, "run_cmd", fake)
    pins = onboard.brain_pins("https://github.com/northwind-co/brain")
    assert pins("harbor") == "https://github.com/harbor-client/harbor" and pins("beacon") is None
    assert calls[0][:3] == ["gh", "api", "repos/northwind-co/brain/contents/_meta/subjects.yaml"]
    monkeypatch.setattr(onboard, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    assert onboard.brain_pins("--upload-pack=x")("harbor") is None   # nothing of another shape reaches gh


def test_a_crew_project_left_out_is_a_row_until_the_registry_pins_it(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[])
    ws = make_space(tmp_path / "ada", name="ada")
    mount_brain(ws, brain)
    onboard.write_yaml(ws / onboard.CREW_LEFT, {"left_out": [
        {"name": "harbor", "repo": "https://github.com/harbor-client/harbor", "why": "not in the brain's account"}]})
    [row] = onboard.crew_left_steps(spaceyaml.load(ws))
    assert row.done and "waits for the brain's registry" in row.result
    (brain / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [HARBOR]}), encoding="utf-8")
    [row] = onboard.crew_left_steps(spaceyaml.load(ws))
    assert not row.done and not row.needs_owner   # the agent's to fix, with the command
    assert "mint.py . --mount harbor https://github.com/harbor-client/harbor" in row.result
    # the daily mounts it itself; the protected space.yaml and .gitmodules then wait for the person's one click
    mounted = []
    [row] = onboard.crew_left_steps(spaceyaml.load(ws), mount=lambda root, name, url: mounted.append((name, url)))
    assert mounted == [("harbor", "https://github.com/harbor-client/harbor")]
    assert row.done and "one click" in row.result


def test_the_daily_upkeep_mounts_a_crew_project_the_registry_pins_later(tmp_path, monkeypatch):
    """Not only a row nothing acts on: the upkeep itself writes the mount, from the registry's pin."""
    calls, added = [], {"code": 128}

    def run(argv, *a, **k):
        calls.append([str(x) for x in argv])
        return done(argv, added["code"] if argv[:3] == ["git", "submodule", "add"] else 1)
    monkeypatch.setattr(onboard, "run_cmd", run)
    brain = make_space(tmp_path / "brainmount", profile="brain", name="brain", subjects=[HARBOR])
    ws = make_space(tmp_path / "ada", name="ada")
    mount_brain(ws, brain)
    onboard.write_yaml(ws / onboard.CREW_LEFT, {"left_out": [
        {"name": "harbor", "repo": "https://github.com/harbor-client/harbor", "why": "not in the brain's account"}]})
    health = ws / "_meta" / "state" / "health" / "mount-keys.yaml"
    # git does not take it (an index.lock, no credentials): no mount row, a health row, and the next run tries again
    onboard.keys_upkeep(ws, gh=FakeGH())
    assert "harbor" not in {m["name"] for m in spaceyaml.load(ws).mounts}
    assert "crew project harbor mounted" in health.read_text(encoding="utf-8")
    added["code"] = 0
    onboard.keys_upkeep(ws, gh=FakeGH())
    mounts = {m["name"]: m for m in spaceyaml.load(ws).mounts}
    assert mounts["harbor"]["repo"] == "https://github.com/harbor-client/harbor"
    assert calls.count(["git", "submodule", "add", "-b", "main", "https://github.com/harbor-client/harbor",
                        "mounts/harbor"]) == 2
    assert "harbor" not in health.read_text(encoding="utf-8")


# ----- 6: offboarding removes access itself, after the owner's yes ---------------------------------------
class Org(FakeGH):
    """FakeGH plus an organisation: its teams, its members and its token grants."""

    def __init__(self, me="ada-gh", **kw):
        super().__init__(**kw)
        self.me, self.teams, self.members, self.collaborators = me, {"crew": {"ben-gh"}, "all": set()}, {"ben-gh"}, {
            "northwind-co/brain": {"ben-gh"}, "northwind-co/harbor": {"ben-gh"}}

    def __call__(self, args, input=None):
        args = [str(a) for a in args]
        if args[:2] == ["api", "user"]:
            self.calls.append(args)
            return done(args, 0, self.me + "\n")
        if args[1:3] == ["-X", "DELETE"] and "/keys/" not in args[3]:
            self.calls.append(args)
            path = args[3]
            if "/collaborators/" in path:
                repo, _, who = path[len("repos/"):].partition("/collaborators/")
                self.collaborators.get(repo, set()).discard(who)
            elif "/teams/" in path:
                self.teams[path.split("/teams/")[1].split("/")[0]].discard(path.rsplit("/", 1)[1])
            elif "/memberships/" in path:
                self.members.discard(path.rsplit("/", 1)[1])
            return done(args, 0)
        if args[:1] == ["api"] and len(args) > 1:
            path = args[1]
            if path == "users/northwind-co":
                self.calls.append(args)
                return done(args, 0, "Organization\n")
            if path.startswith("orgs/northwind-co/teams?"):
                self.calls.append(args)
                return done(args, 0, "crew\nall\n")
            if "/teams/" in path and "/memberships/" in path:
                self.calls.append(args)
                team, who = path.split("/teams/")[1].split("/memberships/")
                return done(args, 0 if who in self.teams[team] else 1, "active\n")
            if path.startswith("orgs/northwind-co/memberships/"):
                self.calls.append(args)
                return done(args, 0 if path.rsplit("/", 1)[1] in self.members else 1, "active\n")
            if "personal-access-tokens" in path:
                self.calls.append(args)
                return done(args, 1)   # only an app may list them
            if path.endswith("/permission") and "/collaborators/" in path:
                self.calls.append(args)
                repo, _, rest = path[len("repos/"):].partition("/collaborators/")
                return done(args, 0, ("write" if rest.split("/")[0] in self.collaborators.get(repo, set()) else "none") + "\n")
        return super().__call__(args, input)


def _leaving(tmp_path):
    people = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
              {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-gh"}, "workspace": "ben",
               "workspace_repo": "https://github.com/ben-gh/ben-workspace"},
              {"id": "harbor", "kind": "projects", "title": "Harbor", "owner": "Ada Okafor", "crew": ["ada"]}]
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=people)
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", audience=["Ada Okafor", "Ben Liu"])
    for root, repo in ((brain, "northwind-co/brain"), (harbor, "northwind-co/harbor")):
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text(f'[remote "origin"]\n\turl = https://github.com/{repo}.git\n',
                                              encoding="utf-8")
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {
        "case": "company", "repo": "northwind-co/brain", "spaces": {"brain": str(brain), "harbor": str(harbor)}})
    return brain


def test_after_the_owners_yes_the_agent_removes_a_leavers_access_and_reads_it_back(tmp_path):
    brain = _leaving(tmp_path)
    gh = Org(keys={"northwind-co/brain": {"5": "spaces checks of ben-gh/ben-workspace",
                                          "6": "spaces checks of northwind-co/ada-workspace"},
                   "northwind-co/harbor": {"9": "spaces checks of ben-gh/other"}})
    report = offboard.leaver(brain, "ben", online=True, remove=True, gh=gh)
    rows = {s.name: s for s in report.steps}
    assert rows["access northwind-co/brain"].done and rows["access northwind-co/harbor"].done
    assert rows["organisation northwind-co"].done and "no longer a member" in rows["organisation northwind-co"].result
    assert rows["tokens in northwind-co"].done and "only an app" in rows["tokens in northwind-co"].result
    assert gh.keys["northwind-co/brain"] == {"6": "spaces checks of northwind-co/ada-workspace"}   # only theirs
    assert gh.keys["northwind-co/harbor"] == {}
    assert gh.teams["crew"] == set() and gh.members == set()
    deletes = [c[3] for c in gh.calls if c[1:3] == ["-X", "DELETE"]]
    assert "repos/northwind-co/brain/collaborators/ben-gh" in deletes and "orgs/northwind-co/memberships/ben-gh" in deletes
    assert not any(c[1:3] == ["-X", "PUT"] or c[1:3] == ["-X", "PATCH"] for c in gh.calls)   # nothing is granted
    assert not (brain / "_inbound" / "needs-you" / "offboard-ben.yaml").exists() or not yaml.safe_load(
        (brain / "_inbound" / "needs-you" / "offboard-ben.yaml").read_text(encoding="utf-8"))["items"]
    assert registry.Registry(brain).get("ben").state == "archived"


def test_without_the_yes_nothing_is_removed_and_the_agents_own_login_never_is(tmp_path):
    brain = _leaving(tmp_path)
    gh = Org()
    offboard.leaver(brain, "ben", online=True, gh=gh)
    assert not [c for c in gh.calls if c[1:3] == ["-X", "DELETE"]]
    mine = Org(me="Ben-GH")
    report = offboard.leaver(_leaving(tmp_path / "two"), "ben", online=True, remove=True, gh=mine)
    assert not [c for c in mine.calls if c[1:3] == ["-X", "DELETE"]]
    assert any(s.name == "access removed" and s.needs_owner for s in report.steps)
    assert offboard.main([str(brain), "--person", "ben", "--remove"]) == 2   # --remove needs --online


def test_a_workspace_offboard_deletes_its_mount_secrets_before_the_archive(tmp_path, monkeypatch):
    ben = make_space(tmp_path / "ben", name="ben", owner="Ben Liu", mounts=[
        {"name": "brain", "repo": "https://github.com/northwind-co/brain", "path": "brain"}])
    onboard.write_yaml(ben / "_meta" / "state" / "onboard.yaml", {"repo": "ben-gh/ben-workspace"})
    secrets, calls = {"MOUNT_DEPLOY_KEYS", "OTHER"}, []

    def fake(argv, dry_run=False, cwd=None):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[:3] == ["gh", "secret", "list"]:
            return done(argv, 0, "".join(f"{n}\t2026\n" for n in sorted(secrets)))
        if argv[:3] == ["gh", "secret", "delete"]:
            secrets.discard(argv[3])
        if argv[:3] == ["gh", "repo", "view"]:
            return done(argv, 0, "true\n")
        return done(argv, 0)
    monkeypatch.setattr(offboard, "run_cmd", fake)
    monkeypatch.setattr(offboard, "remove_sync_task", lambda report, *a, **k: report.add("sync task removed", True, ""))
    monkeypatch.setattr(offboard, "remove_probe_task", lambda *a, **k: None)
    report = offboard.run(ben, online=True)
    rows = {s.name: s for s in report.steps}
    assert rows["mount secrets removed"].done and secrets == {"OTHER"}
    deleted = calls.index(["gh", "secret", "delete", "MOUNT_DEPLOY_KEYS", "--repo", "ben-gh/ben-workspace"])
    assert deleted < calls.index(["gh", "repo", "archive", "ben-gh/ben-workspace", "-y"])
    assert rows["tokens to revoke"].done   # no hand-made token was there
    assert not rows["access to revoke"].done and "--remove" in rows["access to revoke"].result
    secrets.update({"BRAIN_READ_TOKEN"})
    (ben / "_meta" / "state" / "offboard.yaml").unlink()
    again = {s.name: s for s in offboard.run(ben, online=True).steps}
    assert not again["tokens to revoke"].done and again["tokens to revoke"].needs_owner
    assert "BRAIN_READ_TOKEN" in again["tokens to revoke"].result


# ----- round 3, second pass: what the verifiers found ------------------------------------------------------
def _queued(brain, **data):
    assert onboard.queue_mount_key(brain, _note(**data)) == ("applied", "")
    return next((brain / onboard.KEY_REQUESTS).glob("*.yaml"))


def test_a_key_request_git_tracks_came_in_a_pull_request_and_is_never_attached(tmp_path):
    brain = _brain_with_ben(tmp_path)
    path = _queued(brain)
    subprocess.run(["git", "add", "-f", str(path)], cwd=brain, check=True, capture_output=True)
    gh = FakeGH(admin={"northwind-co/brain"}, readers={("northwind-co/brain", "ben-lw"): "read"})
    report = onboard.attach_mount_keys(brain, gh=gh)
    assert not gh.keys and "git tracks" in report.steps[0].result and not path.exists()


def test_a_request_reaches_no_mount_beyond_the_brain_and_their_crew_and_writers_are_always_checked(tmp_path):
    # the verifiers' forged request: Ben's own account, a walled project Ben can read, an attacker's key
    harbor = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
              "match": {"repo": "https://github.com/northwind-co/harbor"}}
    brain = _brain_with_ben(tmp_path, people=[harbor])
    readers = {("northwind-co/harbor", "ben-lw"): "read"}
    _queued(brain, mount="northwind-co/harbor")
    gh = FakeGH(admin={"northwind-co/harbor"}, readers=readers, pushers=("ben-lw", "outsider"))
    assert "neither the brain nor a project" in onboard.attach_mount_keys(brain, gh=gh).steps[0].result
    assert not gh.keys
    reg = registry.Registry(brain)
    reg.subjects["harbor"].crew = ["ben"]
    reg.save()
    _queued(brain, mount="northwind-co/harbor")   # on the crew now: their own account is still checked
    gh = FakeGH(admin={"northwind-co/harbor"}, readers=readers, pushers=("ben-lw", "outsider"))
    assert "someone who can push" in onboard.attach_mount_keys(brain, gh=gh).steps[0].result and not gh.keys
    _queued(brain, mount="northwind-co/harbor")
    gh = FakeGH(admin={"northwind-co/harbor"}, readers=readers, pushers=("ben-lw",))
    assert onboard.attach_mount_keys(brain, gh=gh).steps[0].result == "attached" and gh.keys["northwind-co/harbor"]
    # a request written beside the notes for another repository than their registered one
    pinned = _brain_with_ben(tmp_path / "pinned", workspace_repo="https://github.com/ben-lw/ben-workspace")
    onboard.write_yaml(pinned / onboard.KEY_REQUESTS / "x.yaml", {
        "person": "ben", "from": "ben", "consumer": "ben-lw/not-his-workspace", "mount": "northwind-co/brain",
        "key": public_key()})
    gh = FakeGH(admin={"northwind-co/brain"}, readers={("northwind-co/brain", "ben-lw"): "read"})
    assert "is not Ben Liu's workspace repository" in onboard.attach_mount_keys(pinned, gh=gh).steps[0].result
    assert not gh.keys


def test_a_key_note_in_the_same_batch_as_the_workspace_note_waits_and_is_then_attached(tmp_path):
    brain = _brain_with_ben(tmp_path, workspace=None)   # the workspace note is still in the same batch
    assert onboard.queue_mount_key(brain, _note())[0] == "applied"
    reg = registry.Registry(brain)
    reg.subjects["ben"].extra["workspace"] = "ben"   # the caller's registry, the workspace note applied in memory
    assert onboard.queue_mount_key(brain, _note(), reg=reg) == ("applied", "")
    gh = FakeGH(admin={"northwind-co/brain"}, readers={("northwind-co/brain", "ben-lw"): "read"})
    report = onboard.attach_mount_keys(brain, gh=gh)
    assert report.steps[0].done and "waits until the registry" in report.steps[0].result and not gh.keys
    assert list((brain / onboard.KEY_REQUESTS).glob("*.yaml"))   # kept, not refused
    reg.save()
    assert onboard.attach_mount_keys(brain, gh=gh).steps[0].result == "attached"
    # a workspace in the organisation waits for its workspace_repo, never refused for it
    org = _brain_with_ben(tmp_path / "org")
    _queued(org, consumer="northwind-co/ben-workspace")
    assert "waits until" in onboard.attach_mount_keys(org, gh=gh).steps[0].result


def _workspace_on_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(argv, 1))   # no token, no org secret
    brain = make_space(tmp_path / "brainmount", profile="brain", name="brain", subjects=[])
    ws = _consumer(tmp_path)
    mount_brain(ws, brain)
    asked = []
    monkeypatch.setattr(onboard, "request_key", lambda root, mount, consumer, public: asked.append(public) and None)
    return ws, brain, asked


def _answer(brain, public, outcome, why=""):
    onboard.write_yaml(brain / onboard.ATTACHED_KEYS, {"keys": {"ada-lw/ada-workspace northwind-co/brain": {
        "person": "ada", "consumer": "ada-lw/ada-workspace", "mount": "northwind-co/brain", "outcome": outcome,
        "fingerprint": onboard.key_fingerprint(public), **({"why": why} if why else {})}}})


def test_the_brains_answer_reaches_the_workspace_and_a_refusal_raises_the_token_row(tmp_path, monkeypatch):
    import datetime as dt
    ws, brain, asked = _workspace_on_brain(tmp_path, monkeypatch)
    gh = FakeGH(admin={"ada-lw/ada-workspace"})
    day = dt.date(2026, 11, 5)
    first = onboard.Report("person")
    onboard.mount_access(first, "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]), today=day)
    assert not any(s.needs_owner for s in first.steps) and len(asked) == 1
    [(_, bundle)] = gh.inputs
    assert bundle.startswith("pending northwind-co/brain 2026-11-19\nmount northwind-co/brain\n")
    _answer(brain, asked[0], "attached")
    later = onboard.Report("person")
    onboard.mount_access(later, "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]), today=day)
    state = yaml.safe_load((ws / onboard.MOUNT_KEYS_STATE).read_text(encoding="utf-8"))
    assert state["keys"]["northwind-co/brain"]["attached"] is True and len(asked) == 1
    assert not any(s.needs_owner for s in later.steps)
    # a refusal of the key it sent: left out, and the person hears of the token only now
    ws2, brain2, asked2 = _workspace_on_brain(tmp_path / "two", monkeypatch)
    gh2 = FakeGH(admin={"ada-lw/ada-workspace"})
    onboard.mount_access(onboard.Report("person"), "ada-lw/ada-workspace", ws2, gh=gh2, keygen=_keygen([]), today=day)
    _answer(brain2, public_key(), "refused", "someone else's key")   # another key's outcome: ignored
    quiet = onboard.Report("person")
    onboard.mount_access(quiet, "ada-lw/ada-workspace", ws2, gh=gh2, keygen=_keygen([]), today=day)
    assert not any(s.needs_owner for s in quiet.steps)
    _answer(brain2, asked2[0], "refused", "Ada Okafor (ada-lw) cannot read northwind-co/brain")
    told = onboard.Report("person")
    onboard.mount_access(told, "ada-lw/ada-workspace", ws2, gh=gh2, keygen=_keygen([]), today=day)
    assert "refused it" in told.steps[0].result and len(asked2) == 1
    assert any(s.name == "mount token ada-lw/ada-workspace" and s.needs_owner for s in told.steps)


def test_a_key_the_brain_never_answers_is_asked_again_after_a_week_and_then_the_token_row_shows(tmp_path, monkeypatch):
    import datetime as dt
    ws, _, asked = _workspace_on_brain(tmp_path, monkeypatch)
    gh = FakeGH(admin={"ada-lw/ada-workspace"})
    day = dt.date(2026, 11, 5)
    onboard.mount_access(onboard.Report("person"), "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]), today=day)
    soon = onboard.Report("person")
    onboard.mount_access(soon, "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]), today=day + dt.timedelta(days=3))
    assert len(asked) == 1 and not any(s.needs_owner for s in soon.steps)
    late = onboard.Report("person")
    onboard.mount_access(late, "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]), today=day + dt.timedelta(days=8))
    assert len(asked) == 2   # asked again
    assert any(s.name == "mount token ada-lw/ada-workspace" and s.needs_owner for s in late.steps)


def test_a_mount_left_out_for_a_passing_reason_is_asked_again_on_the_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(argv, 1))
    ws = _consumer(tmp_path, more='[submodule "mounts/harbor"]\n\tpath = mounts/harbor\n'
                                  '\turl = https://github.com/ada-lw/harbor\n')
    gh = FakeGH(admin={"ada-lw/ada-workspace", "ada-lw/harbor"})
    monkeypatch.setattr(onboard, "request_key", lambda *a: "a note is one of owner, workspace")
    first = onboard.Report("person")
    onboard.mount_access(first, "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]))
    assert "could not be asked" in first.steps[0].result
    asked = []
    monkeypatch.setattr(onboard, "request_key", lambda root, mount, consumer, public: asked.append(mount) and None)
    onboard.mount_access(onboard.Report("person"), "ada-lw/ada-workspace", ws, gh=gh, keygen=_keygen([]))
    assert asked == ["northwind-co/brain"]


def test_the_daily_upkeep_gives_an_older_space_its_keys_and_reports_crew_projects_to_the_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(argv, 0 if argv[:3] == ["git", "submodule", "add"]
                                                                        else 1))
    brain = make_space(tmp_path / "brainmount", profile="brain", name="brain", subjects=[HARBOR])
    ws = _consumer(tmp_path)
    mount_brain(ws, brain)
    onboard.write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"repo": "northwind-co/ada-workspace"})
    onboard.write_yaml(ws / onboard.CREW_LEFT, {"left_out": [
        {"name": "harbor", "repo": "https://github.com/harbor-client/harbor", "why": "not in the brain's account"}]})
    gh = FakeGH(admin={"northwind-co/ada-workspace", "northwind-co/brain"}, secrets={"BRAIN_READ_TOKEN"},
                readers={("northwind-co/brain", "ada-lw"): "read"})
    report = onboard.keys_upkeep(ws, gh=gh, keygen=_keygen([]))
    assert gh.keys["northwind-co/brain"] and "MOUNT_DEPLOY_KEYS" in gh.secrets   # no build was needed
    assert "harbor" in {m["name"] for m in spaceyaml.load(ws).mounts}   # the upkeep mounted the pinned project
    assert any(s.name == "crew project harbor mounted" and s.done for s in report.steps)
    assert not any(s.needs_owner for s in report.steps)
    assert onboard.main([str(ws), "--mount-keys", "--dry-run"]) == 2


def test_the_brains_agent_removes_a_recorded_key_whose_person_left_or_lost_access(tmp_path):
    brain = _brain_with_ben(tmp_path)
    _queued(brain)
    gh = FakeGH(admin={"northwind-co/brain"}, readers={("northwind-co/brain", "ben-lw"): "read"})
    onboard.attach_mount_keys(brain, gh=gh)
    assert gh.keys["northwind-co/brain"]
    gh.readers = {}   # Ben no longer reads the brain
    report = onboard.attach_mount_keys(brain, gh=gh)
    assert gh.keys["northwind-co/brain"] == {} and "no longer reads it" in report.steps[0].result
    assert _record(brain)["ben-lw/ben-workspace northwind-co/brain"]["outcome"] == "removed"


def test_offboarding_removes_the_keys_the_record_lists_and_a_routine_never_removes(tmp_path, monkeypatch):
    brain = _leaving(tmp_path)
    onboard.write_yaml(brain / onboard.ATTACHED_KEYS, {"keys": {"ben-gh/ben-workspace northwind-co/payroll": {
        "person": "ben", "consumer": "ben-gh/ben-workspace", "mount": "northwind-co/payroll", "outcome": "attached"}}})
    gh = Org(keys={"northwind-co/payroll": {"101": "spaces checks of ben-gh/ben-workspace",
                                            "102": "spaces checks of northwind-co/ada-workspace"}})
    report = offboard.leaver(brain, "ben", online=True, remove=True, gh=gh)
    assert gh.keys["northwind-co/payroll"] == {"102": "spaces checks of northwind-co/ada-workspace"}
    assert any(s.name == "deploy keys on northwind-co/payroll" and s.done for s in report.steps)
    import routine
    monkeypatch.setenv(routine.ROUTINE_MARK, "1")
    other = Org()
    with pytest.raises(ValueError, match="routine"):
        offboard.leaver(_leaving(tmp_path / "two"), "ben", online=True, remove=True, gh=other)
    assert not [c for c in other.calls if c[1:3] == ["-X", "DELETE"]]
    offboard.leaver(_leaving(tmp_path / "three"), "ben", online=True, gh=Org())   # listing what to remove still runs


def test_a_brain_unread_only_through_a_key_that_waits_warns_until_its_day(tmp_path):
    keys = onboard.key_bundle({"northwind-co/brain": PRIVATE.format(n="brain")}, {"northwind-co/brain": "2999-01-01"})
    res, _, _ = _checkout(tmp_path, MODULES, keys=keys, fail=("ssh://git@github.com/northwind-co/brain.git",))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "waits for the brain's agent until 2999-01-01" in res.stdout
    old = onboard.key_bundle({"northwind-co/brain": PRIVATE.format(n="brain")}, {"northwind-co/brain": "2000-01-01"})
    res, _, _ = _checkout(tmp_path / "late", MODULES, keys=old, fail=("ssh://git@github.com/northwind-co/brain.git",))
    assert res.returncode == 1 and "neither the mount key nor the mount token can read the brain" in res.stdout
    res, calls, _ = _checkout(tmp_path / "read", MODULES, keys=keys)
    assert res.returncode == 0 and [c[1] for c in calls if c[0] == "brain"] == ["ssh://git@github.com/northwind-co/brain.git"]


# ----- re-verify: a key note counts only from main; the record no pull request reaches ----------------
def _commit(root, message="fill"):
    git = ["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run(git + ["add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", message], cwd=root, check=True, capture_output=True)


def test_a_key_note_written_by_hand_into_the_brains_inbound_is_refused(tmp_path, monkeypatch):
    """Model D: a routine a mail talked into it writes a `key` note into _inbound/signals/ naming Ben's real
    workspace and the attacker's key. Not on origin/main, so nothing is queued and nothing is attached; the same
    note once main holds it (a pull request the gate let land) is queued."""
    import classify
    monkeypatch.setattr(onboard, "merged_note", REAL_MERGED_NOTE)
    brain = _brain_with_ben(tmp_path, workspace_repo="https://github.com/ben-lw/ben-workspace")
    _commit(brain)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=brain, check=True)
    attacker = public_key()
    signal = {"id": "p-0a1b2c3d", "kind": "registry", "note": "key", "subject": "ben", "at": "2026-11-05T09:00:00Z",
              "by": "Ben Liu", "from": "ben",
              "data": {"consumer": "ben-lw/ben-workspace", "mount": "northwind-co/brain", "key": attacker}}
    path = brain / "_inbound" / "signals" / "p-0a1b2c3d.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(yaml.safe_dump(signal).encode("utf-8"))
    assert classify.land_notes(brain) == {"applied": 0, "refused": 1}
    assert not list((brain / onboard.KEY_REQUESTS).glob("*.yaml"))
    gh = FakeGH(admin={"northwind-co/brain"}, readers={("northwind-co/brain", "ben-lw"): "read"}, pushers=("ben-lw",))
    assert onboard.attach_mount_keys(brain, gh=gh).steps == [] and not gh.keys
    # committed here (the sync's own commit) is not enough either: only origin/main counts
    path.write_bytes(yaml.safe_dump(signal).encode("utf-8"))
    _commit(brain, "sync")
    assert onboard.queue_mount_key(brain, signal)[0] == "refused"
    # on main as it is: queued; the same id with another key is not
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=brain, check=True)
    assert onboard.queue_mount_key(brain, signal) == ("applied", "")
    other = {**signal, "data": {**signal["data"], "key": public_key()}}
    assert onboard.queue_mount_key(brain, other)[0] == "refused"


def test_an_edited_answers_file_does_not_stop_a_key_being_removed(tmp_path):
    """Model C: a pull request flips Ben's entry in the published answers (or deletes it). The record removals
    read is never staged, so his key on harbor is still removed once he is off its crew, and offboarding still
    finds it. A record git tracks (a pull request added it) is not read."""
    harbor = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "crew": ["ben"],
              "match": {"repo": "https://github.com/northwind-co/harbor"}}
    brain = _brain_with_ben(tmp_path, people=[harbor])
    _queued(brain, mount="northwind-co/harbor")
    gh = FakeGH(admin={"northwind-co/harbor"}, readers={("northwind-co/harbor", "ben-lw"): "read"}, pushers=("ben-lw",))
    assert onboard.attach_mount_keys(brain, gh=gh).steps[0].result == "attached"
    assert onboard.KEY_RECORD.as_posix().startswith("_meta/state/private/key-requests/")
    published = onboard.read_yaml(brain / onboard.ATTACHED_KEYS, {})["keys"]
    assert published["ben-lw/ben-workspace northwind-co/harbor"]["outcome"] == "attached"   # the workspace's answer
    for v in published.values():
        v["outcome"] = "refused"
    onboard.write_yaml(brain / onboard.ATTACHED_KEYS, {"keys": published})
    assert onboard.attached_mounts(brain, "ben") == ["northwind-co/harbor"]
    onboard.write_yaml(brain / onboard.ATTACHED_KEYS, {"keys": {}})
    assert onboard.attached_mounts(brain, "ben") == ["northwind-co/harbor"]
    reg = registry.Registry(brain)
    reg.subjects["harbor"].crew = []
    reg.save()
    report = onboard.attach_mount_keys(brain, gh=gh)
    assert gh.keys["northwind-co/harbor"] == {} and "no longer on its crew" in report.steps[0].result
    # a record a pull request put in the private folder is not the record
    other = _brain_with_ben(tmp_path / "two")
    onboard.write_yaml(other / onboard.KEY_RECORD, {"keys": {"x y": {"person": "ben", "consumer": "ben-lw/ben-workspace",
                                                                      "mount": "northwind-co/harbor", "outcome": "attached"}}})
    assert onboard.attached_mounts(other, "ben") == ["northwind-co/harbor"]
    subprocess.run(["git", "add", "-f", onboard.KEY_RECORD.as_posix()], cwd=other, check=True, capture_output=True)
    assert onboard.attached_mounts(other, "ben") == []


def test_a_forged_answer_removes_no_key_of_another_person(tmp_path):
    """The answers are editable in any pull request: an 'attached' entry for an unregistered person, or for a
    consumer that is not the entry person's, or under another key, is not taken, so it removes nobody's key."""
    harbor = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "crew": ["ben"],
              "match": {"repo": "https://github.com/northwind-co/harbor"}}
    brain = _brain_with_ben(tmp_path, people=[harbor])
    _queued(brain, mount="northwind-co/harbor")
    gh = FakeGH(admin={"northwind-co/harbor"}, readers={("northwind-co/harbor", "ben-lw"): "read"}, pushers=("ben-lw",))
    assert onboard.attach_mount_keys(brain, gh=gh).steps[0].result == "attached"
    held = dict(gh.keys["northwind-co/harbor"])
    own = "ben-lw/ben-workspace northwind-co/harbor"
    legit = onboard.read_yaml(brain / onboard.ATTACHED_KEYS, {})["keys"][own]

    def forge(key, person, lose_record=False):
        if lose_record:
            onboard.key_record_path(brain).unlink(missing_ok=True)
        onboard.write_yaml(brain / onboard.ATTACHED_KEYS, {"keys": {key: {
            "person": person, "consumer": "ben-lw/ben-workspace", "mount": "northwind-co/harbor", "outcome": "attached"}}})
        report = onboard.attach_mount_keys(brain, gh=gh)
        assert gh.keys["northwind-co/harbor"] == held, (key, person, [s.result for s in report.steps])
    forge("forged", "ghost")                      # not under its own key
    forge(own, "ghost")                           # the record already holds that key
    forge(own, "ghost", lose_record=True)         # no such person
    forge(own, "ada", lose_record=True)           # not Ada's repository
    assert onboard.attached_mounts(brain, "ben") == []
    # a lost record still finds a person's own key from their answer
    onboard.write_yaml(brain / onboard.ATTACHED_KEYS, {"keys": {own: legit}})
    assert onboard.attached_mounts(brain, "ben") == ["northwind-co/harbor"]


def test_a_mount_this_account_does_not_administer_is_the_owners_item_and_the_request_waits(tmp_path):
    import needs_you
    harbor = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "crew": ["ben"],
              "match": {"repo": "https://github.com/northwind-co/harbor"}}
    brain = _brain_with_ben(tmp_path, people=[harbor])
    path = _queued(brain, mount="northwind-co/harbor")
    gh = FakeGH(admin=set(), readers={("northwind-co/harbor", "ben-lw"): "read"}, pushers=("ben-lw",))
    onboard.keys_upkeep(brain, gh=gh)
    items = onboard.read_yaml(brain / onboard.MOUNT_KEY_ITEMS, {})["items"]
    item = next(i for i in items if i["text"] == "mount key ben-lw/ben-workspace on northwind-co/harbor")
    assert "admin on northwind-co/harbor" in item["recommendation"] and "Ben Liu" in item["recommendation"]
    assert not any("harbor" in r["text"] for r in needs_you.health_rows(brain, "mount-keys"))
    assert path.exists() and not gh.keys
    gh.admin = {"northwind-co/harbor"}   # admin granted: the next run attaches it
    onboard.keys_upkeep(brain, gh=gh)
    assert list(gh.keys["northwind-co/harbor"].values()) == ["spaces checks of ben-lw/ben-workspace"]


# ----- round 3 repair: the record no merge writes, rows for the person, the owner's login ----------------
def _g(cwd, *args):
    res = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args], cwd=cwd,
                         capture_output=True, text=True)
    assert res.returncode == 0, (args, res.stdout, res.stderr)
    return res.stdout


def test_a_key_on_a_mount_this_account_does_not_administer_is_no_stuck_agent_row(tmp_path):
    """A forged 'attached' answer on a repository the brain does not administer is dropped in one run: the brain
    attaches only where it administers. A real recorded key there (admin lost later) is the owner's item."""
    import needs_you
    harbor = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "crew": ["ben"],
              "match": {"repo": "https://github.com/northwind-co/harbor"}}
    brain = _brain_with_ben(tmp_path, people=[harbor])
    forged = "ben-lw/ben-workspace ben-lw/side-project"
    onboard.write_yaml(brain / onboard.ATTACHED_KEYS, {"keys": {forged: {
        "person": "ben", "consumer": "ben-lw/ben-workspace", "mount": "ben-lw/side-project", "outcome": "attached"}}})
    gh = FakeGH(admin={"northwind-co/brain", "northwind-co/harbor"},
                readers={("northwind-co/harbor", "ben-lw"): "read"}, pushers=("ben-lw",))

    def github(args, input=None):   # a repository this account does not administer lists no keys to it
        repo = next((a for a in map(str, args) if a.startswith("repos/")), "")[len("repos/"):]
        if "/keys" in repo and repo.partition("/keys")[0] not in gh.admin:
            return subprocess.CompletedProcess(args, 1, "", "HTTP 404")
        return gh(args, input)
    onboard.keys_upkeep(brain, gh=github)
    assert needs_you.health_rows(brain, "mount-keys") == []
    assert forged not in onboard._key_entries(onboard.key_record_path(brain))
    assert forged not in onboard._key_entries(brain / onboard.ATTACHED_KEYS)
    assert onboard.attached_mounts(brain, "ben") == []
    # a key the brain did attach, on a project whose admin it then lost, and Ben left the crew
    _queued(brain, mount="northwind-co/harbor")
    onboard.keys_upkeep(brain, gh=github)
    assert gh.keys["northwind-co/harbor"]
    gh.admin.discard("northwind-co/harbor")
    reg = registry.Registry(brain)
    reg.subjects["harbor"].crew = []
    reg.save()
    onboard.keys_upkeep(brain, gh=github)
    assert needs_you.health_rows(brain, "mount-keys") == []
    items = onboard.read_yaml(brain / onboard.MOUNT_KEY_ITEMS, {})["items"]
    assert any("northwind-co/harbor" in i["text"] and "spaces checks of ben-lw/ben-workspace" in i["recommendation"]
               for i in items)
    # the same forged answer on a project Ben IS crew on and reads, which the brain does not administer: no removal
    # is due, yet the entry is not kept, so leaving that crew later raises no owner item and offboarding lists nothing
    side = {"id": "side", "kind": "projects", "title": "Side", "home": "side", "crew": ["ben"],
            "match": {"repo": "https://github.com/ben-lw/side-project"}}
    crew = _brain_with_ben(tmp_path / "crew", people=[side])
    onboard.write_yaml(crew / onboard.ATTACHED_KEYS, {"keys": {forged: {
        "person": "ben", "consumer": "ben-lw/ben-workspace", "mount": "ben-lw/side-project", "outcome": "attached"}}})
    gh = FakeGH(admin={"northwind-co/brain"}, readers={("ben-lw/side-project", "ben-lw"): "read"}, pushers=("ben-lw",))
    onboard.keys_upkeep(crew, gh=github)
    assert forged not in onboard._key_entries(onboard.key_record_path(crew))
    assert forged not in onboard._key_entries(crew / onboard.ATTACHED_KEYS)
    reg = registry.Registry(crew)
    reg.subjects["side"].crew = []
    reg.save()
    onboard.keys_upkeep(crew, gh=github)
    assert not [i for i in onboard.read_yaml(crew / onboard.MOUNT_KEY_ITEMS, {}).get("items") or []
                if "ben-lw/side-project" in i["text"]]
    assert onboard.attached_mounts(crew, "ben") == []
    # a record lost with a computer: an answer for a key the brain sees on a mount it administers is kept
    lost = _brain_with_ben(tmp_path / "lost", people=[harbor])
    entry = "ben-lw/ben-workspace northwind-co/harbor"
    onboard.write_yaml(lost / onboard.ATTACHED_KEYS, {"keys": {entry: {
        "person": "ben", "consumer": "ben-lw/ben-workspace", "mount": "northwind-co/harbor", "outcome": "attached"}}})
    held = FakeGH(admin={"northwind-co/harbor"}, readers={("northwind-co/harbor", "ben-lw"): "read"},
                  keys={"northwind-co/harbor": {"7": "spaces checks of ben-lw/ben-workspace"}})
    # its first run is offline: a failed call is no answer, so the entry stays in the answers and the health says so
    down = lambda args, input=None: subprocess.CompletedProcess(args, 1, "", "error connecting to api.github.com")
    onboard.keys_upkeep(lost, gh=down)
    assert entry not in onboard._key_entries(onboard.key_record_path(lost))
    assert entry in onboard._key_entries(lost / onboard.ATTACHED_KEYS)
    assert any("could not confirm mount key ben-lw/ben-workspace on northwind-co/harbor" in str(r)
               for r in needs_you.health_rows(lost, "mount-keys"))
    onboard.attach_mount_keys(lost, gh=held)
    assert entry in onboard._key_entries(onboard.key_record_path(lost))
    # and once Ben is off the crew, the key it confirmed is removed
    reg = registry.Registry(lost)
    reg.subjects["harbor"].crew = []
    reg.save()
    onboard.keys_upkeep(lost, gh=held)
    assert held.keys["northwind-co/harbor"] == {}
    assert onboard._key_entries(onboard.key_record_path(lost))[entry]["outcome"] == "removed"
    # an answer for a mount GitHub says is not there (deleted, or private and unseen by this account) is an
    # answer, not an outage: dropped in one run, with no row the daily repeats
    gone = _brain_with_ben(tmp_path / "gone", people=[harbor])
    ghost = "ben-lw/ben-workspace ghost-org/gone"
    onboard.write_yaml(gone / onboard.ATTACHED_KEYS, {"keys": {ghost: {
        "person": "ben", "consumer": "ben-lw/ben-workspace", "mount": "ghost-org/gone", "outcome": "attached"}}})
    seen = FakeGH(admin={"northwind-co/brain"})

    def missing(args, input=None):
        if any(str(a).startswith("repos/ghost-org/gone") for a in args):
            return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")
        return seen(args, input)
    onboard.keys_upkeep(gone, gh=missing)
    assert ghost not in onboard._key_entries(gone / onboard.ATTACHED_KEYS)
    assert needs_you.health_rows(gone, "mount-keys") == []


def test_a_crew_member_of_a_walled_project_gets_a_key_for_it(tmp_path):
    """Crew lives in the walled classification (classify and onboarding's fill write it there): that crew member's
    key request for the project mount is attached, and is removed once they are off the classification's crew."""
    harbor = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
              "match": {"repo": "https://github.com/northwind-co/harbor"},
              "classification": {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ben"],
                                 "reason": "given its own space at onboarding", "evidence": [],
                                 "decided_by": "Ada Okafor", "at": "2026-11-01T00:00:00Z",
                                 "checked_with": [], "not_yet_checked": []}}
    brain = _brain_with_ben(tmp_path, people=[harbor])
    _queued(brain, mount="northwind-co/harbor")
    gh = FakeGH(admin={"northwind-co/harbor"}, readers={("northwind-co/harbor", "ben-lw"): "read"}, pushers=("ben-lw",))
    assert onboard.attach_mount_keys(brain, gh=gh).steps[0].result == "attached" and gh.keys["northwind-co/harbor"]
    rows = yaml.safe_load((brain / "_meta" / "subjects.yaml").read_text(encoding="utf-8"))
    next(r for r in rows["subjects"] if r["id"] == "harbor")["classification"]["crew"] = []
    (brain / "_meta" / "subjects.yaml").write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")
    assert "no longer on its crew" in onboard.attach_mount_keys(brain, gh=gh).steps[0].result
    assert gh.keys["northwind-co/harbor"] == {}


def test_an_owner_row_with_no_login_gets_the_codeowners_login_without_a_hand_edit(tmp_path):
    """A brain set up before onboarding wrote the owner's login: the upkeep writes it into the owner's registry row
    (the login CODEOWNERS on main names, else the signed-in account's), so the person only clicks the sync's pull
    request, and no Needs-you item asks for a hand edit."""
    people = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "workspace": "ada"}]
    brain = _git_init(make_space(tmp_path / "brain", profile="brain", name="brain", subjects=people))
    onboard.write_yaml(brain / "_meta" / "state" / "onboard.yaml", {"case": "company", "repo": "northwind-co/brain"})
    (brain / ".github").mkdir(exist_ok=True)
    (brain / ".github" / "CODEOWNERS").write_text("* @adaokafor\n", encoding="utf-8")
    _g(brain, "add", "-A")
    _g(brain, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fill")
    _g(brain, "update-ref", "refs/remotes/origin/main", "HEAD")
    report = onboard.keys_upkeep(brain, gh=FakeGH())
    assert registry.Registry(brain).subjects["ada"].match["github"] == "adaokafor"
    assert any(s.name == "owner login" and s.done for s in report.steps)
    assert not [i for i in onboard.read_yaml(brain / onboard.MOUNT_KEY_ITEMS, {}).get("items") or []
                if not i["id"].startswith("mount-keys-signing-key")]   # this computer's key line is the person's click, not a login
    # no CODEOWNERS login on main: the signed-in account's
    other = _git_init(make_space(tmp_path / "other", profile="brain", name="brain", subjects=people))
    gh = FakeGH()
    onboard.keys_upkeep(other, gh=lambda args, input=None: done(args, 0, "adaokafor\n") if args[:2] == ["api", "user"]
                        else gh(args, input))
    assert registry.Registry(other).subjects["ada"].match["github"] == "adaokafor"


def test_a_merged_pull_request_that_force_adds_the_record_and_flips_the_answers_hides_no_key(tmp_path):
    """The sync's own merge: a colleague's pull request force-adds a file at the old record path and sets the
    published answers to refused. The record is in the git dir, so the merge writes nothing it reads: Ben's key on
    harbor is still removed once he is off its crew, and the tracked private file is a security notice."""
    harbor = {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor", "crew": ["ben"],
              "match": {"repo": "https://github.com/northwind-co/harbor"}}
    brain = _brain_with_ben(tmp_path, people=[harbor])
    (brain / ".gitignore").write_text("_meta/state/private/\n_meta/state/notices/\n", encoding="utf-8")
    _g(brain, "add", "-A")
    _g(brain, "commit", "-qm", "base")
    _g(brain, "branch", "-M", "main")
    origin = tmp_path / "origin.git"
    _g(tmp_path, "init", "-q", "--bare", str(origin))
    _g(brain, "remote", "add", "origin", str(origin))
    _g(brain, "push", "-q", "origin", "main")
    _queued(brain, mount="northwind-co/harbor")
    gh = FakeGH(admin={"northwind-co/harbor"}, readers={("northwind-co/harbor", "ben-lw"): "read"}, pushers=("ben-lw",))
    assert onboard.attach_mount_keys(brain, gh=gh).steps[0].result == "attached"
    assert ".git" in onboard.key_record_path(brain).parts and onboard.key_record_path(brain).is_file()
    _g(brain, "add", onboard.ATTACHED_KEYS.as_posix())
    _g(brain, "commit", "-qm", "sync")
    _g(brain, "push", "-q", "origin", "main")
    pr = tmp_path / "pr"
    _g(tmp_path, "clone", "-q", "-b", "main", str(origin), str(pr))
    onboard.write_yaml(pr / onboard.KEY_RECORD, {"keys": {}})
    answers = onboard.read_yaml(pr / onboard.ATTACHED_KEYS, {})
    for entry in answers["keys"].values():
        entry["outcome"] = "refused"
    onboard.write_yaml(pr / onboard.ATTACHED_KEYS, answers)
    _g(pr, "add", "-f", onboard.KEY_RECORD.as_posix(), onboard.ATTACHED_KEYS.as_posix())
    _g(pr, "commit", "-qm", "tidy")
    _g(pr, "push", "-q", "origin", "HEAD:main")
    _g(brain, "fetch", "-q", "origin")
    _g(brain, "merge", "--no-edit", "origin/main")   # as sync.py merges
    assert (brain / onboard.KEY_RECORD).read_text(encoding="utf-8") == "keys: {}\n"   # the merge wrote that file
    assert onboard.attached_mounts(brain, "ben") == ["northwind-co/harbor"]           # and the record is untouched
    reg = registry.Registry(brain)
    reg.subjects["harbor"].crew = []
    reg.save()
    report = onboard.attach_mount_keys(brain, gh=gh)
    assert gh.keys["northwind-co/harbor"] == {}
    assert any(s.name == "private files tracked" and s.needs_owner for s in report.steps)
    queued = [json.loads(p.read_text(encoding="utf-8")) for p in (brain / "_meta/state/notices/pending").glob("*.json")]
    assert [q["kind"] for q in queued] == ["security"]


def test_offboarding_finds_a_crew_projects_key_with_no_record(tmp_path):
    """The record-free backstop: no record and no answer lists Ben's key on a project he is crew on; offboarding
    still lists the keys there and removes his workspace's."""
    brain = _leaving(tmp_path)
    reg = registry.Registry(brain)
    reg.subjects["harbor"].crew = ["ada", "ben"]
    reg.subjects["harbor"].match = {"repo": "https://github.com/northwind-co/dock"}
    reg.save()
    gh = Org(keys={"northwind-co/dock": {"4": "spaces checks of ben-gh/ben-workspace", "5": "spaces checks of x/y"}})
    assert onboard.attached_mounts(brain, "ben") == []
    offboard.leaver(brain, "ben", online=True, remove=True, gh=gh)
    assert gh.keys["northwind-co/dock"] == {"5": "spaces checks of x/y"}


def test_a_row_only_the_person_can_clear_is_their_needs_you_item_and_health_names_no_denied_command(tmp_path, monkeypatch):
    import datetime as dt
    import needs_you
    ws, brain, asked = _workspace_on_brain(tmp_path, monkeypatch)
    onboard.write_yaml(ws / "_meta" / "state" / "onboard.yaml", {"repo": "ada-lw/ada-workspace"})
    gh = FakeGH(admin={"ada-lw/ada-workspace"})
    day = dt.date(2026, 11, 5)
    onboard.keys_upkeep(ws, gh=gh, keygen=_keygen([]), today=day)
    _answer(brain, asked[0], "refused", "Ada Okafor (ada-lw) cannot read northwind-co/brain")
    onboard.keys_upkeep(ws, gh=gh, keygen=_keygen([]), today=day)
    items = {i.id: i for i in needs_you.routine_items(ws)}
    item = items["mount-keys-mount-token-ada-lw-ada-workspace"]
    assert item.for_ == "Ada Okafor" and "BRAIN_READ_TOKEN" in item.recommendation
    # a space with no origin remote: the person's item, never a health row naming a command routines are denied
    bare = make_space(tmp_path / "bare", name="bare")
    (bare / ".gitmodules").write_text('[submodule "brain"]\n\tpath = brain\n\turl = https://github.com/n/brain\n',
                                      encoding="utf-8")
    monkeypatch.setattr(onboard, "space_repo", lambda root: None)
    onboard.keys_upkeep(bare, gh=FakeGH())
    assert "mount-keys-mount-keys" in {i.id for i in needs_you.routine_items(bare)}
    for root in (ws, bare):
        assert all("onboard.py" not in r["agent"] for r in needs_you.health_rows(root, "mount-keys"))


def test_an_organization_secret_every_colleague_reads_is_a_security_notice(tmp_path, monkeypatch):
    ws = make_space(tmp_path / "ada", name="ada")
    (ws / ".gitmodules").write_text('[submodule "brain"]\n\tpath = brain\n\turl = https://github.com/northwind-co/brain\n',
                                    encoding="utf-8")
    monkeypatch.setattr(onboard, "run_cmd", lambda argv, *a, **k: done(
        argv, 0 if any("orgs/northwind-co/actions/secrets/" in str(x) for x in argv) else 1))
    monkeypatch.setattr(onboard, "space_repo", lambda root: "northwind-co/ada-workspace")
    monkeypatch.setattr(onboard, "mount_access", lambda report, repo, root, **k: onboard.organization_token(
        report, repo, None, False))
    onboard.keys_upkeep(ws, gh=FakeGH())
    onboard.keys_upkeep(ws, gh=FakeGH())   # once per organisation, however many runs
    queued = [json.loads(p.read_text(encoding="utf-8")) for p in (ws / "_meta/state/notices/pending").glob("*.json")]
    assert [q["kind"] for q in queued] == ["security"]


def test_the_owners_row_gets_their_login_from_the_first_fill_and_the_gate_trusts_it(tmp_path, monkeypatch):
    """The brain's registry built by fill_registry, not by hand: the owner's row carries match.github, so the gate
    lands the owner's own signature, upkeep edit and release; a brain from before it gets one item for the person."""
    import needs_you
    from conftest import write_page
    from test_security_round3_gate import _commit, _gate, _git, _init, _stable
    monkeypatch.setattr(gate, "_key_signed", lambda *args: True)   # the login tie; the key tie: test_signing_key.py
    root = make_space(tmp_path / "brain", profile="brain", name="northwind", owner="Ada Okafor", subjects=[])
    answers = {"owner": "Ada Okafor", "owner_email": "ada@example.test", "company": "Northwind",
               "owner_login": "adaokafor"}   # start.py passes who["login"] as owner_login
    onboard.fill_registry(onboard.Report(case="company"), root, answers, "Ada Okafor", "northwind",
                          spaceyaml.load(root).now(), workspaces={"ada-okafor": "ada-okafor"},
                          workspace_repos={"ada-okafor": "https://github.com/northwind/ada-okafor-workspace"})
    rows = yaml.safe_load((root / "_meta/subjects.yaml").read_text(encoding="utf-8"))["subjects"]
    assert next(r for r in rows if r["id"] == "ada-okafor")["match"] == {"github": "adaokafor"}
    base = _init(root)
    _stable(root, "Ada Okafor", "northwind")   # the owner signs a page and her own sync opens the pull request
    _commit(root)
    assert _gate(root, base, author="adaokafor", head_ref="sync/m-1")[0] != "protected"
    _git(root, "reset", "-q", "--hard", base)
    (root / "_meta/subjects.yaml").write_text(yaml.safe_dump({"subjects": rows + [
        {"id": "pilots", "kind": "topics", "title": "Pilots", "home": "northwind"}]}, sort_keys=False), encoding="utf-8")
    _commit(root)   # plain upkeep: a new plain topic
    assert _gate(root, base, author="adaokafor", head_ref="sync/m-2")[0] != "protected"
    _git(root, "reset", "-q", "--hard", base)
    write_page(root, "_inbound/candidates/p-0000ab02.md", "A release.\n", id="p-0000ab02", released_from="ada-okafor")
    _commit(root)   # her release from her own workspace
    assert _gate(root, base, author="adaokafor", head_ref="release/ada-okafor-0123abcd")[0] != "protected"
    # an older brain, its owner's row without a login: one item for the person from the daily upkeep, until filled
    old = make_space(tmp_path / "old", profile="brain", name="brain", subjects=[
        {"id": "ada-okafor", "kind": "people", "title": "Ada Okafor"}])
    onboard.keys_upkeep(old, gh=FakeGH())
    assert "mount-keys-owner-login" in {i.id for i in needs_you.routine_items(old)}
    onboard.fill_registry(onboard.Report(case="company"), old, answers, "Ada Okafor", "brain", spaceyaml.load(old).now())
    assert registry.Registry(old).get("ada-okafor").match.get("github") == "adaokafor"
    onboard.keys_upkeep(old, gh=FakeGH())
    assert "mount-keys-owner-login" not in {i.id for i in needs_you.routine_items(old)}
