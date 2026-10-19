"""Batch 7: the trust root checked at landing, the wider high-stakes words, connector offers,
the path in Left yesterday and a pull-back by email reply."""
import yaml

import landing
import morning_view
import onboard
import pages
import reply
import sign
import spaceyaml
from conftest import NOW, make_space, write_page

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor",
          "classification": {"class": "walled", "state": "settled", "home": "harbor", "crew": ["ada"],
                             "decided_by": "Ada Okafor", "at": "2026-10-01T09:00:00Z"}}   # classified: its pages may be signed


def test_signing_writes_the_fingerprint_line_and_names_the_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(sign, "_own_repo", lambda root: "acme/ada-workspace")
    root = make_space(tmp_path / "ada", subjects=[HARBOR])
    write_page(root, "_inbound/candidates/p-0000b701.md", "Go-live is 3 November.\n", id="p-0000b701",
               type="concept", title="Go-live", subject="harbor")
    sign.apply(root, {"p-0000b701": "yes"}, by="Ada Okafor", now=NOW)
    fm, body = pages.read(next((root / "knowledge").rglob("*.md")))
    assert fm["signed_repo"] == "acme/ada-workspace"
    log = (root / "log.md").read_text(encoding="utf-8")
    assert f" fingerprint p-0000b701 {pages.fingerprint(body)} by Ada Okafor" in log


def released(root, pid, text, repo="acme/ada-workspace"):
    write_page(root, f"_inbound/candidates/{pid}.md", text, id=pid, type="concept", title="Go-live",
               subject="harbor", status="stable", signed_by="Ada Okafor", signed_at="2026-10-11T00:00:00Z",
               signed_in="ada", fingerprint=pages.fingerprint(text), released_from="ada", signed_repo=repo,
               target=f"knowledge/projects/harbor/{pid}.md")


def test_landing_files_silently_only_what_the_signers_history_shows(tmp_path, monkeypatch):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor", subjects=[HARBOR])
    released(root, "p-0000b711", "Go-live is 3 November.\n")
    released(root, "p-0000b712", "The port opens in May.\n")
    line = f"- 2026-10-11T00:00:00Z fingerprint p-0000b711 {pages.fingerprint('Go-live is 3 November.' + chr(10))} by Ada Okafor\n"
    monkeypatch.setattr(landing, "signer_log", lambda repo, cache: line)
    report = landing.land(root, now=NOW)
    assert report.silent == ["p-0000b711"] and report.pending == ["p-0000b712"]
    fm, _ = pages.read(root / "_inbound/candidates/p-0000b712.md")
    assert "signer's history" in fm["landing"]


def test_the_high_stakes_categories_cover_hr_security_and_legal():
    text = " ".join(spaceyaml.DEFAULTS["high_stakes"]).lower()
    assert all(word in text for word in ("hr", "security", "legal", "policy", "contract"))


def test_connecting_a_feed_is_one_line_and_never_twice(tmp_path):
    root = make_space(tmp_path / "ada")
    assert onboard.connect(root, "mail") is True and onboard.connect(root, "mail") is False
    assert {"name": "mail", "adapter": "files", "audience": "owner"} in spaceyaml.load(root).sources
    resources = yaml.safe_load((root / "_meta/access/resources.yaml").read_text(encoding="utf-8"))["resources"]
    assert [r["name"] for r in resources] == ["mail"]


def test_a_reply_pulls_back_what_left_yesterday_by_its_number(tmp_path):
    lines = ["1) 2026-10-04T09:00:00Z released p-0000b721 to harbor (at_once) via standing_choice:harbor:mail",
             "2) 2026-10-04T10:00:00Z answered rq-abc123 for chloe at_once (standing choice: status)"]
    left = morning_view._left_ids(lines)
    assert left == ["p-0000b721", "rq-abc123"]
    assert reply.parse_left("left 1 no\n> left 2 no", left) == ["p-0000b721"]
    assert reply.parse_left("Left 1, 2 no", left) == ["p-0000b721", "rq-abc123"]


def test_a_project_connects_its_channel_tracker_and_repository_with_their_scope(tmp_path):
    """A project space reads only what its sources name: the channels, the tracker project
    and the repository; `repository` is a feed like the others."""
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    assert onboard.connect(root, "chat", audience="crew", scope={"channels": ["C1", "C2"]}) is True
    assert onboard.connect(root, "repository", audience="crew", scope={"repo": "lantern/harbor"}) is True
    sources = {s["name"]: s for s in spaceyaml.load(root).sources}
    assert sources["chat"]["channels"] == ["C1", "C2"] and sources["chat"]["audience"] == "crew"
    assert sources["repository"]["repo"] == "lantern/harbor"
    assert onboard.main([str(root), "--connect", "tracker", "--audience", "crew",
                         "--scope", "team=HAR", "--scope", "project=p-1"]) == 0
    assert {s["name"]: s for s in spaceyaml.load(root).sources}["tracker"]["team"] == "HAR"
