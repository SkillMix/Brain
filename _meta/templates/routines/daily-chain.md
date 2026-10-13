---
name: daily-chain
schedule: "30 6 * * *"
description: Once a day, status, release and {person}'s morning briefing for their workspace; the sync task lands the files
---

You run the daily chain for {person}'s workspace at {workspace}.

1. Run `git -C {workspace} fetch origin --no-recurse-submodules`, then
   `git -C {workspace} merge -q --ff-only origin/main` and
   `git -C {workspace} submodule update --init --remote`. If the fast-forward is refused
   because of local commits, leave it: the sync task owns landing.
2. Read `{workspace}/AGENTS.md` and `.claude/skills/status/SKILL.md` and follow it:
   `python {workspace}/_meta/tools/status.py {workspace}`. Edit no generated page.
3. Follow `.claude/skills/release/SKILL.md`: `python {workspace}/_meta/tools/release.py
   {workspace} ship`.
4. Run `python {workspace}/_meta/tools/outbox_reconcile.py {workspace}`.
5. Follow `.claude/skills/morning-view/SKILL.md`: when
   `python {workspace}/_meta/tools/morning_view.py {workspace} sent-today` exits 0, a
   briefing went out today already: skip the send. Otherwise run
   `python {workspace}/_meta/tools/morning_view.py {workspace} --person "{person}"`, send
   `_meta/state/morning-payload.json` exactly as rendered (its to, subject and body) with the
   mail connector from {person}'s own mailbox to that same address, then record the
   returned message id with `python {workspace}/_meta/tools/morning_view.py {workspace}
   record-send --id <message id>`. One briefing a day, sent even when the list is empty.
   Never send anything else to anyone.
6. Run `python {workspace}/_meta/tools/tests_run.py {workspace} all` when
   `_meta/tests/answers.yaml` exists, and record the counts.
7. Append one run line to `_meta/state/runs.jsonl`: `{{"routine": "daily-chain", "at": <ISO
   time>, "space": <name>, "cost_tokens": <your estimate>, "notes": {{"step": "agent"}}}}`.

Commit nothing, push nothing, merge nothing: the sync task on this machine lands the files.
Never write `status: stable`; never read `personal/`, `academic/` or `_scratch/`. Finish with
one line: subjects, items listed as leaving, the briefing's message id, test counts.
