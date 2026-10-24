"""Codebase links (docs/design/2026-10-22-codebase-link-design.md): resolve, first-time detection,
the person's own link and unlink, and the `ref:` pointer forms."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

import classify
import link
import registry
from conftest import make_space, mount_brain


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


PEOPLE = [{"id": "ada", "kind": "people", "title": "Ada Okafor", "match": {"github": "ada-gh"}},
          {"id": "ben", "kind": "people", "title": "Ben Liu", "match": {"github": "ben-gh"}}]
HARBOR = [{"id": "harbor", "kind": "projects", "client": "northwind"},
          {"id": "harbor-voice-agent", "kind": "projects", "part_of": "harbor", "client": "northwind"}]


def linked_ws(tmp_path, **entry):
    """ada's workspace holding harbor-voice-agent, linked to northwind/harbor-voice."""
    repo = dict({"repo": "northwind/harbor-voice", "linked_by": "detect", "linked": "2026-10-01"}, **entry)
    return make_space(tmp_path / "ada", subjects=PEOPLE + [
        {"id": "harbor-voice-agent", "kind": "projects", "client": "northwind", "repos": [repo]}])


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
