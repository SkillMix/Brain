---
name: distill
description: Run distill.py after a sweep so every subject whose evidence moved gets one candidate page, then rewrite each new candidate's body into plain language from its listed evidence alone. Use as the second step of the sweep chain, or when the person asks what is ready to sign.
---

# distill: one candidate per subject

The tool decides what to propose; you make each candidate readable. Nothing is signed here.

## When

- Scheduled: right after `sweep` in the sweep chain, three times a day.
- On request: "distill", "what is ready to sign?".
- A quiet sweep: the tool reports zero candidates and you stop.

## Run

`python _meta/tools/distill.py <root>`

## What the tool already did

For every subject whose evidence moved since `_meta/state/distill-watermark.yaml` and that
is active, marked `important`, or asked about (the question log answered it from evidence
alone): one candidate `_inbound/candidates/<subject>.md` (`status: draft`, `subject`,
`evidence: [witness ids]`, `destination: <home>` when the home is elsewhere), body = the
subject's current page followed by `## New evidence`, one line per witness page with its
date. An open unsigned candidate for the subject is refreshed in place, never duplicated. A
witness page that starts with `Decision:` becomes its own candidate for
`knowledge/decisions/`. The watermark; one run line naming the candidates it wrote.

## Judgment left to you

For each candidate the run wrote or refreshed (named in the tool's output), rewrite the body
into a clear page in plain language:
- fold the `## New evidence` lines into the page's own paragraphs where they belong, keeping
  each fact's date; keep the `## New evidence` heading and its lines at the end, so the
  signer sees what moved;
- use only the listed evidence and the existing body: never add a fact, number or name that
  is in neither; when two pieces of evidence disagree, write both under `## Disagreements`
  and pick nothing;
- plain words, short sentences, no labels or tags; the person decides in one read;
- frontmatter untouched: `status` stays `draft`, `evidence` keeps every id.
Then your run line: `{"routine": "distill", "at": "<ISO UTC>", "space": "<name>",
"cost_tokens": <n>, "notes": {"step": "rewrite", "candidates": <n>}}`.

## Never

- Never write `status: stable`; never write into `knowledge/`; never sign or promote.
- Never invent a claim: no evidence id, no sentence.
- Never open a second candidate for a subject; never edit a candidate the tool did not
  touch this run (someone may be reading it back).
- Never read `personal/` or `academic/`: they are outside every tool.
- Never run git.

## Scheduled invocation

```
Run distill for the space at <root> (second step of the sweep chain, after sweep.py).
1. Read <root>/AGENTS.md and <root>/rules.md, then <root>/.claude/skills/distill/SKILL.md
   and follow it exactly: run `python _meta/tools/distill.py <root>`, then rewrite the body
   of each candidate it wrote or refreshed in plain language from its listed evidence only.
2. Touch no frontmatter, never `stable`, nothing in knowledge/. Commit nothing.
3. Append your run line to _meta/state/runs.jsonl; report the count of candidates only.
4. Continue with <root>/.claude/skills/check/SKILL.md.
```

## Behaviour scenarios

`_meta/tests/behaviour/distill.yaml`: two witness pages for one active subject give exactly
one candidate; a second run refreshes it in place; writes only under `_inbound/candidates/`
and `_meta/state/`; no `stable`.
