---
name: sweep-chain
schedule: "0 7,13,19 * * *"
description: Three times a day, export {person}'s feeds into their workspace, then run the sweep chain and the tracker step; the sync task lands the files
---

You run the sweep chain for {person}'s workspace at {workspace}.

1. Run `git -C {workspace} fetch origin --no-recurse-submodules`, then
   `git -C {workspace} merge -q --ff-only origin/main` and
   `git -C {workspace} submodule update --init --remote`, so you start from the tip with
   current mounts. If the fast-forward is refused because of local commits, leave it: the
   sync task owns landing.
2. Read `{workspace}/AGENTS.md` and `{workspace}/.claude/skills/sweep/SKILL.md`, and export
   as the skill says: for every source in `space.yaml` `sources`, the records newer than the
   feed file's last `at` (14 days on a first run) with the connectors this session has
   (mail, calendar, chat, calls, tracker, documents: whichever are connected) into
   `_meta/state/feed/<source>.jsonl`, one JSON object per line in the record shape the skill
   gives.
3. Run `{python} {workspace}/_meta/tools/chain.py sweep {workspace}` (sweep, distill, check).
   Then follow `.claude/skills/distill/SKILL.md` and `.claude/skills/check/SKILL.md` for the
   judgment only: improve each new candidate's body in plain language from its listed
   evidence only, and sharpen each check hit's recommendation. Never write `status: stable`;
   never edit a signed page; never read `personal/`, `academic/` or `_scratch/`.
4. Follow `.claude/skills/tracker/SKILL.md`: create what `{python} {workspace}/_meta/tools/tracker.py
   {workspace} pending` lists with the tracker connector, and record each.
5. Append one run line to `_meta/state/runs.jsonl`: `{{"routine": "sweep-chain", "at": <ISO
   time>, "space": <name>, "cost_tokens": <your estimate>, "notes": {{"step": "agent",
   "complete": true or false, "unreachable": [<sources you could not read>]}}}}`.

Commit nothing, push nothing, merge nothing: the sync task on this machine lands the files.
Send nothing to anyone; post nothing anywhere. If a connector is unavailable, skip that
source, name it under `unreachable` and continue. Finish with one line: records exported per
source, witness pages, candidates, checks, issues created.
