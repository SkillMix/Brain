"""Round 2 of the security review, the landing group: what a release carries in flight, where a
person's workspace's signatures are read, and a routine that reaches sign.apply."""
import pytest

import landing
import onboard
import pages
import registry
import release
import routine
import sign
from conftest import NOW, make_space, write_page
from test_sign import candidate, pin

HARBOR = {"id": "harbor", "kind": "projects", "title": "Harbor"}


def signed_release(root, pid, body="Harbor goes live on 3 November.\n", sender="ada", signer="Ada Okafor",
                   **extra):
    fm = dict(id=pid, type="entity", title=f"Harbor ({pid})", status="stable", subject="harbor",
              kind="projects", released_from=sender, released_by=signer, released_at=pages.iso(NOW),
              release_class="at_once", signed_by=signer, signed_at=pages.iso(NOW), signed_in=sender,
              fingerprint=pages.fingerprint(body))
    fm.update(extra)
    fm = {k: v for k, v in fm.items() if v is not None}
    fm["signed_fields"] = sign.fields_fingerprint(fm)
    write_page(root, f"_inbound/candidates/{pid}.md", body, **fm)
    return fm


def history(fm):
    return (f"- {fm['signed_at']} fingerprint {fm['id']} {fm['fingerprint']} by {fm['signed_by']}\n"
            f"- {fm['signed_at']} fields {fm['id']} {fm['signed_fields']} by {fm['signed_by']}\n")


def logs(monkeypatch, by_repo):
    asked = []
    monkeypatch.setattr(landing, "signer_log", lambda repo, cache: asked.append(repo) or by_repo.get(repo))
    return asked


def brain(tmp_path, ada=None, mode="live"):
    """A brain that mounts no workspace: what it knows of Ada's is her registry entry."""
    person = {"id": "ada", "kind": "people", "title": "Ada Okafor", "workspace": "ada", **(ada or {})}
    return make_space(tmp_path / "brain", profile="brain", name="brain", mode=mode,
                      subjects=[HARBOR, person, {"id": "ben", "kind": "people", "title": "Ben Liu",
                                                 "workspace": "ben"}])


def landing_of(root, pid):
    return pages.read(root / f"_inbound/candidates/{pid}.md")[0].get("landing", "")


# ----- review 0: a signed release in flight carries the path of its page ----------------------------
def test_a_signed_release_with_a_page_path_is_not_malformed():
    fm = {"id": "p-0000d001", "released_from": "ada", "status": "stable",
          "page": "knowledge/projects/harbor/harbor.md"}
    assert landing.malformed(fm, "p-0000d001") is None


def test_a_conflict_note_still_names_its_page_by_id():
    fm = {"id": "p-0000d002", "released_from": "ada", "conflict_note": True,
          "page": "knowledge/projects/harbor/harbor.md"}
    assert landing.malformed(fm, "p-0000d002") == "`page` is not a page id"
    assert landing.malformed(dict(fm, page="p-0000f001"), "p-0000d002") is None
    assert landing.malformed({"id": "p-0000d003", "released_from": "ada", "retracts": "../x"},
                             "p-0000d003") == "`retracts` is not a page id"


def test_a_signed_page_queued_for_its_home_carries_no_local_path(tmp_path):
    root = make_space(tmp_path / "ada")
    candidate(root)
    sign.apply(root, {"p-00000101": {"verb": "yes", **pin(root, "p-00000101")}}, by="Ada Okafor", now=NOW,
               require_pin=True)
    fm, _ = pages.read(root / "_outbound/queue/p-00000101.md")
    assert "page" not in fm and fm["id"] == "p-00000101"


def test_a_signed_release_with_a_page_path_lands_silently_at_its_home(tmp_path, monkeypatch):
    root = brain(tmp_path, ada={"workspace_repo": "https://github.com/lantern/ada-workspace"})
    fm = signed_release(root, "p-0000d004", signed_repo="https://github.com/lantern/ada-workspace",
                        page="knowledge/projects/harbor/harbor.md")
    logs(monkeypatch, {"lantern/ada-workspace": history(fm)})
    report = landing.land(root, now=NOW)
    assert report.silent == ["p-0000d004"] and report.pending == []
    filed = next((root / "knowledge").rglob("p-0000d004.md"), None) or \
        next(p for p in (root / "knowledge").rglob("*.md") if pages.read(p)[0].get("id") == "p-0000d004")
    assert "page" not in pages.read(filed)[0]


# ----- review 2: a person's workspace's repository is the receiver's own data ----------------------
def test_the_trust_root_of_a_workspace_is_its_persons_workspace_repo(tmp_path):
    root = brain(tmp_path, ada={"workspace_repo": "lantern/ada-workspace"})
    space = landing.spaceyaml.load(root)
    assert release.trust_repo(space, "ada") == "https://github.com/lantern/ada-workspace"
    assert release.trust_repo(space, "ben") is None


def test_two_people_naming_one_workspace_with_two_repositories_give_no_trust_root(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        HARBOR, {"id": "ada", "kind": "people", "title": "Ada Okafor", "workspace": "ada",
                 "workspace_repo": "lantern/ada-workspace"},
        {"id": "chloe", "kind": "people", "title": "Chloe Park", "workspace": "ada",
         "workspace_repo": "chloe/ada-workspace"}])
    assert release.trust_repo(landing.spaceyaml.load(root), "ada") is None


def test_an_archived_persons_workspace_repo_vouches_for_nothing(tmp_path):
    root = brain(tmp_path, ada={"workspace_repo": "lantern/ada-workspace", "state": "archived"})
    assert release.trust_repo(landing.spaceyaml.load(root), "ada") is None


def test_a_release_from_a_registered_workspace_lands_silently(tmp_path, monkeypatch):
    root = brain(tmp_path, ada={"workspace_repo": "lantern/ada-workspace"})
    fm = signed_release(root, "p-0000d011", signed_repo="https://github.com/lantern/ada-workspace")
    asked = logs(monkeypatch, {"lantern/ada-workspace": history(fm)})
    assert landing.land(root, now=NOW).silent == ["p-0000d011"] and asked == ["lantern/ada-workspace"]


def test_the_release_never_names_its_own_workspace_repository(tmp_path, monkeypatch):
    root = brain(tmp_path, ada={"workspace_repo": "lantern/ada-workspace"})
    fm = signed_release(root, "p-0000d012", signed_repo="https://github.com/ben/evil")
    asked = logs(monkeypatch, {"ben/evil": history(fm), "lantern/ada-workspace": history(fm)})
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000d012"] and "ben/evil" not in asked
    assert landing_of(root, "p-0000d012") == "pending: it names a repository that is not ada's"


def test_without_a_workspace_repo_the_persons_own_login_vouches(tmp_path, monkeypatch):
    """The brain's registry gives Ada her login and no repository: a release signed in a repository
    under that login is read there; under any other owner it waits."""
    root = brain(tmp_path, ada={"match": {"github": "Ada-Okafor"}})
    fm = signed_release(root, "p-0000d021", signed_repo="https://github.com/ada-okafor/vault")
    asked = logs(monkeypatch, {"ada-okafor/vault": history(fm)})
    assert landing.land(root, now=NOW).silent == ["p-0000d021"] and asked == ["ada-okafor/vault"]
    other = signed_release(root, "p-0000d022", signed_repo="https://github.com/ben-liu/vault")
    asked = logs(monkeypatch, {"ben-liu/vault": history(other)})
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000d022"] and asked == []
    assert landing_of(root, "p-0000d022").startswith("pending: no repository is known here for ada yet")
    # the agent's to settle (classify verifies the workspace's repository): not a question to the person
    import needs_you
    assert needs_you.landing_health(pages.read(root / "_inbound/candidates/p-0000d022.md")[0])


def test_a_recorded_workspace_repo_wins_over_the_login(tmp_path, monkeypatch):
    root = brain(tmp_path, ada={"workspace_repo": "lantern/ada-workspace", "match": {"github": "ada-okafor"}})
    fm = signed_release(root, "p-0000d023", signed_repo="https://github.com/ada-okafor/other")
    asked = logs(monkeypatch, {"ada-okafor/other": history(fm)})
    assert landing.land(root, now=NOW).pending == ["p-0000d023"] and asked == []


def test_without_a_repository_or_a_login_the_release_waits_for_the_agent(tmp_path, monkeypatch):
    root = brain(tmp_path)
    fm = signed_release(root, "p-0000d031", signed_repo="https://github.com/lantern/ada-workspace")
    asked = logs(monkeypatch, {"lantern/ada-workspace": history(fm)})
    report = landing.land(root, now=NOW)
    assert report.pending == ["p-0000d031"] and asked == []
    reason = landing_of(root, "p-0000d031")
    assert reason.startswith("pending: no repository is known here for ada") and "workspace note" in reason
    assert "lantern/ada-workspace" not in reason, "a reason never quotes what the release says"


def test_a_login_shared_by_two_people_on_one_workspace_vouches_for_nothing(tmp_path, monkeypatch):
    root = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        HARBOR, {"id": "ada", "kind": "people", "title": "Ada Okafor", "workspace": "ada",
                 "match": {"github": "ada-okafor"}},
        {"id": "chloe", "kind": "people", "title": "Chloe Park", "workspace": "ada",
         "match": {"github": "chloe-park"}}])
    fm = signed_release(root, "p-0000d032", signed_repo="https://github.com/chloe-park/vault")
    logs(monkeypatch, {"chloe-park/vault": history(fm)})
    assert landing.land(root, now=NOW).pending == ["p-0000d032"]


def test_onboarding_records_each_workspaces_repository_and_fills_it_in_later(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "ben", "kind": "people", "title": "Ben Liu", "workspace": "ben"}])
    answers = {"people": [{"name": "Ada Okafor", "slug": "ada"}, {"name": "Ben Liu", "slug": "ben"}]}

    class Report:
        def add(self, *args, **kwargs):
            pass

    onboard.fill_registry(Report(), root, answers, "Ada Okafor", "brain", NOW,
                          workspaces={"ada": "ada", "ben": "ben"},
                          workspace_repos={"ada": "https://github.com/lantern/ada-workspace",
                                           "ben": "https://github.com/lantern/ben-workspace"})
    reg = registry.Registry(root)
    people = {s.title: s for s in reg.everywhere() if s.kind == "people"}
    assert people["Ada Okafor"].extra["workspace_repo"] == "https://github.com/lantern/ada-workspace"
    assert people["Ben Liu"].extra["workspace_repo"] == "https://github.com/lantern/ben-workspace"
    # set once: a later run never moves it
    onboard.fill_registry(Report(), root, answers, "Ada Okafor", "brain", NOW,
                          workspaces={"ben": "ben"}, workspace_repos={"ben": "https://github.com/ben/elsewhere"})
    ben = next(s for s in registry.Registry(root).everywhere() if s.title == "Ben Liu")
    assert ben.extra["workspace_repo"] == "https://github.com/lantern/ben-workspace"


def test_a_workspace_repo_is_never_recorded_beside_another_workspace(tmp_path):
    root = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[
        {"id": "ben", "kind": "people", "title": "Ben Liu", "workspace": "ben-old"}])

    class Report:
        def add(self, *args, **kwargs):
            pass

    onboard.fill_registry(Report(), root, {"people": [{"name": "Ben Liu", "slug": "ben"}]}, "Ben Liu", "brain", NOW,
                          workspaces={"ben": "ben"}, workspace_repos={"ben": "https://github.com/lantern/ben"})
    ben = next(s for s in registry.Registry(root).everywhere() if s.title == "Ben Liu")
    assert ben.extra["workspace"] == "ben-old" and "workspace_repo" not in ben.extra


# ----- review 6: a routine never signs, pinned or not -----------------------------------------------
@pytest.mark.parametrize("require_pin", [True, False])
def test_a_routine_reaching_sign_apply_writes_nothing(tmp_path, monkeypatch, require_pin):
    root = make_space(tmp_path / "ada")
    candidate(root)
    verdict = {"verb": "yes", **pin(root, "p-00000101")}
    log_before = (root / "log.md").read_text(encoding="utf-8")
    monkeypatch.setenv(routine.ROUTINE_MARK, "sweep-chain")
    with pytest.raises(ValueError, match="routine"):
        sign.apply(root, {"p-00000101": verdict}, by="Ada Okafor", now=NOW, require_pin=require_pin)
    assert not (root / "knowledge/projects/harbor/harbor.md").exists()
    assert (root / "_inbound/candidates/harbor.md").exists()
    assert (root / "log.md").read_text(encoding="utf-8") == log_before
    assert not list((root / "_outbound/queue").glob("*.md"))


def test_a_routine_in_a_fixture_space_never_signs_either(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada", mode="fixture")
    candidate(root)
    monkeypatch.setenv(routine.ROUTINE_MARK, "1")
    with pytest.raises(ValueError, match="routine"):
        sign.apply(root, {"p-00000101": "yes"}, by="Ada Okafor", now=NOW)
    assert not (root / "knowledge/projects/harbor/harbor.md").exists()


def test_the_item_refusal_comes_before_the_handler(tmp_path, monkeypatch):
    """`_one` asks again, so no path into it can write a page before the verdict store refuses."""
    root = make_space(tmp_path / "ada")
    candidate(root)
    import status
    monkeypatch.setattr(status, "_routine_running", lambda: "a routine runs this command")
    with pytest.raises(ValueError, match="routine"):
        sign._one(root, landing.spaceyaml.load(root), sign.index(root), "p-00000101", "yes", "", {},
                  "Ada Okafor", NOW, sign.ApplyReport(), False)
    assert not (root / "knowledge/projects/harbor/harbor.md").exists()


# ----- an item about no subject with a signature not made here -------------------------------------
def test_an_item_about_no_subject_with_an_unlogged_signature_is_held_as_about_no_subject(tmp_path):
    root = make_space(tmp_path / "ada")
    body = "Ask before you quote a rate.\n"
    fm = {"id": "p-0000d041", "destination": "brain", "status": "stable", "signed_by": "Ada Okafor",
          "signed_at": pages.iso(NOW), "signed_in": "ada", "fingerprint": pages.fingerprint(body)}
    assert release.item_problem(fm, body, root)[0] == "no subject"
    assert release.item_problem(dict(fm, subject="harbor"), body, root)[0] == "signature"
