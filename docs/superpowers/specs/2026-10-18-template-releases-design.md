# Template releases: one yes per release

Status: approved 2026-10-18. The owner accepted that the release yes is as strong as a signature
(see the trust argument).

## Why

Every template merge reaches every space as a protected sync pull request, one owner click
each. With six spaces and frequent merges that was dozens of clicks a week, and each click is
a review nobody really does six times. The click exists so that no agent can change its own
rules and tools (docs/decisions.md, 2026-10-11 and 2026-10-13). The aim is one decision per
release, made once, with the same guarantee.

## What changes

1. **Releases.** The template publishes annotated tags `vX.Y.Z` on `main`, each with a
   `## [X.Y.Z]` section in `CHANGELOG.md` whose first paragraph is the summary. Template sync
   moves a space to the newest release, never to `main`, unless `space.yaml` says
   `template_updates: main` (then it behaves exactly as today).
2. **One question per release.** When a newer release exists and the person has not answered
   it, their personal workspace's template sync writes one item to
   `_inbound/needs-you/template-release.yaml`: `template-v0.2.0-1a2b3c4d`, "Update to v0.2.0?
   <summary>". The id carries the tag's commit, so a yes binds to that exact content. `sign`
   records the verdict as it does for any routine item: `- <ISO> yes template-v0.2.0-1a2b3c4d
   by <name>` in the workspace's `log.md`. Nothing new in `sign.py`.
3. **One yes covers every space the person owns.** Each space's template sync looks for a yes
   from its owner in its own `log.md` and in the owner's workspace on the same machine (found
   through `~/.spaces/spaces.yaml`, the list the chains already use). With a yes it syncs that
   release and writes the receipt into `_meta/state/template-version.yaml`:
   `release`, `commit`, `approved: {release, commit, by, at, in}`. Without one it stays where
   it is. The daily chain runs the sync on the first day a yes for a newer release appears, not
   only weekly.
4. **Auto-merge on exact match.** The gate in `checks` lets the protected sync pull request
   land on green only when all of these hold, and otherwise it stays a manual protected pull
   request exactly as today:
   - the provenance in the pull request names a release, its commit and an approval of that
     same release and commit by the space's owner (the owner read from the base branch);
   - that tag, fetched from the template named on the base branch, still points at that commit;
   - every changed path under a `protected:` prefix exists in the release with the same blob,
     so byte-identical (a deletion, a file the release lacks, `space.yaml`, `rules.md`,
     `identity/`, any local edit: no match).

## The trust argument

The click guarded content: an agent could write any file into a protected path and only a
person stood between it and `main`. After this change, a protected file lands without a click
only if its bytes are a published release of the template and the space's owner said yes to
that release. The owner's yes moves from "this pull request, in this space" to "this release,
everywhere I own".

How strong the yes is: exactly as strong as a signature today. `sign` records it only after the
person's `submit`, refuses a routine's name as `--by`, and its skill forbids a verdict the
person did not give. An agent that broke those rules could forge a yes, as it could forge a
page signature, and it could cut a tag itself, since the template's own pull requests land on
green. The click has the same limit today: agents run with the owner's GitHub credentials, and
only the harness and the rules keep them from merging. So the gate stops every routine and
every agent that edits the wrong file by mistake; it does not stop one that sets out to forge.
Releases are cut by a person (CONTRIBUTING.md says so); that is a rule, not a lock.

Three things the gate trusts today come from the pull request itself, so an agent could loosen
the gate by editing a file. This change closes them, because the exact-match path is only as
good as the code that checks it:

- `checks` runs `gate.py` from the base branch (a `git archive` of `origin/<base>:_meta/tools`
  into the runner's temp folder), not the pull request's copy;
- the gate reads the protected prefixes, the template marker, the owner and the template's name
  from the base branch with `git show`, and reads the pull request's files only as git objects;
- a change to the template's name (`repo:` in `_meta/state/template-version.yaml`) is a
  protected change by itself, so a pull request cannot repoint the next check at another
  "template".

So a tampered `gate.py`, a `space.yaml` with an emptier `protected:` list, an added
`.github/template-marker`, or a provenance file pointing at another template all still wait
for the click.

What it cannot close: a pull request that changes a workflow file runs its own copy of that
workflow, with the write permissions it declares. Nothing inside the repository can stop that
on GitHub without a second account's required review, which the 2026-10-11 decision ruled out
(an author cannot approve their own pull request). That is the state today and this change does
not make it worse: a release's workflow files are the template's, and a pull request that edits
a workflow any other way is as exposed as it is now. The design says so in the decisions log.

## Fetching a release, public or private

- On the person's machine, template sync clones the template once per run with
  `--filter=blob:none --no-checkout` (all commits and tags, file contents only for the release
  it checks out). Public repositories need nothing; private ones use the git credentials the
  doctor already checks.
- In `checks`, the gate fetches only the approved tag (`git fetch --depth 1 <url>
  refs/tags/vX`). Public: no token. Private: `TEMPLATE_READ_TOKEN`, else `BRAIN_READ_TOKEN`
  or `MOUNT_READ_TOKEN` (the mount tokens already in use), passed through git's environment
  config, never on a command line and never printed. Fetch fails: no match, the click stays,
  the reason line says why.

## Never backwards

A space that last synced `main` records only a commit. It moves to a release only when the
release's commit descends from that commit (checked in the blobless clone); otherwise it waits
for the next release. A space with a recorded release moves only to a higher version. A brand
new space has no provenance yet: it was made from the template's `main`, so its starting point
is the last template commit on `main` before the space's own first commit, and the same
"descends from" rule applies. Where that cannot be read (no history), it takes the newest
release.

## Rollout

1. This pull request merges in the template on green (the template's own pull requests do).
2. Tag `v0.1.0` on that merge commit, with the CHANGELOG section (the owner's call: it
   publishes a release).
3. Each space's next weekly sync still follows `main` (its old tool) and brings this change as
   a protected pull request: the last click per space.
4. From then on, one Needs-you item per release.

## Tests

- Gate, with real git repositories in a temp folder: a release-matched pull request is
  auto-mergeable; the same pull request with one protected file changed by one byte is
  protected (the test the task asks for); also protected: no approval, approval by someone other
  than the owner, a moved tag, a deleted protected file, a protected file the release lacks, a
  tampered `space.yaml` `protected:` list, an added template marker, and a changed template
  name (alone it is protected too).
- Template sync: no release yet; a newer release without a yes writes one item and changes no
  file; with a yes (in its own log or in the owner's workspace) it syncs and writes the receipt;
  `template_updates: main` behaves as today; a space ahead of the newest release stays; a local
  `--from <path>` stays the one-off working-tree source it is today.
- The chain runs the sync on the day a yes for a newer release appears.

## Left out

- No standing "always update": only `yes` counts, once per release; `always` on this item is
  read as nothing.
- No GitHub Release objects are required; `gh release create` is optional for people.
- No onboarding step for `TEMPLATE_READ_TOKEN`: the template is about to be public, and a
  private one falls back to the mount tokens or the click.

## Calls I made (check these)

- The setting is `template_updates: releases | main` in `space.yaml`, default releases.
- The release summary comes from `CHANGELOG.md` in the tag, not the GitHub Release body (works
  the same for public and private, no API).
- Only the personal workspace asks; other spaces never write the item unless no workspace of
  their owner is on the machine.
- A local protected edit in the same sync pull request blocks auto-merge for the whole pull
  request (the rule "nothing else under protected paths").
- `checks` runs the base branch's gate; the residual risk of workflow edits is written down,
  not engineered around.
- A brand-new space's starting point is read from its first commit's time, so onboarding is
  not touched.
