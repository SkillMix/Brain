import ids
from conftest import make_space, mount_brain, write_page


def test_build_writes_sorted_index_and_resolve_finds_own_page(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/projects/harbor/brief.md", id="p-0000bbbb", title="Brief")
    write_page(root, "live/subjects/harbor.md", id="p-0000aaaa", type="snapshot",
               title="Harbor: status")
    write_page(root, "_inbound/candidates/harbor.md", id="p-0000cccc", title="Candidate",
               subject="harbor")
    write_page(root, "_inbound/checks/p-0000dddd.md", id="p-0000dddd", title="Check")
    table = ids.build(root)
    assert list(table) == sorted(table)
    assert table["p-0000bbbb"] == "knowledge/projects/harbor/brief.md"
    assert table["p-0000dddd"] == "_inbound/checks/p-0000dddd.md"
    raw = (root / "_meta" / "state" / "ids.yaml").read_bytes()
    assert b"\r" not in raw
    assert raw.decode("utf-8").startswith("ids:")
    assert ids.resolve(root, "p-0000aaaa") == root / "live" / "subjects" / "harbor.md"
    assert ids.resolve(root, "p-deadbeef") is None


def test_resolve_falls_through_to_the_mount(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain")
    write_page(brain, "knowledge/topics/pricing/rule.md", id="p-0000aaaa", title="Rule")
    ids.build(brain)
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    ids.build(ada)
    assert ids.resolve(ada, "p-0000aaaa") == brain / "knowledge" / "topics" / "pricing" / "rule.md"
    # own pages win over the mount's, and `known` merges both
    write_page(ada, "knowledge/topics/pricing/mine.md", id="p-0000aaab", title="Mine")
    ids.build(ada)
    known = ids.known(ada)
    assert known["p-0000aaaa"].endswith("rule.md") and known["p-0000aaab"].endswith("mine.md")


def test_hubs_and_indexes_without_ids_are_skipped(tmp_path):
    root = make_space(tmp_path / "ada")
    hub = root / "knowledge" / "projects" / "harbor" / "index.md"
    hub.parent.mkdir(parents=True)
    hub.write_text("---\ntitle: Harbor\n---\n# Harbor\n", encoding="utf-8")
    assert ids.build(root) == {}


def test_resolve_without_an_index_scans(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/projects/harbor/brief.md", id="p-0000bbbb", title="Brief")
    assert not (root / "_meta" / "state" / "ids.yaml").exists()
    assert ids.resolve(root, "p-0000bbbb") == root / "knowledge" / "projects" / "harbor" / "brief.md"


def test_cli(tmp_path, capsys):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/projects/harbor/brief.md", id="p-0000bbbb", title="Brief")
    assert ids.main([str(root), "build"]) == 0
    assert (root / "_meta" / "state" / "ids.yaml").exists()
    assert ids.main([str(root), "resolve", "p-0000bbbb"]) == 0
    assert "brief.md" in capsys.readouterr().out
    assert ids.main([str(root), "resolve", "p-deadbeef"]) == 1
    assert ids.main([str(root)]) == 2
