"""Codebase links (docs/design/2026-10-22-codebase-link-design.md): resolve, first-time detection,
the person's own link and unlink, the `ref:` pointer forms, the scan, the daily check and its
health cell."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

import classify
import link
import registry
import spaceyaml
from conftest import NOW, make_space, mount_brain


def make_codebase(root: Path, url: str = "https://github.com/northwind/harbor-voice.git") -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")
    return root


def mount(space_root: Path, name: str, path: Path) -> None:
    """conftest.mount_brain for any mount name."""
    data = yaml.safe_load((space_root / "space.yaml").read_text(encoding="utf-8"))
    mounts = [m for m in (data.get("mounts") or []) if m.get("name") != name]
    mounts.append({"name": name, "path": str(path)})
    data["mounts"] = mounts
    (space_root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def same(repo, repo_id):
    """A host lookup that finds the repository unchanged."""
    return {"repo": repo, "repo_id": repo_id or "R_1"}


PEOPLE = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
          {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-gh"}}]
HARBOR = [{"id": "harbor", "kind": "projects", "client": "northwind"},
          {"id": "harbor-voice-agent", "kind": "projects", "part_of": "harbor", "client": "northwind"}]


def linked_ws(tmp_path, space=None, **entry):
    """ada's workspace holding harbor-voice-agent, linked to northwind/harbor-voice; `space` adds
    space.yaml keys."""
    repo = dict({"repo": "northwind/harbor-voice", "linked_by": "detect", "linked": "2026-10-01"}, **entry)
    return make_space(tmp_path / "ada", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "client": "northwind", "repos": [repo]}], **(space or {}))


def test_resolve_linked(tmp_path, capsys):
    # the subject in ada's own registry, readers inside
    ws = linked_ws(tmp_path, readers="inside")
    code = make_codebase(tmp_path / "code")
    out = link.resolve(ws, code)
    assert out["subject"] == "harbor-voice-agent" and out["readers"] == "inside" and out["repo"] == "northwind/harbor-voice"
    assert set(out) == {"subject", "title", "parent", "home", "audience", "readers", "left_out", "status_page", "repo"}
    assert link.main([str(ws), "resolve", "--cwd", str(code)]) == 0
    assert json.loads(capsys.readouterr().out)["subject"] == "harbor-voice-agent"


def test_resolve_detects_once_then_resolves(tmp_path, now):
    # ws registry: harbor (client northwind) and harbor-voice-agent (part_of harbor, client northwind), no links
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR)
    code = make_codebase(tmp_path / "code")
    out = link.resolve(ws, code, now=now)
    assert out["subject"] == "harbor-voice-agent" and out["parent"] == "harbor"
    assert "unlink northwind/harbor-voice" in (ws / "log.md").read_text(encoding="utf-8")
    entry = registry.Registry(ws).get("harbor-voice-agent").extra["repos"][0]
    assert entry["linked_by"] == "detect" and entry["linked"] == now.date().isoformat()
    assert link.resolve(ws, code, now=now)["subject"] == "harbor-voice-agent"
    assert len((ws / link.SEEN).read_text(encoding="utf-8").splitlines()) == 1


def test_resolve_unlinked_exits_3_and_never_asks_twice(tmp_path, capsys, now):
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR)
    other = make_codebase(tmp_path / "other", "https://github.com/someone/dotfiles")
    assert link.main([str(ws), "resolve", "--cwd", str(other)]) == 3 and capsys.readouterr().out == ""
    before = (ws / link.SEEN).read_text(encoding="utf-8")
    assert link.main([str(ws), "resolve", "--cwd", str(other)]) == 3
    assert (ws / link.SEEN).read_text(encoding="utf-8") == before
    assert link.resolve(ws, tmp_path / "nowhere") is None   # no repository at all


def test_score_prefers_the_specific_subject(tmp_path):
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR)
    reg = registry.Registry(ws)
    assert link.score("northwind/harbor-voice", reg.get("harbor-voice-agent"), reg) == 5
    assert link.score("northwind/harbor-voice", reg.get("harbor"), reg) == 3
    assert link.score("northwind/harbor-voice", reg.get("ada"), reg) == 0   # people never score


def test_detect_asks_once_on_a_tie(tmp_path, now):
    # northwind/voice vs harbor-voice-agent and lighthouse-voice, both client northwind
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR + [
        {"id": "lighthouse-voice", "kind": "projects", "client": "northwind"}])
    out = link.detect(ws, "northwind/voice", now=now)
    assert out["outcome"] == "asked" and out["candidates"] == ["harbor-voice-agent", "lighthouse-voice"]
    items = yaml.safe_load((ws / link.NEEDS_YOU).read_text(encoding="utf-8"))["items"]
    assert len(items) == 1 and items[0]["id"] == "link-northwind-voice"
    assert items[0]["text"] == "northwind/voice could belong to harbor-voice-agent or lighthouse-voice."
    assert "harbor-voice-agent" in items[0]["recommendation"] and items[0]["default"] == "nothing is linked"
    assert link.detect(ws, "northwind/voice", now=now)["outcome"] == "known"
    assert len(yaml.safe_load((ws / link.NEEDS_YOU).read_text(encoding="utf-8"))["items"]) == 1


def test_detect_links_two_against_one(tmp_path, now):
    # lumen/app vs subject app (kind topics, title App) scoring 2, and client lumen scoring 1
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + [{"id": "app", "kind": "topics", "title": "App"},
                                                          {"id": "lumen", "kind": "clients", "title": "Lumen"}])
    out = link.detect(ws, "lumen/app", now=now)
    assert out["outcome"] == "linked" and out["subject"] == "app"


def test_detect_skips_subjects_the_person_may_not_link(tmp_path, now):
    # brain holds harbor-voice-agent, home harbor, crew ["ben"]; ws owner Ada Okafor
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", owner="Ben Liu", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ben"], "client": "northwind"}])
    ws = make_space(tmp_path / "ada")
    mount_brain(ws, brain)
    assert link.detect(ws, "northwind/harbor-voice", now=now)["outcome"] == "none"
    assert not list((ws / "_outbound" / "signals").glob("*.yaml"))


def test_resolve_from_subfolder_and_worktree(tmp_path):     # Review Focus 1
    ws = linked_ws(tmp_path)
    code = make_codebase(tmp_path / "code")
    (code / "src" / "deep").mkdir(parents=True)
    assert link.resolve(ws, code / "src" / "deep")["subject"] == "harbor-voice-agent"
    wt = tmp_path / "wt"
    wt.mkdir()
    (code / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {code / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8")
    assert link.resolve(ws, wt)["subject"] == "harbor-voice-agent"
    (code / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n", encoding="utf-8")
    assert link.resolve(ws, wt)["subject"] == "harbor-voice-agent"


def test_resolve_non_github_remote(tmp_path):              # Review Focus 2
    # the subject links "https://gitlab.example/northwind/harbor-voice"; the codebase remote is that URL plus .git
    ws = linked_ws(tmp_path, repo="https://gitlab.example/northwind/harbor-voice")
    code = make_codebase(tmp_path / "code", "https://gitlab.example/northwind/harbor-voice.git")
    assert link.resolve(ws, code)["subject"] == "harbor-voice-agent"
    bare = tmp_path / "bare"
    (bare / ".git").mkdir(parents=True)
    (bare / ".git" / "config").write_text('[remote "upstream"]\n\turl = https://github.com/northwind/harbor-voice\n',
                                          encoding="utf-8")
    assert link.resolve(ws, bare) is None   # no origin
    (bare / ".git" / "config").write_text('[remote "origin"]\n\tpushurl = https://github.com/someone/fork\n'
                                          '\turl = https://gitlab.example/northwind/harbor-voice.git\n', encoding="utf-8")
    assert link.resolve(ws, bare)["subject"] == "harbor-voice-agent"   # url, not pushurl


def test_resolve_drops_user_info_and_ssh_form(tmp_path, now):
    # ws registry: harbor and harbor-voice-agent, no links; a remote carrying a made-up token, then an ssh:// clone
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR)
    token = "ghp_MadeUpToken123"
    out = link.resolve(ws, make_codebase(tmp_path / "code", f"https://{token}@github.com/northwind/harbor-voice.git"),
                       now=now)
    assert out["subject"] == "harbor-voice-agent" and out["repo"] == "northwind/harbor-voice"
    ssh = make_codebase(tmp_path / "ssh", "ssh://git@github.com/Northwind/harbor-voice.git")
    assert link.resolve(ws, ssh, now=now)["repo"] == "northwind/harbor-voice"
    assert len(registry.Registry(ws).get("harbor-voice-agent").extra["repos"]) == 1
    files = [p for p in ws.rglob("*") if p.is_file()]
    assert not [p for p in files if token.lower() in p.read_text(encoding="utf-8", errors="replace").lower()]


def test_resolve_in_a_space_checkout_is_none(tmp_path, now):
    # project space harbor holds harbor and harbor-voice-agent; its own remote is northwind/harbor-voice
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", subjects=PEOPLE + HARBOR)
    make_codebase(harbor)
    (harbor / "work").mkdir(exist_ok=True)
    before = {p: p.read_bytes() for p in harbor.rglob("*") if p.is_file()}
    assert link.resolve(harbor, harbor / "work", now=now) is None
    assert {p: p.read_bytes() for p in harbor.rglob("*") if p.is_file()} == before
    (harbor / ".github").mkdir()
    (harbor / ".github" / "template-marker").write_text("", encoding="utf-8")
    assert link.resolve(harbor, harbor, now=now)["subject"] == "harbor-voice-agent"   # the template is a codebase


@pytest.mark.parametrize("brain_day, harbor_day, winner", [("2026-10-01", "2026-10-05", "beacon-app"),
                                                           ("2026-10-05", "2026-10-01", "harbor-voice-agent")])
def test_resolve_two_registries_older_wins(tmp_path, brain_day, harbor_day, winner):     # Review Focus 3
    # brain holds beacon-app, a mounted project holds harbor-voice-agent, same repo; the older link wins
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", subjects=PEOPLE + [
        {"id": "beacon-app", "kind": "projects", "repos": [{"repo": "northwind/harbor-voice", "linked": brain_day}]}])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", subjects=[
        {"id": "harbor-voice-agent", "kind": "projects", "repos": [{"repo": "northwind/harbor-voice", "linked": harbor_day}]}])
    ws = make_space(tmp_path / "ada")
    mount_brain(ws, brain)
    mount(ws, "harbor", harbor)
    code = make_codebase(tmp_path / "code")
    assert link.resolve(ws, code)["subject"] == winner
    assert link.resolve(ws, code)["subject"] == winner


def test_resolve_pending_until_the_holder_applies(tmp_path, now):
    # brain holds harbor-voice-agent (home harbor, crew ["ada"]); resolve in ws sends the link note
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ada"], "client": "northwind"}])
    ws = make_space(tmp_path / "ada")
    mount_brain(ws, brain)
    code = make_codebase(tmp_path / "code")
    out = link.resolve(ws, code, now=now)
    assert out["subject"] == "harbor-voice-agent" and out["home"] == "harbor"
    assert out["audience"] is None and out["left_out"] is None and out["status_page"] is None   # harbor not mounted
    assert link.links(ws)[-1].pending and link.links(ws)[-1].holder == "brain"
    (signal,) = (ws / "_outbound" / "signals").glob("*.yaml")
    (brain / "_inbound" / "signals").mkdir(parents=True, exist_ok=True)
    signal.replace(brain / "_inbound" / "signals" / signal.name)
    assert classify.land_notes(brain, now=now)["applied"] == 1
    assert [(l.subject, l.pending) for l in link.links(ws)] == [("harbor-voice-agent", False)]


def test_resolve_gone_link_is_unlinked(tmp_path, now):
    # the repos entry carries gone: <now>; SEEN has a row for the repo
    ws = linked_ws(tmp_path, gone=now.date().isoformat())
    (ws / link.SEEN).write_text(json.dumps({"repo": "northwind/harbor-voice", "at": "2026-10-01T09:00:00Z",
                                            "outcome": "linked", "subject": "harbor-voice-agent"}) + "\n",
                                encoding="utf-8")
    assert link.resolve(ws, make_codebase(tmp_path / "code")) is None


def test_resolve_reads_home_audience_and_status_page(tmp_path):
    # home harbor mounted, audience [Ada Okafor, Ben Liu], with live/subjects/harbor-voice-agent.md
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ada"],
         "repos": [{"repo": "northwind/harbor-voice", "readers": "wider", "linked": "2026-10-01"}]}])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", audience=["Ada Okafor", "Ben Liu"],
                        left_out=["prices"])
    (harbor / "live" / "subjects" / "harbor-voice-agent.md").write_text("# Harbor voice agent\n", encoding="utf-8")
    ws = make_space(tmp_path / "ada")
    mount_brain(ws, brain)
    mount(ws, "harbor", harbor)
    out = link.resolve(ws, make_codebase(tmp_path / "code"))
    assert out["audience"] == ["Ada Okafor", "Ben Liu"] and out["status_page"].endswith("harbor-voice-agent.md")
    assert out["left_out"] == ["prices"] and out["home"] == "harbor" and out["readers"] == "wider"
    assert Path(out["status_page"]).is_absolute()


def test_resolve_is_offline_and_fast(tmp_path, monkeypatch, now):
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR)
    code = make_codebase(tmp_path / "code")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("network"))
    started = time.perf_counter()
    assert link.resolve(ws, code, now=now) is not None
    assert time.perf_counter() - started < 1.0


def test_person_links_and_unlinks(tmp_path, now):
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + [{"id": "product", "kind": "topics", "title": "Product"}])
    assert link.main([str(ws), "link", "lumen/app", "product"]) == 0 and link.link_for(ws, "lumen/app").linked_by == "person"
    assert link.main([str(ws), "unlink", "lumen/app"]) == 0 and link.link_for(ws, "lumen/app") is None
    log = (ws / "log.md").read_text(encoding="utf-8")
    assert "linked lumen/app to product (person); undo: python _meta/tools/link.py . unlink lumen/app" in log
    assert "unlinked lumen/app from product; undo: python _meta/tools/link.py . link lumen/app product" in log
    assert link.main([str(ws), "unlink", "lumen/app"]) == 1   # nothing left to unlink
    assert [json.loads(l)["outcome"] for l in (ws / link.SEEN).read_text(encoding="utf-8").splitlines()] == \
        ["linked", "unlinked"]
    # a pasted remote URL is stored as owner/name, and its token is written nowhere
    assert link.main([str(ws), "link", "https://x-access-token:FAKE_TOKEN_123@github.com/Lumen/App.git", "product"]) == 0
    assert link.link_for(ws, "lumen/app").subject == "product"
    assert not [p for p in ws.rglob("*") if p.is_file() and b"FAKE_TOKEN_123" in p.read_bytes()]


def test_ref_forms():
    assert link.refs_in("x\nref: northwind/harbor-voice:docs/spec.md@abc123\n- ref: `https://github.com/northwind/harbor-voice/pull/7`\n") == \
        ["northwind/harbor-voice:docs/spec.md@abc123", "https://github.com/northwind/harbor-voice/pull/7"]
    assert link.ref_repo("https://github.com/northwind/harbor-voice/pull/7") == "northwind/harbor-voice"
    assert link.ref_repo("northwind/harbor-voice@abc123") == "northwind/harbor-voice"
    assert link.ref_repo("northwind/harbor-voice:docs/spec.md@abc123") == "northwind/harbor-voice"
    assert link.ref_url("northwind/harbor-voice:docs/spec.md@abc123") == "https://github.com/northwind/harbor-voice/blob/abc123/docs/spec.md"
    assert link.ref_url("northwind/harbor-voice@abc123") == "https://github.com/northwind/harbor-voice/commit/abc123"
    assert link.ref_url("https://github.com/northwind/harbor-voice/pull/7") == "https://github.com/northwind/harbor-voice/pull/7"
    assert link.ref_repo("not a pointer") is None and link.ref_url("not a pointer") is None


# ----- scan, check and the health cell ----------------------------------------------------------
def test_scan_runs_detect_per_listed_repo(tmp_path, now):
    # ws registry: harbor, harbor-voice-agent and lighthouse-voice (client northwind); ws's own remote is northwind's
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR + [
        {"id": "lighthouse-voice", "kind": "projects", "client": "northwind"}])
    make_codebase(ws, "https://github.com/northwind/ada-space.git")
    owners = []

    def listed(owner):
        owners.append(owner)
        return ["northwind/harbor-voice", "northwind/voice"]
    out = link.scan(ws, now=now, list_fn=listed, id_fn=lambda r: None)
    assert out["linked"] == 1 and out["asked"] == 1 and out["seen"] == 2 and not out["skipped"]
    assert owners == ["ada-gh", "northwind"]   # the person's login, then the owner of this space's remote
    assert link.link_for(ws, "northwind/harbor-voice").subject == "harbor-voice-agent"


def test_scan_without_host_is_skipped(tmp_path, now):       # Review Focus 2
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + HARBOR)
    assert link.scan(ws, now=now, list_fn=lambda owner: None)["skipped"]
    assert not (ws / link.SEEN).exists()
    alone = make_space(tmp_path / "solo")   # no login for the owner, no remote: nothing to list
    assert link.scan(alone, now=now, list_fn=lambda owner: pytest.fail("listed"))["skipped"] == "no account to list"


def test_scan_leaves_out_space_repositories(tmp_path, now):
    # ws's remote is northwind/ada-space; the project space harbor (home of harbor) has the repository northwind/harbor
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + [
        {"id": "harbor", "kind": "projects", "client": "northwind", "home": "harbor"},
        {"id": "harbor-voice-agent", "kind": "projects", "part_of": "harbor", "client": "northwind"}])
    make_codebase(ws, "https://github.com/northwind/ada-space.git")
    out = link.scan(ws, now=now, list_fn=lambda owner: ["northwind/harbor", "northwind/ada-space",
                                                        "northwind/harbor-voice"], id_fn=lambda r: None)
    assert out == {"seen": 3, "linked": 1, "asked": 0, "skipped": ""}
    assert link.link_for(ws, "northwind/harbor") is None and not (ws / link.NEEDS_YOU).exists()
    assert [json.loads(l)["repo"] for l in (ws / link.SEEN).read_text(encoding="utf-8").splitlines()] == \
        ["northwind/harbor-voice"]


def test_a_repo_that_matched_nothing_is_placed_once_its_subject_is_registered(tmp_path, now):
    # onboarding's scan meets two northwind repositories before their subject is registered
    ws = make_space(tmp_path / "ada", subjects=PEOPLE)
    listed = ["northwind/harbor-voice", "northwind/harbor-voice-agent"]
    assert link.scan(ws, now=now, list_fn=lambda owner: listed, id_fn=lambda r: None)["linked"] == 0
    code = make_codebase(tmp_path / "code")
    assert link.resolve(ws, code, now=now) is None
    assert [json.loads(l)["outcome"] for l in (ws / link.SEEN).read_text(encoding="utf-8").splitlines()] == \
        ["none", "none"]   # a repository that matches nothing is written down once
    (ws / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": PEOPLE + HARBOR}), encoding="utf-8")
    assert link.resolve(ws, code, now=now)["subject"] == "harbor-voice-agent"   # a session places it
    out = link.scan(ws, now=now, list_fn=lambda owner: listed, id_fn=lambda r: None)   # project creation places it
    assert out["linked"] == 1 and link.link_for(ws, "northwind/harbor-voice-agent").subject == "harbor-voice-agent"


def test_check_follows_a_rename(tmp_path, now):
    ws = linked_ws(tmp_path, repo_id="R_1")
    out = link.check(ws, now=now, lookup_fn=lambda r, i: {"repo": "northwind/harbor-voice-2", "repo_id": "R_1"},
                     readers_fn=lambda r: ["ada-gh"])
    assert out["repaired"] == 1 and link.links(ws)[0].repo == "northwind/harbor-voice-2"
    # a link with no repo_id yet follows the rename in the same run (the lookup by name follows the
    # host's redirect) and gets the id
    ws = linked_ws(tmp_path / "second")

    def renamed(r, i):
        return {"repo": "northwind/harbor-voice-2", "repo_id": "R_1"}
    assert link.check(ws, now=now, lookup_fn=renamed, readers_fn=lambda r: None)["repaired"] == 1
    (entry,) = registry.Registry(ws).get("harbor-voice-agent").extra["repos"]
    assert (entry["repo"], entry["repo_id"], entry["was"]) == ("northwind/harbor-voice-2", "R_1",
                                                               ["northwind/harbor-voice"])
    assert link.check(ws, now=now, lookup_fn=renamed, readers_fn=lambda r: None)["repaired"] == 0


def test_check_rename_keeps_old_clones_resolving(tmp_path, now):
    # ws holds and homes harbor-voice-agent, linked to northwind/harbor-voice (R_1) elsewhere: no SEEN row here
    ws = linked_ws(tmp_path, repo_id="R_1")

    def renamed(r, i):   # the host follows the old name to the new one
        return {"repo": "northwind/harbor-voice-2", "repo_id": "R_1"}
    assert link.check(ws, now=now, lookup_fn=renamed, readers_fn=lambda r: None)["repaired"] == 1
    old = make_codebase(tmp_path / "old-clone")   # its origin still has the old URL
    out = link.resolve(ws, old, now=now)
    assert out["subject"] == "harbor-voice-agent" and out["repo"] == "northwind/harbor-voice-2"
    assert not (ws / link.SEEN).exists()   # found, not detected again
    for _ in range(2):
        assert link.check(ws, now=now, lookup_fn=renamed, readers_fn=lambda r: None)["repaired"] == 0
    (entry,) = registry.Registry(ws).get("harbor-voice-agent").extra["repos"]
    assert entry["repo"] == "northwind/harbor-voice-2" and entry["was"] == ["northwind/harbor-voice"]
    # another workspace reading the renamed link through its brain finds the old clone too
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "crew": ["ben"], "repos": [entry]}])
    ben = make_space(tmp_path / "ben", owner="Ben Liu")
    mount_brain(ben, brain)
    assert link.resolve(ben, old, now=now)["subject"] == "harbor-voice-agent"
    assert not list((ben / "_outbound" / "signals").glob("*.yaml")) and not (ben / link.SEEN).exists()


def test_check_drops_an_old_name_a_new_repo_took(tmp_path, now):
    # northwind/harbor-voice (R_1, readers inside) becomes northwind/harbor-voice-legacy; later a new
    # public repository (R_2) takes the old name
    ws = linked_ws(tmp_path, repo_id="R_1")
    new = make_codebase(tmp_path / "new")
    state = {"old_name": "followed"}

    def host(r, i):
        if r == "northwind/harbor-voice" and state["old_name"] != "followed":
            if state["old_name"] == "down":
                raise OSError("rate limited")
            return {"repo": r, "repo_id": "R_2", "private": False} if state["old_name"] == "taken" else None
        return {"repo": "northwind/harbor-voice-legacy", "repo_id": "R_1", "private": True}
    assert link.check(ws, now=now, lookup_fn=host, readers_fn=lambda r: ["ada-gh"])["repaired"] == 1
    for unsure in ("down", "missing"):   # the host cannot say, or finds nothing there: the old name stays
        state["old_name"] = unsure
        assert link.check(ws, now=now, lookup_fn=host, readers_fn=lambda r: ["ada-gh"])["repaired"] == 0
        assert link.links(ws)[0].was == ("northwind/harbor-voice",)
    state["old_name"] = "taken"
    assert link.check(ws, now=now, lookup_fn=host, readers_fn=lambda r: ["ada-gh"])["repaired"] == 1
    (entry,) = registry.Registry(ws).get("harbor-voice-agent").extra["repos"]
    assert entry["repo"] == "northwind/harbor-voice-legacy" and "was" not in entry and entry["readers"] == "inside"
    out = link.resolve(ws, new, now=now)   # the new repository is placed as its own, never with the old readers
    assert out["repo"] == "northwind/harbor-voice" and out["readers"] == "wider"
    assert link.check(ws, now=now, lookup_fn=host, readers_fn=lambda r: ["ada-gh"]) == \
        {"links": 2, "wider": 1, "repaired": 0}
    repos = registry.Registry(ws).get("harbor-voice-agent").extra["repos"]
    assert [(e["repo"], e["repo_id"], e["readers"]) for e in repos] == [
        ("northwind/harbor-voice-legacy", "R_1", "inside"), ("northwind/harbor-voice", "R_2", "wider")]


@pytest.mark.parametrize("rename_lands_first", [True, False])
@pytest.mark.parametrize("older_first", [True, False])
@pytest.mark.parametrize("start", ["old clone", "old clone gone", "new clone"])
def test_an_old_clone_entry_next_to_the_renamed_one_becomes_one(tmp_path, now, start, older_first, rename_lands_first):
    # the brain holds harbor-voice-agent (home harbor) twice for one repository (R_1), renamed from
    # northwind/harbor-voice to northwind/harbor-voice-2: "old clone" is the renamed link plus one linked again
    # from an old clone, "old clone gone" the same after the host lost the old name for a day, "new clone" the
    # link before check followed the rename plus one from a new clone; harbor checks it daily and the brain
    # lands harbor's notes one at a time each day, the rename first or last
    older = {"repo": "northwind/harbor-voice-2", "repo_id": "R_1", "linked": "2026-10-01"}
    newer = {"repo": "northwind/harbor-voice", "linked_by": "detect", "linked": "2026-10-05"}
    if start == "old clone gone":
        newer["gone"] = "2026-10-06"
    if start == "new clone":
        older["repo"], newer["repo"] = newer["repo"], older["repo"]
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor",
         "repos": [older, newer] if older_first else [newer, older]}])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", audience=["Ada Okafor"])
    mount_brain(harbor, brain)

    def host(r, i):   # both names reach the same repository
        return {"repo": "northwind/harbor-voice-2", "repo_id": "R_1", "private": True}
    for day in range(4):
        out = link.check(harbor, now=now + dt.timedelta(days=day), lookup_fn=host, readers_fn=lambda r: ["ada-gh"])
        assert (out["links"], out["repaired"]) == (1, 1 if day == 0 else 0)
        (brain / "_inbound" / "signals").mkdir(parents=True, exist_ok=True)
        sent = sorted((harbor / "_outbound" / "signals").glob("*.yaml"), key=lambda p: (
            "was" in yaml.safe_load(p.read_text(encoding="utf-8"))["data"]) != rename_lands_first)
        for signal in sent:
            signal.replace(brain / "_inbound" / "signals" / signal.name)
            classify.land_notes(brain, now=now + dt.timedelta(days=day))
        (entry,) = registry.Registry(brain).get("harbor-voice-agent").extra["repos"]
        assert (entry["repo"], entry["repo_id"], entry["was"], entry["linked"]) == (
            "northwind/harbor-voice-2", "R_1", ["northwind/harbor-voice"], "2026-10-01")
    assert link.resolve(harbor, make_codebase(tmp_path / "old-clone"), now=now)["repo"] == "northwind/harbor-voice-2"


def test_check_counts_a_repository_once_however_the_host_spells_it(tmp_path, now):
    # ws holds harbor-voice-agent linked to R_1 under its old name and again from a new clone of the new name
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + [{"id": "harbor-voice-agent", "kind": "projects", "repos": [
        {"repo": "northwind/harbor-voice", "repo_id": "R_1", "linked": "2026-10-01"},
        {"repo": "northwind/harbor-voice-2", "linked_by": "detect", "linked": "2026-10-05"}]}])
    out = link.check(ws, now=now, lookup_fn=lambda r, i: {"repo": "Northwind/harbor-voice-2", "repo_id": "R_1"},
                     readers_fn=lambda r: None)
    (src,) = [s for s in spaceyaml.load(ws).sources if s["name"] == "repository"]
    assert out["links"] == 1 and len(src["repos"]) == 1


def test_unlink_removes_the_link_it_names(tmp_path, now):
    # ws holds harbor-voice-agent linked to R_1 (renamed from northwind/harbor-voice) and to R_2, which took the old name
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + [{"id": "harbor-voice-agent", "kind": "projects", "repos": [
        {"repo": "northwind/harbor-voice-2", "repo_id": "R_1", "was": ["northwind/harbor-voice"], "linked": "2026-10-01"},
        {"repo": "northwind/harbor-voice", "repo_id": "R_2", "linked": "2026-10-05"}]}])
    link.unlink(ws, "northwind/harbor-voice", now=now)
    assert [e["repo_id"] for e in registry.Registry(ws).get("harbor-voice-agent").extra["repos"]] == ["R_1"]


def test_unlink_after_a_rename_keeps_old_clones_unlinked(tmp_path, now):
    # ws holds harbor-voice-agent, renamed from northwind/harbor-voice to northwind/harbor-voice-2
    ws = linked_ws(tmp_path, repo="northwind/harbor-voice-2", repo_id="R_1", was=["northwind/harbor-voice"])
    old = make_codebase(tmp_path / "old-clone")
    assert link.resolve(ws, old, now=now)["repo"] == "northwind/harbor-voice-2"
    assert link.main([str(ws), "unlink", "northwind/harbor-voice-2"]) == 0
    new = make_codebase(tmp_path / "new-clone", "https://github.com/northwind/harbor-voice-2.git")
    for _ in range(2):
        assert link.resolve(ws, old, now=now) is None and link.resolve(ws, new, now=now) is None
    assert link.check(ws, now=now, lookup_fn=same, readers_fn=lambda r: None)["links"] == 0
    assert "repos" not in registry.Registry(ws).get("harbor-voice-agent").extra
    assert sorted(json.loads(l)["repo"] for l in (ws / link.SEEN).read_text(encoding="utf-8").splitlines()) == \
        ["northwind/harbor-voice", "northwind/harbor-voice-2"]


def test_check_public_repo_is_wider(tmp_path, now):
    # every collaborator is in ws's audience, but the repository is public
    ws = linked_ws(tmp_path)
    out = link.check(ws, now=now, lookup_fn=lambda r, i: dict(same(r, i), private=False),
                     readers_fn=lambda r: ["ada-gh"])
    assert link.links(ws)[0].readers == "wider" and out["wider"] == 1
    out = link.check(ws, now=now, lookup_fn=lambda r, i: dict(same(r, i), private=True),
                     readers_fn=lambda r: ["ada-gh"])
    assert link.links(ws)[0].readers == "inside" and out["wider"] == 0


def test_a_gone_link_comes_back(tmp_path, now):
    # marked gone on a day the host could not see the repository; the host finds it again
    ws = linked_ws(tmp_path, repo_id="R_1", gone="2026-10-05")
    code = make_codebase(tmp_path / "code")
    assert link.resolve(ws, code, now=now) is None
    assert link.check(ws, now=now, lookup_fn=same, readers_fn=lambda r: None) == {"links": 1, "wider": 1, "repaired": 1}
    assert "gone" not in registry.Registry(ws).get("harbor-voice-agent").extra["repos"][0]
    assert link.resolve(ws, code, now=now)["subject"] == "harbor-voice-agent"
    # the person's own link brings one back too, keeping when it was first linked
    ws = linked_ws(tmp_path / "second", gone="2026-10-05")
    assert link.main([str(ws), "link", "northwind/harbor-voice", "harbor-voice-agent"]) == 0
    (entry,) = registry.Registry(ws).get("harbor-voice-agent").extra["repos"]
    assert "gone" not in entry and entry["linked"] == "2026-10-01" and entry["linked_by"] == "person"
    assert link.resolve(ws, code, now=now)["subject"] == "harbor-voice-agent"


def test_check_marks_a_lost_repo_gone(tmp_path, now):
    ws = linked_ws(tmp_path)
    code = make_codebase(tmp_path / "code")
    out = link.check(ws, now=now, lookup_fn=lambda r, i: None, readers_fn=lambda r: None)
    assert out["repaired"] == 1 and out["links"] == 0
    assert link.links(ws)[0].gone == now.date().isoformat() and link.resolve(ws, code) is None
    assert not (ws / link.NEEDS_YOU).exists()   # never an owner alert


def test_check_host_unavailable_changes_nothing(tmp_path, now):     # Review Focus 2
    ws = linked_ws(tmp_path)

    def down(r, i):
        raise OSError("no gh")
    before = (ws / "_meta" / "subjects.yaml").read_text(encoding="utf-8")
    out = link.check(ws, now=now, lookup_fn=down, readers_fn=lambda r: None)
    assert (ws / "_meta" / "subjects.yaml").read_text(encoding="utf-8") == before
    assert out == {"links": 1, "wider": 1, "repaired": 0}


def test_check_keeps_a_repo_on_another_host(tmp_path, now, monkeypatch):     # Review Focus 2
    # the subject links an ssh clone on another host (scp form); check runs with the default host calls
    ws = linked_ws(tmp_path, repo="git@gitlab.example:northwind/harbor-voice")
    code = make_codebase(tmp_path / "code", "git@gitlab.example:northwind/harbor-voice.git")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("host called"))
    assert link.check(ws, now=now) == {"links": 1, "wider": 1, "repaired": 0}
    assert link.links(ws)[0].gone is None and link.resolve(ws, code, now=now)["subject"] == "harbor-voice-agent"


def test_check_flips_readers_on_a_new_collaborator(tmp_path, now):
    # ws is the home, audience [Ada Okafor, Ben Liu]
    ws = linked_ws(tmp_path, space={"audience": ["Ada Okafor", "Ben Liu"]})
    link.check(ws, now=now, lookup_fn=same, readers_fn=lambda r: ["ada-gh", "Ben-GH"])
    assert link.links(ws)[0].readers == "inside" and link.links(ws)[0].checked == now.date().isoformat()
    before = (ws / "_meta" / "subjects.yaml").read_text(encoding="utf-8")
    link.check(ws, now=now + dt.timedelta(days=1), lookup_fn=same, readers_fn=lambda r: ["ada-gh", "ben-gh"])
    assert (ws / "_meta" / "subjects.yaml").read_text(encoding="utf-8") == before   # nothing changed: nothing sent
    out = link.check(ws, now=now, lookup_fn=same, readers_fn=lambda r: ["ada-gh", "ben-gh", "contractor-gh"])
    assert link.links(ws)[0].readers == "wider" and out["wider"] == 1


def test_check_unknown_readers_default_wider(tmp_path, now):
    ws = linked_ws(tmp_path)
    out = link.check(ws, now=now, lookup_fn=same, readers_fn=lambda r: None)
    assert link.links(ws)[0].readers == "wider" and out["wider"] == 1


def test_check_keeps_the_older_of_two_claims(tmp_path, now):
    # ws holds and homes beacon-app (linked 2026-10-05) and harbor-voice-agent (linked 2026-10-01), same repository
    ws = make_space(tmp_path / "ada", subjects=PEOPLE + [
        {"id": "beacon-app", "kind": "projects", "repos": [{"repo": "northwind/harbor-voice", "linked": "2026-10-05"}]},
        {"id": "harbor-voice-agent", "kind": "projects",
         "repos": [{"repo": "northwind/harbor-voice", "linked": "2026-10-01"}]}])
    out = link.check(ws, now=now, lookup_fn=same, readers_fn=lambda r: None)
    assert [l.subject for l in link.links(ws)] == ["harbor-voice-agent"] and out["repaired"] == 1
    (item,) = yaml.safe_load((ws / link.NEEDS_YOU).read_text(encoding="utf-8"))["items"]
    assert item["id"] == "link-claim-northwind-harbor-voice"
    assert item["text"] == ("northwind/harbor-voice was linked to harbor-voice-agent and beacon-app; "
                            "I kept harbor-voice-agent, linked first.")
    assert item["recommendation"] == "keep harbor-voice-agent" and item["default"] == "harbor-voice-agent keeps it"


def test_check_at_the_home_sends_a_note_to_the_holder(tmp_path, now):
    # the brain holds harbor-voice-agent (home harbor); harbor mounts the brain; harbor's audience Ada, Ben
    brain = make_space(tmp_path / "lantern", profile="brain", name="lantern", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor",
         "repos": [{"repo": "northwind/harbor-voice", "linked": "2026-10-01"}]}])
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", audience=["Ada Okafor", "Ben Liu"])
    mount_brain(harbor, brain)
    before = (brain / "_meta" / "subjects.yaml").read_text(encoding="utf-8")
    link.check(harbor, now=now, lookup_fn=same, readers_fn=lambda r: ["ada-gh", "ben-gh"])
    signals = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in (harbor / "_outbound" / "signals").glob("*.yaml")]
    assert [s["data"]["readers"] for s in signals if s.get("note") == "link"] == ["inside"]
    assert signals[0]["data"]["repo_id"] == "R_1" and signals[0]["destination"] == "lantern"
    assert (brain / "_meta" / "subjects.yaml").read_text(encoding="utf-8") == before   # a mount is never written
    # the brain checks only the links homed in it: none here
    assert link.check(brain, now=now, lookup_fn=same, readers_fn=lambda r: None)["links"] == 0


def test_check_keeps_the_repository_source(tmp_path, now):
    # project space harbor is home and holder of harbor-voice-agent, linked to northwind/harbor-voice
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "repos": [{"repo": "northwind/harbor-voice"}]}])
    link.check(harbor, now=now, lookup_fn=same, readers_fn=lambda r: None)
    src = [s for s in spaceyaml.load(harbor).sources if s["name"] == "repository"][0]
    assert src["repos"] == ["northwind/harbor-voice"] and src["audience"] == "crew"
    # an existing source: only its repos key is replaced; a manual repo key stays
    space = spaceyaml.load(harbor)
    space.data["sources"][0].update(repo="northwind/harbor-space", repos=["northwind/old"])
    spaceyaml.save(space)
    link.check(harbor, now=now, lookup_fn=same, readers_fn=lambda r: None)
    src = [s for s in spaceyaml.load(harbor).sources if s["name"] == "repository"][0]
    assert src["repos"] == ["northwind/harbor-voice"] and src["repo"] == "northwind/harbor-space"


def test_fixture_space_never_calls_the_host(tmp_path, now, monkeypatch):     # Review Focus 5
    # ws has mode: fixture and one link homed in it
    ws = linked_ws(tmp_path, space={"mode": "fixture"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("host called"))
    assert link.check(ws, now=now)["repaired"] == 0 and link.links(ws)[0].gone is None
    assert link.scan(ws, now=now)["skipped"] == "fixture"
    code = make_codebase(tmp_path / "c2", "https://github.com/northwind/harbor-voice-2")
    assert link.resolve(ws, code, now=now) is not None


def test_default_host_calls_tell_signed_out_from_not_found(tmp_path, now, monkeypatch):     # Review Focus 2
    ws = linked_ws(tmp_path, repo_id="R_1")
    signed_out = (4, "", "To get started with GitHub CLI, please run:  gh auth login\n")
    answers = {"api": signed_out, "repo": signed_out}

    def run(args, **kwargs):
        code, out, err = answers["graphql" if "graphql" in args else args[1]]
        return subprocess.CompletedProcess(args, code, out, err)
    monkeypatch.setattr(subprocess, "run", run)
    before = (ws / "_meta" / "subjects.yaml").read_text(encoding="utf-8")
    assert link.check(ws, now=now, readers_fn=lambda r: None)["repaired"] == 0   # signed out: retried next run
    assert (ws / "_meta" / "subjects.yaml").read_text(encoding="utf-8") == before
    assert link.scan(ws, now=now)["skipped"]
    answers["api"] = (0, '{"repo":"northwind/harbor-voice-2","repo_id":"R_1"}\n', "")
    assert link.check(ws, now=now, readers_fn=lambda r: None)["repaired"] == 1
    assert link.links(ws)[0].repo == "northwind/harbor-voice-2"
    answers["api"] = (1, "", "gh: Not Found (HTTP 404)\n")
    answers["graphql"] = (1, "", "gh: Could not resolve to a node with the global id of 'R_1'\n")
    link.check(ws, now=now, readers_fn=lambda r: None)
    assert link.links(ws)[0].gone == now.date().isoformat()


def test_health_cell(tmp_path, now):
    ws = linked_ws(tmp_path)
    assert link.health_cell(ws) == "links 0"
    link.check(ws, now=now, lookup_fn=same, readers_fn=lambda r: ["x-gh"])
    assert link.health_cell(ws) == "links 1, wider 1, repaired 0"


# ----- notes from a codebase session travel as records (D8) ---------------------------------------
REF_A = "ref: northwind/harbor-voice:docs/spec.md@abc"
ENTRY_A = f"- The call-flow spec, so the pilot can start.\n- {REF_A}\n- subject: harbor-voice-agent\n"


def journal_ws(tmp_path, crew=("ada",), entry_a=ENTRY_A, **space):
    """ada's workspace (a mail source declared) holding harbor-voice-agent (home harbor, walled with
    `crew`, linked to northwind/harbor-voice), beacon (linked to beacon/app) and her own ada-notes.
    Her journal for the test day: A (10:15) about harbor-voice-agent with `entry_a` as its body, B
    the same subject without a ref, C ada-notes with A's ref, D harbor-voice-agent with a ref into
    beacon/app."""
    wall = {"class": "walled", "state": "settled", "home": "harbor", "crew": list(crew), "reason": "its own space",
            "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}
    ws = make_space(tmp_path / "ada", name="ada", sources=[{"name": "mail", "adapter": "gmail", "audience": "owner"}],
                    subjects=PEOPLE + [
                        {"id": "harbor-voice-agent", "kind": "projects", "home": "harbor", "important": True,
                         "classification": wall,
                         "repos": [{"repo": "northwind/harbor-voice", "linked_by": "detect", "linked": "2026-10-01"}]},
                        {"id": "beacon", "kind": "projects", "home": "beacon",
                         "repos": [{"repo": "beacon/app", "linked_by": "detect", "linked": "2026-10-01"}]},
                        {"id": "ada-notes", "kind": "topics", "home": "ada"}], **space)
    note = ws / "journal" / f"{NOW:%Y}" / f"{NOW:%m}" / f"{NOW:%Y-%m-%d}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(f"# {NOW:%Y-%m-%d}\n\n"
                    f"## 10:15Z: call-flow spec written\n\n{entry_a}\n"
                    "## 11:00Z: pilot scope\n\n- Two flows only.\n- subject: harbor-voice-agent\n\n"
                    f"## 11:30Z: my own notes\n\n- {REF_A}\n- subject: ada-notes\n\n"
                    "## 12:00Z: other code\n\n- ref: beacon/app@def\n- subject: harbor-voice-agent\n",
                    encoding="utf-8")
    return ws


def journal_rows(ws) -> list:
    return [json.loads(l) for l in (ws / link.JOURNAL_FEED).read_text(encoding="utf-8").splitlines()]


def test_journal_records_only_for_linked_refs(tmp_path, now):              # Review Focus 4
    ws = journal_ws(tmp_path)
    assert link.journal_records(ws, now=now) == 1
    row, = journal_rows(ws)
    assert row["identifiers"] == {"subject": "harbor-voice-agent"} and row["codebase"] == "northwind/harbor-voice"
    assert row["id"] == f"journal-{now:%Y%m%d}T1015" and row["at"] == f"{now:%Y-%m-%d}T10:15:00Z"
    assert (row["source"], row["kind"], row["audience"], row["from"]) == ("journal", "document", "owner", "Ada Okafor")
    assert row["subject"] == "call-flow spec written"
    assert link.journal_records(ws, now=now) == 0                          # idempotent
    project = journal_ws(tmp_path / "p", profile="project")
    assert link.journal_records(project, now=now) == 0                     # personal spaces only


def test_journal_record_puts_pointers_first(tmp_path, now):
    body = ("- " + "the pilot needs words " * 140 + "\n- subject: harbor-voice-agent\n- " + REF_A
            + "\n- lesson: retry webhooks with backoff\n- from: beacon\n")
    ws = journal_ws(tmp_path, entry_a=body)
    link.journal_records(ws, now=now)
    row, = journal_rows(ws)
    assert row["text"].startswith(REF_A + "\n\n- the pilot needs words")
    assert row["text"].count(REF_A) == 1
    assert "lesson: retry webhooks" in row["text"] and "from:" not in row["text"]   # ring 3 stays home
