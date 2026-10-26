# Changelog

Notable changes to the template, newest first. Versions follow [semantic
versioning](https://semver.org); while the version starts with 0, a minor release may change
the shape and ships its migration. Each choice behind a change has a line in
`docs/decisions.md`.

## [Unreleased]

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
