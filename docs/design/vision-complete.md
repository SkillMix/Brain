---
title: Completing the vision
---
# Completing the vision: design and plan

This page is the spec and the plan for everything the vision left for later, its open items
as decided on 2026-10-12, the phase-1 items an audit found missing, five defects the first
install exposed, and phase 5 readiness. One section per sub-project, built in this order:
template first (spec, tests, a fixture scenario in CI), then template sync into each live
space and a live check. Decisions made while building are one line each in
`docs/decisions.md`.

Principles carried from the vision: minimal engineering; files in, files out; humans sign;
questions to people rare and pre-answered; measurement before what it measures; every
optional layer off until measured.

## 0. Scope

| # | Sub-project | Source |
|---|---|---|
| 1 | Loose ends: five defects, go-live inside onboard | first install |
| 2 | Measurement: the probe, the health line, the engine file, an index per project | vision 11, 13 |
| 3 | Phase-1 completion: landing at the home, sample and trust switch, path pause, outbox reconcile, procedure pages, reconnect items, the trust root | vision 9, 10, 5, 12, 14 (audit) |
| 4 | Content upkeep: the weekly review batch, the window pass, conflict notes, spec state, ages | vision 13 |
| 5 | The librarian: kind moves, splits, merges, umbrellas, strays, see-also, shape migrations, one commit per change, act then verify, ownership asked once | vision 13, 6, 16 |
| 6 | The decisions index (the decisions API) | open item, decided |
| 7 | Transcripts: the adapter (off by a switch), the capture rate, people without a workspace | open items, decided |
| 8 | The fourth wire: requests, standing choices by class, the corridor, the channel step, expiry, proposals, the way back, the helper, both logs, asker leak tests, mint linking, divisions and sub-teams | vision 10, 4 |
| 9 | Answering by email reply (an adapter) | vision 14 |
| 10 | The research step and a generic cold start | vision 15 |
| 11 | Phase 5 readiness | vision 18 |

Parked by the vision and left parked: a finance vault, hosted search for personal
workspaces, company history across walls beyond catalogue pages. Budget stays declared and
unenforced (decided 2026-10-11).

## 1. Loose ends

**Defects.**
- D1. The briefing's doubt line counts list entries, the health line counts pages. Fix: the
  doubt item carries the page count, and the mail's count for it is that page count.
- D2. The once-a-day briefing guard reads a field a chain's run line may not carry. Fix:
  `morning_view.py <root> sent-today` (exit 0 when a send line exists for this scaled day,
  whichever routine wrote it: `notes.email` or `notes.message_id`) and
  `morning_view.py <root> record-send --id <message id>` writes the canonical line. The skill
  and the chain prompts use both; no agent writes the line by hand.
- D3. Offboard leaves the root's onboard state listing the offboarded space. Fix: offboard
  removes the space's row from the onboarding root's `_meta/state/onboard.yaml` when that
  root is reachable on disk, and lists it as an owner item otherwise.
- D4. A high-stakes window item is never stamped `listed`, so it never ships. Fix: the
  morning view stamps every window item leaving within the window, high-stakes or not; at
  the destination it lands pending (section 3), which is where the domain owner acts.
- D5. The behaviour runner copies the whole space, private zones included. Fix: the copy
  skips `personal/`, `academic/`, `_scratch/`, `.git/` and `_meta/state/feed/`.

**Go-live inside onboard.** `onboard --go-live` on a shadow space switches it to live:
`mode: live`, `time_scale` removed, mounts repointed from shadow names to live names (a map
in the answers), onboard state rewritten for the live paths, the sync task re-registered for
the live checkout, platform settings re-applied, each step a row with its read-back.
Re-runnable; a live space is left unchanged.

**Tests.** One unit test per defect; go-live: a shadow space goes live, the validator
passes, a second run changes nothing.

## 2. Measurement

**The probe.** Answer tests gain `kind: walk | status | decision` (default walk) and the
methods `walk`, `search` and `decisions`. Every run records, per question, which methods
found every expected fact, so recall is known per method and per kind:
`_meta/state/probe.jsonl`, one line per run. `tests_run.py <root> probe` runs the set by
every method; the weekly walk-and-search probe is this command, run by the librarian once a
scaled week. A question logged with `needed_steering` is proposed as a probe question (one
Needs-you item with the question and the pages that answered).

**The health line** gains, per space: `probe walk p/n, search p/n, decisions p/n`, `capture
rate` for personal spaces, `requests asked, answered, expired` and `index <engine or none>`.
The checker hit rate stays.

**The engine file.** `_meta/search.yaml`: `{engine: none, version: null, sources:
[knowledge, live], freshness_hours: 24, link_step: off, atoms: off, measured: []}`. The
validator refuses `link_step` or `atoms` on without a `measured` entry showing a probe gain.
`search.py <root> query <text>` is the plain-text search every space has; an engine is an
adapter behind the same command, and `search.py <root> prune` drops entries whose files
moved (a no-op without an engine).

**An index per project when it grows.** A project space with more than 400 pages whose
probe shows search recall above walk recall gets one Needs-you item proposing an engine for
its own audience (yes writes `engine:` in its `search.yaml`). Nothing turns on by itself.

**Fixture.** The fixture's answer tests carry walk, status and decision questions; the probe
line appears in the fixture's health page.

## 3. Phase-1 completion

**Landing at the home.** A release landed in `_inbound/candidates/` with `released_from` is
filed by the destination's librarian per the vision's table: a routine signed release (its
fingerprint matches its body, it is not high-stakes, it does not change someone else's
signed page) is filed into `knowledge/` silently and logged; a high-stakes one stays pending
for the domain owner; a change to someone else's signed page stays pending for that page's
signer; two releases for one page with different text both stay, each marked
`disagrees_with`, for the domain owner to pick; a release whose fingerprint equals the
page's is a duplicate, dropped with a log line. Unsigned releases (records) stay candidates.
A releaser under the trust switch lands pending.

**The weekly sample and the trust switch.** Once a scaled week the librarian picks up to
three silent landings and at-once arrivals of the last week (seeded by the week) and writes
one `sample` item for the domain owner, else the space owner. Yes means fine; no is a failed
sample. A releaser whose failed share over four scaled weeks is above 0.2 with at least five
samples lands pending until the share recovers (`_meta/state/trust.yaml`).

**The path pause.** Every at-once item records the path that let it through (`path:
standing_choice:<destination>:<kind>` or `path: audience:<source>`). A pull-back of such an
item, or a failed sample of it, pauses that path for four scaled weeks
(`_meta/state/paused.jsonl`); while paused the sweep classifies the path's records as
`window`. Lowering is automatic; nothing raises a level but a person.

**Outbox reconcile.** `outbox_reconcile.py <root>`: a draft in `outbox/` whose subject and
recipient match a sent record in the mail feed moves to `outbox/sent/` with a `## Sent`
section (message id, time); a draft idle fourteen scaled days moves to `outbox/archive/`
silently. It never sends. It runs in the daily chain before the morning view.

**Procedure pages.** A `process` page has a `## Steps` list and a `## Checks` list; a check
line may carry a machine form in backticks (`never: margin`, `always: crew sends`). The
validator requires both sections on process pages. `procedure.py <root> check <page>
<output file>` applies the machine checks to an outcome; a failed check writes an evidence
page against the procedure's subject, so the checker and distill see it like any evidence.

**Reconnect items.** An agent's sweep step that records sources as unreachable
(`notes.unreachable: [names]`) makes the next status run write one `reconnect` item per
source with its read-back (the next sweep line shows it reachable); the item clears itself.

**The trust root.** rules.md gains section 9 (what a signature is and why another space
believes it) and section 10 (people without a workspace, sub-project 7).

**Fixture.** Ada's signed Harbor page travels to harbor and lands silently; a conflicting
release lands beside it as a disagreement; a high-stakes release lands pending; the weekly
sample appears; a pull-back of an at-once item pauses its path, and the next record of that
kind waits the window.

## 4. Content upkeep

**The weekly review batch.** Once a scaled week the checker bundles every page past its
window and every draft older than its window into one candidate,
`_inbound/candidates/review-<date>.md` (type review): one numbered line each, with the
evidence since it was last verified and a recommendation. Window findings no longer become
separate check items; contradictions still do, at once. Verdicts name lines:
`review-<date>#3: yes` (still true: `verified_at`, `verified_by`; on a draft: sign it),
`#4: [edit, "<new body>"]` (apply it), `#5: retire` or `no` (status `deprecated`). A page
unanswered four weeks past its window is confidence low, derived from its dates.

**The window pass.** Unchanged: once a scaled day the checker adds every signed page whose
window closes within seven scaled days; these feed the next weekly batch.

**Conflict notes.** When the checker meets newer evidence against a page that lives in a
mounted ancestor (another space owns it), it queues a release: `class: window`,
`conflict_note: true`, destination the page's space, a short scrubbed note naming the page
id, the label and both values, and an evidence pointer. At the destination the librarian
marks the page `contested: [<note id>]` (frontmatter only; the signed body is untouched) and
the note joins the owner's next weekly batch.

**Spec state.** A page with `tracker: <issue id>` carries `state: proposed | approved |
building | live | superseded`, set by the librarian from the newest tracker record for that
issue among the witness pages (backlog, triage: proposed; todo, planned: approved; in
progress, in review: building; done: live; canceled, duplicate: superseded).

**Ages in every answer.** `ground.py <root> <id or path>...` prints one grounding line per
page: tier, scope, date, age in days, and "past window", "confidence low" or "contested"
where they apply; then "company view as of <brain pin date>" and "index built <age>". The
query skill ends every answer with these lines.

**Fixture.** On day 7 Ada's batch lists the Harbor page once its window (shortened by
`review_every`) has passed; a contradicting record about a brain page reaches the brain as a
conflict note and the page is marked contested.

## 5. The librarian

`librarian.py` holds the duties; `status.py` calls it and stays the one writer of
structure. Each duty finds work and either acts (mechanical) or writes a proposal
(judgment).

| Finding | Class | Action |
|---|---|---|
| a subject's registry kind differs from its folders | mechanical | move its folders to the registry kind |
| a page whose `subject` puts it in another folder; a loose file under a kind | mechanical | move it where it belongs; links by id keep working |
| a subject with more than 12 efforts or pages | judgment | propose parts under an umbrella, grouped by shared leading words |
| two subjects sharing a `match` identifier, a title or an alias | judgment | propose a merge; the old id becomes an alias |
| three or more subjects sharing a `client` or `theme` without an umbrella | judgment | propose an umbrella |
| a question that needed steering | mechanical | a see-also line on the hub (built) |
| a pending shape migration | mechanical | run it |
| a change that would move ownership | ask | one question to the owners, asked once |

**Proposals** are files `_meta/state/librarian/<id>.yaml` with the exact registry and file
edits. The status skill has an independent second agent review each one;
`librarian.py <root> apply <id> --reviewed-by <name>` executes it. Nothing judgment-class
applies without a recorded review.

**Act, then verify.** Before a change the librarian runs the answer and leak tests and
snapshots the files it will touch; after, it runs them again. A lower score restores the
snapshot and records the revert. Each change is one line in `log.md` with how to undo it.

**One commit per structural change.** After each verified change the librarian asks the sync
script to land exactly its paths: `sync.py --root <root> --only <paths>`, one commit
`chore: structure`. No session runs git; the script does.

**Ownership asked once.** Before any change the librarian compares `owner_of` for every
subject before and after; a difference stops the change and writes one question to the
owners involved, recorded in `_meta/state/librarian/asked.yaml` so it is never asked twice.

**Shape migrations.** `_meta/migrations/shape_<n>.py` each define `migrate(root)` taking a
space from shape n-1 to n. The librarian runs pending ones in order, verifies, and lands them
as one commit. Shape 1 has none; the runner is tested with a temporary shape.

**Fixture.** A stray page and a subject filed under the wrong kind are moved mechanically;
two subjects sharing an identifier produce a merge proposal; three projects sharing a client
produce an umbrella proposal; a reviewed proposal is applied with its reviewer recorded.

## 6. The decisions index

`decisions.py <root> build` writes `_meta/state/decisions.yaml`: every signed decision page
here (`knowledge/decisions/` and any page of type decision) with id, title, subject, signed
date, signer, `supersedes`, `superseded_by` (derived) and its first line. The librarian
rebuilds it every run. `decisions.py <root> find [--subject S] [--text T]` searches this
space and every mount's index and lists the decisions in force first, superseded ones
marked. The query skill asks it first for "what did we decide" questions; the probe's
`decisions` method measures it against the walk and the search.

**Fixture.** A signed decision and a superseding one; the probe's decision question is
answered by the index.

## 7. Transcripts and capture

**The adapter.** `transcripts.py <root> scan` reads a harness's session transcripts from the
folder in `space.yaml` `transcripts: {path, switch: off}`, lists each session (id, start,
working directory) into `_meta/state/sessions.jsonl` (ids and times only, never content).
With `switch: on`, every session not captured in the daily notes becomes a feed record
(`kind: session`) in `_meta/state/feed/sessions.jsonl`, and the sweep turns it into a
witness page in this personal space only. Transcripts never travel: `transcript` is on
every destination's left-out list.

**The capture rate.** A session is captured when a daily-note entry carries its marker
(`<!-- session: <id> -->`, written by the capture skill). The health line shows captured
over seen for the last four scaled weeks. Below one half for four weeks, the librarian
proposes turning the switch on.

**People without a workspace** (rules.md section 10): their sessions are not captured and
nothing is swept for them; what they know reaches a space through the releases of people
who have a workspace, and through a project's own sources.

**Fixture.** Three session files for Ada, one captured: capture rate 0.33; with the switch
on, the two others become witness pages in Ada's space and in no other.

## 8. The fourth wire

**Requests as files.** `request.py <root> ask --subject S --class C --question Q --narrowest
N --asked-by P` writes `_outbound/requests/<rq-id>.md` addressed to the subject's home, with
`expires_at` one scaled day out. The asking agent first walks and searches what it may read;
a subject its space can read is answered there, without a request.

**Classes** (template defaults): `status` (status tier, milestones, last contact date),
`milestones`, `contact` (last contact date), `detail` (anything else from the knowledge
layer), `correspondence` (mail, calls, documents). **Standing choices for requests**
(`space.yaml` `request_choices: [{from, class, answer}]`, `from` a space name, a profile or
`any`; `answer` `at_once`, `ask` or `never`): the project template answers `status`,
`milestones` and `contact` at once to any space and never answers `correspondence`; a
personal space ships no choices, so every request waits for its person. The never-leaves
list and the asker's left-out list always apply.

**The corridor.** A `channel` space between two spaces carries requests and answers; both
mount it read-only and write into it through the release leg (`_inbound/requests/`,
`_inbound/answers/`). `mint.py <root> --channel <other space>` creates it (offline: a
sibling folder) and adds the mount to both.

**The channel step.** The sync task's release leg ships requests into the corridor and runs
`request.py <root> answer` in the holder: for each new request it decides the covered part
from the standing choices, renders it from the holder's status page and registry (tier and
age on every line), scrubs it and releases it into the corridor at once, naming the
uncovered part without sending it. The remainder becomes one pre-answered Needs-you item
for the holder ("<asker> asks Q; I would answer Y; it widens Z; I suggest the summary
only"): yes sends, edit sends the edit, no sends nothing, always writes a standing choice
for that class and asker.

**Expiry and proposals.** Unanswered after one scaled day a request expires: the asker's
request reads "no answer", the holder's item disappears and the holder's health line counts
it. After five requests of one class from one asker in thirty scaled days, the holder gets
one proposal: answer that class at once from now on (yes writes the choice).

**The way back.** `request.py <root> collect` in the asker lands each answer as a dated
witness page with its tier and age, marks the request answered, writes an `answer` item on
Needs you ("one answer arrived") and an owner notice payload. `request.py <root> wait
<rq-id> --timeout <minutes>` is the helper a live session runs in the background; a harness
without background agents runs `request.py <root> status <rq-id>` on the next message.

**Both logs.** The asker's question log records the question with `answered_by_request`; the
holder's `_meta/state/requests.jsonl` records who asked, what was answered and by which
choice. Neither leaves its space; both feed the proposals and the weekly sample.

**Asker leak tests.** `leaks.yaml` entries may carry `request: {asker, subject, class,
question}` with `must_not_find`; the test renders the holder's covered answer and fails on
any forbidden phrase.

**Mint linking.** Mint fills the new space's registry `match` with the chat channel and
tracker project named like the space, and proposes creating them (one Needs-you item each)
when they are missing. **Divisions and sub-teams:** mint takes `--parent`; a sub-team mounts
every ancestor flat (the brain, its division) and its releases go to the subject's home.

**Fixture.** Corridors chloe-harbor and ben-ada. Chloe asks harbor for Harbor's status:
answered at once by the project's default choice. Chloe asks harbor for its
correspondence: never. Chloe asks harbor a `detail` question: one item for harbor's owner.
Ben asks Ada's workspace something: it waits for Ada and expires. An asker leak test proves
the day rate never reaches Chloe by asking.

## 9. Answering by email reply

The mail sweep sees the person's reply to their morning briefing (a reply to the briefing
subject, from and to the owner's own address). `reply.py <root>` parses its first lines
("1-4 yes, 5 edit: the date is the ninth, 6 later") against the numbering of that day's
`live/needs-you.md`, writes the verdicts with the read-back of each named item to
`_inbound/answers/reply-<date>.md`, and lists one `answer` item: the next session reads it
back and asks for "submit". A reply never signs by itself.

## 10. The research step and a generic cold start

**Research first.** `research.py <root> ingest <records.jsonl>` takes records an agent
exported from the public web (the company's own site, the business registry, the code
organization; never private profiles compiled across sources) and writes witness pages with
`source: web` and their URL, and confirmation questions "I think X, based on Y; right?" for
identity and the registry.

**The cold start.** `cold_start.py <root> --archive <path>` reads an archive space or folder
as evidence: one confirmation per finding, batched by how much depends on it, capped at
twenty-five a batch, ordered; one candidate per active important subject with the archive
lines as unsigned evidence. Nothing is signed or copied as signed. The research step runs
first, then the archives.

## 11. Phase 5 readiness

A colleague's onboarding runs offline from the template with their answers and passes
`onboard --check`; the brain lists one Needs-you item per invitation (the owner invites;
onboard runs on the colleague's machine). Each other project's swap is a runbook plus
`onboard --go-live`, ready on the owner's name decision. Every owner item sits on Needs you
with its read-back.

## The plan

Each sub-project: write the failing tests from its section, implement, run the unit tests,
the validator and the fixture, land the template through the sync script, run template sync
in each live space, merge the protected pull requests after green checks, run the live
check, record the result in the daily note.

Live check, each: fixture green; validator 0 errors in every live space; the leak tests pass
on real data, or fail only on findings already on Needs you; Needs you renders; the health
line carries what the sub-project adds; nothing reaches Slack, Linear, clients or anyone but
the owner. From sub-project 8 on, a trial request from the owner's workspace to the project
is answered by a standing choice.

## 12. The audit of 2026-10-13: closing every gap

A second audit read every requirement of the vision against the template, with file
evidence. What it found partial or missing is built here, in six batches, each with its
tests, landed through the template and synced to every live space. Decisions are one line
each in `docs/decisions.md`. Parked items stay parked (section 0).

**Batch 1, defects in the flow.** Records handed sideways land as evidence
(`knowledge/sources/`), never as something to sign. Items held in doubt can be opened one by
one (`needs_you.py --doubt`), each with its reason and a recommendation, and answered: yes
releases it at once (the holder approved it), no keeps it home for good, always adds the
standing choice and releases it. Signing or approving a high-stakes item never skips the
window. A client mail naming a registered person is high-stakes; an amount the space's own
person sent out is a record of what was said, not a pricing rule. The weekly sample takes
five silent landings (the vision's number) and draws from request answers too; the morning
view counts the week's silent landings. A signal flags the home's status page ("changed
<date>, signal from <space>"). Releases carry the subject's `domain`.

**Batch 2, the badge.** `authority.py` answers one question, `level(space, action)`, from
`space.yaml` within the floors of `rules.md` section 3, and the tools ask it: release ships
nothing above the space's `release` level (`propose`: a person's yes first), the tracker
routine writes only at its `outside_system` level, nothing sends or spends. The validator
refuses a level above a floor. Every action above `propose` records `acted_for` and `path`.
Agent rows carry `acts_for`, `space` and `badge`; the never-leaves list is inherited from
the brain; `models:` is stated in `AGENTS.md` so any harness reading the contract knows
whether it may read the space.

**Batch 3, status that answers.** Open items by direction (we owe, waiting on them,
internal) from the last record of each thread; the `system` tier for tracker and code
records. The brain's projects page (`live/projects.md`) is generated from the registry,
the signals and the landings. The sweep writes snapshots in the same pass (`live/today.md`:
today's and tomorrow's meetings), and the morning briefing carries a who-is-this line per
meeting from the registry and the last contact. Onboarding's identity drafts reach Needs you.

**Batch 4, routines.** One routine writes the tracker: sessions queue requests as files
(`_outbound/tracker/`), the tracker routine creates the issues with its connector and
records their ids; nothing else writes the tracker. A failed chain step leaves yesterday's
output, labelled stale on the health line and in the briefing. Template sync runs weekly in
the daily chain, with the answer and leak tests before and after, and reverts a change that
lowers the score.

**Batch 5, structure, checks and releases.** The validator checks audience and parent, warns
on missing ancestor mounts and on pages past their window, and checks `review_every`. Online
mint adds every ancestor mount. A minted deal becomes a project kind. The checker compares a
named-person claim with live state and both hit rates use one formula. A brain sweeps nothing
of its own; a sweep reads only declared sources; status and distill follow only attached
records. Identical records released by several people land once; a disagreement writes a
Disagreements line. The left-out defaults gain role and other clients, and a mixed passage
keeps only its project facts. Approvals by name and at-once answers show under "left
yesterday", numbered for a pull-back by reply; a pull-back before shipping pauses its path;
the subject's owner can retract at the home; a person-to-person channel has exactly two
readers. Union merge covers nested run files; the search index is ignored by git.

**Batch 6, start and end.** `seed` backfills a person's feeds and distills only what is
active and important now; the cold start filters on important too and takes agent notes as
weak evidence. `forget.py <subject>` lists everything about a subject id; offboard lists the
raw personal records and removes them only on the person's own command. A recurring sharing
group or a second regular reader proposes a mint; mint makes a vault (a list of names). Every
skill has three behaviour scenarios, procedure pages included.

**Batch 7, the trust root and the last details.** `sign` records the repository it signed in
and a fingerprint line in its own log; landing at the home files a signed release silently
only when the signer's history shows that line, else pending. The default high-stakes words
cover HR, security and legal. A workspace with no feeds gets one connector offer each on
Needs you, and `onboard.py --connect <feed>` adds one. A release's log line names its path,
and a reply `left 2 no` pulls back what the briefing listed under Left yesterday.

**Settled by decision, not code** (the vision leaves the method open; each is a line in
`docs/decisions.md`): evidence lowers paths only; `models:` is stated in the contract;
connector permissions stay per person; combined answers and hit expansion live in the query
skill; the narrowest-shared-space rule is stated in `rules.md`; a corridor to a project
includes its crew; the project case starts from a seed or a mint signal.
