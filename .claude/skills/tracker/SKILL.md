---
name: tracker
description: The one writer of the tracker (Linear, GitHub issues or any tracker connector). Sessions queue a request ("make an issue for X", "track this", "add a ticket") with tracker.py queue; the scheduled tracker step creates the queued issues with the connector and records their keys. Use when someone asks for a tracker issue, or as the tracker step of the sweep chain.
---

# tracker: one writer, never a session

Each outside system is written by one routine (rules.md section 1, rule 5). For the tracker
that is this step. A session that wants an issue queues it; the next run creates it, within
the hour.

## When a person asks for an issue

`python _meta/tools/tracker.py <space> queue --title "<title>" --subject <subject id>
--body "<what and why, two lines>" --by "<the person>"`. Tell them it will be created on the
next run. Never call the tracker's create tool from a session.

## The scheduled step

1. `python _meta/tools/tracker.py <space> pending` lists what may be created now (all queued
   requests at the badge's `at_once`, only approved ones at `propose`).
2. For each, create the issue with the tracker connector this session has, in the team or
   project the request's subject matches (`match.tracker` in the registry), with its title
   and body and nothing else.
3. Record it: `python _meta/tools/tracker.py <space> record <id> --issue <KEY> --url <url>`.
4. No tracker connector in this session: create nothing and name the tracker as unreachable
   in the chain's run line.

## Never

- Never write the tracker from a session; never edit or close issues people made.
- Never put a price, a person's pay or status, a credential or another client's name in an
  issue (the left-out list of the project the issue belongs to applies).

## Behaviour scenarios

`_meta/tools/tests/test_tracker.py`.
