"""Outbox reconcile, procedure pages, reconnect items and the landing line on Needs you."""
import datetime as dt
import json

import yaml

import needs_you
import outbox_reconcile
import pages
import procedure
import status
import validate
from conftest import NOW, make_space, write_page


def draft(root, name, title, to=None, at=NOW):
    fm = {"id": pages.new_id(), "type": "source", "title": title, "status": "draft",
          "generated": pages.generated("agent", at)}
    if to:
        fm["to"] = to
    return pages.write(root / "outbox" / f"{name}.md", fm, f"Dear all,\n\n{title}.\n")


def test_outbox_reconcile_files_what_was_sent_and_archives_what_idled(tmp_path):
    root = make_space(tmp_path / "ada")
    draft(root, "proposal", "Pilot proposal for Northgate", to=["lead@northgate.example"])
    draft(root, "old", "An idea nobody sent", at=NOW - dt.timedelta(days=20))
    draft(root, "fresh", "Still thinking", at=NOW - dt.timedelta(days=2))
    feed = root / "_meta/state/feed/mail.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text(json.dumps({"id": "m-900", "kind": "mail", "at": "2026-10-11T16:00:00Z",
                                "from": "owner@example.test", "to": ["lead@northgate.example"],
                                "subject": "Re: Pilot proposal for Northgate", "text": "..."}) + "\n",
                    encoding="utf-8")
    report = outbox_reconcile.run(root, now=NOW)
    assert report.sent == ["outbox/sent/proposal.md"] and report.archived == ["outbox/archive/old.md"]
    assert report.waiting == 1 and (root / "outbox/fresh.md").exists()
    text = (root / "outbox/sent/proposal.md").read_text(encoding="utf-8")
    assert "## Sent" in text and "m-900" in text
    assert not (root / "outbox/proposal.md").exists()
    line = json.loads((root / "_meta/state/runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["routine"] == "outbox-reconcile" and line["notes"]["sent"] == 1


PROCESS = """# Client report

## Steps
1. Draft the report from the signed pages.
2. Hand it to the crew.

## Checks
- Never include margins in a client document. `never: margin`
- The crew sends; agents never send. `always: crew sends`
"""


def test_procedure_checks_an_outcome_and_files_evidence_on_failure(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "reporting", "kind": "topics", "title": "Reporting"}])
    page = write_page(root, "knowledge/topics/reporting/client-report.md", type="process",
                      title="Client report", subject="reporting", body=PROCESS)
    good = tmp_path / "good.md"
    good.write_text("Status for the client. The crew sends this on Friday.\n", encoding="utf-8")
    assert procedure.check(root, page, good).failed == []
    bad = tmp_path / "bad.md"
    bad.write_text("Our margin on this is 30%.\n", encoding="utf-8")
    result = procedure.check(root, page, bad)
    assert result.failed == ["never: margin", "always: crew sends"]
    fm, body = pages.read(root / result.evidence)
    assert fm["type"] == "source" and fm["subject"] == "reporting" and fm["status"] == "draft"
    assert "30%" not in body and "never: margin" in body
    assert procedure.main([str(root), "check", str(page), str(bad)]) == 1


def test_validator_wants_steps_and_checks_on_a_signed_process_page(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "reporting", "kind": "topics", "title": "Reporting"}])
    body = "# Client report\n\nJust do it.\n"
    write_page(root, "knowledge/topics/reporting/client-report.md", type="process", title="Client report",
               subject="reporting", status="stable", body=body, signed_by="Ada Okafor",
               signed_at=pages.iso(NOW), fingerprint=pages.fingerprint(body))
    errs = [e for e in validate.validate(root) if not e.startswith("warning:")]
    assert any("## Steps" in e for e in errs) and any("## Checks" in e for e in errs)


def test_reconnect_items_appear_and_clear_themselves(tmp_path):
    root = make_space(tmp_path / "ada")
    runs = root / "_meta/state/runs.jsonl"
    runs.write_text(json.dumps({"routine": "sweep-chain", "at": pages.iso(NOW), "space": "ada",
                                "cost_tokens": 10, "notes": {"step": "agent", "unreachable": ["chat"]}})
                    + "\n", encoding="utf-8")
    status.run(root, now=NOW)
    items = yaml.safe_load((root / "_inbound/needs-you/librarian.yaml").read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in items] == ["reconnect-chat"] and items[0]["kind"] == "reconnect"
    assert any(i.kind == "reconnect" for i in needs_you.collect(root, now=NOW, online=False))
    with runs.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"routine": "sweep-chain", "at": pages.iso(NOW + dt.timedelta(hours=8)),
                             "space": "ada", "cost_tokens": 10, "notes": {"step": "agent"}}) + "\n")
    status.run(root, now=NOW + dt.timedelta(hours=9))
    items = yaml.safe_load((root / "_inbound/needs-you/librarian.yaml").read_text(encoding="utf-8"))["items"]
    assert items == []


def test_a_pending_landing_says_why_and_for_whom(tmp_path):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    write_page(root, "_inbound/candidates/p-0000d001.md", id="p-0000d001", type="entity", title="Harbor",
               subject="harbor", released_from="ada", landing="pending: high stakes (contract)",
               **{"for": "Ben Liu"})
    item, = [i for i in needs_you.collect(root, now=NOW, online=False) if i.kind == "candidate"]
    assert "pending: high stakes (contract)" in item.text and "Ben Liu" in item.text
