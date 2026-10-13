---
name: daily-chain
schedule: "30 6 * * *"
description: Once a day, the daily chain and {person}'s morning briefing for their workspace; the sync task lands the files
---

You run the daily chain for {person}'s workspace at {workspace}.

1. Run `git -C {workspace} fetch origin --no-recurse-submodules`, then
   `git -C {workspace} merge -q --ff-only origin/main` and
   `git -C {workspace} submodule update --init --remote`. If the fast-forward is refused
   because of local commits, leave it: the sync task owns landing.
2. Run `python {workspace}/_meta/tools/chain.py daily {workspace} --person "{person}"`: once
   a week the guarded template sync, then status, requests, release, outbox reconcile, the
   morning view and the answer and leak tests. A failed step is labelled stale on the
   health line; do not repair it by hand.
3. Follow `{workspace}/.claude/skills/morning-view/SKILL.md` for the send: when
   `python {workspace}/_meta/tools/morning_view.py {workspace} sent-today` exits 0, a briefing
   went out today already; skip the send. Otherwise send `_meta/state/morning-payload.json`
   exactly as rendered (its to, subject and body) with the mail connector from {person}'s own
   mailbox to that same address, then
   `python {workspace}/_meta/tools/morning_view.py {workspace} record-send --id <message id>`.
   One briefing a day, sent even when the list is empty. Never send anything else to anyone.

Commit nothing, push nothing, merge nothing: the sync task on this machine lands the files.
Never write `status: stable`; never read `personal/`, `academic/` or `_scratch/`. Finish with
one line: what the chain reported, the briefing's message id.
