"""The engine file and the search command: plain search by default, an index only when chosen."""
import yaml

import search
import validate
from conftest import make_space, mount_brain, write_page


def errors(root):
    return [e for e in validate.validate(root) if not e.startswith("warning:")]


def test_config_defaults_and_validation(tmp_path):
    root = make_space(tmp_path / "ada")
    cfg = search.config(root)
    assert cfg["engine"] == "none" and cfg["link_step"] == "off" and cfg["atoms"] == "off"
    (root / "_meta" / "search.yaml").write_text(yaml.safe_dump({"engine": "none", "link_step": "on"}),
                                                encoding="utf-8")
    assert any("link_step" in e for e in errors(root))
    (root / "_meta" / "search.yaml").write_text(yaml.safe_dump(
        {"engine": "none", "link_step": "on",
         "measured": [{"feature": "link_step", "gain": 0.05, "at": "2026-10-01"}]}), encoding="utf-8")
    assert not any("link_step" in e for e in errors(root))


def test_plain_query_searches_every_layer_and_expands_to_the_subject(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "sales", "kind": "domains", "title": "Sales"}])
    write_page(brain, "knowledge/domains/sales/pipeline.md", type="concept", title="Pipeline",
               subject="sales", body="The pilot budget is 40k.\n")
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    mount_brain(root, brain)
    write_page(root, "knowledge/projects/harbor/harbor.md", type="entity", title="Harbor",
               subject="harbor", body="Harbor pilot goes live on 3 November.\n")
    hits = search.query(root, "pilot")
    by_subject = {(h.layer, h.subject) for h in hits}
    assert ("", "harbor") in by_subject and ("brain", "sales") in by_subject
    assert all(h.line for h in hits)
    assert search.query(root, "nothing like this") == []


def test_index_engine_builds_queries_and_prunes(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    page = write_page(root, "knowledge/projects/harbor/harbor.md", type="entity", title="Harbor",
                      subject="harbor", body="Harbor migration dry run on the 14th.\n")
    (root / "_meta" / "search.yaml").write_text(yaml.safe_dump({"engine": "index"}), encoding="utf-8")
    built = search.build(root)
    assert built["files"] >= 1 and (root / "_meta" / "state" / "search-index.json").exists()
    assert [h.subject for h in search.query(root, "migration")] == ["harbor"]
    page.unlink()
    dropped = search.prune(root)
    assert dropped == 1
    assert search.query(root, "migration") == []
    assert search.main([str(root), "query", "harbor"]) in (0, 1)


def ringed_workspace(tmp_path):
    """ada's workspace mounting the brain (a lesson page), harbor (home of harbor-voice-agent)
    and beacon (another client's project); every layer, and ada's own pages, say "webhook"."""
    from test_link import mount
    brain = make_space(tmp_path / "brain", profile="brain", subjects=[
        {"id": "harbor", "kind": "projects", "client": "northwind", "home": "harbor"},
        {"id": "harbor-voice-agent", "kind": "projects", "part_of": "harbor", "home": "harbor"},
        {"id": "harbor-ops", "kind": "projects", "part_of": "harbor-voice-agent", "home": "harbor"},
        {"id": "beacon-app", "kind": "projects", "client": "beacon", "home": "beacon"}])
    write_page(brain, "knowledge/topics/lessons/retries.md", body="Retry a webhook twice.\n")
    harbor = make_space(tmp_path / "harbor", profile="project")
    write_page(harbor, "knowledge/projects/harbor-voice-agent/calls.md", body="The webhook fires on hang-up.\n")
    beacon = make_space(tmp_path / "beacon", profile="project")
    write_page(beacon, "knowledge/projects/beacon-app/hooks.md", body="Beacon's webhook signs with a key.\n")
    ws = make_space(tmp_path / "ada")
    mount_brain(ws, brain)
    mount(ws, "harbor", harbor)
    mount(ws, "beacon", beacon)
    for sid in ("harbor-voice-agent", "harbor", "harbor-ops", "beacon-app"):
        write_page(ws, f"knowledge/projects/{sid}/notes.md", subject=sid, body=f"{sid} webhook notes.\n")
    return ws


def test_query_for_subject_keeps_rings_one_and_two(tmp_path):
    ws = ringed_workspace(tmp_path)
    for engine in ("none", "index"):   # the plain path and the engine path alike
        (ws / "_meta" / "search.yaml").write_text(yaml.safe_dump({"engine": engine}), encoding="utf-8")
        hits = search.query(ws, "webhook", for_subject="harbor-voice-agent")
        assert {h.layer for h in hits} == {"", "brain", "harbor"}
        assert {h.subject for h in hits if h.layer == ""} == {"harbor-voice-agent", "harbor", "harbor-ops"}
    assert search.main([str(ws), "query", "--for", "harbor-voice-agent", "webhook"]) == 0


def test_query_for_subject_keeps_a_page_routed_to_the_ring(tmp_path):
    ws = ringed_workspace(tmp_path)
    write_page(ws, "knowledge/sources/2026/10/call.md", subject="harbor-voice-agent", body="webhook call\n")
    write_page(ws, "knowledge/sources/2026/10/mail.md", routing={"routes": ["ada/harbor"]}, body="webhook mail\n")
    write_page(ws, "knowledge/sources/2026/10/other.md", subject="beacon-app", body="webhook other\n")
    paths = {h.path for h in search.query(ws, "webhook", for_subject="harbor-voice-agent") if h.layer == ""}
    assert {"knowledge/sources/2026/10/call.md", "knowledge/sources/2026/10/mail.md"} <= paths
    assert "knowledge/sources/2026/10/other.md" not in paths


def test_query_without_for_is_unchanged(tmp_path):
    ws = ringed_workspace(tmp_path)
    assert {h.layer for h in search.query(ws, "webhook")} == {"", "brain", "harbor", "beacon"}
