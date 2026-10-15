---
name: release
description: Ship queue items whose window has passed and that a morning view listed to their subject's home, scrubbed; pull back or retract a release when the person asks. Use inside the daily chain, through the sync script's release leg, or on the person's word.
---

# release: the audience widens by class, never by hand

Releases go to the subject's `home`. A release waits only when it widens who can see
something (rules.md section 6). The tool ships; you act only on the person's word.

## When

- Scheduled: in the daily chain after `status`; and every 15 minutes the sync script's
  release leg ships what is due.
- On request: "hold X", "pull X back", "retract X", "what leaves tonight?".

## Run

- Ship what is due: `python _meta/tools/release.py <root> ship` (online this normally runs
  as `python _meta/tools/sync.py --root <root> --release-leg`, the one process that runs git).
- Stop an item before it leaves: `python _meta/tools/release.py <root> pull-back <id>`
  (moves it to `_outbound/held/`).
- Take back an item that left: `python _meta/tools/release.py <root> retract <id>` (a
  retraction travels at once).

Requests and answers ride the same leg: `ship` carries open requests and finished answers
through the corridor (`channel`) between the two spaces, at once, an answer scrubbed like
any release.

## What the tool already did

For each `_outbound/queue/<id>.md` with `stage_after` passed and (`class: at_once` or
`listed: true`): scrubbed it for facts (a secret-scan hit fails it into `held/` with a
reason; what it says was judged before it was queued, by the router and its check or by a
person, and no word in it decides); wrote it into the destination's
`_inbound/candidates/<id>.md` as `status: draft` with `released_by`, `released_from`,
`release_class` and an opaque `evidence` pointer, signature fields carried unchanged on a
signed page; refused a destination whose `mode` differs; online, updated the destination
checkout, committed `chore: release`, pushed `release/<source>` and opened a pull request;
one run line. A pull-back or a failed sample pauses the path that let the item through (a
standing choice or a source's audience) for four weeks.

## Judgment left to you

None on what ships: a windowed item no morning view has listed waits, whatever the clock
says. On the person's word, run pull-back or retract and tell them what the four-week pause
means for that path. Run line: `{"routine": "release", "at": "<ISO UTC>",
"space": "<name>", "cost_tokens": <n>, "notes": {"step": "agent", "action": "<verb> <id>"}}`.

## Never

- Never ship by editing `listed`, `stage_after`, `class` or `destination` on a queue item.
- Never write into another space's folders yourself; never release anything from `held/`
  without the person's yes (the doubt count on Needs you is where that yes is given).
- Never shorten the window below 24 hours; never release never-leaves material at any level.
- Never write `stable`; never run git (the sync script runs the release leg).

## Scheduled invocation

```
Run the release step for the space at <root> (daily chain, after status).
1. Read <root>/AGENTS.md and <root>/rules.md, then <root>/.claude/skills/release/SKILL.md
   and follow it exactly: run `python _meta/tools/release.py <root> ship`.
2. Change no queue item. Commit nothing. Append your run line to _meta/state/runs.jsonl.
3. Report counts only: shipped by class, held with reasons, refused.
4. Continue with <root>/.claude/skills/morning-view/SKILL.md.
```

## Behaviour scenarios

`_meta/tests/behaviour/release.yaml`: a `window` item not yet listed stays; a listed one
past its window lands in the offline destination with `release_class`; a `day rate` item is
held; writes only under `_outbound/` and the destination's `_inbound/candidates/`; no
`stable`.
