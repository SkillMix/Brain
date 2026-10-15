# A year of history and a project register: design

Status: approved by the owner, 2026-10-15 ("implement the things we discussed, including the
new rules regarding backfill period"). Date: 2026-10-14, revised 2026-10-15 after the check
against the vision.
Builds on: `2026-10-14-judgment-routing-design.md` (section 4.6, backfill) and works with
`2026-10-15-understand-first-design.md` (briefs, research, checked pages).

## 1. Why

The router went live on 2026-10-14, but it only judges what the workspace holds:

- **Short and shallow.** The owner's feeds reached back two and a half weeks, a mail record's text
  a 200-character median, one call in 32 with its transcript; 864 witness pages waited for a
  decision, built from snippets. The scheduled sweep chain is paused until the backfill is done.
- **No company memory.** Asked "what projects have we done", the brain answers with seven names,
  every one marked active, nothing signed, nothing about who, when or how it ended.

The owner's direction: read a year back, "where the last 90 days work as it currently does", the
older months "also swept and taken into consideration, BUT the questions and signing
significantly lower", "only on super high level", and the brain must answer "what are the
projects we did".

## 2. The owner's decisions (2026-10-14 and 15)

1. **One year back, two depths.** The last 90 days get the full judgment of the routing design.
   Older records are *history*: captured as fully, judged as background.
2. **Signing grows with activity, not with history** (the vision, section 9). History asks no
   question per record and opens no items. Subjects that are active, important or asked about
   get candidates as always; a past engagement is listed on the projects page, marked
   unconfirmed, and becomes a question only when it matters (it reappears in recent records, or
   the question log shows it answered from evidence alone).
3. **The projects page is the register.** The brain's generated `live/projects.md` answers "what
   projects have we done": every project, client and deal with state, period, client, crew and
   the kind of work.
4. **Count before reading.** A metadata-only count decides how far back activity goes; history
   starts there, never more than a year back.
5. **The archive stays out for now** (`routing.archives` off): the owner wants to measure the
   space on its own records first.
6. **Look for context, quality over cost**; **session notes included** (understand-first spec).
7. **A pilot first:** one client with two projects (understand-first spec, section 4.10).

## 3. Approaches considered

- **A. 90 days only.** No memory of anything older; the register stays empty.
- **B. A year at full judgment.** Hundreds of stale to-dos and a signing queue nobody clears.
- **C. A year, two depths (chosen).** Full capture everywhere, full judgment where facts are
  current, background where they are not, and signatures only where the vision puts them.

## 4. Design

### 4.1 Phases

| Phase | What |
|---|---|
| 0. Hold | The scheduled sweep chain stays paused; the backfill session runs the chain itself and turns it back on at the end; the daily chain keeps running |
| A. Count | Records per month per source that the door would call company, from metadata only, into `_meta/state/private/activity-count.yaml`; sets `history_since` |
| B. Recent | `routing.since` to now: lossless re-export, sweep, understand-first routing, distill rebuilt |
| C. History | `routing.history_since` to `routing.since`: the same, under the history rules |
| D. Close | The projects page, candidates for active and important subjects, the sweep chain back on |

The backfill is the `seed` skill run again at full depth over this window; B and C run oldest
first, source by source, in two-week slices.

```yaml
routing:
  since: <90 days back>          # recent: full judgment
  history_since: <start month>   # history: background; unset means no history window
  archives: false                # archive-derived pages stay out of briefs and pages
```

### 4.2 Capture

The sweep's record shape, depth and private-at-the-door rule apply unchanged; re-exported
records keep their ids, so the sweep refreshes each shallow page in place. For history, the
export narrows by metadata first (the mail connector's categories, the `transcripts`
exclude words, known academic senders) and reads in full what remains; the door still decides
each record, and what it drops is counted, never stored.

### 4.3 History rules

A record whose `at` is before `routing.since` (and not before `routing.history_since`) is
history; `route.py pending` marks it `history: true` and lists it only inside that window.
`route.py record` enforces:

| Decision | For history |
|---|---|
| `private` | yes |
| `route`, whole or parts | yes: background on the destination's timeline |
| `informs` kind `open_item` | refused ("history opens no items") |
| `informs` kind `decision`, `person`, `page` | yes; a decision is historical, with its date |
| `informs` key `closes` | yes |
| `confidence: low` | recorded as private and counted (`history_low`), never a question |

Recent beats old: a history line that contradicts a recent record or a signed page shows on the
destination's timeline marked superseded, never in Open and never as a question. The gates of
the routing design are unchanged; the morning view counts history projections in one line.

### 4.4 The register

- **Subjects** carry `state` (projects: `active`, `delivered`, `stopped`; deals: `open`, `won`,
  `lost`), `period` (`from`, `to`, months), `client`, `crew` (people ids) and `work` (one line).
  State is status, never signed: the status step sets it from evidence on every run. The signed
  page holds the facts that do not change (understand-first spec, page shapes).
- **New engagements** found in records go through the librarian's proposals (reviewed by a
  second agent), never a new path.
- **`live/projects.md`** (generated by status, never signed): one line per project, client and
  deal with state, period, client, crew and work; a subject without a signed page is marked
  unconfirmed. The query skill reads it first for "what projects", "who worked on", "have we done".

### 4.5 Answers

"Status of" and "prep me for the call with" read the subject's brief and the newest records,
then the pages; history lines are labelled background, unconfirmed and superseded lines marked.

## 5. What changes

| Where | Change |
|---|---|
| `spaceyaml.py` | `routing.history_since`, `routing.archives` |
| `route.py` | `history: true` and the window; the rules of 4.3 |
| `registry.py` | `period`, `client`, `crew`, `work` |
| `status.py` | the projects page as the register (4.4) |
| seed, sweep, route, query skills | the phases, the count, the history pre-filter, history in answers |

## 6. Testing

- A history record with an `open_item` is refused; low confidence on history records as private
  with no question; `closes` from history is accepted; records before `history_since` are not
  listed; without `history_since` nothing changes.
- The projects page lists every project with state, period, client, crew and work, and marks a
  subject without a signed page unconfirmed.

## 7. Out of scope

The archive (switch off); mail attachments the connector cannot open; amounts on any page
bound for a wider audience (they stay on the left-out lists).
