---
name: daily-chain
schedule: "30 6 * * *"
description: Once a day, the daily chain for {person}'s spaces on this machine, their morning briefing and the owner notices; the sync tasks land the files
---

You run the daily chain for {person}'s spaces on this machine: every root that
`{python} {workspace}/_meta/tools/chain.py spaces {workspace}` prints, one after another. You
never run git: each space's sync task merges, pins the mounts and lands every file. For each
root <root>:

1. Run `{python} <root>/_meta/tools/chain.py daily <root> --person "{person}"`: once a week the
   guarded template sync, then status, the access check, requests, release, outbox reconcile,
   the morning view and the answer and leak tests. A failed step is labelled stale on the
   health line; do not repair it by hand.
2. Follow `<root>/.claude/skills/owner-email/SKILL.md`: send each notice that
   `{python} <root>/_meta/tools/owner_email.py pending --root <root>` lists, exactly as rendered,
   to {person}'s own address, and record each with its message id.

Then, for the workspace `{workspace}` only, the briefing: follow
`{workspace}/.claude/skills/morning-view/SKILL.md` for the send. When
`{python} {workspace}/_meta/tools/morning_view.py {workspace} sent-today` exits 0, a briefing
went out today already; skip the send. Otherwise send `{workspace}/_meta/state/morning-payload.json`
exactly as rendered (its to, subject and body) with the mail connector from {person}'s own
mailbox to that same address, then
`{python} {workspace}/_meta/tools/morning_view.py {workspace} record-send --id <message id>`.
One briefing a day, sent even when the list is empty.

Commit nothing, push nothing, merge nothing. Never send anything to anyone but {person}
themselves. Never write `status: stable`; never read `personal/`, `academic/` or `_scratch/`.
Finish with one line per space: what the chain reported, notices sent; and the briefing's
message id.
