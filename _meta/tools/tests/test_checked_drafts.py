"""A checked draft is readable knowledge with its label; the high-stakes kinds still wait for a signature."""
import datetime as dt
import re

import ground
import pages
import search
import status
from conftest import NOW, make_space, write_page

LANTERN = {"id": "lantern-works", "kind": "clients", "title": "Lantern Works"}
BEN = {"id": "ben-mensah", "kind": "people", "title": "Ben Mensah"}
CHECKED = {"by": "checker", "at": "2026-10-11T08:00:00Z"}


def draft(root, name, title, checked=True, **fm):
    fm = {"type": "entity", "subject": "lantern-works", "kind": "clients", "status": "draft",
          "generated": pages.generated("distill", NOW - dt.timedelta(days=1)), **fm}
    if checked:
        fm["checked"] = CHECKED
    return write_page(root, f"_inbound/candidates/{name}.md", title=title,
                      body=f"{title}: the kiln line runs two shifts. [src: m-1]\n", **fm)


def space(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[LANTERN, BEN])
    draft(root, "plain", "Kiln shifts")                                          # readable
    draft(root, "unchecked", "Kiln shifts unchecked", checked=False)
    draft(root, "policy", "Kiln safety policy", type="policy")                    # a rule or policy
    draft(root, "decision", "Kiln supplier chosen", type="decision", kind="decisions")   # may bind
    draft(root, "person", "Ben on the kiln line", subject="ben-mensah", kind="people")   # a named person
    draft(root, "promise", "Kiln delivery promised", high_stakes="a promise to a client")
    return root


def test_only_a_checked_draft_outside_the_high_stakes_list_is_labelled_readable(tmp_path):
    root = space(tmp_path)
    labels = {}
    for name in ("plain", "unchecked", "policy", "decision", "person", "promise"):
        rel = f"_inbound/candidates/{name}.md"
        fm, body = pages.read(root / rel)
        labels[name] = ground.label(rel, fm, body)
    assert labels == {"plain": "pending, checked, not signed", "unchecked": "pending", "policy": "pending",
                      "decision": "pending", "person": "pending", "promise": "pending"}
    # a signed page and a witness page keep their tier as it was
    assert ground.label("knowledge/sources/2026/10/m-1.md", {"source": "mail"}) == "witness"


def test_a_released_held_or_misplaced_page_or_a_change_to_a_signed_page_is_never_labelled_readable(tmp_path):
    root = space(tmp_path)
    ok = {"type": "entity", "kind": "clients", "checked": CHECKED}
    assert ground.label("_inbound/candidates/x.md", ok) == "pending, checked, not signed"
    for rel, fm in (
            ("_inbound/candidates/x.md", {**ok, "released_from": "harbor", "landing": "pending: sender"}),
            ("_inbound/questions/x.md", ok),
            ("_inbound/candidates/x.md", {**ok, "type": "process"}),
            ("_inbound/candidates/x.md", {**ok, "type": "Decision"}),
            ("_inbound/candidates/x.md", {**ok, "checked": "no"})):
        assert ground.label(rel, fm) == "pending", fm
    signed = write_page(root, "knowledge/clients/lantern-works/overview.md", type="entity", status="stable",
                        title="Lantern Works", body="Signed.\n")
    over = {**ok, "target": signed.relative_to(root).as_posix()}
    assert ground.label("_inbound/candidates/x.md", over, "", root) == "pending"
    assert ground.label("_inbound/candidates/x.md", {**over, "target": "knowledge/clients/lantern-works/new.md"},
                        "", root) == "pending, checked, not signed"


def test_search_and_the_grounding_line_show_the_label(tmp_path):
    root = space(tmp_path)
    hits = {h.path: h.tier for h in search.query(root, "kiln")}
    assert hits["_inbound/candidates/plain.md"] == "pending, checked, not signed"
    assert hits["_inbound/candidates/policy.md"] == "pending"
    assert {f.path: f.tier for f in search.tiered(root, search.lines(root, "kiln"))} == hits
    line = ground.line_for(root, "_inbound/candidates/plain.md", NOW)
    assert line.startswith("- Kiln shifts (pending, checked, not signed, mine, ")
    assert ground.line_for(root, "_inbound/candidates/unchecked.md", NOW).startswith(
        "- Kiln shifts unchecked (pending, mine, ")


def test_the_walk_reads_a_checked_draft_apart_from_what_waits_for_a_signature(tmp_path):
    root = space(tmp_path)
    status.run(root, now=NOW)
    body = pages.read(root / "live" / "subjects" / "lantern-works.md")[1]
    checked = body[body.index("### Checked, not signed"):]
    waiting = body[body.index("### Waiting for a signature"):body.index("### Checked, not signed")]
    assert "- Kiln shifts (pending, checked, not signed, mine, 2026-10-11, " in checked
    for title in ("Kiln shifts unchecked", "Kiln safety policy", "Kiln delivery promised"):
        assert f"- {title} (pending, mine, 2026-10-11, " in waiting
    hub = pages.read(root / "knowledge" / "clients" / "lantern-works" / "index.md")[1]
    assert re.search(r"- Pending: \[Kiln shifts\]\(id:p-[0-9a-f]{8}\) \(pending, checked, not signed, mine, 2026-10-11\)", hub)


def test_a_checked_decision_both_labellers_call_not_binding_is_readable(tmp_path):
    decision = {"type": "decision", "kind": "decisions", "checked": CHECKED}
    rel = "_inbound/candidates/2026-10-11-kiln-supplier.md"
    assert ground.label(rel, {**decision, "binds": False, "binds_second": False}) == "pending, checked, not signed"
    for labels in ({"binds": False, "binds_second": True}, {"binds": False}, {}):
        assert ground.label(rel, {**decision, **labels}) == "pending", labels
    assert ground.label(rel, {**decision, "binds": False, "binds_second": False, "checked": None}) == "pending"
