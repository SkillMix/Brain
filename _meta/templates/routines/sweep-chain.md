---
name: sweep-chain
schedule: "0 7,13,19 * * *"
description: Three times a day, export {person}'s feeds into their spaces on this machine, then run the sweep chain, the tracker step and the owner notices; the sync tasks land the files
---

You run the sweep chain for {person}'s spaces on this machine: every root that
`{python} {workspace}/_meta/tools/chain.py spaces {workspace}` prints, one after another, their
workspace `{workspace}` first. You never run git: each space's sync task merges, pins the mounts
and lands every file. For each root <root>:

1. Read `<root>/AGENTS.md` and `<root>/.claude/skills/sweep/SKILL.md`, and export as the skill
   says: for every source in `space.yaml` `sources`, the records newer than the feed file's
   last `at` (14 days on a first run) with the connector for that system (mail, calendar,
   chat, calls, tracker, documents: whichever are connected; a repository through `gh`) into
   `<root>/_meta/state/feed/<source>.jsonl`, one JSON object per line in the record shape the
   skill gives. A source that names `channels`, `team`, `project` or `repo` is read for exactly
   those. A space without sources exports nothing. Export in depth, each `text` capped at
   8,000 characters: a mail's full body (never the snippet) with `from`, `to` and `cc` as
   addresses; a document's content; a call's summary, action items and key points, each on
   its own line, never the transcript; an event's description with its guests as `participants`. Then read every
   attachment that `{python} <root>/_meta/tools/sweep.py <root> --attachments` lists into the
   same feed file as a document record of its own, as the skill says.
2. Run `{python} <root>/_meta/tools/chain.py sweep <root>` (sweep, distill, check). Then follow
   `.claude/skills/distill/SKILL.md` and `.claude/skills/check/SKILL.md` for the judgment only:
   improve each new candidate's body in plain language from its listed evidence only, and
   sharpen each check hit's recommendation. Never write `status: stable`; never edit a signed
   page; never read `personal/`, `academic/` or `_scratch/`.
3. In the workspace only: follow `.claude/skills/tracker/SKILL.md`: create or update what
   `{python} {workspace}/_meta/tools/tracker.py {workspace} pending` lists with the tracker
   connector, and record each. Write nothing else to the tracker.
4. Follow `<root>/.claude/skills/owner-email/SKILL.md`: send each notice that
   `{python} <root>/_meta/tools/owner_email.py pending --root <root>` lists, exactly as rendered,
   from {person}'s own mailbox to that same address, then record it with
   `owner_email.py record <file> --id <message id> --root <root>`.
5. Landing health, as `.claude/skills/sync/SKILL.md` says: when the newest `"routine": "sync"`
   line in `<root>/_meta/state/runs.jsonl` is older than an hour or shows `errors`, `conflict`
   or `scan_hits`, run `{python} <root>/_meta/tools/sync.py --root <root> --release-leg` once.
   Never resolve a conflict, remove a lock or edit a scan-flagged file: name what stays broken
   in your run line under `landing`.
6. Append one run line to `<root>/_meta/state/runs.jsonl`: `{{"routine": "sweep-chain", "at":
   <ISO time>, "space": <name>, "cost_tokens": <your estimate>, "notes": {{"step": "agent",
   "complete": true or false, "unreachable": [<sources you could not read>], "landing": [<what
   stays broken>]}}}}`.

Commit nothing, push nothing, merge nothing. Send nothing to anyone but the owner notices of
step 4, to {person} themselves; post nothing anywhere. If a connector is unavailable, skip that
source, name it under `unreachable` and continue. Finish with one line per space: records
exported per source, witness pages, candidates, checks, issues created, notices sent, landing
health.
