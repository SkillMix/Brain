---
name: tracker
description: The one writer of the tracker (Linear, GitHub issues or any tracker connector). Sessions queue a request ("make an issue for X", "track this", "add a ticket") with tracker.py queue; the scheduled tracker step creates the queued issues with the connector and records their keys. Use when someone asks for a tracker issue, or as the tracker step of the sweep chain.
---

# tracker: one writer, never a session

Each outside system is written by one routine (rules.md section 1, rule 5). For the tracker
that is this step. A session that wants an issue queues it; the next run creates it, within
the hour.

## When work is agreed, or a person asks for an issue

Decide whenever company work is requested or agreed in a session, and again when the session
turns from talking to doing. Take the first case that matches:
1. Confidential or private (ownership, equity or settlement terms; a named person's
   employment, role, pay or status; deliberation before a decision is announced; legal
   positions; anything personal or academic): never in the tracker, not even under a bland
   title. Keep it in its desk folder.
2. A brainstorm, an idea, a question or an exploration: no issue.
3. Any other concrete company deliverable the person asked for or agreed to: queue it,
   `python _meta/tools/tracker.py <space> queue --title "<title>" --subject <subject id>
   --body "<what and why, two lines>" --by "<the person>"`. Tell them it appears on the next
   run. Add `--spec <page path>` when a spec or plan page covers the work; it gets the issue key.

When work on an issue this tracker made moves, queue the change with its evidence:
`python _meta/tools/tracker.py <space> update --issue <KEY> --state "In Progress" | "Done"
--comment "<what happened, one line>" --by "<the person>"`. A daily-note line that names the
key with "done", "blocked" or "started", and a merged pull request that names it, are queued
for you by `tracker.py observe` in the sweep chain. Never call the tracker's tools from a
session.

## The scheduled step

1. `python _meta/tools/tracker.py <space> pending` lists what may be written now (all queued
   requests at the badge's `at_once`, only approved ones at `propose`), each `create` or
   `update`.
2. A `create`: first search the project the request's subject matches (`match.tracker` in the
   registry) for an open issue with the same title; found, record that key instead. Otherwise
   create the issue with the tracker connector this session has, in that project (or its
   team), with its title and body and nothing else.
3. An `update`: set the issue's state when the update names one, and add its comment as a
   comment, nothing else.
4. Record each: `python _meta/tools/tracker.py <space> record <id> --issue <KEY> [--url <url>]`.
4. No tracker connector in this session: create nothing and name the tracker as unreachable
   in the chain's run line.

## Never

- Never write the tracker from a session; never edit or close issues people made (only
  issues in `_meta/state/tracker.jsonl` get updates).
- Never put a price, a person's pay or status, a credential or another client's name in an
  issue (the left-out list of the project the issue belongs to applies).

## Behaviour scenarios

`_meta/tools/tests/test_tracker.py`.
