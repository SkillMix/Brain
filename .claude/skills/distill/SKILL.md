---
name: distill
description: Run distill.py after a sweep so every subject whose evidence moved gets one candidate page, then write each candidate from an understanding of its subject - the subject's brief, research for what the evidence leaves open, the page shape of its kind, a source on every line, written for where it lands - and have a second agent check it before it reaches anyone. Use as the second step of the sweep chain, after a backfill (--rebuild), or when the person asks what is ready to sign.
---

# distill: understand the subject, then write its page

The tool decides which subjects get a candidate and gathers their evidence. You understand each
subject, research what is missing, and write a page a person can sign in one read, with every
line traceable to its record. Nothing is signed here. Quality over token cost: read everything,
search until the gap is closed, and let a second agent check.

## When

- Scheduled: right after `sweep` in the sweep chain, three times a day.
- After a backfill, or when a candidate's evidence is known to be wrong: `--rebuild`.
- On request: "distill", "what is ready to sign?".
- A quiet sweep: the tool reports zero candidates and you stop.

## Run

1. `python _meta/tools/distill.py <root>` (after a backfill: `--rebuild [--subject <id>]`,
   which writes the candidate from scratch: no old body, the evidence as it is now).
2. For each candidate the run wrote or refreshed (named in the output):
   1. **Lessons.** Read `_meta/lessons.md`: lines for this subject and for `all`.
   2. **Understand.** Read the subject's brief `_meta/state/private/briefs/<subject>.md`; without
      one, `python _meta/tools/brief.py <root> pack <subject>`, read every page the pack lists
      in full, and write the brief (sections of `_meta/templates/brief.md`).
   3. **Read every evidence record in full**, not its title: the witness pages the candidate's
      `evidence` names.
   4. **Set aside** evidence that is not about this subject: move its id from `evidence` to
      `set_aside: [{id, why}]` in the frontmatter (the only frontmatter edit you make). The tool
      keeps it out from then on.
   5. **Research** every gap a page of this kind needs filled (what it is, who is who and in
      which role, how it started, what was agreed): this space's pages and journal, the spaces
      you can read, then the connectors within `space.yaml` sources. Export what a search finds
      into its feed (sweep record shape, `"research": "<subject id>"`); it becomes evidence after
      the next `sweep.py`, and you
      add its id to `evidence`. No "[unknown]" and no question until every source is exhausted;
      what stays open goes to the brief's Open questions, and only a gap that blocks the page
      becomes a question, with your best guess.
   6. **Write** the body in the shape of `_meta/templates/pages/<kind>.md`:
      - lasting facts only: what the subject is, how it started, scope, people and roles,
        agreements, decisions, how it ended. Where it stands is the status page's; at most one
        line pointing to it;
      - every sentence or bullet ends with `[src: <record id>, ...]`, ids from `evidence`;
      - newer evidence replaces older; `## Disagreements` only for records of the same moment
        that conflict, each side with its source;
      - written for where it lands (`destination`, else this space): nothing about a category
        on the never-leaves list or the destination's left-out list (prices, terms, pay,
        ownership, a person's private situation), however it is worded. Soften ("currently
        unavailable") or leave the fact in the brief;
      - plain words, short sentences; keep `## New evidence` and its lines at the end.
   7. **Verify.** `python _meta/tools/distill.py <root> verify <candidate>` and fix every problem
      it lists (a line without a source, a source not in evidence, a placeholder). It checks
      facts only: what the page is about is the check's judgement.
   8. **Check.** A second agent (a fresh subagent that has not seen your reasoning) reads the
      candidate against every cited record: each line supported, current, and fit for its
      destination (nothing about a never-leaves or left-out category). When the page is about
      one of the destination's `high_stakes` categories it sets `high_stakes: <why, one line>`
      in the frontmatter: the signed page then waits the window and lands pending. It fixes or
      removes what fails, runs `verify` again, then
      `python _meta/tools/distill.py <root> checked <candidate> --by <its name>`.
3. Your run line: `{"routine": "distill", "at": "<ISO UTC>", "space": "<name>", "cost_tokens":
   <n>, "notes": {"step": "rewrite", "candidates": <n>, "checked": <n>, "set_aside": <n>,
   "researched": <n>}}`.

## What the tool already did

For every subject whose evidence moved since `_meta/state/distill-watermark.yaml` and that is
active, marked `important`, or asked about: one candidate `_inbound/candidates/<subject>.md`
(`status: draft`, `subject`, `evidence`, `destination: <home>` when the home is elsewhere), body
= the subject's current page (with `--rebuild`: a stub) followed by `## New evidence`, one line
per witness page with its date. An open unsigned candidate is refreshed in place; set-aside ids
stay out. A witness page that starts with `Decision:`, or a decision the router recorded, becomes
its own candidate for `knowledge/decisions/`. Archive-derived pages are not evidence while
`routing.archives` is off.

## Never

- Never write `status: stable`; never write into `knowledge/`; never sign or promote.
- Never write a line without a source; never invent a fact, name or number.
- Never put a bracketed question or "unknown" on a page: research, or the brief's Open questions.
- Never open a second candidate for a subject; never edit a candidate the tool did not touch
  this run (someone may be reading it back).
- Never read `personal/` or `academic/`, nor the archive while `routing.archives` is off.
- Never let the brief travel; never run git.

## Scheduled invocation

```
Run distill for the space at <root> (second step of the sweep chain, after sweep.py).
1. Read <root>/AGENTS.md and <root>/rules.md, then <root>/.claude/skills/distill/SKILL.md
   and follow it exactly: run `python _meta/tools/distill.py <root>`, then for each candidate
   it wrote or refreshed: lessons, the subject's brief, every evidence record in full, set aside
   what is not about it, research the gaps, write it in its kind's shape with a source on every
   line, verify, and have a second agent check it.
2. Frontmatter: only `evidence`/`set_aside` moves and the `checked` stamp; never `stable`,
   nothing in knowledge/. Commit nothing.
3. Append your run line to _meta/state/runs.jsonl; report counts only.
4. Continue with <root>/.claude/skills/check/SKILL.md.
```

## Behaviour scenarios

`_meta/tests/behaviour/distill.yaml`: two witness pages for one active subject give exactly
one candidate; a second run refreshes it in place; writes only under `_inbound/candidates/`
and `_meta/state/`; no `stable`. `_meta/tools/tests/test_distill.py`: `verify` flags a line
without a source and a bracketed placeholder, and no word decides; set-aside
evidence stays out; `--rebuild` starts from scratch.
