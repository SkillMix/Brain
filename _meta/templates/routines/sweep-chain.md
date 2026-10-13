---
name: sweep-chain
schedule: "0 7,13,19 * * *"
description: Three times a day, export {person}'s feeds into their workspace, then sweep, distill and check; the sync task lands the files
---

You run the sweep chain for {person}'s workspace at {workspace}.

1. Run `git -C {workspace} fetch origin --no-recurse-submodules`, then
   `git -C {workspace} merge -q --ff-only origin/main` and
   `git -C {workspace} submodule update --init --remote`, so you start from the tip with
   current mounts. If the fast-forward is refused because of local commits, leave it: the
   sync task owns landing.
2. Read `{workspace}/AGENTS.md`, `{workspace}/rules.md` (through the brain mount when it is
   not in the workspace) and `{workspace}/.claude/skills/sweep/SKILL.md`, and follow the
   skill exactly: for every source in `space.yaml` `sources`, export the records newer than
   the feed file's last `at` (14 days on a first run) with the connectors this session has
   (mail, calendar, chat, calls, tracker, documents: whichever are connected) into
   `_meta/state/feed/<source>.jsonl`, one JSON object per line in the record shape the
   skill gives; then run `python {workspace}/_meta/tools/sweep.py {workspace}`.
3. Follow `.claude/skills/distill/SKILL.md`, then `.claude/skills/check/SKILL.md`
   (`python _meta/tools/distill.py {workspace}`: improve each new candidate's body in plain
   language from its listed evidence only; `python _meta/tools/check.py {workspace}`:
   sharpen each hit's recommendation). Never write `status: stable`; never edit a signed
   page; never read `personal/`, `academic/` or `_scratch/`.
4. Append one run line to `_meta/state/runs.jsonl`: `{{"routine": "sweep-chain", "at": <ISO
   time>, "space": <name>, "cost_tokens": <your estimate>, "notes": {{"step": "agent",
   "complete": true or false, "unreachable": [<sources you could not read>]}}}}`.

Commit nothing, push nothing, merge nothing: the sync task on this machine lands the files.
Send nothing to anyone; post nothing anywhere. If a connector is unavailable, skip that
source, name it under `unreachable` and continue. Finish with one line: records exported per
source, witness pages, candidates, checks.
