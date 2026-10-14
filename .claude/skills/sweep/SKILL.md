---
name: sweep
description: Export new records from this space's own connected sources into feed files, then run sweep.py, which writes witness pages, attaches evidence by identifier, classes releases and queues them. Use as the first step of the scheduled sweep chain, or when the person asks to sweep their feeds.
---

# sweep: evidence in

A space sweeps only the sources its audience owns (`space.yaml: sources`). The brain
sweeps nothing of its own. You export; the tool decides.

## When

- Scheduled: `routines.sweep` in `space.yaml` (default 01:00, 10:00, 16:00 UTC), first
  step of the sweep chain (sweep, then distill, then check).
- On request: "sweep my mail", "anything new from the calendar?". No `sources`: nothing to do.

## Run

1. For each entry in `space.yaml: sources` (`name`, `adapter`, `audience`, and for a project's
   own sources what to read: `channels`, `team` and `project`, `repo`; read only those), read
   with your connector for that system the records newer than the last `at` already in
   `_meta/state/feed/<name>.jsonl` (no file yet: the last 14 days) and append one JSON
   object per line:
   `{"id", "source", "kind": mail|call|message|event|document, "at": ISO, "from",
   "to": [...], "subject", "text", "identifiers": {"thread", "channel", "labels": [...],
   "repo", "tracker", "calendar"}, "attachments": [...], "audience": owner|crew|everyone,
   "participants": [...]}`; a tracker record adds `"issue"` (its key) and `"state"`, and its `id`
   is the key with the issue's last update (`SM-224@2026-10-02T15:46:23Z`), so each change to an
   issue is a record of its own and the newest one decides its state.
   Per kind: mail = messages sent, received or copied to the person (`thread`, `labels`);
   Mail and messages always carry `from` and `to` as addresses: without them no status page
   knows who owes whom. call = a recorded meeting, `text` its transcript or summary, `participants` everyone on
   it; message = chat DMs and channels (`channel`); event = calendar entries (`calendar`);
   document = a new or changed file, `text` an excerpt, the file only as a pointer in
   `attachments`; issue = a tracker issue created, changed or closed (`identifiers.tracker` the
   project, `issue`, `state`); change = a pull request merged or a release in the repository
   (`identifiers.repo`, `text` its title and description), never the space's own machinery
   pull requests (`chore: sync`, `chore: pins`, `chore: release`, `chore: structure`). Export
   them with `gh pr list -R <repo> --state merged --search "merged:>=<since> NOT chore in:title"
   --limit 200 --json number,title,body,mergedAt,baseRefName,author` (the search, not a filter
   afterwards: the machinery pull requests outnumber the rest and would fill any limit), `id`
   `<repo>#<number>`. `audience` is the source's declared audience, copied as is. `id` is the
   system's own id, stable across runs.
2. `python _meta/tools/sweep.py <root>` (`--dry-run` prints the plan and writes nothing).

## What the tool already did

Witness pages `knowledge/sources/<yyyy>/<mm>/<record-id>.md` (drafts, never signed);
attachment to the one subject whose `match:` identifier the record carries (none or several:
no attachment, names in the text kept as hints); the release class for records whose subject
has a `home` elsewhere (at once, window, or held: doubt, left_out, never_leaves); queue
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
- Never a secret in a feed file (hard rule 1): a key or password is exported as `[redacted]`,
  and the record carries `"redacted": true`; the sweep then queues one security notice to the
  person (where and when, never the value).
- A link is not a secret: links to Google files, calendars and meetings (share keys, `usp`,
  `resourcekey` and `token` parameters included), Zoom and Teams meeting links with their
  passcode, and Claude artifact links stay in the record as they are; never redact one and never
  mark its record `redacted`. Redact only the key, token or password itself.

## Scheduled invocation

```
Run the sweep chain for the space at <root> as <person>.
1. Read <root>/AGENTS.md and <root>/rules.md, then <root>/.claude/skills/sweep/SKILL.md
   and follow it exactly: export new records for every source in space.yaml into
   _meta/state/feed/<source>.jsonl with the record shape, then run
   `python _meta/tools/sweep.py <root>`.
2. Then follow <root>/.claude/skills/distill/SKILL.md and <root>/.claude/skills/check/SKILL.md
   in that order (they read what the sweep wrote).
3. Append your run line to _meta/state/runs.jsonl. Commit nothing; the sync script lands it.
4. Report counts only: records exported per source, witness pages, queued, held in doubt.
```

## Behaviour scenarios

`_meta/tests/behaviour/sweep.yaml`: a fixture feed in, witness pages and queue items out;
writes only under `knowledge/sources/`, `_outbound/`, `_meta/state/`; no `stable`; no send.
