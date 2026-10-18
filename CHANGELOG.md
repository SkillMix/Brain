# Changelog

Notable changes to the template, newest first. Versions follow [semantic
versioning](https://semver.org); while the version starts with 0, a minor release may change
the shape and ships its migration. Each choice behind a change has a line in
`docs/decisions.md`.

## [Unreleased]

### Added

- **Onboarding page.** `start.py` keeps `~/.spaces/onboarding.html` up to date after every step:
  what is done, what runs now, what is next, what only the person can do (each with its exact
  command and check), what happens tomorrow and how the system works. `onboard --check`
  rewrites it, and after setup it also shows the day: the last sweep, the Needs-you count and
  what leaves within the window. One self-contained file in the house style, light and dark,
  no server and nothing fetched.

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
- **CI that costs little.** One `checks` job per pull request (about one billed minute); the
  tool tests run only when the machinery changed; `platforms` runs the setup on Linux, macOS
  and Windows in the template only.
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog and `docs/data-protection.md`.
