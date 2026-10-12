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
