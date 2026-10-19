# Changelog

Notable changes to the template, newest first. Versions follow [semantic
versioning](https://semver.org); while the version starts with 0, a minor release may change
the shape and ships its migration. Each choice behind a change has a line in
`docs/decisions.md`.

## [Unreleased]

### Added

- **Onboarding page.** `start.py` keeps `~/.spaces/onboarding.html` up to date after every step:
  the stages as a row of steps, what only the person can do (each with its command), when the
  first briefing comes, and how the system works as a diagram. `onboard --check` rewrites it.
  One self-contained file in the house style, dark by default with a switch to light, no
  server, no script and nothing fetched.

### Fixed

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
- **A release clone of another repository.** Release clones under `~/.spaces/.release/` are
  kept by space name. A clone left there by an older space of the same name (a company set up
  again after its repositories were deleted) was reused: releases and classification notes
  went nowhere while the run line said nothing failed. Such a clone is now moved to
  `.stale/` and the destination cloned fresh.

## [0.1.0] - 2026-10-18

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
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog and `docs/data-protection.md`.
