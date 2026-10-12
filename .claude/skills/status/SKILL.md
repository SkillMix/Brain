---
name: status
description: Run status.py, the librarian, to rebuild status pages, hubs, indexes, ids and the health and now pins, and turn any change that would move ownership into a question instead of an action. Use as the first step of the daily chain, or when asked what is going on with a subject.
---

# status: the librarian

One routine owns structure. It acts on the mechanical and asks on the human.

## When

- Scheduled: once a day (`routines.daily`), first step of the daily chain (status, then
  release, then morning view).
- On request: "what is going on with X?" (run it, then read `live/subjects/<id>.md`); after
  a desk folder appears or a subject changes kind.

## Run

`python _meta/tools/status.py <root>`

## What the tool already did

Registered every new `work/<kind>/<subject>/` folder as a subject (personal spaces); wrote
`live/subjects/<id>.md` for each stateful subject (Open, Recently closed, Timeline, Coming
up; every line ending with tier, scope and date); rebuilt the hubs
`knowledge/<kind>/<id>/index.md`, the kind indexes and the Subjects block of `index.md`;
marked subjects quiet for eight weeks `dormant`; added see-also lines from question-log
entries that needed steering; rebuilt `live/health.md` (one line per space: runs, cost,
checker hit rate, held in doubt, open questions, the probe's recall per method, the
capture rate in a personal space, requests asked and answered, the index), `live/now.md`
and `_meta/state/ids.yaml`; once a scaled week ran the walk-and-search probe
(`tests_run.py probe`, `_meta/state/probe.jsonl`); kept its own Needs-you file
`_inbound/needs-you/librarian.yaml` (a big project whose search beats the walk: an index
proposal; a question that needed steering: a test-question proposal) and applied the
verdicts given on it (an index turned on, a question added to `_meta/tests/answers.yaml`);
before anything else, landed every signed release that arrived (`landing.py`: silently into
`knowledge/` when routine, pending with `landing:` and `for:` when high-stakes, distrusted,
another signer's page, a disagreement or a fingerprint mismatch; duplicates dropped);
paused the path of every item a `sample_failed` signal names; listed the week's `sample`
for the domain owner and one `reconnect` item per source the last sweep could not reach;
one run line.

It also ran the librarian's duties (`librarian.py`): a subject's folders under the wrong
kind, a page in another subject's folder, a loose page, a desk folder named like a subject:
moved, each verified by the answer and leak tests before and after (a lower score was
reverted), one line in `log.md` with how to undo it, recorded for the sync script to land as
its own commit; pending shape migrations run; splits, merges, umbrellas and desk folders it
cannot place written as proposals under `_meta/state/librarian/`; a change that would move
ownership turned into one question to the owners, asked once.

## Judgment left to you

Each open proposal (`python _meta/tools/librarian.py <root> list`): have an independent
second agent review it (a fresh agent that did not write it, given the proposal and the
affected hubs; it answers apply or not, with a reason). Apply only what it approves:
`python _meta/tools/librarian.py <root> apply <id> --reviewed-by "<reviewer>"`. The tool
re-checks ownership and the tests itself.

The tool's output lists what it refused: a regroup, split or merge that would change who
owns a subject. For each, write one question, never the change:
- the full text at `_inbound/questions/<subject>-ownership.md`: what would move, who owns
  it now, who would, the evidence, your recommendation;
- one item appended to `_inbound/needs-you/status.yaml` (`kind: question`, `text`,
  `recommendation`, `default: "nothing moves"`, `deadline: null`, `payload:` that path), so
  both owners see it on Needs you.
Nothing else. Then your run line: `{"routine": "status", "at": "<ISO UTC>",
"space": "<name>", "cost_tokens": <n>, "notes": {"step": "questions", "written": <n>}}`.

## Never

- Never edit a status page, hub or index by hand: they are generated and rebuilt.
- Never change `owner`, `part_of` or `home` in the registry yourself; never delete a
  subject (`dormant` and `archived` are states).
- Never write `stable` (status pages are operational state, never signed); never run git.

## Scheduled invocation

```
Run the daily chain for the space at <root> as <person>.
1. Read <root>/AGENTS.md and <root>/rules.md, then <root>/.claude/skills/status/SKILL.md
   and follow it exactly: run `python _meta/tools/status.py <root>`; turn each refused
   ownership move it reports into a question in _inbound/questions/ plus an item in
   _inbound/needs-you/status.yaml. Edit no generated page.
2. Then follow <root>/.claude/skills/release/SKILL.md and
   <root>/.claude/skills/morning-view/SKILL.md in that order.
3. Append your run line to _meta/state/runs.jsonl. Commit nothing.
4. Report counts only: subjects, new registrations, dormant, questions written.
```

## Behaviour scenarios

`_meta/tests/behaviour/status.yaml`: a new `work/projects/harbor/kickoff/` folder registers
`harbor`; hubs link by id and `ids.yaml` resolves them; writes only under `live/`,
`knowledge/*/index.md`, `index.md`, `_meta/subjects.yaml`, `_meta/state/`; no `stable`.
