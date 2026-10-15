---
name: route
description: Decide where each new witness page belongs - private in this workspace, or which space and subject it informs, at once or after the window, whole or as verbatim parts, and what it updates there - after first understanding each subject it touches (its brief, research for what the records leave open), then record each decision with route.py, which enforces the gates. Use right after the sweep when space.yaml has routing, when the owner asks to route a record, or to backfill a subject.
---

# route: understand first, judgment in, gates in the tool

The sweep captured every record in full. Before you decide where a record goes, you understand
the subject it is about: what it is, who is on it, how it started, where it stands. Then you
research what the new records leave open, then you place them. The tool checks each decision,
writes the projections and logs it. Depth over thrift: tokens are not the constraint, the
quality of the decision is. Nothing is lost by a decision: the original stays here, and a route
can be retracted.

## When

- Scheduled: step 4 of the sweep skill, when `space.yaml` has `routing:`.
- On request: "route this", "where did the kickoff call go?", "backfill <subject>".
- Backfill of a subject: `--subject <id>` reaches back to the first mention (bounded by
  `routing.history_since` when it is set). Each decision then carries `scope: [<the backfilled
  subject ids>]`: it decides the record for those subjects only, and a record that also informs
  other subjects stays pending for them (`decided_for` in the next pending).

## Run

1. **Pending.** `python _meta/tools/route.py <root> pending [--subject <id>]`. Read
   `_meta/state/private/route-pending.json`: `records` (each with its full `text`, people,
   thread, `episode`, `history`, `same_meeting` and the subjects its identifiers `suggested`),
   `spaces` (name, audience, left-out terms), `subjects` (homes, aliases), `status_pages`,
   `briefs` (subjects that already have one), `lessons` and `archives`. Read `lessons` first:
   each is a correction the owner made once and never wants to make again.
2. **Triage.** For every record, name the subjects it may be about: identifiers, names,
   aliases, mail domains, the `suggested` list. A name the registry does not know is a research
   item, not a reason to keep the record private.
3. **Understand each subject** before placing any of its records. With a brief: read it. Without
   one: `python _meta/tools/brief.py <root> pack <subject>`, read every page the pack lists, in
   full (titles are not enough), and write `_meta/state/private/briefs/<subject>.md` with the
   sections of `_meta/templates/brief.md`. The brief is private and holds everything, sensitive
   facts included; only what fits a destination ever leaves.
4. **Research** what the records leave open: an unknown person or company, "as discussed in
   August", a project name you cannot place, a reference to a document. Search this space's
   witness pages and journal, the spaces you can read (their signed and status pages), then the
   connectors within `space.yaml` sources (mail by sender, domain and terms; chat search; the
   call recorder's search; documents). Export every record a search finds into its feed in the
   sweep's record shape with `"research": "<subject id>"` (it gets a witness page from the next
   `sweep.py` and waits for that subject even when it never names it). Keep going until the
   gap is closed or a search finds nothing new; there is no fixed number of searches. Fold what
   you learned into the brief, and log the searches in its Research log. The archive is read only
   when `archives` is true.
5. **Place.** Read each episode as one event (a calendar entry, its recordings, the follow-up
   mail; `same_meeting` marks a second recording of one meeting). For each record write a
   decision to `_meta/state/private/decisions/<record id>.yaml`:

```yaml
record: <record id>
decision: private | route
scope: [<subject ids>]         # a subject backfill only: decided for these subjects, open for others
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

6. **Check.** A second agent (a fresh subagent, or you in a separate pass with the brief closed)
   reads at least one decision in five, every `window` route and every `whole` route against its
   record: is the subject right, is every part verbatim and needed, is anything sensitive leaving?
   Fix what it finds before recording.
7. **Record.** `python _meta/tools/route.py <root> record _meta/state/private/decisions/*.yaml`.
   A recorded file moves to `decisions/done/`; a record that already has a decision is skipped.
   A refused decision prints why and its file stays: fix it once and record again; refused
   twice, record it with `confidence: low` so it becomes a question for the owner. The tool
   checks everything that would travel: the parts, what the projection informs and your `why`.
   To undo a route: `python _meta/tools/route.py <root> retract <record> --by <name>` (queued
   projections are withdrawn, shipped ones retracted); add `--queued-only` to change a decision
   that still waits without touching what already left.
8. Your run line in `_meta/state/runs.jsonl`: `{"routine": "route", "at": "<ISO UTC>", "space":
   "<name>", "cost_tokens": <n>, "notes": {"records": n, "routed": n, "private": n, "questions":
   n, "refused": n, "history_low": n, "briefs_built": n, "context_searches": n,
   "context_records": n}}`.

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
  becomes a question, nothing leaves). Research first: low confidence is for what research
  could not settle, never for what you did not look up.
- **Informs.** What the destination's pages should say now: the open items with owner, due date
  and who moves next (`we owe`, `waiting on them`, `internal`), a decision taken, a fact about a
  person, an update to a page. Close an item the status page shows when this record settles it.
- **History** (`history: true`: before `routing.since`). Background only: route it to the
  subject's home for its timeline, or keep it private. Never an `open_item` (the tool refuses
  one); `closes`, decisions (dated, historical) and people are fine. Low confidence on history
  is recorded as private, never a question. When it contradicts a recent record or a signed
  page, recent wins: say so in `why`.

## Never

- Never paraphrase into `parts`; never route personal or academic material; never edit a witness
  page or a queue item by hand; never write `status: stable`; never send or post anything.
- Never route to a space that is not the subject's home: register the subject first (the
  librarian) or keep the record private.
- Never ask the owner what a search could answer; never read the archive while `archives` is off.
- Never let the brief travel: it stays in `_meta/state/private/briefs/`.
