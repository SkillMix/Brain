---
name: seed
description: A person's backfill - count their activity per month, export up to a year of their own declared sources in full (the last 90 days with full judgment, the older months as history), sweep, route with understanding first, rebuild the candidates and check them. Use once after onboarding ("seed my workspace", "backfill my mail"), when re-reading a space at full depth, or for one subject as a pilot ("backfill <subject>"); never on a schedule.
---

# seed: evidence eagerly, signatures only for what matters

Run in the person's own workspace. Depth over thrift: every record in full, every subject
understood before anything is placed or written. The last 90 days (`routing.since`) get full
judgment; older months (`routing.history_since`) are history: captured as fully, used as
background, never a question per record and never an open item.

## Run

1. **Hold.** While a backfill runs, the scheduled sweep chain is paused (ask the person to pause
   it, or pause it when they already said so); the daily chain keeps running. You turn it back
   on at the end (step 9).
2. **Count.** Per source and month, count from metadata only (sender, domain, labels, channel,
   meeting title; no bodies) the records the door would call company, and those it would drop.
   Write `_meta/state/private/activity-count.yaml`. History starts at the first month with
   company activity, never more than 365 days back: set `routing.history_since` to it in
   `space.yaml` (protected: it lands through the sync script's protected branch).
3. **Clean map.** For each subject the backfill will touch (all of them, or the one being
   piloted): check its registry entry and its parts. Duplicates merged, umbrellas set
   (`part_of`), aliases complete, through the librarian's proposals. Add what research finds
   later the same way, each with the record that shows it.
4. **Export** every source in `space.yaml` `sources` from `history_since` to now, in the sweep's
   record shape and depth (`.claude/skills/sweep/SKILL.md`), oldest first, in two-week slices,
   into `_meta/state/feed/<source>.jsonl`. Existing records keep their ids, so the sweep refreshes
   their pages in place. For history months, narrow by metadata first (the mail connector's
   categories, the `transcripts.exclude_words`, known academic senders), then read in full what
   remains; the door still decides each record (personal and academic: counted, never stored).
   For a pilot of one subject: select by its domains, names and aliases, whole threads.
   Large connector results that land in a file are converted with Python, never retyped.
5. `python _meta/tools/sweep.py <root>`; read what `sweep.py <root> --attachments` lists, as the
   sweep skill says, then `sweep.py` again.
6. **Route**, as `.claude/skills/route/SKILL.md` says: triage, a brief per subject, research,
   placement per episode, the second agent's check, `route.py record`. History records follow the
   history rules there. For a pilot: `route.py pending --subject <id>`.
7. **Distill from scratch:** `python _meta/tools/distill.py <root> --rebuild [--subject <id>]`,
   then every candidate as `.claude/skills/distill/SKILL.md` says (brief, full evidence, set
   aside, research, the page shape, a source per line, verify, checked by a second agent).
   A space without `routing:` in `space.yaml` runs `python _meta/tools/seed.py <workspace>
   --days <n>` instead of steps 6 and 7: witness pages, candidates only for what is active and
   important now.
8. **Land:** the sync tasks land the files; releases leave by their tiers.
9. **Close:** turn the scheduled sweep chain back on (the person's go-ahead from step 1 covers
   it). Tell the person in at most six lines: records read per source (and dropped as private),
   history start month, briefs built, records routed (at once, after the window, private,
   questions), candidates checked and waiting on Needs you, and what could not be read.

## Never

- Never sign anything; never read `personal/`, `academic/` or `_scratch/`; never export
  another person's mailbox; never read the archive while `routing.archives` is off.
- Never let history open an item or raise a question per record.

## Behaviour scenarios

`_meta/tools/tests/test_batch6.py` (seed.py: witness pages, candidates only for active and
important subjects); `_meta/tools/tests/test_route.py` (the history rules).
