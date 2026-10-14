---
name: route
description: Decide where each new witness page belongs - private in this workspace, or which space and subject it informs, at once or after the window, whole or as verbatim parts, and what it updates there - then record each decision with route.py, which enforces the gates. Use right after the sweep when space.yaml has routing, when the owner asks to route a record, or to backfill a project.
---

# route: judgment in, gates in the tool

The sweep captured every record in full. You read each one and decide where it belongs; the
tool checks your decision, writes the projections and logs it. Depth over thrift: read the whole
record and the destination's pages before you decide. Nothing is lost by a decision: the original
stays here, and a route can be retracted.

## When

- Scheduled: step 4 of the sweep skill, when `space.yaml` has `routing:`.
- On request: "route this", "where did the kickoff call go?", "backfill <project>".
- Backfill of a project: `--subject <id>` reaches back to the first mention, however old.

## Run

1. `python _meta/tools/route.py <root> pending [--subject <id>]`. Read
   `_meta/state/private/route-pending.json`: `records` (each with its full `text`, people,
   thread and the subjects its identifiers `suggested`), `spaces` (name, audience, left-out
   terms), `subjects` (homes, aliases) and `status_pages` (each subject's current page: open
   items and their ids). Records of one thread are listed together: read them together.
2. For each record, write a decision to `_meta/state/private/decisions/<record id>.yaml`:

```yaml
record: <record id>
decision: private | route
confidence: high | medium | low
why: <one or two sentences>
routes:
  - space: <the subject's home>
    subject: <subject id>
    tier: at_once | window
    why: <why this space; why this tier>
    content: whole | parts
    parts:
      - text: <a passage or transcript segment copied exactly, whole lines>
        note: <optional: why this part is here, one line>
    informs:
      - kind: open_item | decision | person | page
        text: <one line>
        owner: <name>            # open items, when known
        due: <YYYY-MM-DD>        # when the record says
        direction: we owe | waiting on them | internal
        closes: <open item id from the status page, when this record settles it>
        person: <person subject id>   # kind person
```

3. `python _meta/tools/route.py <root> record _meta/state/private/decisions/*.yaml`. A recorded
   file moves to `decisions/done/`; a record that already has a decision is skipped. A refused
   decision prints why and its file stays: fix it once and record again; refused twice, record it
   with `confidence: low` so it becomes a question for the owner (the owner's answer through
   `sign` applies it). The tool checks everything that would travel: the parts, what the
   projection informs (text and owner) and your `why`.
   To undo a route: `python _meta/tools/route.py <root> retract <record> --by <name>` (queued
   projections are withdrawn, shipped ones retracted); the record can then take a new decision.
   To change a decision that still waits without touching what already left, add `--queued-only`.
4. Your run line in `_meta/state/runs.jsonl`: `{"routine": "route", "at": "<ISO UTC>", "space":
   "<name>", "cost_tokens": <n>, "notes": {"records": n, "routed": n, "private": n, "questions":
   n, "refused": n}}`.

## Judgment left to you

- **Where.** Which spaces need to know this, for which subject. A record can inform several
  spaces; each gets only what it needs. Identifiers are suggestions, never the decision. Session
  notes and chats count like any record. A record that matters only to the owner stays private.
- **What travels.** `whole` when the record is about one subject and everyone on it is inside the
  destination's audience (its crew, and the client's own people for a client project); the tool
  refuses `whole` otherwise. Else `parts`: the relevant passages and transcript segments verbatim,
  whole lines, never a summary in their place. Leave out every passage with a left-out term of
  the destination (prices, fees, pay, credentials, client data, other clients) or a never-leaves
  term: the tool holds a projection that carries one.
- **Tier.** `at_once` for ordinary work of that space (a client writing to the team about the
  work, a meeting of the crew). `window` when it is more sensitive: a person's situation, money,
  a disagreement, anything the owner may want to see leave. Say why in `why`: the morning view
  shows it.
- **Confidence.** `high` when you would defend the route to the owner; `medium` when another
  reading is plausible (it waits the window); `low` when you would want the owner's eye (it
  becomes a question, nothing leaves).
- **Informs.** What the destination's pages should say now: the open items with owner, due date
  and who moves next (`we owe`, `waiting on them`, `internal`), a decision taken, a fact about a
  person, an update to a page. Close an item the status page shows when this record settles it.

## Never

- Never paraphrase into `parts`; never route personal or academic material; never edit a witness
  page or a queue item by hand; never write `status: stable`; never send or post anything.
- Never route to a space that is not the subject's home: register the subject first (the
  librarian) or keep the record private.
