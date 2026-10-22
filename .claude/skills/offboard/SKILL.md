---
name: offboard
description: Close a space when its owner leaves, with offboard.py - show what still waits, stop the sync task, remove the personal instruction, archive the repository and retire the registry entry; nothing is deleted. Use when the owner leaves, or to verify a departure was completed.
---

# offboard: the mirror of onboard

Raw personal records live only in personal spaces and are frozen with them; signed
conclusions that already left stay where they landed, with their signature.

## When

- The owner is leaving and asks; or the owner of the parent space asks after they left.
- To verify: run it again; it reports what is still open and changes nothing that is done.

## Run

`python _meta/tools/offboard.py <root> [--online]`

## What the tool already did

Listed what still waits in `_outbound/queue/` and `_inbound/candidates/`; removed the sync
task from the OS scheduler; removed the personal instruction block from the user-level
instructions; archived the repository on the host (`--online`); set the owner's subject to
`state: archived` in the registry (a subject the brain holds by a registry note, shipped
alone at once); recorded each step; deleted nothing.

## Forgetting

`python _meta/tools/forget.py <space> list <subject id>` lists everything about one subject;
it changes nothing. A personal space's raw records (feed files, witness pages) stay at offboard
unless the person themselves says to remove them: then, and only then, run
`python _meta/tools/offboard.py <space> --forget-records`. Git keeps history; a true erasure
is a history rewrite only the law demands, and it is the person's call.

## Judgment left to you

Before running, show the person what the tool will list as still waiting: queue items are
theirs to pull back or let go, candidates theirs to sign or decline in one last `sign` pass
if they want it; after offboard nothing ships from this space. Confirm the ask came from the
owner or from the owner of the parent space; anyone else is a judgment call that goes to
them as a question. Run line: `{"routine": "offboard", "at": "<ISO UTC>", "space":
"<name>", "cost_tokens": <n>, "notes": {"step": "agent", "open_items": <n>}}`.

## Never

- Never delete a file, a page or the repository; an archive is frozen, read-only.
- Never sign the remaining candidates or ship the remaining queue for the person.
- Never run it on someone else's word without the check above; never run git.

## Scheduled invocation

None. Offboard runs in a session with the person who asks.

## Behaviour scenarios

`_meta/tools/tests/test_offboard.py`: offline, the registry entry flips to `archived` and
every file is still in place.
