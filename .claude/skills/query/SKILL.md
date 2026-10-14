---
name: query
description: Answer a question about the company, a subject, a person or a decision from this space and its mounts - the question log first, then the walk and plain search over every layer, every grounding line with its tier and age, and the question logged. Use for any what, how, why, when or who question; no tool runs the answer.
---

# query: every layer, with tiers and ages

Two paths, both always valid: walking needs no tools; search is faster when it runs. No
first match: gather every layer the asker may read before answering.

## When

Any knowledge question: "what did we decide about X", "what is going on with Harbor",
"when does the pilot start", "who owns delivery". Not for editing or filing.

## Run

1. The log first: `python _meta/tools/questions_log.py <root> lookup --question
   "<question>" --subject <id>`. Start from its pointers (the pages that answered, the path
   that worked after someone steered); they are pointers, never the answer.
2. A "what did we decide" question asks the decisions index first:
   `python _meta/tools/decisions.py <root> find --subject <id> --text "<question>"` (this
   space and every mount, decisions in force first, superseded ones marked); then walk.
3. Walk: `index.md`, then `knowledge/<kind>/index.md`, then
   `knowledge/<kind>/<subject>/index.md` (the hub, links by id; `_meta/state/ids.yaml` maps
   an id to its file), then the pages and `live/subjects/<id>.md`. Repeat in every mounted
   layer (`brain/`, `mounts/<name>/`): company, team, mine.
4. Search as the second path: `python _meta/tools/search.py <root> query "<term>"` (plain
   text over this space and every mount, or the space's index when `_meta/search.yaml`
   turned one on); each hit names its layer and subject: expand it to the hub, the status
   page and every layer above it.
5. Answer, then ground it: `python _meta/tools/ground.py <root> <id or path> ...` prints one
   line per page you used (tier, scope, date, age, and "past window", "confidence low" or
   "contested") plus the company view's date and the index's age; end the answer with them.
   Every grounding line carries tier and age, like `(signed, company, 2026-10-02, 10 days)`;
   tiers are `signed`, `system`, `pending`, `witness`, `reported`, `working`; a witness line
   is "unverified"; a page past its freshness window is named as such; "company view as of
   <the brain mount's pin date>"; where layers disagree, show both and say which is signed.
   An answer from evidence alone says so.
6. Log it: `python _meta/tools/questions_log.py <root> append --question "<question>"
   --subject <id> --tier <lowest tier used> --page <relpath> [--page <relpath> ...]`; add
   `--steering` when the person had to redirect you. The log never leaves the space.
7. In a personal space, an answer that took more than one lookup is captured to the daily
   note with its sources (the capture skill), so the next asker starts from it.

### "Prep me for the call with <subject or person>"

Steps 1 to 5 for that subject, then one screen, in this order: what we owe them and what we
wait on (the status page's open items, newest first, with their ids); the last contact (the
newest witness page for the subject: date, channel, one line); what is coming up; anything
signed that changed since the last contact; and an agenda of at most three points, each tied
to an open item. Grounding lines as in step 5. Nothing is sent: a follow-up the person wants
afterwards is drafted into `outbox/`.

## What the tool already did

Nothing runs the answer. `status.py` kept the hubs, status pages and `ids.yaml` current;
`questions_log.py` holds what was asked before.

## Judgment left to you

Everything above the files: which subject the question is about (the registry's aliases:
`python _meta/tools/registry.py <root> find "<name>"`), which layers apply, what disagrees,
and how old the ground is.

## Never

- Never stop at the first match; never answer from one layer when another exists.
- Never state a witness or working line as fact; never drop the tier or the age.
- Never quote never-leaves material or anything from a space the asker cannot read; a
  subject whose home the asker cannot read gets "ask <owner>", not a guess.
- Never write the answer into `knowledge/` (capture it; distill proposes; a person signs).

## Scheduled invocation

None. The nightly answer tests (`_meta/tests/answers.yaml`, run by `tests_run.py`) cover
the walk and the search without a model.

## Behaviour scenarios

`_meta/tests/answers.yaml` and `_meta/tests/leaks.yaml` through `_meta/tools/tests_run.py`
(`_meta/tools/tests/test_tests_run.py`): what must be found, and what an audience must not.
