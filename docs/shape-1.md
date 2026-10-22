---
title: Shape 1
---
# Shape 1: what any agent may assume, and must never do

A space is a git repository created from one template. `shape: 1` in `space.yaml` names
this contract and `python _meta/tools/validate.py <root>` checks it. The skills under
`.claude/skills/` are one implementation of the shape (the folder this reference
implementation's harness reads); any harness may read the same files. A tool on any harness
that reads and writes the files below, and nothing else, is conformant (section 3).

## 1. What any agent may assume

### The layout

Every profile is a subset of the brain's tree. One line each:

```
space.yaml                        the one settings file (keys below); everything else is derived from it or from rules.md
index.md                          the root index: a Subjects block between <!-- subjects --> markers; every walk starts here
rules.md                          the constitution (brain; mounted elsewhere); section numbers are stable, tools cite them
log.md                            append-only: signatures, landings, tombstones, one line each; merged by union
AGENTS.md                         generated from space.yaml by agents_md.py; never edited by hand
identity/                         charter and spine: who this is and what never leaves (brain; mounted elsewhere)
knowledge/<kind>/<subject>/       signed pages and the hub index.md (generated, links by id only)
knowledge/decisions/              signed, frozen decisions; a replacement is a new page with supersedes:
knowledge/sources/<yyyy>/<mm>/    witness pages: dated evidence, type source, status draft, never signed
live/now.md                       the pin: what is going on here, rebuilt by status
live/subjects/<id>.md             status pages, type snapshot, every line ending with (tier, scope, date); never signed
live/health.md                    one line per space: runs, cost, checker hit rate, held in doubt, open questions
live/needs-you.md                 the person's list as the morning view rendered it
work/<kind>/<subject>/<effort>/   the desk (personal only), free-form inside
journal/<yyyy>/<mm>/<date>.md     daily notes (personal only), append-only, merged by union
outbox/                           drafts the person fires (personal; optional in a project); agents never send
_inbound/candidates/              one candidate per subject page, waiting for a yes
_inbound/checks/                  the checker's hits, each with a recommendation and a route
_inbound/questions/               questions to a person, full text
_inbound/needs-you/<writer>.yaml  Needs-you items, one file per routine writer
_outbound/queue/                  releases waiting for their class and window
_outbound/held/                   releases that stay home, each with a reason
_outbound/signals/                content-free signals to a subject's home
_outbound/requests/  _outbound/answers/   the fourth wire: requests asked, answers given (corridor-bound)
_inbound/requests/  _inbound/answers/     in a channel: what crosses the corridor; in a space, answers that arrived
_meta/subjects.yaml               the registry (fields below)
_meta/access/resources.yaml       connected systems, each with the audience that can already read its records
_meta/questions.log               the question log, one JSON object per line; never leaves the space
_meta/lessons.md                  corrections the person made once, one line each; read by the router, distill and the brief
_meta/state/                      watermarks, ids.yaml, runs.jsonl, payloads; feed/ is local and never committed
_meta/state/private/briefs/       one private brief per subject the space understands (and its pack); local, disposable, never travels
_meta/tests/                      answers.yaml, leaks.yaml, behaviour/<skill>-<n>.yaml, fixture/, forbidden-names.sha256
_meta/tools/  _meta/templates/    the tools (stdlib plus PyYAML) and the page templates
.github/                          the checks workflow (it also arms auto-merge); template-marker only in the template
.claude/skills/<name>/SKILL.md    the toolbox: one skill per routine, each wrapping one tool
personal/  academic/  _scratch/   gitignored in every profile; outside every tool and every sweep
```

| Entry | brain | project (or division) | personal | channel |
|---|---|---|---|---|
| `space.yaml`, `index.md`, `log.md` | yes | yes | yes | yes |
| `identity/`, `rules.md` | the constitution | mounted | mounted | no |
| `_meta/subjects.yaml` | the root registry | own ids, reuses ancestors' | own | no |
| `knowledge/`, `live/` | yes | yes | yes | no |
| `work/`, `journal/` | no | no | yes | no |
| `outbox/` | no | optional | yes | no |
| `_inbound/`, `_outbound/` | yes | yes | yes | is the corridor |

### `space.yaml`

| Key | Meaning |
|---|---|
| `shape` | the version of this contract; `1`; anything else fails validation |
| `profile` | `brain`, `project` (also a division or a vault), `personal` or `channel`; decides which entries exist; `template` is legal only where `.github/template-marker` exists |
| `mode` | `live`, `shadow` or `fixture`; a shadow releases only to shadows; fixture and shadow run on the virtual clock (`SPACE_NOW`) |
| `name` | the space's name; registry `home` and queue `destination` fields use it |
| `owner` | the one named human owner; every subject without an owner is theirs |
| `owner_email` | where the morning mail goes; the one address the system ever sends to |
| `audience` | `everyone` (the brain) or a list of names: who may read this space |
| `parent` | the parent space's name; `null` for the brain |
| `kinds` | the company's vocabulary under `knowledge/`, declared once in the brain and inherited through the mount; `decisions` and `sources` are fixed and never declared |
| `authority` | the badge: per action class (`edit_inside`, `write_knowledge`, `release`, `write_outside_system`, `send_outward`, `spend`) one level, `propose` (the agent drafts, a person acts), `after_window` (happens after the window, can be pulled back) or `at_once`; floors: `send_outward` and `spend` never above `propose`, `release` never above `after_window` where the audience would widen; raised only by a human owner, lowered by evidence for four weeks; the brain's `floors:` make any floor stricter below it; tools ask `authority.py` before they act |
| `agents` | who acts here, `{name, acts_for}`; every action above `propose` records whom it acted for and by which path |
| `models` | which model providers may read this space; the harness's permission profile enforces it |
| `budget` | `{tokens_per_day}`: declared and shown in the health line, not enforced; the shed order is rules.md section 7 |
| `window_hours` | the release window; 24 is the floor, a person may set longer |
| `standing_choices` | `[{destination, kind}]`: one kind of record (`mail`, `call`, `message`, `event`, `document`) to one destination at once; set by the person; never applied to calls |
| `left_out` | whole-word terms that never travel to this space, whatever the release class |
| `never_leaves` | whole-word terms that never leave this space, by any path, at any badge level; the brain's terms are added to every space's list |
| `agents` | one row per agent: `name`, `acts_for` (a person or `owner`), optionally `space` and a `badge:` that only lowers the space's levels |
| `protected` | paths whose pull requests wait for the owner's one click instead of merging on green |
| `sources` | `[{name, adapter, audience}]`: connected systems and who can already read their records (`owner`, `crew`, `everyone` or names) |
| `mounts` | `[{name, repo, branch, path}]`: read-only, pinned windows on ancestors; the brain at `./brain`, flat, never nested |
| `routines` | retired, not read: each routine runs at the `schedule` of its prompt in `_meta/templates/routines/`, cron in the machine's local time; the chain is driven by dependency, not by the clocks alone |
| `routing` | turns the judgment router on (a personal workspace): `since` the oldest witness page it reads with full judgment, `history_since` (optional, at most 365 days back) the start of the history window before it (background only, no open items, no question per record), `archives` (default false) whether archive-derived pages count as evidence for briefs and distill, `review_until` (optional, off unless set; onboard never sets it) every projection waits the window until then; without it, evidence attaches by identifier and the sweep releases |
| `time_scale` | fixture and shadow only; divides every duration, so a 24-hour window at `48` is 30 minutes |
| `transcripts` | personal only: `{path, switch}`; the harness's session transcripts folder; `off` measures the capture rate only, `on` also turns missed sessions into witness pages here |
| `request_classes`, `request_choices` | the fourth wire: classes of request and the standing choices that answer them (`[{from, class, answer}]`, answer `at_once`, `ask` or `never`) |

### Pages

Every `.md` under `knowledge/`, `live/`, `identity/`, `_inbound/candidates/` and
`_inbound/checks/` (except `index.md` and `log.md`) has YAML frontmatter with:

- `id`: `p-` plus eight hex characters, permanent; a move never changes it;
- `type`: one of `concept`, `entity`, `source`, `decision`, `directive`, `process`,
  `policy`, `snapshot`, `review`, `status`;
- `title`; `status`: `draft`, `stable` or `deprecated`; `generated: {by, at}`;
- `subject`: the registry id the page is about (optional on identity pages);
- on a `stable` page: `signed_by` (a person's name), `signed_at`, `signed_in` (the space it
  was signed in) and `fingerprint`, the sha256 of the normalized body (line endings
  unified, trailing whitespace dropped, one final newline); a body that differs from its
  fingerprint fails the validator and the merge gate;
- `review_every` (days) overrides the type's freshness window: snapshot and status 7,
  process, directive and review 90, concept and entity 180, policy and decision 365; a
  page unanswered four weeks past its window is confidence low, derived from its dates;
- on a released page: `released_by`, `released_from`, `release_class`, `evidence` (an
  opaque pointer, never the content); on a decision: `supersedes`.

Witness pages (`knowledge/sources/`) are `type: source` and never `stable`; status pages
(`live/`) are never `stable`.

### Links by id

A link is `[text](id:<id>)`, the id being the page's `p-` plus eight hex characters.
`_meta/state/ids.yaml` maps every id in this space to its file; an id not found here is
looked up in each mount's `ids.yaml`. The validator fails a link that resolves nowhere.
Hubs hold links only.

### The registry (`_meta/subjects.yaml`)

`subjects:` is a list; each entry has `id` (a lowercase slug, shared up the ladder: a space
reuses an ancestor's id and mints a new one only when no space above has one), `kind` (one
of the kinds), `title`, `state` (`active`, `dormant`, `archived`), optional `part_of` (any
depth, loops refused), `owner` (parts inherit their umbrella's; none means the space
owner), `home` (the space where the subject's shared conclusions live; none means they
stay here), `match` (the fixed identifiers by which a sweep attaches evidence; keys
`tracker`, `repo`, `channel`, `label`, `thread`, `calendar`, `github` (a person's host
login), and two inferred ones: `domain` (a client's mail domain on any address of the
record: from, to, cc, participants) and `keywords` (whole words in the title of a call or a
calendar event); a record matching none or several subjects is never attached, and one
placed by an inferred key alone never travels at once), `aliases` (for a person, also any
second mail address they use: calls name people by address),
`important`, `stateful` (status page yes or no), `added`, `added_by`. Hierarchy and
ownership live here, never in paths. A subject's home may hold its id too, to add the
`aliases` and `match` keys its parent keeps out (a restricted client's project names and
mail domains); a space that mounts the home reads them on top of the parent's entry, and no
other space may add names to it.

### Needs-you items

One list per person. Each routine writes its own file `_inbound/needs-you/<writer>.yaml`,
a list of `{id, kind, text, recommendation, default, deadline, depends, payload,
fingerprint}`; `kind` is one of `candidate`, `check`, `leaving`, `request`, `question`,
`answer`, `reconnect`, `outbox`, `high_stakes`, `sample`, `protected_pr`, `fork_pr`. Candidates and
checks are read from their folders directly; items held in doubt appear as one count line.
An item may carry `check` (the read-back that proves an owner step done) and `for` (whom it
is for, when not the space's owner). A personal space's list also shows the items its
mounted spaces route to its person (a pending landing or a question `for` them, a check
routed to them first), marked with the space's name: one list per person. Order: deadline
first (none last), then `depends`, then kind. About seven are shown, the rest counted. The verbs are `yes`, `edit`, `no`, `later`, `always`, in bulk; `submit`
executes them for the named person.

### Queue items and their classes

`_outbound/queue/<id>.md` carries `class` (`at_once`, `window`, `never`, `doubt`,
`signal`, `retraction`), `destination` (a space name), `stage_after` (an ISO time) and
`listed` (`false` until a morning view listed it, then the date), and on an `at_once`
item the `path` that let it through (`standing_choice:<destination>:<kind>` or
`audience:<source>`). `at_once` ships when `stage_after` has passed; `window` ships only
after `stage_after` has passed and `listed` is set; a path paused in
`_meta/state/paused.jsonl` (four scaled weeks after a pull-back or a failed sample) sends its
records the window way; `never` and `doubt` go to `_outbound/held/<id>.md` with `reason` (`doubt`,
`left_out`, `never_leaves`, `scan`). Signals are `_outbound/signals/<id>.yaml` and carry no
content.

### Run lines

`_meta/state/runs.jsonl`, one JSON object per line, append-only, merged by union:
`{"routine", "at" (ISO UTC), "space", "cost_tokens", "notes": {...}}`. Every tool run
appends one; an agent's own step appends another with `notes.step` set, never editing the
tool's line. The health line sums `cost_tokens` per space.

### The question log

`_meta/questions.log`, one JSON object per line: the question, its `subject`, the `tier`
of the answer, the `pages` used, `needed_steering` (the person had to redirect),
`answered_by_request`, `at`. It is read first by any query, feeds distill's "asked about"
trigger, the test questions and the hubs' see-also lines, and never leaves its space.

### The release rule and the 24-hour floor

A release waits only when it widens who can see something (rules.md section 6). At once:
content its receiving audience can already read (the source's declared `audience` covers
the destination), content its holder approved (by name on `submit`, or by a standing choice
for that destination), retractions and signals. Otherwise after one window (24 hours is the
floor) and one morning view that listed it: an item no morning view listed does not leave.
Doubt about the class means the window; doubt about privacy means the item stays home and
is shown as a count. The destination's `left_out` and the source's `never_leaves` are
checked on every release, whatever the class. External disclosure is not internal
permission. A pull-back or a failed sample pauses the path that let an item through for
four weeks.

### Landing at the home

A release with `released_from` that carries a signature is filed by the destination's
librarian (`landing.py`): silently into `knowledge/` when it is routine; pending in
`_inbound/candidates/` with `landing:` (why) and `for:` (whom) when it is high-stakes, from a
releaser under the trust switch (`_meta/state/trust.yaml`), a change to someone else's signed
page, one of two releases for one page with different text (`disagrees_with`), or a body that
does not match its fingerprint; a duplicate is dropped with a line in `log.md`. Silent
landings are listed in `_meta/state/landed.jsonl`; the weekly `sample` item draws from them.

### Procedure pages

A `process` page has a `## Steps` section and a `## Checks` section; a check may carry a
machine form in backticks (`never: <term>`, `always: <term>`) that `procedure.py check`
applies to an outcome. A failed check becomes a witness page against the procedure's subject.

### Requests (the fourth wire)

A request is `_outbound/requests/<rq-id>.md` with `to` (the subject's home), `from`,
`asked_by`, `subject`, `class` (`status`, `milestones`, `contact`, `detail`,
`correspondence`), `question`, `narrowest` and `expires_at` (one scaled day). It travels at
once through the `channel` space whose `between` names both spaces (`_inbound/requests/`
there); the holder's channel step answers from its `request_choices` (`[{from, class,
answer}]`, `from` a space, a profile or `any`, `answer` `at_once`, `ask` or `never`; project
and brain defaults answer status, milestones and contact at once and never correspondence; a
personal space has none), writes its answer to `_outbound/answers/<rq-id>.md` with
`approved_by` and every line's tier and age, and lists the remainder in
`_inbound/needs-you/requests.yaml`. The asker lands an answer as a witness page and an
`_inbound/answers/<rq-id>.md` item. Logs: `_meta/state/requests-asked.jsonl` (asker) and
`_meta/state/requests.jsonl` (holder); neither leaves its space.

### The high-stakes list (closed)

Waits 24 hours and lands pending for the domain owner, whatever the release path (rules.md
section 2): identity and rules; anything that binds the company (pricing rules, contract
terms, commitments); policy (HR, security, legal); claims about a named person; changes to
someone else's signed page.

### Tiers

Every grounding line carries one: `signed` (a `stable` page), `system` (a tracker, merged
code, sent mail), `pending` (a candidate or a release awaiting a yes), `witness` (a dated
source page, unverified), `reported` (someone's word, recorded), `working` (desk material).
Answers carry the tier and the age of every line they rest on.

## 2. What an agent must never do

The hard rules of rules.md section 1, restated for a tool:

1. Secrets never enter a repository: no keys, tokens, passwords or access links; the scan
   unstages a hit, counts it and never quotes it.
2. Never-leaves material never leaves its space, by any path, at any badge level.
3. Only a person signs: `status: stable` is written only by `sign.py apply` executing a
   named person's verdict, bound to the fingerprint read back to them; a routine or agent
   that writes it is a defect.
4. Agents never widen their own authority: a badge level is raised only by a human space
   owner; evidence lowers it for four weeks.
5. One writer each: only the platform writes `main`; each append-only file is merged by
   union; each outside system is written by one routine, never by sessions.
6. Agents never send, post, spend or delete. Drafts go to `outbox/`; the person fires them.
   The one send is the morning mail, the person's own mail to themselves, sent unchanged.
7. Agents edit files; the sync script commits; the platform merges. No session runs a git
   write, and nothing writes directly into another space.

## 3. What makes a tool conformant

A tool is conformant when it:

- reads and writes the files above, under the paths above, and nothing else; it keeps its
  own state in `_meta/state/` and its items in its own `_inbound/needs-you/<writer>.yaml`;
- never writes `status: stable` except while executing a named person's verdict whose
  fingerprint matches the text that lands;
- never runs git: the sync script commits, the platform merges;
- respects the badge: nothing above `propose` for `send_outward` and `spend`; a release
  only through the queue, by class, after the window and the morning view when it widens;
- appends one run line per run and leaves yesterday's output, labelled, when it fails;
- passes `validate.py` on what it wrote and keeps the answer and leak tests green;
- works from the files alone, on any harness; a connector, a search engine or a tracker is an
  adapter outside the shape (files in, files out).

A tool that does all of this without the skills in `.claude/skills/` is as conformant as
one that uses them: the shape is the standard, the skills its reference implementation.

## 4. Versioning

`shape:` in `space.yaml` names the contract a space follows; the validator refuses a space
whose `shape` it does not know. A new shape version ships with the migration that takes a
space from the previous one; the librarian (`status`) runs it as one commit, the answer and
leak tests guard it, and template sync lands it in every space. This page is the contract
of shape 1; a later shape adds its own page beside it and names what changed.
