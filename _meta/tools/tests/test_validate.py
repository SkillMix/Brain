import yaml

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


def _candidate_for_harbor(tmp_path, harbor_present):
    """A personal space with the brain and a project, harbor, mounted; distill's candidate for
    harbor links to one of its pages and to one that exists nowhere."""
    brain = make_space(tmp_path / "brain", profile="brain")
    harbor = tmp_path / "harbor"
    if harbor_present:
        make_space(harbor, profile="project")
        write_page(harbor, "knowledge/projects/harbor/brief.md", id="p-0000bbbb", title="Harbor")
    ada = make_space(tmp_path / "ada")
    mount_brain(ada, brain)
    data = yaml.safe_load((ada / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"].append({"name": "harbor", "path": str(harbor)})
    (ada / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    write_page(ada, "_inbound/candidates/harbor.md", title="Harbor", destination="harbor",
               body="Per [the brief](id:p-0000bbbb) and [a gone page](id:p-0000cccc).\n")
    return ada


def test_a_link_into_a_mount_not_on_disk_is_a_warning_that_names_it(tmp_path):
    # Seen live: CI's mount token could not read a project, validate skipped the cross-space
    # checks with a warning, and still failed on a candidate's links into that project.
    ada = _candidate_for_harbor(tmp_path, harbor_present=False)
    problems = validate.validate(ada)
    assert errors(ada) == []
    for pid in ("p-0000bbbb", "p-0000cccc"):
        assert any(p.startswith("warning:") and pid in p and "harbor" in p for p in problems), problems


def test_a_link_resolves_in_any_mount_on_disk_and_else_stays_an_error(tmp_path):
    ada = _candidate_for_harbor(tmp_path, harbor_present=True)
    assert not any("p-0000bbbb" in p for p in validate.validate(ada))
    assert any("p-0000cccc" in e for e in errors(ada))


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
