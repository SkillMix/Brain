# Lossless capture and judgment routing: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every company record is captured in full in the person's workspace, and an agent decides per record which space and subject it informs, with deterministic gates, a log, and projections that update the destination's status page and candidates.

**Architecture:** The export (agent) writes full records; `sweep.py` writes witness pages and stops releasing when routing is on. A new `route.py` lists what waits (`pending`), validates and applies an agent's decision (`record`: gates, projections into `_outbound/queue/`, a log, a stamp on the witness page), and handles owner answers and retractions. `status.py` and `distill.py` read routed subjects and `informs`. Unclassifiable records wait in a never-staged private zone and are listed in the owner's morning email.

**Tech Stack:** Python 3.10+, PyYAML, pytest; the existing tools in `_meta/tools/`.

**Spec:** `docs/superpowers/specs/2026-10-14-judgment-routing-design.md`

## Global Constraints

- Template wording: no company, client, product or person names (the name gate, `no_company_names.py`).
- No character caps anywhere in capture; witness pages split above 900 KB (`PART_BYTES = 900_000`) because the sync script never lands a file over 1 MB.
- Veto window: the space's `window_hours` (default 24).
- Never write `status: stable`; never send, post or run git from a tool or skill.
- Never-leaves terms are a hard stop on projected content; the high-stakes list is a floor (window, lands pending).
- `_meta/state/private/` is never staged by `sync.py` and is gitignored in the template.
- Routing is on only when `space.yaml` has a `routing:` mapping; without it every tool behaves as before (project spaces, the brain, older workspaces).
- Every test runs under `python -m pytest _meta/tools/tests -q`; the full suite stays green after every task.

## Review Focus

1. **The workspace's own briefing and its reply come back through the mail feed.** A person expects the subject lines of unsure records, listed in their morning email, never to land in the repository: `sweep.py` must never write a witness page for a mail from the owner to the owner whose subject is a briefing or a workspace notice (Task 3).
2. **A part that differs from the page only in whitespace or line breaks** (a transcript segment copied across lines) must be accepted; a part with one changed word must be refused (Task 4).
3. **A decision for a record already routed** (an agent run twice, a crash mid-run) must not queue a second projection: refused unless the page was refreshed, and then the queued projection is replaced, not doubled (Task 4).
4. **A route to the workspace's own space** (a subject whose home is here) attaches without any release (Task 4).
5. **An unsure record answered "yes" twice** (two replies) must enter the feed once (Task 9).

---

### Task 1: The private zone and the routing setting

**Files:**
- Modify: `_meta/tools/sync.py:54` (PRIVATE_ZONES)
- Modify: `.gitignore`
- Modify: `_meta/tools/spaceyaml.py` (Space accessors and errors)
- Test: `_meta/tools/tests/test_sync.py`, `_meta/tools/tests/test_spaceyaml.py`

**Interfaces:**
- Produces: `sync.PRIVATE_ZONES` includes `"_meta/state/private/"`; `Space.routing -> dict`, `Space.routing_on -> bool`, `Space.review_until -> dt.datetime | None`, `Space.routing_since -> dt.datetime | None`; `spaceyaml.PRIVATE_STATE = Path("_meta") / "state" / "private"`.

- [ ] **Step 1: Failing tests**

```python
# test_spaceyaml.py
def test_routing_setting_is_off_by_default_and_read_when_present(tmp_path):
    import datetime as dt
    root = make_space(tmp_path / "ada")
    space = spaceyaml.load(root)
    assert space.routing_on is False and space.review_until is None and space.routing_since is None
    root2 = make_space(tmp_path / "ben", routing={"review_until": "2026-10-28", "since": "2026-07-16"})
    space2 = spaceyaml.load(root2)
    assert space2.routing_on is True
    assert space2.review_until == dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc)
    assert space2.routing_since == dt.datetime(2026, 7, 16, tzinfo=dt.timezone.utc)
    assert space2.errors() == []
    root3 = make_space(tmp_path / "eve", routing={"review_until": "soon"})
    assert any("routing" in e for e in spaceyaml.load(root3).errors())

# test_sync.py
def test_the_private_state_zone_is_never_staged():
    import sync
    assert "_meta/state/private/" in sync.PRIVATE_ZONES
```

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: routing_on`, assertion on PRIVATE_ZONES)

Run: `python -m pytest _meta/tools/tests/test_spaceyaml.py _meta/tools/tests/test_sync.py -q -k "routing or private_state"`

- [ ] **Step 3: Implement**

`sync.py`: `PRIVATE_ZONES = ("personal/", "academic/", "_scratch/", "_meta/state/private/")`.
`.gitignore`: add `_meta/state/private/` under "Local caches".
`spaceyaml.py` (in `class Space`):

```python
    @property
    def routing(self) -> dict:
        value = self.data.get("routing")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def routing_on(self) -> bool:
        return isinstance(self.data.get("routing"), dict)

    def _routing_date(self, key: str):
        value = self.routing.get(key)
        if not value:
            return None
        import datetime as _dt
        text = str(value)
        try:
            day = _dt.date.fromisoformat(text[:10])
        except ValueError:
            return None
        return _dt.datetime(day.year, day.month, day.day, tzinfo=_dt.timezone.utc)

    @property
    def review_until(self):
        return self._routing_date("review_until")

    @property
    def routing_since(self):
        return self._routing_date("since")
```

In `errors()`: for `key in ("review_until", "since")`, a value present whose first ten characters are not an ISO date adds `f"routing: {key} must be a date (YYYY-MM-DD)"`. Module constant `PRIVATE_STATE = Path("_meta") / "state" / "private"`.

- [ ] **Step 4: Run, expect PASS**; then the full suite.
- [ ] **Step 5: Commit** (`git add` the four files; message `chore: sync`).

---

### Task 2: The capture contract (skill and routine text)

**Files:**
- Modify: `.claude/skills/sweep/SKILL.md` (Run section), `_meta/templates/routines/sweep-chain.md` (step 1)
- Test: `_meta/tools/tests/test_sweep.py` (`test_the_export_contract_asks_for_depth_and_the_attachments_of_matching_threads`)

**Interfaces:** Produces the record shape every later task reads: `text` lossless; `scope` never written (only `company` records reach the feed); unsure records go to `_meta/state/private/unsure.jsonl` with `"feed": <source name>` added; attachment records carry `read_via`.

- [ ] **Step 1: Change the contract test first**

Replace the body's assertions with:

```python
    for text in (p.read_text(encoding="utf-8") for p in paths):
        flat = " ".join(text.split())
        assert "8,000" not in flat and "never the transcript" not in flat, "capture is lossless"
        assert "full transcript" in flat and "full body" in flat and "full text" in flat
        assert "personal" in flat and "academic" in flat and "unsure.jsonl" in flat
        assert "sweep.py <root> --attachments" in flat and "read_via" in flat
        assert "route" in flat and "SKILL.md" in flat
```

- [ ] **Step 2: Run, expect FAIL** (`8,000` still present).
- [ ] **Step 3: Rewrite the text**

In the sweep skill's Run step 1, replace the Depth/Per-kind passage with the spec's section 4.1 table in prose:
"Capture is lossless: no character cap. Per kind: mail = one message, `text` its full body
(quoted history included), `from`, `to`, `cc` as addresses, file names in `attachments`;
call = one meeting, `subject` its own title, `text` the recorder's summary, action items and
key points, then the full transcript, one line per utterance `[hh:mm:ss] Speaker: words`,
`participants` by address; message = one chat thread, every message with author and time, its
files read with the chat connector's file reader, AI notes and canvases as `document` records;
event = `text` the description, guests as `participants`; document = one file version or one
attachment, `text` its full text. Before writing a record decide `company`, `personal`,
`academic` or `unsure`: only `company` is written to the feed; count `personal` and `academic`
per source in your run line (`notes.skipped`); write an `unsure` record to
`_meta/state/private/unsure.jsonl` with `"feed": "<source>"` added (it never enters the
repository; the morning email asks about it)."
Step 2 (attachments) gains the read order: "the mail connector's attachment reader; else the
browser, reading the attachment in the mail web app's preview without downloading; else leave it
for the daily list. Each attachment record carries `read_via: connector | preview |
approved_batch`." Add step 4: "Route: follow `.claude/skills/route/SKILL.md` when `space.yaml`
has `routing:`." Remove "each `text` capped at 8,000 characters" and "never the transcript"
from the routine; add "the full transcript", "the full body", "the full text", the private-door
sentence and "then route as `.claude/skills/route/SKILL.md` says when `space.yaml` has `routing:`".

- [ ] **Step 4: Run, expect PASS**; full suite.
- [ ] **Step 5: Commit.**

---

### Task 3: `sweep.py`: refresh, split, own briefings out, no release when routing is on

**Files:**
- Modify: `_meta/tools/sweep.py` (`run`, `witness_path`, new helpers)
- Test: `_meta/tools/tests/test_sweep.py`

**Interfaces:**
- Consumes: `Space.routing_on` (Task 1).
- Produces: `sweep.PART_BYTES = 900_000`; `sweep.is_own_notice(space, record) -> bool`; witness pages may carry `refreshed_at` and lose `routing` when refreshed; split parts are pages with record ids `<id>#2`, `<id>#3`, each with `part: n` and `parts: total`.

- [ ] **Step 1: Failing tests**

```python
def test_with_routing_on_the_sweep_writes_pages_and_releases_nothing(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}],
                      routing={"since": "2026-07-01"})
    write_feed(ada, [record()])
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 1 and report.attached == 1
    assert report.queued == 0 and report.signals == 0 and queue_items(ada) == []


def test_a_deeper_export_refreshes_the_page_and_keeps_its_id(tmp_path):
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record(text="Short preview.")])
    sweep.run(ada, now=NOW)
    page, = list((ada / "knowledge" / "sources").rglob("m-0001.md"))
    first_id = pages.read(page)[0]["id"]
    fm, body = pages.read(page)
    pages.write(page, dict(fm, routing={"decision": "private"}), body)
    write_feed(ada, [record(text="Short preview. And the whole rest of the message, in full.")])
    marks = ada / "_meta" / "state" / "sweep-watermark.yaml"
    marks.unlink()   # the export re-read an old record: the sweep sees it again
    sweep.run(ada, now=NOW + dt.timedelta(hours=1))
    fm, body = pages.read(page)
    assert fm["id"] == first_id and "whole rest of the message" in body
    assert fm.get("refreshed_at") and "routing" not in fm, "a refreshed page is routed again"


def test_a_page_over_the_size_limit_is_split_on_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "PART_BYTES", 60)
    _, _, ada = build(tmp_path, routing={})
    write_feed(ada, [record("c-0900", source="calls", kind="call", subject="Long call", to=[],
                            text="\n".join(f"[00:00:{i:02d}] Ada: line {i} of the meeting" for i in range(6)))],
               source="calls")
    sweep.run(ada, now=NOW)
    found = sorted(p.name for p in (ada / "knowledge" / "sources").rglob("c-0900*.md"))
    assert len(found) >= 3
    texts = "".join(pages.read(p)[1] for p in sorted((ada / "knowledge" / "sources").rglob("c-0900*.md")))
    assert all(f"line {i} of the meeting" in texts for i in range(6)), "nothing lost in the split"


def test_the_workspaces_own_briefing_and_its_reply_never_become_pages(tmp_path):
    _, _, ada = build(tmp_path, owner_email="ada@lantern.example", routing={})
    write_feed(ada, [record("m-brief", subject="Morning briefing: 3 items need you", **{"from": "ada@lantern.example"},
                            to=["ada@lantern.example"], text="7. Is this work? Dinner on Friday"),
                     record("m-reply", subject="Re: Morning briefing: 3 items need you",
                            **{"from": "ada@lantern.example"}, to=["ada@lantern.example"], text="7 no")])
    report = sweep.run(ada, now=NOW)
    assert report.witness_written == 0
    assert not list((ada / "knowledge" / "sources").rglob("m-brief.md"))
```

(`build()` passes `routing=` through to `make_space`; `routing={}` turns routing on with no dates.)

- [ ] **Step 2: Run, expect FAIL** on each.
- [ ] **Step 3: Implement**

```python
PART_BYTES = 900_000
OWN_NOTICE = re.compile(r"^\s*((re|aw|sv|fwd?)\s*:\s*)*(\[shadow\]\s*)?(morning briefing|security check|"
                        r"action needed|for your info)\b", re.I)


def is_own_notice(space: spaceyaml.Space, record: dict) -> bool:
    """The workspace's own briefings and notices, and replies to them: read by reply.py from the
    feed, never a witness page (they may list subject lines of records kept out)."""
    own = registry.address_of(space.owner_email)
    if not own or registry.address_of(record.get("from")) != own:
        return False
    to = [registry.address_of(t) for t in record.get("to") or []]
    return own in to and bool(OWN_NOTICE.match(str(record.get("subject") or "")))


def split_text(text: str, limit: int) -> list:
    """Line-boundary chunks of at most `limit` bytes (a single longer line stands alone)."""
    parts, current, size = [], [], 0
    for line in text.splitlines(keepends=True):
        n = len(line.encode("utf-8"))
        if current and size + n > limit:
            parts.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += n
    if current:
        parts.append("".join(current))
    return parts or [""]
```

In `run()`, at the top of the record loop: `if is_own_notice(space, record): report.plan.append(f"skip {record['id']}: the workspace's own notice"); continue` (before counting it). Replace the `if target.exists(): skip` branch with:

```python
        if target.exists():
            old_fm, old_body = pages.read(target)
            new_text = str(record.get("text") or "")
            if record.get("_reattach") or len(new_text) <= len(old_body) or old_fm.get("status") == "stable":
                report.plan.append(f"skip {record['id']}: witness page exists")
                continue
            refreshed = {k: v for k, v in old_fm.items() if k != "routing"}
            refreshed["refreshed_at"] = pages.iso(now)
            report.plan.append(f"refresh {target.relative_to(root).as_posix()}")
            if not dry_run:
                pages.write(target, refreshed, new_text)
            report.witness_written += 1
            continue
```

Splitting: when `len(text.encode("utf-8")) > PART_BYTES`, write part 1 to the record's own path and parts 2..n as records `f"{rid}#{k}"` (paths via `witness_path` with that id), each frontmatter with `part: k, parts: n`; attachment, classification and release use the first part only.
Release: right after `report.attached += 1`, `if space.routing_on: continue` (the router releases).

- [ ] **Step 4: Run, expect PASS**; full suite (no old sweep test changes: routing stays off there).
- [ ] **Step 5: Commit.**

---

### Task 4: `route.py`: pending and record (the gates)

**Files:**
- Create: `_meta/tools/route.py`
- Test: `_meta/tools/tests/test_route.py`

**Interfaces:**
- Consumes: `sweep.item_frontmatter`, `sweep.signal_for`, `sweep._person_name`, `sweep.crew_of`, `sweep.witness_path`, `sweep.file_name`, `release.first_term`, `release.hold`, `release.destination_space`, `release.left_out_of`, `release.QUEUE`, `release.SIGNALS`, `release.append_line`, `release.append_run_line`, `registry.Registry`, `registry.record_domains`, `registry.address_of`, `Space.review_until`, `Space.routing_since`.
- Produces:
  - `route.RouteError(ValueError)`
  - `route.pending(root, subject: str | None = None, now=None) -> dict` with keys `records` (list of dicts: `record, path, title, kind, at, from, to, cc, participants, thread, text, suggested, mentions`), `spaces` (list: `name, profile, audience, left_out`), `subjects` (list: `id, kind, title, home, aliases, match`), `status_pages` (dict `subject id -> text`).
  - `route.record(root, decision: dict, now=None) -> dict` with keys `queued` (item ids), `held` (list of `(item id, reason)`), `question` (relpath or None), `attached` (list of `space/subject`).
  - `route.find_page(root, record_id) -> Path | None`
  - `route.LOG = Path("_meta") / "state" / "routing.jsonl"`
  - Witness stamp: `routing: {decided_at, decision, confidence, routes: ["<space>/<subject>"], items: [ids]}`.

- [ ] **Step 1: Failing tests** (fixture helpers shared by Tasks 4 to 6)

```python
"""route.py: an agent's routing decision, gated, logged and turned into projections."""
import datetime as dt
import json

import pytest
import yaml

import pages
import route
import sweep
from conftest import NOW, make_space, mount_brain

SUBJECTS = [
    {"id": "harbor", "kind": "projects", "title": "Harbor", "home": "harbor",
     "match": {"domain": "harbor.example"}},
    {"id": "beacon", "kind": "projects", "title": "Beacon", "home": "beacon"},
    {"id": "ben", "kind": "people", "title": "Ben Liu", "email": "ben@lantern.example"},
]
CALL = "\n".join([
    "Summary: the pilot scope and the board dinner.",
    "[00:01:00] Chloe: The first list should hold forty leads.",
    "[00:02:00] Ada: We send it by Friday 2026-10-23.",
    "[00:03:00] Ben: For Beacon, the venue moves to the harbour hall.",
])


def space_set(tmp_path, **ada):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=SUBJECTS)
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ada Okafor",
                        audience=["Ada Okafor", "Ben Liu"])
    beacon = make_space(tmp_path / "beacon", profile="project", name="beacon", owner="Ada Okafor",
                        audience=["Ada Okafor"])
    ada.setdefault("routing", {})
    ada.setdefault("sources", [{"name": "calls", "adapter": "files", "audience": "owner"}])
    root = make_space(tmp_path / "ada", name="ada", owner_email="ada@lantern.example", **ada)
    mount_brain(root, brain)
    data = yaml.safe_load((root / "space.yaml").read_text(encoding="utf-8"))
    data["mounts"] += [{"name": "harbor", "path": str(harbor)}, {"name": "beacon", "path": str(beacon)}]
    (root / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root, harbor, beacon


def captured(root, rid="c-1", text=CALL, participants=None, title="Kickoff"):
    rec = {"id": rid, "source": "calls", "kind": "call", "at": "2026-10-12T08:00:00Z",
           "from": "ada@lantern.example", "to": [], "subject": title, "text": text,
           "participants": participants or ["ada@lantern.example", "ben@lantern.example", "chloe@harbor.example"],
           "identifiers": {"thread": rid}}
    feed = root / "_meta" / "state" / "feed" / "calls.jsonl"
    feed.parent.mkdir(parents=True, exist_ok=True)
    with feed.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    sweep.run(root, now=NOW)
    return rec


def decision(**over):
    base = {"record": "c-1", "decision": "route", "confidence": "high", "why": "the client's kickoff",
            "routes": [{"space": "harbor", "subject": "harbor", "tier": "at_once", "why": "pilot work",
                        "content": "parts",
                        "parts": [{"text": "[00:01:00] Chloe: The first list should hold forty leads."},
                                  {"text": "[00:02:00] Ada: We send it by Friday 2026-10-23."}],
                        "informs": [{"kind": "open_item", "text": "Send the first list", "owner": "Ada Okafor",
                                     "due": "2026-10-23", "direction": "we owe"}]}]}
    base.update(over)
    return base


def queued(root):
    return [pages.read(p) for p in sorted((root / "_outbound" / "queue").glob("*.md"))]


def test_pending_lists_unrouted_pages_with_their_context(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    pack = route.pending(root, now=NOW)
    rec, = pack["records"]
    assert rec["record"] == "c-1" and "forty leads" in rec["text"] and rec["suggested"] == ["harbor"]
    assert {s["name"] for s in pack["spaces"]} >= {"harbor", "beacon"}
    assert any(s["id"] == "harbor" and s["home"] == "harbor" for s in pack["subjects"])


def test_record_queues_one_projection_per_route_with_verbatim_parts(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    out = route.record(root, decision(), now=NOW)
    (fm, body), = queued(root)
    assert out["queued"] == [fm["id"]] and fm["class"] == "at_once" and fm["destination"] == "harbor"
    assert "forty leads" in body and "Beacon" not in body and "board dinner" not in body
    assert fm["informs"][0]["due"] == "2026-10-23" and fm["why"] == "pilot work"
    page = route.find_page(root, "c-1")
    stamp = pages.read(page)[0]["routing"]
    assert stamp["decision"] == "route" and stamp["routes"] == ["harbor/harbor"]
    log = [json.loads(l) for l in (root / route.LOG).read_text().splitlines()]
    assert log[-1]["record"] == "c-1" and log[-1]["routes"][0]["outcome"] == "queued"
    assert route.pending(root, now=NOW)["records"] == []


def test_a_part_may_differ_only_in_whitespace(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    spaced = decision()
    spaced["routes"][0]["parts"] = [{"text": "[00:01:00]  Chloe:\nThe first list   should hold forty leads."}]
    route.record(root, spaced, now=NOW)
    paraphrase = decision(record="c-2")
    captured(root, rid="c-2")
    paraphrase["routes"][0]["parts"] = [{"text": "[00:01:00] Chloe: The first list should hold 40 leads."}]
    with pytest.raises(route.RouteError, match="verbatim"):
        route.record(root, paraphrase, now=NOW)


def test_whole_needs_everyone_inside_the_audience(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    whole = decision()
    whole["routes"][0].update(content="whole", parts=[])
    route.record(root, whole, now=NOW)      # ada, ben (crew), chloe (the client's domain)
    captured(root, rid="c-3", participants=["ada@lantern.example", "eve@elsewhere.example"])
    with pytest.raises(route.RouteError, match="outside"):
        route.record(root, dict(whole, record="c-3"), now=NOW)


def test_gates(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root, text=CALL + "\n[00:04:00] Ada: The settlement terms are separate.")
    out = route.record(root, decision(), now=NOW)          # never-leaves elsewhere: window
    (fm, _), = queued(root)
    assert fm["class"] == "window" and "never-leaves" in fm["why"]
    captured(root, rid="c-4", text="[00:01:00] Chloe: Our day rate is 950.\n[00:02:00] Ada: Fine.")
    held = decision(record="c-4")
    held["routes"][0]["parts"] = [{"text": "[00:01:00] Chloe: Our day rate is 950."}]
    out = route.record(root, held, now=NOW)
    assert out["held"][0][1] == "left_out"
    captured(root, rid="c-5", text="[00:01:00] Chloe: Please sign the contract.\n")
    stakes = decision(record="c-5")
    stakes["routes"][0]["parts"] = [{"text": "[00:01:00] Chloe: Please sign the contract."}]
    route.record(root, stakes, now=NOW)
    fm = [f for f, _ in queued(root) if f["evidence"] == ["c-5"]][0]
    assert fm["class"] == "window" and fm["high_stakes"] is True
    captured(root, rid="c-6")
    route.record(root, decision(record="c-6", confidence="medium"), now=NOW)
    assert [f for f, _ in queued(root) if f["evidence"] == ["c-6"]][0]["class"] == "window"


def test_low_confidence_becomes_a_question_and_nothing_leaves(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    out = route.record(root, decision(confidence="low"), now=NOW)
    assert queued(root) == [] and out["question"]
    fm, _ = pages.read(root / out["question"])
    assert fm["type"] == "review" and fm["record"] == "c-1" and "harbor" in fm["recommendation"]
    assert route.pending(root, now=NOW)["records"] == [], "a question waits; it is not pending again"


def test_review_mode_holds_every_projection_for_the_window(tmp_path):
    root, _, _ = space_set(tmp_path, routing={"review_until": "2026-10-28"})
    captured(root)
    route.record(root, decision(), now=NOW)
    (fm, _), = queued(root)
    assert fm["class"] == "window" and "review" in fm["why"]
    assert pages.parse_iso(fm["stage_after"]) == NOW + dt.timedelta(hours=24)


def test_private_and_own_space_routes_release_nothing(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(decision="private", routes=[]), now=NOW)
    assert queued(root) == [] and pages.read(route.find_page(root, "c-1"))[0]["routing"]["decision"] == "private"
    (root / "_meta" / "subjects.yaml").write_text(yaml.safe_dump({"subjects": [
        {"id": "pipeline", "kind": "topics", "title": "Pipeline"}]}), encoding="utf-8")
    captured(root, rid="c-7")
    mine = decision(record="c-7")
    mine["routes"] = [dict(mine["routes"][0], space="ada", subject="pipeline")]
    out = route.record(root, mine, now=NOW)
    assert queued(root) == [] and out["attached"] == ["ada/pipeline"]


def test_a_routed_record_is_not_routed_twice_unless_refreshed(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(), now=NOW)
    with pytest.raises(route.RouteError, match="already routed"):
        route.record(root, decision(), now=NOW)
    assert len(queued(root)) == 1


def test_a_refreshed_record_replaces_its_queued_projection(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(routes=[dict(decision()["routes"][0], tier="window")]), now=NOW)
    page = route.find_page(root, "c-1")
    fm, body = pages.read(page)
    pages.write(page, dict({k: v for k, v in fm.items() if k != "routing"}, refreshed_at=pages.iso(NOW)),
                body)
    route.record(root, decision(routes=[dict(decision()["routes"][0], tier="window")]), now=NOW)
    assert len(queued(root)) == 1
    held = [pages.read(p)[0] for p in (root / "_outbound" / "held").glob("*.md")]
    assert [h["reason"] for h in held] == ["superseded"]


def test_refused_routes(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    with pytest.raises(route.RouteError, match="home"):
        route.record(root, decision(routes=[dict(decision()["routes"][0], space="beacon")]), now=NOW)
    with pytest.raises(route.RouteError, match="no witness page"):
        route.record(root, decision(record="nope"), now=NOW)
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: route`).

Run: `python -m pytest _meta/tools/tests/test_route.py -q`

- [ ] **Step 3: Implement `route.py`**

```python
"""route: the judgment router's tool (docs/superpowers/specs/2026-10-14-judgment-routing-design.md).

An agent reads each new witness page in full and decides where it belongs; this tool lists what
waits, validates a decision, applies the gates, writes the projections and the log. It never
decides a route.

    python route.py <root> pending [--subject ID] [--out FILE]
    python route.py <root> record FILE [FILE ...]
    python route.py <root> answer <record id> --yes | --private --by NAME
    python route.py <root> retract <record id> [--space NAME] --by NAME
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry  # noqa: E402
import release  # noqa: E402
import spaceyaml  # noqa: E402
import sweep  # noqa: E402

LOG = Path("_meta") / "state" / "routing.jsonl"
QUESTIONS = Path("_inbound") / "questions"
SOURCES = Path("knowledge") / "sources"
DEFAULT_SINCE_DAYS = 90
CONFIDENCE = ("high", "medium", "low")
TIERS = ("at_once", "window")
INFORMS = ("open_item", "decision", "person", "page")


class RouteError(ValueError):
    pass


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def _pages(root: Path):
    base = root / SOURCES
    for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
        if not pages.is_page_path(path):
            continue
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("type") == "source" and fm.get("record"):
            yield path, fm, body


def find_page(root, record_id) -> Path | None:
    for path, fm, _ in _pages(Path(root)):
        if str(fm.get("record")) == str(record_id):
            return path
    return None


def _questioned(root: Path) -> set:
    out = set()
    for path in (root / QUESTIONS).glob("route-*.md") if (root / QUESTIONS).is_dir() else []:
        try:
            out.add(str(pages.read(path)[0].get("record")))
        except Exception:
            continue
    return out


def _record_of(fm: dict, body: str) -> dict:
    ident = fm.get("identifiers") or {}
    return {"record": str(fm.get("record")), "title": str(fm.get("title") or ""), "kind": fm.get("kind"),
            "at": fm.get("at"), "from": fm.get("from"), "to": list(fm.get("to") or []),
            "cc": list(fm.get("cc") or []), "participants": list(fm.get("participants") or []),
            "thread": ident.get("thread") if isinstance(ident, dict) else None,
            "identifiers": ident, "text": body}


def _mentions(reg, rec: dict, sid: str) -> bool:
    subject = reg.get(sid)
    if subject is None:
        return False
    as_record = {"subject": rec["title"], "text": rec["text"], "kind": rec["kind"], "from": rec["from"],
                 "to": rec["to"], "cc": rec["cc"], "participants": rec["participants"],
                 "identifiers": rec["identifiers"]}
    return subject in reg.mentions(as_record) or bool(reg.matched_by(subject, as_record))


def pending(root, subject: str | None = None, now: dt.datetime | None = None) -> dict:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry.Registry(root, space)
    since = space.routing_since or (now - dt.timedelta(days=DEFAULT_SINCE_DAYS))
    asked = _questioned(root)
    records = []
    for path, fm, body in _pages(root):
        if fm.get("routing") or str(fm.get("record")) in asked or fm.get("part", 1) != 1:
            continue
        rec = _record_of(fm, body)
        try:
            at = pages.parse_iso(rec["at"])
        except (TypeError, ValueError):
            continue
        if subject:
            if not _mentions(reg, rec, subject):
                continue
        elif at < since:
            continue
        as_record = {"subject": rec["title"], "text": body, "kind": rec["kind"], "from": rec["from"],
                     "to": rec["to"], "cc": rec["cc"], "participants": rec["participants"],
                     "identifiers": rec["identifiers"]}
        rec["suggested"] = [s.id for s in reg.match(as_record)]
        rec["mentions"] = list(fm.get("mentions") or [])
        rec["path"] = path.relative_to(root).as_posix()
        records.append(rec)
    records.sort(key=lambda r: (str(r.get("thread") or r["record"]), str(r["at"])))
    homes = sorted({h for h in (reg.home_of(s.id) for s in reg.everywhere()) if h and h != space.name})
    spaces = []
    for name in homes:
        dest = release.destination_space(space, name)
        spaces.append({"name": name, "profile": dest.profile if dest else "unknown",
                       "audience": dest.audience if dest else "unknown",
                       "left_out": release.left_out_of(dest)})
    subjects = [{"id": s.id, "kind": s.kind, "title": s.title, "home": reg.home_of(s.id),
                 "aliases": list(s.aliases), "match": dict(s.match)} for s in reg.everywhere()]
    status_pages = {}
    for s in reg.everywhere():
        home = reg.home_of(s.id)
        base = space.mount_root(home) if home and home != space.name else root
        page = (base / "live" / "subjects" / f"{s.id}.md") if base else None
        if page is not None and page.exists():
            status_pages[s.id] = page.read_text(encoding="utf-8")
    return {"records": records, "spaces": spaces, "subjects": subjects, "status_pages": status_pages}


def _inside(space, reg, dest_space, subject, rec: dict) -> list:
    """Everyone on the record outside the destination's audience (the client's domain is inside)."""
    people = [rec.get("from")] + list(rec.get("to") or []) + list(rec.get("cc") or []) + \
        list(rec.get("participants") or [])
    return sweep.outsiders(space, dest_space, [p for p in people if p], reg, subject)


def _projection(rec: dict, route_: dict) -> str:
    if route_.get("content") == "whole":
        return rec["text"]
    chunks = []
    for part in route_.get("parts") or []:
        note = str(part.get("note") or "").strip()
        chunks.append((f"_{note}_\n\n" if note else "") + str(part["text"]).strip())
    return "\n\n".join(chunks) + "\n"


def _validate(root, space, reg, decision: dict) -> tuple:
    rid = str(decision.get("record") or "")
    path = find_page(root, rid)
    if path is None:
        raise RouteError(f"{rid}: no witness page for that record")
    fm, body = pages.read(path)
    if fm.get("routing"):
        raise RouteError(f"{rid}: already routed ({fm['routing'].get('decision')}); retract it first")
    if decision.get("decision") not in ("private", "route"):
        raise RouteError(f"{rid}: decision must be private or route")
    if decision.get("confidence") not in CONFIDENCE:
        raise RouteError(f"{rid}: confidence must be one of {', '.join(CONFIDENCE)}")
    rec = _record_of(fm, body)
    flat_body = _flat(body)
    routes = list(decision.get("routes") or [])
    if decision["decision"] == "route" and not routes:
        raise RouteError(f"{rid}: a route decision names at least one route")
    for r in routes:
        subject = reg.get(str(r.get("subject") or ""))
        if subject is None:
            raise RouteError(f"{rid}: unknown subject {r.get('subject')}")
        home = reg.home_of(subject.id) or space.name
        if str(r.get("space")) != home:
            raise RouteError(f"{rid}: {subject.id}'s home is {home}, not {r.get('space')}")
        if r.get("tier", "window") not in TIERS:
            raise RouteError(f"{rid}: tier must be at_once or window")
        for item in r.get("informs") or []:
            if item.get("kind") not in INFORMS:
                raise RouteError(f"{rid}: informs kind must be one of {', '.join(INFORMS)}")
        if home == space.name:
            continue
        if r.get("content") == "whole":
            outside = _inside(space, reg, release.destination_space(space, home), subject, rec)
            if outside:
                raise RouteError(f"{rid}: whole is refused, people outside {home}'s audience: {', '.join(outside)}")
        elif r.get("content") == "parts":
            parts = r.get("parts") or []
            if not parts:
                raise RouteError(f"{rid}: content parts names at least one part")
            for part in parts:
                if not _flat(part.get("text")) or _flat(part.get("text")) not in flat_body:
                    raise RouteError(f"{rid}: a part is not verbatim from the record: {str(part.get('text'))[:60]!r}")
        else:
            raise RouteError(f"{rid}: content must be whole or parts")
    return path, fm, body, rec


def _log(root: Path, row: dict) -> None:
    release.append_line(root / LOG, json.dumps(row, ensure_ascii=False))


def _stamp(path: Path, fm: dict, body: str, row: dict) -> None:
    pages.write(path, dict(fm, routing=row), body)


def _question(root: Path, space, decision: dict, fm: dict, path: Path, now) -> str:
    plan = "keep it in the workspace" if decision["decision"] == "private" else \
        ", ".join(f"{r['space']} ({r['subject']}, {r.get('tier', 'window')})" for r in decision["routes"])
    rel = QUESTIONS / f"route-{sweep.file_name(decision['record'])}.md"
    qfm = {"id": pages.new_id(), "type": "review", "title": f"Route: {fm.get('title')}", "status": "draft",
           "generated": pages.generated("route", now), "record": str(decision["record"]),
           "question": f"Where should '{fm.get('title')}' go? I think: {plan}. Right?",
           "recommendation": f"yes: {plan}", "default": "stays in the workspace",
           "payload": path.relative_to(root).as_posix(), "decision": decision}
    pages.write(root / rel, qfm, f"# Route: {fm.get('title')}\n\n{decision.get('why', '')}\n")
    return rel.as_posix()


def record(root, decision: dict, now: dt.datetime | None = None, by: str | None = None) -> dict:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry.Registry(root, space)
    path, fm, body, rec = _validate(root, space, reg, decision)
    out = {"queued": [], "held": [], "question": None, "attached": []}
    row = {"at": pages.iso(now), "record": rec["record"], "decision": decision["decision"],
           "confidence": decision["confidence"], "why": decision.get("why", ""), "by": by or "router",
           "routes": []}
    if decision["confidence"] == "low" and not by:
        out["question"] = _question(root, space, decision, fm, path, now)
        _log(root, dict(row, outcome="question"))
        return out
    if fm.get("refreshed_at"):   # a deeper page: its earlier projections still waiting are replaced
        for qp in sorted((root / release.QUEUE).glob("*.md")):
            qfm, qbody = pages.read(qp)
            if rec["record"] in [str(e) for e in qfm.get("evidence") or []]:
                release.hold(root, qfm, qbody, "superseded", "replaced by the projection of the refreshed page")
                qp.unlink()
    full_text = f"{rec['title']}\n{body}"
    elsewhere = release.first_term(full_text, space.never_leaves)
    items = []
    for r in decision.get("routes") or []:
        dest_name, sid = str(r["space"]), str(r["subject"])
        if dest_name == space.name:
            out["attached"].append(f"{dest_name}/{sid}")
            row["routes"].append({"space": dest_name, "subject": sid, "outcome": "attached"})
            continue
        dest_space = release.destination_space(space, dest_name)
        content = _projection(rec, r)
        projected = f"{rec['title']}\n{content}"
        term = release.first_term(projected, space.never_leaves)
        hold_reason = "never_leaves" if term else None
        if not hold_reason:
            term = release.first_term(projected, release.left_out_of(dest_space))
            hold_reason = "left_out" if term else None
        tier, reasons, extra = r.get("tier", "window"), [str(r.get("why") or "")], {}
        if by:
            extra["approved_by"], extra["path"] = by, f"approved:{by}"
        if elsewhere and not hold_reason:
            tier = "window"
            reasons.append("the record holds never-leaves material elsewhere")
        if decision["confidence"] == "medium" and not by:
            tier = "window"
            reasons.append("medium confidence")
        if release.first_term(projected, space.high_stakes):
            tier = "window"
            extra["high_stakes"] = True
            reasons.append("high-stakes term: lands pending")
        if space.review_until and now < space.review_until and not by:
            tier = "window"
            reasons.append(f"review mode until {space.review_until:%Y-%m-%d}")
        stage_after = now if tier == "at_once" else now + space.window()
        extra.update({"why": "; ".join(x for x in reasons if x), "informs": list(r.get("informs") or []),
                      "confidence": decision["confidence"], "routed_by": by or "router"})
        as_record = {"id": rec["record"], "kind": rec["kind"], "subject": rec["title"], "at": rec["at"],
                     "from": rec["from"], "to": rec["to"], "identifiers": rec["identifiers"]}
        item = sweep.item_frontmatter(space, as_record, sid, dest_name, hold_reason or tier, stage_after,
                                      now, extra)
        if hold_reason:
            release.hold(root, item, content, hold_reason, f"{hold_reason} term `{term}` in the projection")
            out["held"].append((item["id"], hold_reason))
            row["routes"].append({"space": dest_name, "subject": sid, "outcome": f"held:{hold_reason}",
                                  "item": item["id"]})
        else:
            pages.write(root / release.QUEUE / f"{item['id']}.md", item, content)
            out["queued"].append(item["id"])
            row["routes"].append({"space": dest_name, "subject": sid, "tier": tier, "outcome": "queued",
                                  "item": item["id"]})
        if hold_reason != "never_leaves":
            signal = sweep.signal_for(sid, dest_name, now)
            target = root / release.SIGNALS / f"{signal['id']}.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.safe_dump(signal, sort_keys=False), encoding="utf-8", newline="\n")
        items.append(item["id"])
    _stamp(path, fm, body, {"decided_at": pages.iso(now), "decision": decision["decision"],
                            "confidence": decision["confidence"],
                            "routes": [f"{r['space']}/{r['subject']}" for r in decision.get("routes") or []],
                            "items": items, "by": by or "router"})
    _log(root, row)
    return out
```

(`main()` with `argparse` subcommands `pending` (writes `json.dumps(pending(...))` to `--out`, default `_meta/state/private/route-pending.json`, and prints the count), `record` (reads YAML or JSON files, one decision or a list; prints each result or the `RouteError`; exit 1 when any was refused), `answer`, `retract` (Task 5).)

- [ ] **Step 4: Run, expect PASS**; full suite.
- [ ] **Step 5: Commit.**

---

### Task 5: `route.py`: answer and retract

**Files:** Modify `_meta/tools/route.py`; Test `_meta/tools/tests/test_route.py`

**Interfaces:**
- Produces: `route.answer(root, record_id, yes: bool, by: str, now=None) -> dict` (the `record()` result, or `{"private": True}`); `route.retract(root, record_id, by: str, space: str | None = None, now=None) -> dict` with `withdrawn` (queue item ids moved to held as `withdrawn`) and `retractions` (retraction item paths).

- [ ] **Step 1: Failing tests**

```python
def test_an_owner_yes_applies_the_proposed_route_as_approved(tmp_path):
    root, _, _ = space_set(tmp_path, routing={"review_until": "2026-10-28"})
    captured(root)
    route.record(root, decision(confidence="low"), now=NOW)
    out = route.answer(root, "c-1", yes=True, by="Ada Okafor", now=NOW)
    (fm, _), = queued(root)
    assert out["queued"] == [fm["id"]] and fm["class"] == "at_once" and fm["approved_by"] == "Ada Okafor"
    assert not list((root / "_inbound" / "questions").glob("route-*.md"))


def test_an_owner_no_keeps_it_private(tmp_path):
    root, _, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(confidence="low"), now=NOW)
    route.answer(root, "c-1", yes=False, by="Ada Okafor", now=NOW)
    assert queued(root) == [] and pages.read(route.find_page(root, "c-1"))[0]["routing"]["decision"] == "private"


def test_retract_withdraws_queued_and_retracts_shipped(tmp_path):
    import release
    root, harbor, _ = space_set(tmp_path)
    captured(root)
    route.record(root, decision(), now=NOW)
    assert release.ship(root, now=NOW, offline_roots={"harbor": harbor}).shipped == 1
    captured(root, rid="c-8")
    route.record(root, decision(record="c-8", routes=[dict(decision()["routes"][0], tier="window")]), now=NOW)
    out = route.retract(root, "c-8", by="Ada Okafor", now=NOW)
    assert len(out["withdrawn"]) == 1 and queued(root) == []
    out = route.retract(root, "c-1", by="Ada Okafor", now=NOW)
    assert len(out["retractions"]) == 1
    assert pages.read(route.find_page(root, "c-1"))[0]["routing"]["retracted_by"] == "Ada Okafor"
```

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: answer`).
- [ ] **Step 3: Implement**

```python
def answer(root, record_id, yes: bool, by: str, now: dt.datetime | None = None) -> dict:
    root = Path(root)
    qpath = root / QUESTIONS / f"route-{sweep.file_name(record_id)}.md"
    if not qpath.exists():
        raise RouteError(f"{record_id}: no routing question waits")
    qfm, _ = pages.read(qpath)
    proposed = dict(qfm.get("decision") or {})
    qpath.unlink()
    if not yes:
        proposed = {"record": str(record_id), "decision": "private", "confidence": "high", "routes": [],
                    "why": f"{by} kept it in the workspace"}
    proposed["confidence"] = "high"
    result = record(root, proposed, now=now, by=by)
    return {"private": True} if not yes else result


def retract(root, record_id, by: str, space: str | None = None, now: dt.datetime | None = None) -> dict:
    root = Path(root)
    sp = spaceyaml.load(root)
    now = now or sp.now()
    out = {"withdrawn": [], "retractions": []}
    for qp in sorted((root / release.QUEUE).glob("*.md")):
        fm, body = pages.read(qp)
        if str(record_id) in [str(e) for e in fm.get("evidence") or []] and (space is None or fm.get("destination") == space):
            release.hold(root, fm, body, "withdrawn", f"withdrawn by {by}")
            qp.unlink()
            out["withdrawn"].append(fm["id"])
    released = root / release.RELEASED
    for line in released.read_text(encoding="utf-8").splitlines() if released.exists() else []:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if str(record_id) in [str(e) for e in entry.get("evidence") or []] and \
                (space is None or entry.get("destination") == space) and entry.get("class") != "retraction":
            out["retractions"].append(release.retract(root, entry["id"], now=now).relative_to(root).as_posix())
    page = find_page(root, record_id)
    if page is not None:
        fm, body = pages.read(page)
        stamp = dict(fm.get("routing") or {}, retracted_at=pages.iso(now), retracted_by=by)
        pages.write(page, dict(fm, routing=stamp), body)
    _log(root, {"at": pages.iso(now), "record": str(record_id), "decision": "retract", "by": by,
                "space": space, "withdrawn": out["withdrawn"], "retractions": out["retractions"]})
    return out
```

- [ ] **Step 4: Run, expect PASS**; full suite.
- [ ] **Step 5: Commit.**

---

### Task 6: Destination: status and distill read routed subjects and `informs`

**Files:** Modify `_meta/tools/status.py` (`collect`), `_meta/tools/distill.py` (`_evidence`, `run`); Test `_meta/tools/tests/test_route.py` (end to end), `_meta/tools/tests/test_status_answers.py`, `_meta/tools/tests/test_distill.py`

**Interfaces:**
- Consumes: witness `routing.routes` (`"<space>/<subject>"`), landed `informs` list (Task 4).
- Produces: `status.routed_subjects(fm, space_name) -> list`; status Open lines `"{text} (owner {owner}, due {due})"`; `informs[].closes` closes the item whose page id it names; distill decision candidates from `informs` kind `decision`; people evidence from kind `person` (`person: <id>` field).

- [ ] **Step 1: Failing tests**

```python
# test_status_answers.py
def test_routed_records_and_their_open_items_reach_the_status_page(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR], owner_email="ada@lantern.example")
    witness(root, "c1", "2026-10-11T08:00:00Z", kind="call", title="Kickoff",
            routing={"decision": "route", "routes": ["ada/harbor"]})
    witness(root, "p1", "2026-10-11T09:00:00Z", kind="call", title="Kickoff (projection)", subject="harbor",
            informs=[{"kind": "open_item", "text": "Send the first list", "owner": "Ada Okafor",
                      "due": "2026-10-23", "direction": "we owe"}])
    status.run(root, now=NOW)
    text = page_text(root, "harbor")
    owe = text.split("### We owe")[1].split("##")[0]
    assert "Send the first list (owner Ada Okafor, due 2026-10-23)" in owe
    assert "Kickoff" in text.split("## Timeline")[1]
    assert "2026-10-23" in text.split("## Coming up")[1]


def test_an_informs_closes_line_closes_the_earlier_item(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[HARBOR], owner_email="ada@lantern.example")
    witness(root, "p1", "2026-10-11T09:00:00Z", kind="call", title="Kickoff", subject="harbor", id="p-0000aa01",
            informs=[{"kind": "open_item", "text": "Send the first list", "direction": "we owe"}])
    witness(root, "p2", "2026-10-11T12:00:00Z", kind="mail", title="List sent", subject="harbor",
            informs=[{"kind": "open_item", "text": "Wait for feedback", "direction": "waiting on them",
                      "closes": "p-0000aa01"}])
    status.run(root, now=NOW)
    text = page_text(root, "harbor")
    assert "Send the first list" not in text.split("## Open")[1].split("## Recently closed")[0]
    assert "Wait for feedback" in text.split("### Waiting on them")[1]

# test_distill.py
def test_an_informs_decision_becomes_a_decision_candidate(tmp_path):
    root = make_space(tmp_path / "ada", subjects=[{"id": "harbor", "kind": "projects", "title": "Harbor"}])
    write_page(root, "knowledge/sources/2026/10/p1.md", type="source", title="Kickoff", subject="harbor",
               at="2026-10-11T09:00:00Z", record="p1", body="[00:01:00] Chloe: forty leads.\n",
               informs=[{"kind": "decision", "text": "The first list holds forty leads"}])
    distill.run(root, now=NOW)
    cands = list((root / "_inbound" / "candidates").glob("*forty-leads*.md"))
    assert len(cands) == 1 and pages.read(cands[0])[0]["type"] == "decision"
```

(`test_route.py` gains the end-to-end: `captured` → `record` → `release.ship` → `status.run(harbor)`; the harbor status page shows "Send the first list" under "### We owe" and "Kickoff" on the timeline.)

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**

`status.py`:

```python
def routed_subjects(fm: dict, space_name: str) -> list:
    """The subject ids a page is evidence for here: its own `subject`, and every route of its
    routing decision whose space is this one (a routed record attaches by judgment)."""
    out = [str(fm["subject"])] if fm.get("subject") else []
    routing = fm.get("routing") if isinstance(fm.get("routing"), dict) else {}
    for entry in routing.get("routes") or []:
        where, _, sid = str(entry).partition("/")
        if sid and where == space_name and sid not in out:
            out.append(sid)
    return out
```

In `collect`, loop over `routed_subjects(fm, space.name)` instead of the single `sid` (each known one gets the same `add` calls). After the thread pass, for every source page with `informs`: first gather `closes` ids into a set; then for each `open_item` whose page id is not closed, `add(sid, tier, scope, label, at, rel, pid, body, "open", direction=item.get("direction") or "internal")` with `label = text + (f" (owner {owner}, due {due})" | f" (owner {owner})" | f" (due {due})" | "")`. An informs-bearing page's thread is not also added as a thread open item (its informs replace it). Distill: in `_evidence`, `subjects = status.routed_subjects(fm, space.name)` (pass `space_name` through `gather_evidence`), plus `str(i.get("person"))` for each `informs` item of kind `person`; in `run`, for each new evidence with `informs` kind `decision`, call `write_decision` with a synthetic body `f"Decision: {text}\n\n{ev.body}"`.

- [ ] **Step 4: Run, expect PASS**; full suite.
- [ ] **Step 5: Commit.**

---

### Task 7: The morning view shows the router's reason

**Files:** Modify `_meta/tools/needs_you.py` (`_queue`); Test `_meta/tools/tests/test_needs_you.py`

- [ ] **Step 1: Failing test**

```python
def test_a_leaving_item_shows_why_it_waits(tmp_path):
    root = make_space(tmp_path / "ada")
    write_page(root, "_outbound/queue/p-0000ab01.md", id="p-0000ab01", type="source", title="Kickoff",
               **{"class": "window", "destination": "harbor", "stage_after": "2026-10-12T20:00:00Z",
                  "why": "pilot work; review mode until 2026-10-28"})
    items = [i for i in needs_you.collect(root, now=NOW) if i.kind == "leaving"]
    assert items[0].text.endswith("(pilot work; review mode until 2026-10-28)")
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement:** in `_queue`, `reason = _text(fm.get("why"), "")`, and the leaving text becomes `f"{title} leaves for {destination} at {pages.iso(stage_after)}" + (f" ({reason})" if reason else "")`.
- [ ] **Step 4: PASS**; full suite. **Step 5: Commit.**

---

### Task 8: Unsure records in the private zone

**Files:**
- Create: `_meta/tools/unsure.py`
- Test: `_meta/tools/tests/test_unsure.py`

**Interfaces:**
- Produces: `unsure.STORE = Path("_meta") / "state" / "private" / "unsure.jsonl"`; `unsure.key_of(record) -> str` (10 hex of sha1 of the record id); `unsure.waiting(root) -> list[dict]` (each record plus `key`, `first_listed`); `unsure.mark_listed(root, keys, now)`; `unsure.admit(root, key) -> bool` (appends the record, without `feed`, `key`, `first_listed`, to `_meta/state/feed/<feed>.jsonl` once; removes it from the store); `unsure.drop(root, key) -> bool`; `unsure.expire(root, now, days=14) -> int`; `unsure.line_for(record) -> str` (plain one-line label: sanitized subject, source, `%d %b` date).

- [ ] **Step 1: Failing tests**

```python
import datetime as dt
import json

import unsure
from conftest import NOW, make_space


def store(root, *records):
    path = root / unsure.STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


REC = {"id": "m-77", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z", "from": "x@y.example",
       "to": ["ada@lantern.example"], "subject": "Dinner — Friday? https://example.com/x", "text": "Body."}


def test_admit_moves_the_record_into_its_feed_once(tmp_path):
    root = make_space(tmp_path / "ada")
    store(root, REC)
    key = unsure.key_of(REC)
    assert [r["key"] for r in unsure.waiting(root)] == [key]
    assert unsure.admit(root, key) is True and unsure.admit(root, key) is False
    feed = (root / "_meta" / "state" / "feed" / "mail.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(feed) == 1 and json.loads(feed[0])["id"] == "m-77" and "feed" not in json.loads(feed[0])
    assert unsure.waiting(root) == []


def test_drop_and_expire(tmp_path):
    root = make_space(tmp_path / "ada")
    store(root, REC, dict(REC, id="m-78"))
    assert unsure.drop(root, unsure.key_of(REC)) is True
    unsure.mark_listed(root, [unsure.key_of(dict(REC, id="m-78"))], NOW)
    assert unsure.expire(root, NOW + dt.timedelta(days=13)) == 0
    assert unsure.expire(root, NOW + dt.timedelta(days=15)) == 1 and unsure.waiting(root) == []


def test_a_line_is_plain_and_short():
    line = unsure.line_for(REC)
    assert "—" not in line and "http" not in line and len(line) <= 80 and "mail" in line
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: unsure`).
- [ ] **Step 3: Implement** `unsure.py` with the functions above: `waiting` reads the store (one JSON per line, skipping bad lines) and adds `key`; every write rewrites the store atomically (write `.tmp`, `replace`); `admit` checks the feed for the id before appending; `line_for` replaces dashes with commas, removes URLs (`https?://\S+|www\.\S+`), drops tokens of 32+ characters, collapses spaces, cuts at 60 characters on a word, and appends `f" ({feed}, {at:%d %b})"`.
- [ ] **Step 4: PASS**; full suite. **Step 5: Commit.**

---

### Task 9: The morning email lists unsure records; the reply brings them in

**Files:**
- Modify: `_meta/tools/morning_view.py` (NOTICE, PAYLOAD paths; `run`), `_meta/tools/reply.py` (`run`)
- Modify: `.claude/skills/morning-view/SKILL.md`, `_meta/templates/routines/daily-chain.md` (payload path)
- Test: `_meta/tools/tests/test_morning_view.py`, `_meta/tools/tests/test_reply.py`

**Interfaces:**
- Consumes: `unsure.waiting`, `unsure.line_for`, `unsure.mark_listed`, `unsure.expire`, `unsure.admit`, `unsure.drop`.
- Produces: `morning_view.NOTICE = PRIVATE_STATE / "morning-notice.json"`, `morning_view.PAYLOAD = PRIVATE_STATE / "morning-payload.json"`; morning json items gain `{"id": "unsure-<key>", "kind": "unsure", "text": "a record to classify", "fingerprint": ""}` (no subject line in the tracked file); the notice gains one `also` item `{"title": "Is this work?", "text": "<n>. <line> ..."}`; reply verbs on an `unsure` item: `yes` admits, `no` drops, applied at once and never written to the answers page.

- [ ] **Step 1: Failing tests**

```python
# test_morning_view.py
def test_unsure_records_are_listed_in_the_email_only(tmp_path):
    import unsure
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    path = root / unsure.STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"id": "m-77", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z",
           "subject": "Dinner on Friday", "text": "Body."}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    report = morning_view.run(root, person="Ada Okafor", now=NOW)
    payload = json.loads(report.payload_path.read_text(encoding="utf-8"))
    assert "Dinner on Friday" in payload["body"] and "_meta/state/private/" in report.payload_path.as_posix()
    day = json.loads((root / "_meta" / "state" / "morning" / "2026-10-12.json").read_text(encoding="utf-8"))
    assert day["items"][-1]["kind"] == "unsure" and "Dinner" not in json.dumps(day)
    assert "Dinner" not in (root / "live" / "needs-you.md").read_text(encoding="utf-8")

# test_reply.py
def test_a_yes_on_an_unsure_item_brings_the_record_in_once(tmp_path):
    import unsure
    root = make_space(tmp_path / "ada", owner_email="ada@lantern.example")
    rec = {"id": "m-77", "feed": "mail", "kind": "mail", "at": "2026-10-11T08:00:00Z", "subject": "Dinner", "text": "B."}
    (root / unsure.STORE).parent.mkdir(parents=True, exist_ok=True)
    (root / unsure.STORE).write_text(json.dumps(rec) + "\n", encoding="utf-8")
    key = unsure.key_of(rec)
    (root / "_meta" / "state" / "morning").mkdir(parents=True)
    (root / "_meta" / "state" / "morning" / "2026-10-12.json").write_text(json.dumps(
        {"date": "2026-10-12", "items": [{"id": f"unsure-{key}", "kind": "unsure", "text": "a record to classify"}]}))
    feed = root / "_meta" / "state" / "feed" / "mail.jsonl"
    for n in (1, 2):
        with feed.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": f"r-{n}", "kind": "mail", "at": "2026-10-12T10:00:00Z",
                                 "from": "ada@lantern.example", "to": ["ada@lantern.example"],
                                 "subject": "Re: Morning briefing", "text": "1 yes"}) + "\n")
    reply.run(root, now=NOW)
    ids = [json.loads(l)["id"] for l in feed.read_text(encoding="utf-8").splitlines()]
    assert ids.count("m-77") == 1
    assert not list((root / "_inbound" / "answers").glob("*.md")), "nothing to sign: applied at once"
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**

`morning_view.run`: after `items = needs_you.collect(...)`, `unsure.expire(root, now)`, `waiting = unsure.waiting(root)`; build `lines = [f"{len(items) + i + 1}. {unsure.line_for(r)}" for i, r in enumerate(waiting)]`, keep as many as fit `owner_email.LIMITS["also_text"] - 40` characters, end with `"Reply with the number and yes or no."` and, when some did not fit, `f"{rest} more tomorrow."`; add `{"title": "Is this work?", "text": " ".join(kept)}` to `notice["also"]`; `unsure.mark_listed(root, listed_keys, now)`; when `owner_email.check(notice)` fails on that entry, replace each failing line by `f"{n}. a {r['kind']} from {r['at'][:10]}"`. The morning json appends `{"id": f"unsure-{r['key']}", "kind": "unsure", "text": "a record to classify", "fingerprint": ""}` for the listed ones. `NOTICE` and `PAYLOAD` move to `spaceyaml.PRIVATE_STATE`.
`reply.run`: split `chosen` into unsure ids and the rest; for unsure ids with `yes` call `unsure.admit(root, key)`, with `no` call `unsure.drop(root, key)`; write the answers page only when the rest is non-empty; the `SEEN` line records `{"unsure": {"admitted": n, "dropped": m}}`.
Skill and daily-chain text: the payload path becomes `_meta/state/private/morning-payload.json`.

- [ ] **Step 4: PASS**; full suite. **Step 5: Commit.**

---

### Task 10: Rules, defaults, decisions, docs, the route skill, onboarding

**Files:**
- Modify: `rules.md` (sections 4 and 6), `_meta/tools/spaceyaml.py` (DEFAULTS `left_out`), `docs/decisions.md`, `docs/shape-1.md`, `_meta/templates/space-personal.yaml`, `_meta/tools/onboard.py` (the personal `extra` block)
- Create: `.claude/skills/route/SKILL.md`, `_meta/tests/behaviour/route.yaml`
- Test: `_meta/tools/tests/test_spaceyaml.py`, `_meta/tools/tests/test_onboard.py`, `_meta/tools/tests/test_behaviour.py` (runs every scenario)

- [ ] **Step 1: Failing tests**

```python
# test_spaceyaml.py
def test_left_out_defaults_name_information_not_its_form():
    assert not {"attachment", "attachments", "transcript", "transcripts"} & set(spaceyaml.DEFAULTS["left_out"])
    assert {"price", "fee", "salary", "password"} <= set(spaceyaml.DEFAULTS["left_out"])

# test_onboard.py (beside the existing personal-space test that renders space.yaml)
def test_a_new_workspace_starts_routing_in_review_mode(tmp_path):
    # use the existing helper in this file that creates a personal space through onboard
    space = spaceyaml.load(new_personal_space(tmp_path))      # helper name from test_onboard.py
    assert space.routing_on and space.review_until is not None
    assert (space.review_until - space.now()).days in (13, 14)
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**

- `spaceyaml.DEFAULTS["left_out"]`: remove the four form words. Existing tests that used `transcript` as the left-out example switch to `day rate` (`test_a_call_summary_travels_without_its_transcript_line` becomes `..._without_its_day_rate_line`, its text line `Day rate: 950.`).
- `rules.md` section 4: "Prices, fees and other commercial terms; a person's employment, role, pay or immigration; credentials and access links; client data files; other clients." Section 6, after the first sentence: "Where `space.yaml` turns `routing` on, an agent reads each record in full and picks the tier: ordinary work for the destination goes at once, anything more sensitive waits one window and one morning view with its reason; never-leaves terms hold it, the high-stakes list and medium confidence force the window, low confidence asks the owner, and a review period (`routing.review_until`) holds everything for the window."
- `docs/decisions.md`: two rows dated 2026-10-14: "Judgment routing replaces attachment by identifier" (choice: an agent decides per record, identifiers are shortcuts, the decision is logged and reversible; reason: the owner's direction, records without identifiers never reached their project) and "The agent picks the release tier" (choice: at once for ordinary project work, the window for anything sensitive, gates enforced by `route.py`; reason: the owner's decision of 2026-10-14). Mark the 2026-10-11 and 2026-10-13 rows "(replaced 2026-10-14 where routing is on)".
- `docs/shape-1.md` `space.yaml` table: a `routing` row ("turns the judgment router on; `since` the oldest page it reads, `review_until` every projection waits the window until then").
- `onboard.py`: when it renders a personal space, `extra` gains `routing: {since: <today - 90 days>, review_until: <today + 14 days>}`.
- `.claude/skills/route/SKILL.md`: frontmatter `name: route`, description "Decide where each new witness page belongs ...". Body: When (after the sweep, when `routing:` is set; on request "route this"), Run (1. `python _meta/tools/route.py <root> pending` and read `_meta/state/private/route-pending.json`; 2. per record read the whole text, the spaces, the subjects and the status pages, write a decision in the spec's YAML shape into `_meta/state/private/decisions/<record>.yaml`; 3. `python _meta/tools/route.py <root> record _meta/state/private/decisions/*.yaml`; fix and rerun what is refused, once; then it becomes a question), Judgment (the spec's 4.2 and 2.3 rules: whole only when single-topic and everyone is inside; verbatim parts; leave out passages with left-out or never-leaves terms; tier at once for ordinary work, window when sensitive; low confidence when you would want the owner's eye; `informs` with owner, due and direction), Never (summaries in place of parts; routing personal material; editing a witness page; sending anything). Run line `{"routine": "route", ..., "notes": {"records": n, "routed": n, "private": n, "questions": n, "refused": n}}`.
- `_meta/tests/behaviour/route.yaml`: setup as `sweep.yaml` plus `routing: {}` in `space.yaml`, one witness page and a decisions file; `command: [python, _meta/tools/route.py, "{root}", record, decisions.yaml]`; expect exit 0, `writes_only_under: [knowledge/sources/, _outbound/, _meta/state/]`, no `status: stable`.

- [ ] **Step 4: PASS**; full suite; `python _meta/tools/validate.py . --template`; `python _meta/tools/no_company_names.py .`.
- [ ] **Step 5: Commit.**

---

### Task 11: Land, sync, and the first proof (operational)

- [ ] Land the template with `python _meta/tools/sync.py --root <template> --only <every changed path> --message "chore: sync"`; wait for `checks`; it auto-merges.
- [ ] For each space in `~/.spaces/spaces.yaml`: `template_sync.py <space> --guarded --from <template>`, then its `sync.py`; merge each `sync/<host>-protected` PR once `checks` pass.
- [ ] The owner's workspace `space.yaml` (protected, the owner's approved design): `routing: {since: <90 days back>, review_until: <his answer, two weeks by default>}`; drop the four form words from its `left_out`; the same `left_out` change in the first client project's `space.yaml`. Land through `sync.py`, merge the protected PRs after `checks`.
- [ ] First proof: re-export in full (connectors, lossless shape) every record about the first client project back to its first mention: mail threads, call transcripts, chat threads with files, calendar entries, documents and attachments; run `sweep.py` (refreshes the shallow pages); `route.py <root> pending --subject <id>`; decide each record (route skill); `route.py <root> record ...`; land with `sync.py`.
- [ ] Report: what was staged (item, record, destination, tier, reason), what was held and why, what became a question, counts of private and unsure records; the status page of the project after the window.

---

## Self-review

- Spec coverage: 4.1 capture (Tasks 2, 3, 8, 9), 4.2 routing (4), 4.3 gates (4, 5), 4.4 destination (6), 4.5 failures (4: refusals; skill text: retry once, then a question), 4.6 backfill and review mode (1, 4 `pending --subject`, 10 onboarding, 11), 4.7 table (every row has a task), section 5 tests (in each task; the fixture end to end is the `test_route.py` end-to-end test with a fixed decision, the same role as the fixture's verdicts file).
- Types: `routing.routes` entries are `"<space>/<subject>"` strings in Tasks 4 and 6; `informs` items carry `kind, text, owner, due, direction, closes, person` across Tasks 4 and 6; `unsure` keys are `key_of(record)` in Tasks 8 and 9.
- Review Focus lines each have a test: 1 (Task 3, own briefing), 2 (Task 4, whitespace), 3 (Task 4, not twice), 4 (Task 4, own space), 5 (Task 9, two replies).
