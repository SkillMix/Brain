---
name: sign
description: The person's one verb over Needs you - show the list, brief each item with a recommendation, take yes/edit/no/later/always in bulk, read back the exact text, and only on an explicit "submit" run sign.py apply for that named person. Use when someone types /sign, answers an item, or asks what needs them.
---

# sign: ten minutes of yes, no and fix-that

Only a person signs. The read-back shows the exact text that will land and its fingerprint;
`submit` binds the approval to that text. You do every bit of the filing after it.

## When

- The person types `/sign`, asks "what needs me?", or answers an item the morning email or
  the session start showed them.
- Never on a schedule, never for a routine.

## Run

1. Show the list: `python _meta/tools/needs_you.py <root>` (about seven lines, "and N
   more"; `--json` for the full set). The line "held in doubt: N" opens on demand:
   `python _meta/tools/needs_you.py <root> --doubt` lists each held page with its reason and
   recommendation; its id takes the same verbs (yes releases it, no keeps it home for good,
   `edit <subject>` names the one subject it is about, always adds the standing choice). The
   line's ", M wait for a classification" part is counted, never offered: those leave on their
   own once their owner's workspace classifies the client, project or deal. A line
   "<subject>: the first N items since it was classified company leave for ..." stands for every
   one of them: its verdict applies to each.
2. Brief and take verdicts (contract below). Verbs: `yes`, `edit <note>`, `no`, `later`,
   `always`; in bulk ("1 to 4 yes, 5 edit: the date is the ninth, 6 later").
3. Read back: `python _meta/tools/sign.py <root> read-back <ids>` prints each item's exact
   text, fingerprint and destination. For an `edit`, compose the complete corrected body
   and read that back in full yourself; it is what apply will sign.
4. The word `submit` (exact) executes. Anything else amends verdicts and repeats step 3.
5. Write the verdicts to a scratch file outside the tracked zones (your harness's temp
   directory, or `_scratch/verdicts-<ISO>.yaml`), one key per item: `p-1a2b3c4d: yes`,
   `c-7f: [edit, "<complete corrected body>"]`, `q-02: no`, `l-19: later`, `k-33: always`.
   Then `python _meta/tools/sign.py <root> apply --by "<the person's name>" --verdicts <file>`.
6. Lessons: for every `edit` or `no` that corrects content with a reason ("that is not about
   this project", "you can find that yourself", "never put prices on the project page"), and
   for any pushback on how an agent did its work, append one line to `_meta/lessons.md`:
   `- <YYYY-MM-DD> <route|distill|all> [<subject>]: <the lesson, as a rule> (from: <the
   person's words, short>)`. The router, distill and the brief read it every run, so the same
   correction is never needed twice.
7. Receipt in one line: signed, edited, declined, deferred, standing choices added, lessons.

## What the tool already did

`apply`: `yes` moved the candidate to `knowledge/<kind>/<subject>/` (or `decisions/`) with
`status: stable`, `signed_by`, `signed_at`, `signed_in`, `fingerprint`, and queued its
release when `destination` is set, both without the `## New evidence` list (it is for the
review; the read-back already leaves it out); `edit` took the note as the new body, then signed; `no`
wrote a tombstone line to `log.md` and removed the candidate; `later` moved the deadline a
day; `always` added `{destination, kind}` to `standing_choices` and applied `yes`; a check
verdict went to `_meta/state/check-verdicts.jsonl`; a leaving item's `no` went to `held/`;
a `no` on an item that already left (listed under "left yesterday") retracted it, which
pauses the path that let it through for four weeks; a verdict on a librarian item (a sample,
an index, a test question, a reconnect) is read back by the next status run; a `yes` on a
template release (`template-<tag>-<commit>`) is read by each space's template sync, which moves
every space the person owns to that release;
one `log.md` line per verdict. It refuses a `--by` that names a routine.

An email reply to the morning briefing ("1-4 yes, 5 edit: ...") arrives as one `answer`
item, `_inbound/answers/reply-<date>-*.md`, written by `reply.py` in the sweep chain: its
`verdicts` map the day's numbers to item ids. Read each back like any verdict, ask for
"submit", and only then run `sign.py apply` with those verdicts; a reply never signs by
itself.

## Judgment left to you

The brief: per item, two or three plain sentences (what happened, what the page will say,
any date that matters; no labels, paths or field names), then "I recommend <verb> because
<reason>" grounded in the item, then the question. Name a decide-relevant risk unasked: an
item flagged `high_stakes` (say its why), or one you judge is about a high-stakes category, a destination that widens the audience ("it will go to the
Harbor crew"), a claim about a named person. Numbers echoed digit by digit. Read back only
the verdicts the person gave; an unanswered item stays where it is.

## Never

- Never run `apply` without the exact word `submit`, never with a routine's name or your
  own as `--by`; never write `status: stable` yourself.
- Never touch disk before `submit`; the verdicts file is the first write (lessons come after).
- Never reword the read-back text: the fingerprint is of that text.
- Never invent or embellish a brief; never a verdict for an item the person did not answer.
- Never run git; the sync script lands the signed pages.

## Scheduled invocation

None. This skill runs only in a live session with the named person.

## Behaviour scenarios

`_meta/tests/behaviour/sign.yaml`: `apply yes` yields a `stable` page whose fingerprint
equals its body and one `log.md` line; `apply` with `--by routine:x` is refused; writes only
under `knowledge/`, `_inbound/`, `_outbound/`, `log.md`, `space.yaml`, `_meta/state/`.
