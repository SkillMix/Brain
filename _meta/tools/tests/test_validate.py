import pages
import validate
from conftest import make_space, mount_brain, write_page


def errors(root, **kw):
    return [e for e in validate.validate(root, **kw) if not e.startswith("warning:")]


def test_clean_personal_space_passes(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/projects/harbor/brief.md", type="entity", title="Harbor")
    assert errors(root) == []


def test_project_with_journal_fails(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project")
    (root / "journal").mkdir()
    assert any("journal" in e for e in errors(root))


def test_page_without_id_fails(tmp_path):
    root = make_space(tmp_path / "ada")
    path = root / "knowledge" / "topics" / "x" / "page.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ntype: concept\ntitle: X\nstatus: draft\n"
                    "generated: {by: t, at: 2026-10-12T09:00:00Z}\n---\nBody\n", encoding="utf-8")
    assert any("`id`" in e for e in errors(root))


def test_unresolved_id_link_fails_and_resolved_passes(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/topics/a/one.md", id="p-00000011", title="One")
    write_page(root, "knowledge/topics/a/two.md", id="p-00000012", title="Two",
               body="See [one](id:p-00000011) and [gone](id:p-deadbeef).\n")
    errs = errors(root)
    assert any("p-deadbeef" in e for e in errs)
    assert not any("p-00000011" in e for e in errs)


def test_link_to_ancestor_id_resolves_through_mount(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain")
    write_page(brain, "knowledge/topics/pricing/rule.md", id="p-0000aaaa", title="Rule")
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    write_page(ada, "knowledge/topics/pricing/note.md", title="Note",
               body="Per [the rule](id:p-0000aaaa).\n")
    assert errors(ada) == []


def test_unknown_kind_folder_fails(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain")
    (brain / "knowledge" / "departments").mkdir(parents=True)
    assert any("departments" in e for e in errors(brain))
    # without a brain mount the company's kinds are unknown: a warning, not an error
    ada = make_space(tmp_path / "ada")
    (ada / "knowledge" / "departments").mkdir(parents=True)
    assert not any("departments" in e for e in errors(ada))
    assert any("departments" in w and w.startswith("warning:") for w in validate.validate(ada))


def test_time_scale_in_live_fails_validation(tmp_path):
    root = make_space(tmp_path / "ada", time_scale=48)
    assert any("time_scale" in e for e in errors(root))


def test_stable_page_needs_matching_signature(tmp_path):
    root = make_space(tmp_path / "ada")
    body = "Signed truth.\n"
    write_page(root, "knowledge/topics/a/signed.md", status="stable", body=body,
               signed_by="Ada Okafor", signed_at="2026-10-12T09:00:00Z",
               fingerprint=pages.fingerprint(body))
    assert errors(root) == []
    write_page(root, "knowledge/topics/a/edited.md", status="stable", body="Edited later.\n",
               signed_by="Ada Okafor", signed_at="2026-10-12T09:00:00Z",
               fingerprint=pages.fingerprint(body))
    assert any("fingerprint" in e for e in errors(root))
    write_page(root, "knowledge/topics/a/unsigned.md", status="stable", body=body)
    assert any("signed_by" in e for e in errors(root))


def test_witness_pages_are_never_signed(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/sources/2026/10/m-1.md", type="source", status="stable",
               signed_by="x", signed_at="2026-10-12T09:00:00Z", fingerprint=pages.fingerprint("Body.\n"))
    assert any("never signed" in e for e in errors(root))


def test_duplicate_ids_fail(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "knowledge/topics/a/one.md", id="p-00000011")
    write_page(root, "knowledge/topics/b/two.md", id="p-00000011")
    assert any("already used" in e for e in errors(root))


def test_template_flag_requires_template_profile(tmp_path):
    root = make_space(tmp_path / "ada")
    assert any("profile: template" in e for e in errors(root, template=True))
