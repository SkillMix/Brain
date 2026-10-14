---
name: sweep-chain
schedule: "0 7,13,19 * * *"
description: Three times a day, export {person}'s feeds into their workspace, then run the sweep chain, the tracker step and the owner notices; the sync task lands the files
---

You run the sweep chain for {person}'s workspace at {workspace}. You never run git: the sync
task on this machine merges, pins the mounts and lands every file.

1. Read `{workspace}/AGENTS.md` and `{workspace}/.claude/skills/sweep/SKILL.md`, and export
   as the skill says: for every source in `space.yaml` `sources`, the records newer than the
   feed file's last `at` (14 days on a first run) with the connectors this session has
   (mail, calendar, chat, calls, tracker, documents: whichever are connected; a repository
   through `gh`) into
   `_meta/state/feed/<source>.jsonl`, one JSON object per line in the record shape the skill
   gives, with `from` and `to` on every mail record.
2. Run `{python} {workspace}/_meta/tools/chain.py sweep {workspace}` (sweep, distill, check).
   Then follow `.claude/skills/distill/SKILL.md` and `.claude/skills/check/SKILL.md` for the
   judgment only: improve each new candidate's body in plain language from its listed
   evidence only, and sharpen each check hit's recommendation. Never write `status: stable`;
   never edit a signed page; never read `personal/`, `academic/` or `_scratch/`.
3. Follow `.claude/skills/tracker/SKILL.md`: create what `{python} {workspace}/_meta/tools/tracker.py
   {workspace} pending` lists with the tracker connector, and record each. Create nothing else.
4. Follow `.claude/skills/owner-email/SKILL.md`: send each notice that
   `{python} {workspace}/_meta/tools/owner_email.py pending --root {workspace}` lists, exactly as
   rendered, from {person}'s own mailbox to that same address, then record it with
   `owner_email.py record <file> --id <message id> --root {workspace}`.
5. Landing health, as `.claude/skills/sync/SKILL.md` says: when the newest `"routine": "sync"`
   line in `_meta/state/runs.jsonl` is older than an hour or shows `errors`, `conflict` or
   `scan_hits`, run `{python} {workspace}/_meta/tools/sync.py --root {workspace} --release-leg`
   once. Never resolve a conflict, remove a lock or edit a scan-flagged file: name what stays
   broken in your run line under `landing`.
6. Append one run line to `_meta/state/runs.jsonl`: `{{"routine": "sweep-chain", "at": <ISO
   time>, "space": <name>, "cost_tokens": <your estimate>, "notes": {{"step": "agent",
   "complete": true or false, "unreachable": [<sources you could not read>], "landing": [<what
   stays broken>]}}}}`.

Commit nothing, push nothing, merge nothing. Send nothing to anyone but the owner notices of
step 4, to {person} themselves; post nothing anywhere. If a connector is unavailable, skip
that source, name it under `unreachable` and continue. Finish with one line: records exported
per source, witness pages, candidates, checks, issues created, notices sent, landing health.
