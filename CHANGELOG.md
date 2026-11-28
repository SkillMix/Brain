# Changelog

Notable changes to the template, newest first. Versions follow [semantic
versioning](https://semver.org); while the version starts with 0, a minor release may change
the shape and ships its migration. Each choice behind a change has a line in
`docs/decisions.md`.

## [Unreleased]

A record the export could not place as work or personal no longer waits for your answer in the
morning email. The sweep lets it in as work by itself when a script finds a hard signal (its
thread, channel or event series already holds company work, or it carries a registered
client's identifier) and says on its witness page why. Otherwise it waits, comes in by itself
when later company work shares its thread, channel or series, and goes after 90 days. Nobody is
asked; the health page counts what happened.

A recorded meeting's transcript is now the source, every time, from any notetaker. The
notetaker's summary and action items only say where to look, a disagreement between them and the
transcript is written down, and a line can cite the moment it rests on.

One mail, message or meeting that two colleagues' rooms both released now counts once where it
lands, when one copy adds nothing to the other. A script lists the likely pairs, a cheap agent
reads each pair, and a copy whose text its main already holds is marked, never deleted. Copies
that each hold what the other lacks (two routers sent different parts) stay evidence.

### Added

- **`landing.py <root> duplicates`** lists, as JSON, the likely pairs of one record arriving
  twice: two records under `knowledge/sources/` or still waiting in `_inbound/candidates/`, at
  least one released from another space and both sharing a subject (copies for different subjects
  each count once already), with the same original id once written one way (a source's name, as
  in `slack:` or `gcal-`, a calendar's `＠version` and the separators do not count), or mail from
  one sender in one minute, or two calls on one day starting within 30 minutes, whatever their
  titles. It never compares text, so two transcripts of one meeting with different errors still
  pair. A space's first run lists the pairs over every page already there.
- **`mark <main> <copy>`, `keep-both <a> <b>` and `undo <copy>`**, each with `--by`. `mark` marks
  a copy whose text the main already holds `duplicate_of: {id, by, at}`, filed or still waiting,
  and moves anything marked against it to the main; `keep-both` writes the pair to
  `_meta/state/duplicates.jsonl` and leaves both pages untouched and both evidence; `undo` takes a
  mark back, leaving the page as it was, and records the pair as kept. Each writes one line to
  `log.md`; a refused verdict prints one `refused:` line.
- **A marked copy counts once.** Distill folds it into its main copy, which takes its subjects
  (`distill.counted`, which also does what the archive filter did), so a subject candidate drops it
  on its next refresh; Needs you does not ask to file a marked arrival. The gate lets the
  receiving room's sync carry a `duplicate_of` mark on a release that still waits, and holds a
  release whose sender wrote one: the mark is the receiving room's.
- **The daily chain judges the pairs** after the librarian's proposals, as the status skill says
  under "One record that arrived twice".

### Changed

- **The door settles unsure records itself.** Before the sweep writes a page it checks every
  record parked in `_meta/state/private/unsure.jsonl` for a hard signal: its `identifiers.thread`,
  `channel` or `series` (a recurring event's series, a new field of the event record; a whole calendar
  is no signal) on a witness page of the space, or an identifier of a registered subject
  (a mail domain, a label, a linked repository; never a keyword of its title or a subject it names
  itself). With one, and no `"reason": "mixed"` from the export, it is written to its feed at once
  and its witness page carries `door: {by: context, signal: <what matched>}`. Nothing a record
  says admits it.
- **A parked record is a pointer.** Its line in the tracked `_meta/state/unsure-ids.jsonl` holds
  its id, source, when it was parked, a hash of each of its thread, channel and series ids and
  its reason (`no-signal` or `mixed`): no content, subject, sender, address or name.
- **Pointers resolve themselves.** After writing its pages the sweep compares every waiting
  pointer with their ids. A match admits a record this computer holds (`door: {by: match}`); a
  match is listed by `python _meta/tools/sweep.py <root> --refetch` until its witness page exists, so
  a record a cloud run parked, or admitted into a feed its clone took with it, is not lost: the next
  export fetches that record again by its id with the read-only connector.
- **Pointers expire silently** 90 days after they were parked, from the private store and for
  matching. Expiry counts from parking, never from a listing.
- **One health line** of counts: admitted at the door, admitted later by a match, left out as
  personal or academic (the export's run lines), waiting, expired. The morning email leaves it out.
- **A call record says what it holds.** Its text keeps up to three labelled parts, in order: the
  notetaker's summary or notes, its action items (both marked AI-written), then the transcript,
  one `[hh:mm:ss] <speaker>: <words>` line per utterance. A field says whether the transcript is
  there: `transcript: complete`, `pending` or `none`, the last two with a `transcript_why`. The
  witness page carries both fields. The sweep refuses a call record that says neither, or that
  says `complete` without one utterance line: no page, a `refuse` plan line, `refused` in the run
  line, and the feed's mark stays before it, so the fixed line is read on the next run.
- **A pending transcript is fetched later, like an unread attachment.** The sweep lists it in
  `_meta/state/attachments-unread.jsonl` under the name `transcript`; `--attachments` shows it
  every run, a cloud run's fresh clone included, until a call record `<record id>/transcript` is
  swept, or `--attachment-read` says the notetaker has none.
- **"The transcript, every time" is a rule the run cannot read as optional.** The sweep skill and
  the cloud sweep chain say a long transcript is exactly the case it is for. The chain's line that
  left "the transcripts" to the computer now names what it meant: Claude's own session transcripts.
- **The transcript is the source; the notetaker's output is a map.** One section of the route
  skill, which distill and the check point to: who, when and how much come from the transcript; a
  conflict is written down in one clause (in a draft, under `## Disagreements`); an unclear
  transcript stays "owner unclear"; a call page without its transcript is hearsay; the witness
  page is never corrected. It comes with guidance on reading a transcript: misheard names, generic
  or swapped speaker labels, misheard numbers and dates, mixed languages, soft spoken agreement.
- **Citations can name the moment.** `[src: <record id>@<hh:mm:ss>]` (or `@mm:ss`): `distill.py
  verify` accepts it when the record is in evidence and its page, split parts included, holds an
  utterance at or before that time and not past the last. A plain `[src: <record id>]` still
  passes.

### Removed

- The morning email's "Is this work?" list, its eight-digit codes and its count of parked
  records; the `<code> yes` and `<code> no` reply verbs in `reply.py`; `first_listed`,
  `mark_listed`, `waiting`, `keep`, `drop`, `code_of`, `line_for` and `kept_since` in `unsure.py`;
  the 14-day expiry from a record's first listing.

### Upgrading

A record already parked on a computer is checked at the door once on the first sweep after the
update, and comes in (labelled `match`) when its thread, channel or series already holds company
work. Pointer lines written before the update hold no ids, so a record a cloud run parked before it
can no longer match and goes 90 days after it was parked. An older store's `code` and
`first_listed` fields are never copied into a feed.

Records that arrived twice before the update are checked by the first daily chain after it:
`duplicates` reads every page already there, so that first run lists more pairs than later ones.
To run it by hand: `python _meta/tools/landing.py <root> duplicates`, then a verdict per pair.

## [0.3.10] - 2026-11-27

A space whose registry uses a kind the brain adds (`deals`, say) no longer fails its checks, which
run without the brain mounted, so its protected update can land.

### Fixed

- **A kind of the brain's is a warning while the brain is not on disk.** The company's kinds are
  declared in the brain, and the checks of a space run without its mounts, so there the space fell
  back to the default kinds and called a subject of a kind the brain added an error. While a
  space's brain mount is listed but absent (and the space declares no kinds of its own), the
  unknown kind is a warning naming the absent brain, as an id link into an absent mount already
  is. With the brain on disk a kind it does not declare is still an error.

## [0.3.9] - 2026-11-27

A space's checks no longer fail on twelve tests that read files only the template has, so the
protected update a space was holding back can land again, with every fix it carries.

### Fixed

- **Tests that read template-only files run in the template only.** Every space runs the
  template's tests in its checks, but template-sync never carries `SECURITY.md`, the requirements
  files, `.gitignore`, `.github/CODEOWNERS` or `docs/decisions.md` into one. Twelve tests read one
  of them, so a space's protected pull request stayed red, and the machinery it carried reached
  neither the space's `main` nor its cloud runs. They now carry the `template_only` mark, as the
  tests that read the template's own `space.yaml` and `.gitattributes` already did. Where a test
  also checked something every space has (the workflows' hash pins, the release workflow, the
  tools' docstrings), that part is a test of its own and still runs everywhere.

## [0.3.8] - 2026-11-25

Search in your own space now also reads your daily notes, your drafts and your private briefs,
and says how far to trust each hit. A draft that a second agent checked can be read as
knowledge, marked "checked, not signed". The router labels what each sentence is, so a draft
that binds nothing no longer waits on Needs you. And once a day is over, your notes about a
project or client can travel to its space as one digest, cut by the router and checked by a
second agent, after the usual window.

### Added

- **Search reads where a personal space writes.** `search.py query`, `lines` and the index read
  `journal/`, `_inbound/candidates/` and `_meta/state/private/briefs/` next to `work/`. Every hit
  prints its tier. A mount keeps its own zones, and a linked codebase's search (`--for`) still
  reads no note and no brief. The index file moves to `_meta/state/private/`, which the sync never
  stages.
- **Checked drafts are readable.** A draft in `_inbound/candidates/` with a `checked:` stamp
  counts as knowledge in search, the grounding lines, the hub and the status page (under its own
  heading), always labelled `checked, not signed`. A policy, directive, process or decision draft,
  a draft about a person, a draft that would change a signed page and anything the check flagged
  high stakes still wait for a signature. The distill check now flags a claim about a named
  person, a promise to a client, a rule or a binding decision.
- **Labels at routing.** The router gives each sentence it extracts a kind (`decision`,
  `commitment`, `event`, `fact`, `position`) and says whether it binds the company; a second
  labeller says it again. A draft leaves Needs you only when both say it does not bind, and a
  checked one is then read as `checked, not signed`. Events go to the subject's timeline. A
  stub that no agent has written yet never reaches you: Needs you counts it on the health line
  for the agent instead.
- **The daily-note digest.** Once a day is over, your notes about a project or client homed in
  another space become one digest record for that day. The router sends it only as verbatim
  parts, always after the window, never to a deal or a part of one, and never with a part that
  names a registered person; a second agent checks it first, as for every record that leaves.
  An entry with a `private:` line never travels. The rule the router writes it under is the one
  measured in a seeded leak test; the suite tests what code stops, and the judged kinds rest on
  that agent test.

### Changed

- Every search hit and every `lines` file now ends with its tier in brackets.
- The status page's timeline shows the router's sentence for an event in place of the record's
  title.

## [0.3.7] - 2026-11-25

A space that starts from an old archive (the cold start) no longer drafts identity pages that
turn your space's check red. It asks you instead, quotes the archive and names the page you edit
by hand. If your space still holds such a draft, the check now tells you how to clear it.

### Fixed

- **An identity confirmation from the cold start is a question, not a candidate.** Since 0.3.3
  sign lands pages only under `knowledge/` and the gate guards `identity/`, so a candidate for
  `identity/<name>.md` could never be signed and the check called it a bad target, which turned
  your space's required check red. The question now names the page, quotes the archive and says to
  edit the page yourself; the line Needs you shows says so too.
- **An older identity draft gets a check message that says what to do**: move it out of
  `_inbound/candidates/` and edit `identity/` yourself.

## [0.3.6] - 2026-11-24

What you release now arrives, or comes back to you: a release counts as released only once its
pull request merged, and a page released again lands over the old one. Needs you shows what is
waiting for you, and a no says what else it does before you give it.

### Fixed

- **A new draft that links another new draft no longer turns the sync check red.** The check
  trusted only the saved id index, which only the daily status step rebuilds. The ids of the pages
  it has just read on disk now count too. A link to an id found neither on disk nor in the saved index still fails.
- **A page released again passes the destination's check.** The landed page and its new release
  counted as two pages with one id, so the release never reached landing, which updates the page
  in place.
- **A release counts as released only once its pull request merged.** An item sent in a pull
  request waits in `_outbound/sent/` until it merged; closed without merging, it is held as
  `not landed` for its owner, and one still unmerged a day later is on the health line.
- **A home's own entry for a subject keeps the brain's owner and classification.** An entry with
  only names (aliases, match) hid them, so the subject looked unclassified in its own home and its
  drafts dropped out of the sign tool.
- **At `propose`, Needs you lists every queue item that waits for your yes**, at-once items
  included, and says "waits for your yes" where it said "leaves".
- **Many plain drafts fold into one line.** More than ten drafts with no deadline and no
  high-stakes flag become "drafts to sign when you have time: N"; `needs_you.py --drafts` opens it.
- **A no that pauses a release path says so** on Needs you and in the read-back, and the apply
  report names the path it paused.
- **An edit on a record is refused, and the read-back says so.** It was filed unchanged without a
  word.
- **The read-back of an item that already left says a no retracts it there.**

### Tests

- Proof for two earlier fixes: a release run builds on an open release pull request (0.3.3), and
  the sign read-back prints any character on a cp1252 pipe (0.3.3).

## [0.3.5] - 2026-11-17

A space now tells you when part of it has stopped: a routine that did not run in time, a computer
that cannot bring main in and a mount that fell behind each become one item on Needs you. Status
and filing go on when no agent starts the daily, and no tool writes into a mount any more.

### Added

- **The flow runs without the model.** When no agent started the daily chain (Claude signed out,
  or out of allowance), the sync, which the OS runs every 15 minutes, runs the daily's steps that
  need no model itself, once a day: status with landing and the id index, access, the link check,
  requests, release, the tests. No morning view, so no window item is listed and none ships on a
  briefing nobody received; no template sync. The health line keeps saying `daily not started`,
  and what the librarian proposes waits for a run with an agent.
- **A stage that stopped is said by the sync.** Every stage has a last run (`sync.last_ran`), the
  health line dates each, and a stage past its window is `<stage> stale` in
  `_meta/state/stale.yaml` beside a chain that did not start: sweep, distill and the router after
  twice the sweep's longest gap, status and release after two days, a mount the sync could not move
  for a week. One item on Needs you names everything that is late, and the sync's run line carries
  it (`stale`, `pins_held`, `gap_hours`). While the routines are paused nothing is asked.
- **A computer that cannot bring main in says so.** A fetch that fails, a merge that stops, a run
  that dies and a run skipped because git is mid-operation were retried silently for good. Once the
  same cause has lasted a day it is one item on Needs you, with since when and what is in the way.
- **`chain.py checkout <root> <mount>`** prints this computer's own checkout of a mounted space.

### Fixed

- **A mount is read-only to every tool.** A tool run with a mount as its root wrote drafts and
  verdicts that nothing ever committed, and the changed copy stopped following its space. The page
  writers now refuse every destination inside a copy of another space (a mount a space lists, or a
  nested git folder nobody listed), and the tools that write by other means refuse one as their
  root. A mount kept outside the space that lists it is a checkout of its own. Reading a mount is
  unchanged. The sign skill says where an item of another space is answered.
- **A cloud run lands the tools' own logs past 1 MB.** The run log and the router's log pass that
  size for good one day; from then on a cloud run's own run line never landed and the health
  signals said the cloud was not running.
- **An answered item that comes back is shown again.** The routines' sign-in and reconnect items
  had one fixed id each, and Needs you hides an id that has an answer: a second loss of the sign-in
  was never shown. Each occurrence now has an id of its own.
- **A pull request that waits for your merge says why it cannot be merged**: a conflict with main,
  a failed check or a check that has not passed yet, before you click.

### After the update

- A mount that holds local changes is named in the sync's run line (`pins_held`) from the first
  run, and after a week on Needs you. Nothing cleans it for you: look at what was written there
  before you reset it, since a verdict given in a mount was never recorded in its space.
- The first sync may put one item on Needs you for what has been late for a while. That is the
  alarm working, not a new fault.

## [0.3.4] - 2026-11-16

Spaces made from the template before it moved keep their updates: template sync no longer follows
a template GitHub records as archived, so they follow the official template, without a
`template:` line. Take this release rather than 0.3.3.

### Fixed

- **An archived template is no template to follow.** GitHub records the template a repository
  was made from by repository, and reports it under its current name: after the move every space
  made before it names the archive, and 0.3.3's sync stopped there. Template sync and onboarding
  now read the record with its archived flag and treat an archived template as none, so the
  space follows `template:` in `space.yaml`, its recorded origin, or the official template.

## [0.3.3] - 2026-11-15

The template is hardened before it goes public: a release can no longer write outside its place,
your answers live where routines cannot write them, and every path made from data is checked. The
apps step and the routines see your connected apps again.

### Security

- **A red-team pass before the template goes public.** Its fixes: one path check for every path
  made from data, so a released page or a file move cannot write outside its place; the person's
  answers in `_meta/state/verdicts.jsonl`, which routines cannot write, with every earlier answer
  kept by a one-time cut-over; the person's verbs refuse inside a routine; queue files, clone
  addresses and template sources are validated between spaces; a withdrawal of a signed page waits
  for its owner; and the secrets scan covers more formats.
- **Template sync follows the template a space was made from.** Without `template:` in
  `space.yaml` or `_meta/state/template-origin.yaml`, the source is the template GitHub recorded
  when the space's repository was made, else the official one, never what the provenance says. A
  space whose record names an earlier copy of the template sets `template: owner/name` once; the
  sync stops and says so until it does.

### Added

- **Search shows the matching lines.** `search.py lines "<term>"` prints every matching line with
  its line number and two lines of context, grouped by file, over the same layers and zones as
  `query`. It runs on ripgrep when `rg` is installed and on plain Python otherwise, with the
  same output; `--regex` takes alternatives, `-C` more context, `--for` the rings of a linked
  subject. The query skill now searches with it only, never with `grep -r` from the space's root
  (which also read every session worktree) and never file by file: those two took about half of a
  five-minute answer.

### Fixed

- **The apps step and the routines see the connected apps again.** Unless `MCP_CONNECTION_NONBLOCKING`
  is `false`, Claude's command line connects a person's apps in the background, and on Claude Code
  2.1.312 its first event lists none of them: the apps step answered "No app is connected to Claude
  yet." on an account with six, and a routine was granted no app's tools. A desktop session sets the
  variable to `true` for the runs it starts, so setup's own runs took that value. Every run that
  setup and the routines start (the readers, the probe, the sign-ins, a routine) now sets it to
  `false`, whatever the caller's environment said, so the run waits for the apps and lists them.
- **A cloud sweep no longer stops for good on a branch that will not merge.** When the last
  cloud run's branch conflicted with main (two candidate pages rewritten on both sides was
  enough), every later run stopped at its first step and the computer's sync would not adopt
  it either, so nothing was swept until someone merged it by hand; the Needs-you item it left
  said the cloud runs "keep adding to it". Now the run sets that branch aside and starts fresh
  from main, which loses nothing (what it wrote comes again from main's cursors), and its push
  replaces the branch. Three fresh starts in a row become one plain item on Needs you; a clean
  run ends it. The computer no longer asks about a cloud pull request it cannot adopt, and its
  old `cloud-adopt-<n>` items go.
- **A refreshed page keeps its records and its high-stakes flag.** A draft whose body is the
  signed page's, written by an older distill or by an agent that started from the page, named
  only the records that had just arrived: `verify` flagged every line it took over from the page
  ("source ... is not in evidence", about 125 lines on one deal page), and signing it cut the
  page's evidence from 74 records to 2 and dropped its `high_stakes` flag, so the release was
  queued `at_once` and skipped the window. Distill now carries the page's evidence and flag into
  every draft over it, whatever the body, and `sign` keeps the page's evidence (the draft's
  records first, then the page's), its `set_aside` entries and its flag when a draft is signed
  over it. A record the draft sets aside leaves the evidence; a flag the draft names replaces the
  page's.

## [0.3.2] - 2026-10-30

A draft for a subject whose page lives in the brain starts from that page, not a bare title, and
the router's context now holds that subject's status page. Setup turns on the app's archiving of
idle sessions, so their copies of the space stop piling up.

### Added

- **Setup turns on archiving of idle sessions.** Each session the desktop app starts may get a
  worktree inside the space, a copy of the whole space, and the app keeps it until the session is
  archived. With archiving off, the app's default, one space held 31 of them after a week, and a
  search from the space's root read every copy. Setup now turns archiving on for sessions idle
  for 7 days, through the app's own card and only when it is off; a session with unsaved work
  keeps its worktree.

### Fixed

- **A draft for a subject homed elsewhere starts from its page.** Distill looked for the existing
  page only in its own space, so a subject whose page lives in the brain (or a crew's space) got a
  title with nothing under it, then the `## New evidence` list. It now reads the page at the home
  through its mount, found by the mount's name or the space's own (the brain is mounted as `brain`
  and named otherwise), and falls back to the page here. The page's `evidence` comes along, so
  its lines keep their sources and pass `verify`; the list names only the records not behind the
  page. A draft that is still the stub takes up the page on its next refresh; one an agent wrote is
  kept. The brief pack (`brief.py pack`) looked the home up by the mount's name alone, so it missed
  the brain's signed pages and status page; it now lists them.
- **One lookup finds a space by its mount's name or its own.** A home, a holder and a destination
  name the space, but several tools looked them up by the mount's name, and the brain is mounted
  as `brain` and named otherwise. `Space.space_root` now does it once, and route, classify,
  request, release, link, brief, distill and the answer tests use it. The router's pack had no
  status page for a subject homed in the brain, and the answer tests' walk read none of its pages
  there; both do now.

## [0.3.1] - 2026-10-29

A cloud sweep's lines in the run log, the journal and the question log are no longer taken out
again by the computer's next sync.

### Fixed

- **A split push never undoes what main got.** When the computer's merge could not bring main in
  (a conflict, or a file the scan left local that main also changed) and a protected change split
  the push, the plain part, built on main's tip, carried HEAD's older copy of every path main had
  moved: a cloud run's lines in `runs.jsonl`, the journal or `_meta/questions.log` landed and were
  then taken out again. The push now carries only what the checkout itself changed since the two
  parted; a path both changed waits for the merge (`waiting_merge` on the run line).

## [0.3.0] - 2026-10-28

Setup is one guided conversation, the sign sitting asks only what no record answers, and the sweep can run in
the cloud, read-only, while the computer is closed.

Cloud sweep: the sweep runs when the computer is closed, read-only; the daily stays on the computer.

### Added

- **The sweep in the cloud.** "Run my routines in the cloud" moves the sweep to a scheduled task on
  claude.ai, after a proof run shows the task refuses a protected edit, a tool it was not given and
  a host outside its network limit (claude.ai's own check of a run's actions refused a shell change
  of `rules.md`, and then the landing after it, so the proof no longer tries one); "run my routines here"
  brings it back. The daily chain stays on the computer: it sends the owner notices and the
  briefing, and now runs the tracker step too. Each run lands its own files once, at its end
  (`sync.py --cloud sweep`): it pushes `cloud/sweep` and opens a pull request through REST that
  the space's checks merge; the next run carries one that did not merge, and the computer's sync
  adopts one that is still open after 30 minutes and conflicts with `main` or is red (a green one
  that is only slow is left to auto-merge). The task gets the read tools of the person's apps
  alone, and its deny list. A protected
  change never lands from the cloud, and the gate's minimum list now holds, for every pull request,
  what instructs or runs code on the computer (the rendered routine prompts, `onboard.yaml`, the
  migrations, `.mcp.json`, `CLAUDE.local.md`, pytest's config), and what Claude Code or pytest load
  wherever it sits at any depth. `cloud.py trigger --environment`
  names the cloud environment claude.ai requires. The computer's sync keeps the transcripts scan, the
  release leg and requests. Merges settle by file kind and never wedge, GitHub is read through
  REST, the gate always protects a minimum list whatever `space.yaml` says, and `main` gets
  `enforce_admins`. Rule 7 names the cloud's landing.

### Changed

- **No guard plugin.** The trial of 0.3.0-rc3 showed claude.ai never loads a repository's plugin
  in a cloud run, so the `spaces-guard` plugin, its marketplace file, the cloud daily task and the
  proof's `--guard` and `--send` are gone. The cloud sweep sends nothing and writes to no app; the
  proof needs no plugin.

### Fixed

- **`onboard.py --dry-run` writes nothing.** It used to fill the template and create a workspace
  folder while saying what it would do. An offline `--register-sync --dry-run` records no command
  either, and `--dry-run` beside a mode that cannot honour it (the routines' moves, a pause,
  `--connect`, `--invite`) is refused instead of ignored.
- **The move renders the routine prompts again**, so a daily rendered before this version gains
  the tracker step it now runs, and the computer's sweep its `--chain sweep`.
- **A notice is never overwritten.** A queue in the same second as a recorded notice could reuse a
  pending file's name; it now takes the first name not in use. A security notice is its own file,
  and the daily joins them into one digest when it sends.
- **A new space is written with the gate's whole minimum list.**

<!-- onboarding-launch-entry -->
### Added

- **Setup as a guided conversation.** One sentence still starts it. The onboard skill runs
  `start.py`'s phases (`signins`, `apps`, `research`, `build`, `history`) and shows one message
  per milestone in plain words (`.claude/skills/onboard/messages.md`); the detail lives on the
  progress page. A setup journal (`~/.spaces/setup.yaml`) lets a closed session, or the same
  sentence the next day, continue where setup stopped (`start.py resume`).
- **Every sign-in in the first two minutes.** GitHub's device code and the Claude command line's
  browser sign-in start together; the doctor installs GitHub's tool and Claude's command line
  first (pinned, checksummed, no admin rights). In the desktop app setup asks once, through the
  app's own approval card, to run without asking for each step, and switches back when it is
  done; the agent never switches without that card.
- **The template without git.** Each tagged release carries `brain-template-<tag>.zip` and
  `SHA256SUMS` (`template_archive.py`, `.github/workflows/release.yml`); setup downloads the
  zip and checks it before unpacking.
- **One analysis run per app, and the app card.** A read-only reader per connected app decides
  whose account it is, whether it holds work for the company and how much there is per month.
  What holds no work is skipped and the card says why; it is shown at least ten minutes before
  any content is copied.
- **Extensive company research.** The company's own site, business register entry and code
  organisation, its documents in the connected documents app, its files on this computer and
  the person's Claude notes (as weak evidence), with secrets dropped, merged into a profile that
  cites a source on every line and is drafted in the brain for the person to correct or sign.
- **Routines that run on their own and prove it.** The build registers the routines itself with
  a scheduler that catches up after sleep (Task Scheduler, a launch agent on macOS, a systemd
  user timer on Linux, cron as the last resort with a plain line that says it does not catch
  up), and says done only after a test run through the scheduler passes twice. Routines run on
  an allowlist built from what a scheduled run sees, never in bypass mode, with a send guard that
  lets mail go to the person alone; one run per workspace at a time; each run writes its own
  outcome line with the usage it really used.
- **The history, oldest first, in paced parts.** The plan's window, 90 days by default, is read
  in consecutive parts, the oldest stretch first, so what was settled stays settled. Parts pace
  themselves to the person's own limits (in working hours at least half the 5-hour window stays
  free; on day one at most 30% of the week left), save as they read with a checkpoint after
  every batch of at most 50 records, pause at a limit and resume where they stopped, and never
  draw on extra usage. On a paid account the plan is written and the person starts the read.
- **The progress page and the manual.** The page shows what runs now, what was found, the
  history's parts, usage, what is waiting with the one thing that clears it, and the settings
  nobody was asked about; the sync task keeps it true without the model. Once part 1 is read it
  opens on a manual, "Your <company> brain", in the company's own names.
- **Routines run on one computer, through one scheduler, and can be paused.** The workspace
  records which computer runs them (`_meta/state/routine-host.yaml`); a second computer, or an
  install whose routines are the desktop app's scheduled tasks, schedules none, and a routine
  started anywhere else leaves by itself. "Run my routines here" moves them once the probe passes
  on the new computer, then turns the app's old tasks off; "pause my routines" (until 06:00 the
  next morning, or a date) and "resume my routines" hold them, the history's parts included.
  Two computers that claim before either syncs end with neither running and one plain Waiting
  line: the record merges by union, and sync writes that rule into each space's own
  `.git/info/attributes`.
- **The friction log ships** with its format (`_meta/friction-log.md`), so the personal
  instruction's rule has a log in every new space.

### Changed

- **One first-read window everywhere.** It is the plan's, 90 days by default, in the skills and
  the README; onboarding no longer runs a 14-day first sweep, since part 1 of the history read
  replaces it.
- **The sweep saves as it reads.** It exports oldest first and appends at most 50 records per
  batch; while the history is read, the workspace's own sources are read by part from the plan's
  start mark; a sweep asked for in a session first checks that no routine holds the workspace's
  lock; a personal workspace's documents are the person's own files only.
- **A usage reading at session start.** In the desktop app, while the history is read, each
  session records the person's limits, so a part may start during working hours without crowding
  their own chats.
- **Workspaces in the company's organisation** by default, private. Members can read it while the
  organisation's base permission allows that; setup puts that setting on the owner's Needs-you list.
  The decisions log records what the launch supersedes: "Export watermark" for
  the backfill, "Workspace owner on the platform", and "Connector permissions stay per person"
  for routines.
- **Setup reads the apps it does not know by name.** One short run sees only each such app's
  name and its tools' names and sorts it: a kind setup reads, work of a kind setup does not read
  yet (a CRM, say), or nothing to read (an image maker). A sorted app gets its read tools and
  nothing that acts. The app card, the chat and the progress page name the apps it saw but did
  not read and the ones that hold nothing to read; an app the run could not look at is looked at
  the next time. Known apps are read as before.

The sign sitting: a page's questions are ready before the person sits down, they are answered
back to back, and nothing a record or the person settled is asked again.

### Added

- **The question ledger.** `_meta/state/sign-questions.jsonl` keeps every question the sign
  flow puts to the person, one event per line, append-only and merged by union;
  `sign_questions.py` is its only reader and writer. A question has an id (`q-` and 8 hex, kept
  through a reword) and a state: open, settled by a record, answered by the person, parked
  ("I don't know": never asked again, but every recheck still tries to settle it) or dropped.
  It belongs to its subject and carries over to the subject's next candidate; a subject holds
  at most four open questions. Only a named person answers, and answers again to correct it:
  the later answer wins. A dropped question that matters again is reopened. A sensitive answer
  is kept in a desk file in the answerer's own workspace, and the ledger holds only where it
  is kept. `forget.py` lists a subject's ledger lines.
- **Answers a page can cite.** A page line can cite an answered or settled question of its own
  subject as `[src: q-...]`, and `verify` accepts it. `verify --body <file>` checks a write-up
  against its candidate.
- **`distill.py prepare`.** Lists each candidate whose questions need preparing: a subject
  never checked, or with records since its last check. It writes nothing. Distill's new
  Recheck step settles, rewords or drops open questions from the records that arrived, before
  research; its Ask step puts what only the person can answer into the ledger with a
  recommendation. The second agent's check reopens a settle its record does not support.
- **The question count on Needs you.** A candidate's line ends with its subject's open
  questions, "(2 questions)". The morning email stays counts-only.

### Changed

- **`sign.py apply` at the command line requires the pin.** A yes, edit or always on a page
  names the `fingerprint` and `base` its read-back printed, and an edit names its write-up as
  `body_file`. A signed page's release takes the same pin. A page whose text or draft changed
  since the read-back, or that names no fingerprint, is refused, stays on Needs you and is read
  back again; the other items go ahead. `no` and `later` need no pin. A weekly review batch
  line that would sign a draft is refused: the draft is signed by its own id after its
  read-back. A yes on a page from an emailed reply is bare, so it is refused too.
  `read-back --body <id>=<file>` reads back a write-up. The Python API keeps
  `require_pin=False` for the tools and tests that call it.
- **The sign sitting.** The agent picks the order, shows what each page says, where it lands and
  who reads it, then asks only what no source can answer, at most four questions per page.
  Answers are recorded as they are given. A helper writes each answered page's edit to
  `_scratch/sign/<page id>.md` and verifies it while the person answers the next page; the
  read-back comes at the next break, and each takes its own word. `submit` signs every page
  that has its word; "stop" waits for the running write-ups and reads them back.

### Fixed

- **A first-release line reads back.** `sign.py read-back` failed on a `first-<subject>` line,
  and took every other item of the same call with it. It now lists the line's items and prints
  no fingerprint: a bare verb answers it.
- **Routines and the sync run from the Windows desktop app's setup.** Claude's desktop app is a
  packaged Windows app: what setup wrote under `%APPDATA%` (uv's Python, gh's sign-in) landed in
  a private copy the scheduled tasks never see, so they died without a word. The bootstrap installs
  Python under `~/.spaces/tools/python`, a venv made from a Python under `%APPDATA%` is made again,
  there is no `--user` install on Windows, and gh's folder is `~/.spaces/gh` (the sign-in setup made
  is copied there). The probe task writes what it printed to `~/.spaces/logs/`, so a Python that
  cannot start fails the build at once with its own words instead of after four silent minutes;
  the task ends with Python's own exit code, so Task Scheduler's last result shows a run that
  failed; and the probe checks that a scheduled run can use the GitHub sign-in.
- **A copy of the template is no longer the template.** The checks ask GitHub's own
  `is_template` setting, which a repository made from the template never has, instead of the
  marker file it keeps until onboarding's first commit lands: a workspace's sync pull requests no
  longer run the template's own tests, and the gate no longer lets a protected change in such a
  workspace merge without the owner. The sync reruns a failed check at most three times a day, and
  not at all when the same checks failed again after a rerun; a check whose rerun passed is rerun
  again the next time it fails.
- **The briefing goes to the work mailbox the app card named.** The card now says where the
  morning briefing goes: the work mailbox the routines can send from, when a second app's reader
  names the same account (a mail anyone can write cannot name it alone). The person sees it for
  ten minutes and can say "send my briefing to" another address; the build takes it before git's
  address. The morning-view skill says that having the send tool is the mailbox check, so a
  routine no longer holds the briefing because Claude is signed in on another domain.
- **An app still connecting is not taken for one that is not connected.** When Claude's command
  line starts, connected apps can still be `pending` for a few seconds. The probe that the apps
  step, the research and the routines' proof use looks again while one is (three looks at most,
  while the next one, as slow as the last, ends within two and a half minutes of the first) and
  keeps the look with the fewest still connecting, so the first apps step no
  longer finds no apps and the routines' proof no longer fails an app that was only slow.
- **The first push and the brain mount say when they did not happen.** git signs in to GitHub
  through gh in everything the tools start, as gh's own clone does, so a credential manager that
  would ask in a window no one sees no longer stops it. A first push to `main` that fails, or that
  leaves `main` the template's, is a failed step with git's own words, and the build is not done;
  a brain or project that did not mount is a failed step too, one for each mount. While `main` is
  still the template's, the sync sends everything as one protected pull request, checked as one
  tree, so the owner merges once instead of a protected half that fails on a tree without the
  folders.
- **A company site that moved is read where it moved to.** The site's reader may fetch only the
  company's domain and its `www.` twin, so a domain that redirects to another host (a `.nl` that
  moved to a `.com`) came back with nothing and the build said the site gave nothing to keep. When
  the reader says the site sent it elsewhere, the domain's own server is asked where it redirects,
  and the research reads once more on the host it really lands on, never one a page names; it keeps
  what the first read found on the pages that did not move, and the company's domain stays the one
  given.
- **Task names another Windows account holds.** Where another account on the computer already has
  the space's task names, Windows said only "Access is denied". The row now says the name is
  another account's (Windows' own words for it are read in the computer's display language too),
  with the line an administrator runs to remove it, and
  `start.py build --task-prefix space-<letters>-` gives this computer names of its own, kept in
  `~/.spaces/settings.yaml`.
- **The app card.** "Read <app> again" (`start.py apps --unskip <app>`) takes a skip back, with
  the reader's own verdict; an app skipped before it was read is read the next time the sentence
  is said, and its answer says so instead of promising it to the routines at once. Once the build
  has kept the apps' verdicts in the workspace, every later run of the apps phase keeps them there
  too, so an app read after the build reaches the routines. Two apps on one mailbox (a mailbox and a reader of its attachments)
  count its mail once in the history's estimate. Claude's own connectors (`Claude …`) are not the
  person's apps: they are neither read nor sorted. The card's ten minutes start again only when an
  app is read that the card did not read before.
- **What the sentence allows, and who else reads the workspace.** The README says what the
  sentence is the go-ahead for; `docs/data-protection.md` says what a code-review app installed on
  the Git account reads; troubleshooting covers a task name another account holds, Python under
  the desktop app, and an app the command line reports as not signed in.

**Migration.** After upgrading, run `python _meta/tools/distill.py <root> prepare` once and
follow distill's Recheck and Ask steps for each page it lists.

## [0.2.0] - 2026-10-24

Codebase links: a session opened in a linked repository gets its project's context, and its
notes about that work reach the project.

### Added

- **Codebase links.** `link.py resolve` tells a session in a codebase which subject the code
  belongs to, from its git remote and the registries the workspace mounts, with no network. A
  repository nothing links yet is linked at once on a clear match, asked about once on a tie,
  and never asked twice. Onboarding and project creation scan the repositories the person can
  reach. The daily check follows a renamed repository (its old names still resolve), marks a
  lost one gone and brings it back when it is found again, and sets who reads it (a public
  repository is always wider); the health line shows `links N, wider M, repaired K`.
- **Four rules for working in a codebase**, in the personal instruction: context is pulled on
  a trigger (a brainstorm, a spec, a call, a question about a client or a past decision), never
  at session start; three rings (the subject and its parent, the company layer, and anything
  else only when the person names it); what goes into a codebase read by people outside the
  subject's audience passes the release scrub; entries carry `subject:` and a `ref:` pointer
  into the code.
- **Notes from linked codebases travel as records.** A journal entry about a subject homed
  elsewhere, with a `ref:` into a codebase linked to that subject, goes to its home after the
  window (at once when the router judged it, to a walled home whose crew includes the writer).
  Every other entry stays home.
- **Pointers on status pages.** Distill names an entry's pointers on its evidence lines, and a
  subject's status page lists them under `## Pointers`.
- **Lessons to the brain.** A `lesson:` line becomes a candidate for the brain carrying the
  lesson alone, never the subject it came from; one the signer discarded is not proposed again
  from the same subject.
- **Spec-like files in the repository feed.** The sweep files the spec, design and decision
  files a merged pull request adds or changes as records, each with a permalink at the merge
  commit.
- **Needs you once a day.** The personal instruction runs `needs_you.py --once-a-day`, which
  shows the list in the day's first session only.

## [0.1.0] - 2026-10-24

The first public release.

### Added

- **Spaces, shape 1.** A company is a tree of git repositories: one brain everyone reads, one
  workspace per person, walled project spaces, corridors between two spaces. One settings
  file per space (`space.yaml`), pages with permanent ids and links by id, a registry of
  subjects, and `validate.py` to check all of it (`docs/shape-1.md`).
- **Setup in one sentence.** `start.py` with a doctor that checks the machine and installs
  `gh`, Git and Python where they are missing (pinned, checksummed, no admin rights), creates
  and clones the person's repositories, mounts the brain and their projects, registers the
  sync task and renders their routines. Invitations for colleagues; `onboard --check`
  re-runs every read-back.
- **Lossless capture with a privacy door.** The sweep keeps full mail bodies, call
  transcripts, chat threads and document text as dated witness pages in the person's own
  workspace. Personal and academic records are counted and never stored; unsure ones wait on
  the person's machine for a yes or no.
- **Judged routing.** An agent reads each record whole, builds a private brief of the subject
  first, and decides whether it stays private or which space and subject it informs, as the
  whole record or verbatim parts, at once or after the 24-hour window. A second agent checks
  every decision that sends anything out; the tools check the facts (secrets, verbatim parts,
  destination, audience).
- **The classification gate.** Nothing about a client, project or deal leaves the space it
  came into until its owner's agent classifies it `company`, `walled` or `owner`, after asking
  the other workspaces for obligations, recorded as kind and date only. A new obligation
  re-opens the classification.
- **Release windows and the morning view.** What would widen an audience waits one window and
  one morning view; a counts-only briefing mailed from the person to themselves; pull-back by
  one word, also by replying to the briefing.
- **Signing by people only.** Candidates with a source on every line, checked by a second
  agent before they reach Needs you; `sign` writes `stable` only for a named person's
  verdict, with the fingerprint of the approved text; landing at the home checks it.
- **Status that answers.** Status pages per subject with open items by direction, timeline
  and coming up; the brain's projects page as the company's register; answers that end with
  the tier and age of every page they rest on.
- **Upkeep.** The weekly review batch, conflict notes, the weekly sample of silent landings,
  the path pause after a pull-back, the librarian's moves and proposals, guarded weekly
  template sync.
- **Requests between spaces** through corridors, answered by standing choices or by the
  holder's person.
- **The fixture company Lantern Works**, built offline in one command, with answer and leak
  tests.
- **Updates by release.** Spaces follow tagged releases, not `main`; one question per release
  on the owner's Needs you, and their yes moves every space they own. A protected update lands
  without a click only where its protected files are the approved release's, byte for byte;
  the gate that decides is the base branch's own (`template_updates: main` opts out).
- **CI that costs little.** One `checks` job per pull request (about one billed minute); the
  tool tests run only when the machinery changed; `platforms` runs the setup on Linux, macOS
  and Windows in the template only.
- **One security email a day.** Passwords or keys found in records are told to the person in
  one digest, counted by where they showed up; at most one security email goes out in any
  24 hours.
- **Onboarding page.** `start.py` keeps `~/.spaces/onboarding.html` up to date after every step:
  the stages as a row of steps, what only the person can do (each with its command), when the
  first briefing comes, and how the system works as a diagram. `onboard --check` rewrites it.
  One self-contained file in the house style, dark by default with a switch to light, no
  server, no script and nothing fetched.
- **Public docs.** `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, this changelog and
  `docs/data-protection.md`; a status line at the top of the README and the template's real
  address in it; issue and pull request templates; `requirements.txt` and
  `requirements-dev.txt`; the design pages in one folder, `docs/design/`.
  `agents_md.py --help` prints its usage instead of writing `AGENTS.md`.

### Security

- **Pull request code runs read-only.** CI is split so that a pull request's own code runs
  with read-only permissions; the part that may write does not run it.
- **Fork pull requests are not recommended for merge.** Needs you no longer recommends yes
  for a pull request from a fork.
- **Routines cannot edit the tools or the settings.** A routine started by `routine.py` runs
  with its own permission profile: it may not change the tools, tests or harness settings, and
  runs only the tools its prompts name. The space's own profile names each tool it allows, so a
  newly written script asks before it runs.
- **The forbidden-names list is a CI secret.** The hashes the name gate compares with are no
  longer in the repository; CI reads them from a secret, and a local run without the list
  skips the name check.
- **Run state is not tracked in the template.** What a run writes under `_meta/state/` no
  longer lives in the template repository.

### Fixed

Found during the private preview, before the first release.

- **A red sync pull request heals itself.** When the checks of a machine's sync pull request
  fail, the next `sync` run reads the failed log. Answer tests it names are set aside for CI
  (`held: {by: ci}`, since a pass on this machine does not prove one there) and land with that
  run's push. Any other failure is rerun once per head, as a flaky one. Once the pull request
  merges, the run line says `healed` and the morning view lists it under "Fixed by itself".
  What is still red after the rerun stays in the run line (`pr_red`) for the maintenance
  agent. Nothing asks the owner.
- **A test question its pages cannot answer is asked again, not added.** A yes or edit on a
  steered question wrote the given facts into `answers.yaml` as they were typed. The answer
  test looks for each fact as a page writes it, so a summary in other words, another
  language, or a source no page carries failed from its first run, and the space's sync
  pull request could not land anything after it. `status` now checks the facts first: one no
  page carries keeps the question out, and the item comes back as `probe-q-<id>-2` naming
  the missing facts. The first item asks for the facts as the pages write them.
- **A failing answer test no longer holds every other change back.** The checks run the answer
  tests on every pull request, so one question no page answered kept a space's sync pull
  request red, and its desk notes, journal and run lines waited behind it (a day, on 20 Oct).
  Before it publishes, `sync` now runs the answer tests and sets aside each question that
  fails: `held: {since, by: sync, missing}` in `answers.yaml`, the question named in the run
  line. `tests_run` names a held question in every run and never counts it as passed. Once a
  page carries its facts again, the next sync brings it back.
- **Signing ships the evidence list.** A signed candidate kept the `## New evidence` list
  that distill keeps at the bottom of the page, and a release sent it on. Its lines are record
  titles, which can name prices, pay terms or other clients the page itself leaves out. `sign`
  now leaves the list out of the signed page, its release and the read-back; the page keeps its
  own `[src: ...]` markers and its `evidence` ids, and the candidate keeps the list for the review.
- **A classification counts at once where it was made.** After `classify.py decide`, recording
  the refused route again was refused again until the note had gone to the brain, been applied
  there and come back through the mount: up to four sync runs, while the refusal only said to
  record the decision again. The deciding workspace now checks the note against the brain's
  rules and follows it at once, until the brain's log shows it applied or refused. The refusal
  names the real next step (classify it here, name an owner, or wait for the owner's
  workspace), and the README and the classify skill say when a classification takes effect where.
- **Routines that outlive the session.** The README sent a Claude Code session to its own
  scheduled tasks, which in a terminal end with the session, while the onboarding page showed
  Routines done as soon as the prompts were rendered and `onboard --check` had no row for them.
  The README and the onboard skill now schedule with `onboard.py --os-routines` (the OS
  scheduler, through `claude -p`), or with a harness's tasks only when they persist, recorded
  with `--routines-scheduled <read-back>`. The page and `--check` show Routines done only once
  the schedule reads back; until then `--check` shows "routines scheduled" with the command.
- **Needs you counts what waits for a classification.** The count covered only projections held
  at home, so a route refused as unclassified left no trace, and a draft page about a client
  nobody had classified was offered for a signature. One line now counts held projections,
  refused routes (the router logs each refusal) and candidates about the client, names the
  clients and says who classifies each. Such a candidate is not offered or signed until the
  client is classified. The doubt line counts only what is held in doubt.
- **Inviting a colleague from a personal account.** The access item always advised a
  company-wide team, which a personal account does not have, and did not say that a private
  template can only be shared by its owner. It now reads who can grant each repository: one the
  inviter cannot administer is its owner's to share; on a personal account it advises a
  collaborator, with the `gh api -X PUT repos/<owner>/<repo>/collaborators/<login>` line for the
  owner to run; in an organisation, a team as before.
- **The fixture on Windows.** The README said the fixture takes a few minutes on Windows; a
  newcomer's run took 24. Before each of its 61 behaviour scenarios the runner read back every
  file it had just copied, and on Windows the first read of a new file waits for the virus
  scanner: about two and a half seconds per scenario. The copy now hashes each file as it copies
  it and both snapshots read only what changed since; on the machine measured the fixture went
  from about four minutes to under two. The README gives the time as measured, says the output
  comes at the end when sent to a file, and shows the table the fixture prints today.
- **A release clone of another repository.** Release clones under `~/.spaces/.release/` are
  kept by space name. A clone left there by an older space of the same name (a company set up
  again after its repositories were deleted) was reused: releases and classification notes
  went nowhere while the run line said nothing failed. Such a clone is now moved to
  `.stale/` and the destination cloned fresh.

Found in a drill that sets off each never-used part of the template in a throwaway fixture
company, before the first release.

- **A retraction withdraws the release where it landed.** A landed record is marked
  `retracted` and no longer read as evidence; a release retracted before landing is not filed.
- **Pulling a router item pauses the router for that destination.** The router's at-once items
  carry the path `router:<destination>`, so a pull or a failed sample pauses them for 4 weeks.
- **Dormancy reaches every subject at its home and wakes on new evidence.** The home judges
  every subject homed there, also those the brain holds, and tells the holder with a state note.
- **Offboard archives a subject homed here in the registry that holds it.** A leaving space
  sends an archive note for each such subject, and no note moves a subject out of `archived`.
- **The librarian splits a desk subject that another registry holds.** The split check walks
  the subjects a space sees from the brain, not only its own registry.
- **The librarian files a loose page that sits right under `knowledge/`.**
- **A shape migration's commit carries only what the migration changed.** The person's
  unrelated uncommitted work stays out of the structure commit.
- **An answered checker hit no longer silences a later different value.** A hit's id includes
  the value it is about.
- **The checker reads a newly signed page without waiting for new evidence.**
- **The weekly review batch keeps its weekly stamp.** The checker's run no longer drops
  `review_at` from its watermark.
- **Each sending space's conflict note has an id of its own.** Two spaces contesting one page
  both reach its owner.
- **The forget listing finds doubt items and signals about the subject.**

Found while landing the drill fixes and preparing the first release.

- **A later different value from the same space sends a conflict note of its own.** The note's
  id includes the value, so a second value no longer overwrites the first.
- **A spec page queued with a tracker request gets the issue key.** `tracker.py queue --spec`
  names the page; once the issue is made, `tracker: <key>` is written into it and its state moves.
- **A step that fails in the daily run shows on the same morning's briefing.** `stale.yaml`
  moves as each step ends, and the morning view reads it directly.
- **The sync script names a chain its scheduler never starts.** A chain whose last run is older
  than twice its schedule's longest gap is labelled stale until it runs again.
- **A failed shape migration is undone whole and tried again after a template update.** Every
  path it changed is restored, and nothing asks a person.
- **A green pull request is merged when the base moves under it.** The merge step retries after
  a short wait and arms auto-merge with a method the repository allows.
- **A token in a link is cut from the page and sends no security email.** Verification,
  unsubscribe, invitation, signing and tracking links keep their host and path; only a key, a
  private key or a labelled password still warns.
- **The full template copy test runs in the template only, not in every space.**
- **A link into a mount that is not on disk is a warning, not an error.** A space whose checks
  cannot read one of its mounts still validates.
- **The permission tests read the skills a space has.** A project space without the personal
  skills no longer fails them; in the template every skill is still required.
