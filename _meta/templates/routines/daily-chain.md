---
name: daily-chain
schedule: "30 6 * * *"
description: Once a day, the daily chain, {person}'s morning briefing and the owner notices for their workspace; the sync task lands the files
---

You run the daily chain for {person}'s workspace at {workspace}. You never run git: the sync
task on this machine merges, pins the mounts and lands every file.

1. Run `{python} {workspace}/_meta/tools/chain.py daily {workspace} --person "{person}"`: once
   a week the guarded template sync, then status, requests, release, outbox reconcile, the
   morning view and the answer and leak tests. A failed step is labelled stale on the
   health line; do not repair it by hand.
2. Follow `{workspace}/.claude/skills/morning-view/SKILL.md` for the send: when
   `{python} {workspace}/_meta/tools/morning_view.py {workspace} sent-today` exits 0, a briefing
   went out today already; skip the send. Otherwise send `_meta/state/morning-payload.json`
   exactly as rendered (its to, subject and body) with the mail connector from {person}'s own
   mailbox to that same address, then
   `{python} {workspace}/_meta/tools/morning_view.py {workspace} record-send --id <message id>`.
   One briefing a day, sent even when the list is empty.
3. Follow `{workspace}/.claude/skills/owner-email/SKILL.md`: send each notice that
   `{python} {workspace}/_meta/tools/owner_email.py pending --root {workspace}` lists, exactly as
   rendered, to {person}'s own address, and record each with its message id.

Commit nothing, push nothing, merge nothing. Never send anything to anyone but {person}
themselves. Never write `status: stable`; never read `personal/`, `academic/` or `_scratch/`.
Finish with one line: what the chain reported, the briefing's message id, notices sent.
