---
name: onboard
description: Set up a space from a fresh copy of the template, or check one that exists, with onboard.py - one conversation with defaults; the agent does every step but the grants only the owner can give, each with a read-back check. Use when someone says "set up my workspace" or "set up our brain", or asks whether a space is set up.
---

# onboard: one verb, one conversation

The tool detects what it is setting up: no brain yet, a company; a brain exists, a person; a
desk seed, a project (that is `mint`). Every question has a default; a skipped one returns
on Needs you.

## When

- A repository just created from the template ("set up my workspace", "set up our brain").
- Any time, to check: `--check` re-runs the read-back checks; its one repair is that an
  onboarding root forgets spaces that were offboarded.
- A shadow space going live: `--go-live --answers <file>` (the file's `mounts:` maps each
  mount name to its live repository) sets `mode: live`, drops `time_scale`, repoints the
  mounts, rewrites the onboard state for this checkout, re-registers the sync task and
  re-applies the platform settings; a live space is left unchanged.
- To watch it work before connecting anything real: `--fixture <dir>` runs the fixture
  company end to end, offline.

## Run

`python _meta/tools/onboard.py [--answers FILE] [--brain URL] [--seed PATH] [--offline]
[--org ORG] [--check] [--go-live] [--fixture DIR]`

Run it once without flags to see the interview; ask the person each question in plain words
with its default, accept "skip", write the answers to a YAML file and run again with
`--answers <file>`.

## What the tool already did

Filled `space.yaml` (profile, name, owner, owner_email, audience, parent, kinds for a
brain, never-leaves defaults); for a company, drafted `identity/charter.md`, registered the
domains as umbrella subjects with their owners and created the top owner's workspace with
the brain mounted; wrote `_meta/access/resources.yaml` for the chosen connectors (none
required); installed the personal instruction at user level for the harness; registered the
sync task with the OS scheduler every 15 minutes; armed the repository settings (auto-merge,
the required check); rebuilt `AGENTS.md` and `ids.yaml`; ran validate, the first status and
the tests; recorded every step as `{step, done, check, result}`.

## Colleagues and other projects (phase 5)

- Invite a colleague, in the brain: `python _meta/tools/onboard.py <brain> --invite <slug>`
  writes `_meta/state/invitations/<slug>.yaml` (their name, the brain, the projects they are
  crew on) and one Needs-you item for the owner with its read-back. The owner hands the file
  over; on the colleague's own machine onboard runs with `--answers <file>`, asks the rest
  (their email, their feeds), mounts the brain and every crew project, installs their
  personal instruction and registers their sync task. Nothing is done for them.
- A project that already has a repository: `python _meta/tools/swap.py <checkout> --name
  <space name> --owner <name> --crew <names> --brain <url> --online` keeps its code and
  history, moves today's layer under `_archive/`, adds the shape-1 layer without touching
  its own files, and fills `space.yaml`; then `onboard --go-live` for its platform settings
  and sync task. The sync script lands the result.
- Every owner step `--check` cannot do itself sits on Needs you
  (`_inbound/needs-you/onboard-owner.yaml`) with its read-back until the read-back passes.

## A company with history: research first, then the archives

After a company's onboarding, before anyone signs anything:
1. Research first: read the company's own public sources (its site, the business registry,
   its code organization; never a person's private profiles compiled across sources), and
   write what you read as records (one JSON object per line: `id`, `url`, `title`, `at`,
   `about` identity, people, registry or code, `text`, and `claims` you would confirm),
   then `python _meta/tools/research.py <brain> ingest <records.jsonl>`.
2. The archives as evidence: draft a confirmations file ("I think X, based on Y"), batched
   by how much depends on each answer, from the old knowledge you were given read-only, then
   `python _meta/tools/cold_start.py <brain> --confirmations <file> --archive <path>
   --label <archive name>`. Only what is active and important becomes a candidate.
3. The morning email of the next day carries the first batch. Nothing is signed by you.

## Judgment left to you

Run the interview as a conversation; never guess an answer. When the tool lists steps that
need the owner's identity (signing in to their own mail, calendar and chat through the
connector; organization-level grants on the repository host), hand them over one at a time
with the read-back check that proves each took, then run `--check`. A repeated `--check`
that still fails names what to fix, not what to ask again. Run line: `{"routine":
"onboard", "at": "<ISO UTC>", "space": "<name>", "cost_tokens": <n>, "notes": {"step":
"agent", "case": "<company|person|project|check>"}}`.

## Never

- Never ask for, type or store a password, token or key: the person signs in in their own
  browser and the connector holds the grant (hard rule 1).
- Never leave `profile: template` in an instance; never set it anywhere but the template.
- Never write `stable`: the charter and spine stay drafts until the owner signs them.
- Never widen the badge beyond the template defaults; never run git.

## Scheduled invocation

None for setup. The check may be scheduled:
```
In the space at <root>, run `python _meta/tools/onboard.py --check` and report the rows
that fail with their read-back check. Change nothing; commit nothing.
```

## Behaviour scenarios

`_meta/tools/tests/test_onboard.py` (an offline company onboard creates a brain and
workspaces that validate; `--check` twice changes nothing) and
`_meta/tools/tests/test_fixture_e2e.py` (the fixture company end to end).
