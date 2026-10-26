---
name: sweep
description: Export new records from this space's own connected sources into feed files, then run sweep.py, which writes witness pages, attaches evidence by identifier, classes releases and queues them. Use as the first step of the scheduled sweep chain, or when the person asks to sweep their feeds.
---

# sweep: evidence in

A space sweeps only the sources its audience owns (`space.yaml: sources`). The brain
sweeps nothing of its own. You export; the tool decides.

## When

- Scheduled: 07:00, 13:00 and 19:00 on the machine's own clock (the sweep-chain routine), first
  step of the sweep chain (sweep, then distill, then check).
- On request: "sweep my mail", "anything new from the calendar?". No `sources`: nothing to do.

## Run

1. For each entry in `space.yaml: sources` (`name`, `adapter`, `audience`, and for a project's
   own sources what to read: `channels`, `team` and `project`, `repo`; read only those), read
   with your connector for that system the records newer than the last `at` already in
   `_meta/state/feed/<name>.jsonl` (no file yet: the last 90 days) and append one JSON
   object per line:
   `{"id", "source", "kind": mail|call|message|event|document, "at": ISO, "from",
   "to": [...], "cc": [...], "subject", "text", "identifiers": {"thread", "channel", "labels": [...],
   "repo", "tracker", "calendar"}, "attachments": [...], "audience": owner|crew|everyone,
   "participants": [...]}`; a tracker record adds `"issue"` (its key) and `"state"`, and its `id`
   is the key with the issue's last update (`SM-224@2026-10-02T15:46:23Z`), so each change to an
   issue is a record of its own and the newest one decides its state.
   Capture is lossless: no character cap, no preview, no snippet. Per kind: mail = one
   message sent, received or copied to the person (`thread`, `labels`), `text` its full body
   (quoted history included), the file names in `attachments`. Mail and messages always carry
   `from`, `to` and `cc` as addresses: without them no status page knows who owes whom. call =
   one recorded meeting, `subject` the meeting's own title, `text` the recorder's summary, action
   items and key points (each owner and date kept), then the full transcript, one line per
   utterance `[hh:mm:ss] Speaker: words`, `participants` everyone on it by address; message =
   one chat thread (`channel`), every message with its author and time, its files read with the
   chat connector's file reader, and AI notes and canvases as `document` records of their own;
   event = one calendar entry (`calendar`), `subject` the title, `text` the description,
   `participants` the guests by address; document = one new or changed file, `text` its full
   text, the file only as a pointer in `attachments`; issue = a tracker issue created,
   changed or closed (`identifiers.tracker` the project, `issue`, `state`); change = a pull request merged or a release in the repository
   (`identifiers.repo`, `text` its title and description), never the space's own machinery
   pull requests (`chore: sync`, `chore: pins`, `chore: release`, `chore: structure`). Export
   them with `gh pr list -R <repo> --state merged --search "merged:>=<since> NOT chore in:title"
   --limit 200 --json number,title,body,mergedAt,baseRefName,author` (the search, not a filter
   afterwards: the machinery pull requests outnumber the rest and would fill any limit), `id`
   `<repo>#<number>`. `audience` is the source's declared audience, copied as is. `id` is the
   system's own id, stable across runs. A backfill (a person's first 90 days, or a project's
   history back to its first mention) re-reads older records in full with the same `id` and
   adds `"backfill": true`: the sweep reads them behind the feed's mark and deepens their pages.
   A record exported by research for a subject carries `"research": "<subject id>"`; its page
   keeps it, so the router decides it for that subject even when the text never names it.
   Private at the door: before writing a record decide whether it is `company`, `personal`,
   `academic` or `unsure`. Only a `company` record is written to the feed. Count `personal` and
   `academic` records per source in your run line (`notes.skipped`) and write them nowhere. Write
   an `unsure` record, with `"feed": "<source name>"` added, to
   `_meta/state/private/unsure.jsonl`: a local private zone the sync never stages; the owner's
   morning email asks about it and one word brings it in. The workspace's own briefings and
   notices (from the owner to the owner) stay in the feed for the reply reader; the sweep never
   makes pages of them.
2. Attachments: `python _meta/tools/sweep.py <root> --attachments` lists, one per line,
   `<record id> <subject> <file name>` for every attachment not read yet (with `routing:` in
   `space.yaml`, on every company record; without it, on threads of exactly one subject). Read
   each, in this order: the mail connector's attachment reader; else the browser, opening the
   message in the mail web app and reading the attachment in its preview, never downloading;
   else leave it for the daily list the owner approves. Append it to the same feed file as a
   document record of its own: `id` `<record id>/<file name>`, `subject` the file name as it is,
   `text` its full text (a file with no text, such as an image: empty `text`, so it is not listed
   again; it gets no witness page), `read_via: connector | preview | approved_batch`, and `from`, `to`, `cc`, `at` and
   `identifiers` copied from its mail.
3. `python _meta/tools/sweep.py <root>` (`--dry-run` prints the plan and writes nothing).
4. With `routing:` in `space.yaml`: route what the sweep wrote, as
   `.claude/skills/route/SKILL.md` says. The sweep itself then releases nothing.

## Saving as you read, the history read and the lock

- **Oldest first, in batches.** Export each source oldest first and append to its feed file
  at most 50 records per batch, so a run that stops (a limit, a closed lid, the hour's timeout)
  keeps what it read and the next run goes on from there.
- **While the person's history is read** (`_meta/state/backfill.yaml` has a part not done), the
  workspace's own sources are read by part, never in one unpaced run (decision F). The part
  this run may read is the one `python _meta/tools/backfill.py <root> due --json` names; each
  source starts at `python _meta/tools/backfill.py <root> mark <source>` (that part's
  checkpoint, else its start), not at the feed file's last `at`, and ends at the part's `to`.
  After every batch: `python _meta/tools/backfill.py <root> checkpoint <part> <source> <the
  newest record's at>`. With no part due, export nothing new for those sources and say so in
  your run line, except while the plan is held for the person (`due --json` says `held`: a paid
  account's plan waiting for their go, or notes that could not be read): then the history is
  not read, but what is new is, from the later of the feed file's last `at` and what `mark`
  prints (the day the plan was made). Once every part is done, `mark` prints nothing and the
  feed file's last `at` takes over again. A feed with no file yet starts at the plan's first-read window, 90 days by
  default.
- **One sweep at a time.** Before a sweep someone asks for in a session, run
  `python _meta/tools/routine.py --locked <workspace>`. When it prints a run, a routine holds
  the workspace's lock and is sweeping now: say so in one line and export nothing. A routine
  run holds the lock itself and skips this check.
- **A personal workspace's documents** are the person's own files only (the files they own or
  made), never a company-wide file search.

## What the tool already did

Witness pages `knowledge/sources/<yyyy>/<mm>/<record-id>.md` (drafts, never signed; a deeper
re-export refreshes a page in place, a page over 900 KB is split into parts, the workspace's
own notices never become pages). With `routing:` in `space.yaml` that is all: attachment by
identifier stays a suggestion for the router and nothing is queued. Without it, as before:
attachment to the one subject whose `match:` identifier the record carries (a fixed
identifier, a client's mail domain on any address, a keyword in a call's or event's title;
none or several: no attachment, names in the text kept as hints), and the same again for older
pages once the registry learns a new identifier; the release class for records whose subject
has a `home` elsewhere: at once only when the destination's audience already reads the
source; everything else held in doubt for its person's yes, because nothing has read what it
says (a standing choice makes yes the recommendation, never the answer); queue
items in `_outbound/queue/`, held items in `_outbound/held/` with a reason; content-free
signals in `_outbound/signals/`; the watermark; one run line.

## Judgment left to you

None on classification: the tool decides, doubt stays home and is shown as a count on Needs
you. Your only call is a broken export: a connector you could not reach (export nothing for
it, say so in your run line, continue with the others) or a record that is not valid JSON
(fix the export, rerun). Then your run line in `_meta/state/runs.jsonl`:
`{"routine": "sweep", "at": "<ISO UTC>", "space": "<name>", "cost_tokens": <n>,
"notes": {"step": "export", "sources": {"<name>": <records or "unreachable">}}}`.

## Never

- Never write a record from memory, edit a record's text, or drop one because it looks
  private: the scrub and the release rule decide, in the tool.
- Never reclassify, move or edit a queue, held or signal item; never write into another
  space (release does that, through the sync script's release leg).
- Never write `status: stable`; never send, post, spend or delete; never run git.
- Never a secret in a feed file (hard rule 1): an obvious password or key (a labelled password,
  an API key, a private key, a login someone typed out) is exported as `[redacted]` with its label
  kept in front (`Password: [redacted]`), and the record carries `"redacted": true`; the sweep then
  queues one security notice to the person (where and when, never the value).
- A token in a link is no password. In a verification, unsubscribe, invitation, signing,
  ticket or tracking link, cut only the token to `[link-token]` and keep the rest of the link; never
  mark the record `redacted` for it. A meeting passcode or PIN, and a one-time verification code,
  are no password either: export a one-time code as `[code]`, leave the rest as it is. The sweep
  cuts any link token you missed and warns only about obvious passwords and keys.
- A link is not a secret: links to Google files, calendars and meetings (share keys, `usp`,
  `resourcekey` and `token` parameters included), Zoom and Teams meeting links with their
  passcode, and Claude artifact links stay in the record as they are; never redact one and never
  mark its record `redacted`.

## Scheduled invocation

```
Run the sweep chain for the space at <root> as <person>.
1. Read <root>/AGENTS.md and <root>/rules.md, then <root>/.claude/skills/sweep/SKILL.md
   and follow it exactly: export new records for every source in space.yaml into
   _meta/state/feed/<source>.jsonl with the record shape and its depth, read what
   `python _meta/tools/sweep.py <root> --attachments` lists, then run
   `python _meta/tools/sweep.py <root>`.
2. Then follow <root>/.claude/skills/distill/SKILL.md and <root>/.claude/skills/check/SKILL.md
   in that order (they read what the sweep wrote).
3. Append your run line to _meta/state/runs.jsonl. Commit nothing; the sync script lands it.
4. Report counts only: records exported per source, witness pages, queued, held in doubt.
```

## Behaviour scenarios

`_meta/tests/behaviour/sweep.yaml`: a fixture feed in, witness pages and queue items out;
writes only under `knowledge/sources/`, `_outbound/`, `_meta/state/`; no `stable`; no send.
