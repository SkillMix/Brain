---
name: seed
description: A person's first backfill - export the last 90 days of their own declared sources (sent mail and calendar included), then seed.py sweeps them into witness pages and proposes candidates only for what is active and important now. Use once right after onboarding ("seed my workspace", "backfill my mail"), never on a schedule.
---

# seed: evidence eagerly, signatures only for what matters

Run once, after onboarding, in the person's own workspace.

1. For every source in `space.yaml` `sources`, export the last 90 days (or what the person
   asks for) with the connectors this session has, into `_meta/state/feed/<source>.jsonl`,
   in the record shape `.claude/skills/sweep/SKILL.md` gives. Include sent mail and the
   calendar: they are the best evidence of what the person is doing. Mail and messages
   always carry `from` and `to` (addresses); without them nothing knows who owes whom.
2. `python _meta/tools/seed.py <workspace> --days 90`: everything becomes witness pages; only
   active and important subjects get a candidate.
3. Tell the person in two lines: how many records became evidence, and that the candidates
   wait on their Needs-you list.

## Never

- Never sign anything; never read `personal/`, `academic/` or `_scratch/`; never export
  another person's mailbox.

## Behaviour scenarios

`_meta/tools/tests/test_batch6.py`.
