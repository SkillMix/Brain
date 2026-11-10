# Codebases in the loop: design

Status: approved 2026-10-22.

## 1. The problem

Most real work happens in a codebase, and a codebase is not a space. Today a session opened in
one is cut off from the loop:

- **No context comes in.** The `query` skill and the mounts exist only inside a space. A
  session in a codebase does not know which project or client the code is for, so a feature
  brainstorm after a client call pulls in nothing from the meeting, the project or the brain.
- **Little goes out.** Capture runs on the agent's judgment and is skipped most in coding
  sessions (capture reaches well under half of coding sessions). Entries from a codebase carry
  no `subject:` line, so they stay in the workspace and never reach the project. They have no
  way to point at a spec or a pull request.
- **Code activity never reaches the project.** A project space may mount its codebases
  read-only, but nothing reads their merged pull requests or specs as records.
- **Codebases carry stale instructions.** Older codebases still tell agents to load a pinned
  brain and file knowledge in a `wiki/` inside the repository.
- **Nothing stops context leaking into the code.** A codebase's readers are often wider than
  the project's crew (other developers, contractors, the client's engineers). The release
  scrub guards what leaves through a release, not what an agent types into a repository.

## 2. What we want

A session in any codebase, for any company, one SaaS repository or many client codebases:

1. knows which subject the code belongs to, without anyone setting it up;
2. pulls the right context when the work needs it, and costs nothing when it does not;
3. writes into the codebase only what the codebase's readers may see;
4. sends its decisions back to the subject's home, tagged and with pointers;
5. lets the home see what changed in the code, whether or not the session journaled.

## 3. Decisions

Settled with the owner on 2026-10-22:

| # | Decision |
|---|---|
| D1 | The link lives on our side (the registry) by default. A short block in the codebase is added only where the company owns the repository. |
| D2 | Opening a session costs nothing extra. Context is pulled by the kind of work, never at session start. |
| D3 | Links are made automatically: onboarding and project creation scan the repositories a person can reach; a session in an unlinked repository proposes the link; the daily routine keeps links healthy. |
| D4 | The codebase holds the content (code, full specs). The spaces hold the why (decisions, commitments, open questions, sensitive context) plus pointers into the code. |
| D5 | Context comes in three rings: (1) this subject and its parent, in full; (2) the company layer (brain pages, lessons, standards), always; (3) anything else the person can read, only when they name it, and then for thinking only. |
| D6 | Reaching across is a lesson signal. When ring 3 helped, the journal entry records the lesson, and distill proposes it as a company lesson with the client removed, so next time it arrives through ring 2. |
| D7 | Writing into a codebase is a release to its readers. Readers all inside the subject's audience: write freely. Wider readers: only what passes the release scrub. |
| D8 | Records from a codebase session reach their home after the window, listed in the morning view first; at once only when the router judged them and the home is walled with the writer in its crew. (Owner's choice of 2026-10-24: without the router nothing reads a note for a left-out kind before it leaves.) |
| D9 | A session never writes into a project space or the brain directly: one door in, one scrub, one place to pull back. |
| D10 | Needs you shows once a day, in the person's first session of the day, not in every session. |
| D11 | Capture on events: writing a spec, plan or design file, or opening a pull request, in a linked codebase always makes one journal entry. |
| D12 | A contractor counts as inside only when registered as a person in the brain; an unregistered collaborator makes the readers wider. |
| D13 | Cloud sessions are deferred until after the first public release (section 8). The docs name the limitation in one line. |

## 4. The pieces

```
 codebase ──(remote URL)──► link.py resolve ──► subject, home, readers, brief
                                    ▲
                registry: subject.repos[]  ◄── scan · detect · check
```

### 4.1 The link (registry)

A subject entry in the registry that holds it gains a list:

```yaml
- id: harbor-voice-agent
  home: harbor
  repos:
    - repo: northwind/harbor-voice      # owner/name as last seen
      repo_id: R_kgDOabc123             # platform id: survives renames and transfers
      linked_by: detect                 # onboard | mint | detect | person
      readers: wider                    # inside | wider, set by check
      checked: 2026-10-22
```

In the first version one codebase links to one subject; links scoped to folders are a planned
extension (4.9). A product company links its one repository to a subject
whose home is the brain (for example `product`), so the same rules hold with the company as the
audience.

### 4.2 `link.py`

| Command | Runs in | Does |
|---|---|---|
| `resolve [--cwd]` | any session, on a trigger | Reads the git remote of the folder, finds the subject in this space's registry and every mounted one, prints subject, home, audience, readers and the brief's path as JSON. No network. Exit 3 when unlinked. |
| `detect` | inside `resolve`, the first time it meets an unlinked repo | Scores the repo's owner and name against subject ids, titles, aliases, clients and existing links. One clear match links at once only when the repository's owner is trusted (the person's own login, this space's or a mount's remote owner, an owner of an existing link, or a registered `match.github`/`repo_owner` identifier on a subject), and logs the undo; otherwise it asks once, naming the owner. Several: one Needs-you item with a recommendation. None: nothing, and never asked twice for that repo. |
| `scan` | onboarding, project creation | Lists the repos the person can reach (their account and the workspace organisation) and runs `detect` on each. |
| `check` | the daily chain | For every link: finds the repo by `repo_id` (updates a renamed or moved name), recomputes `readers` from the platform's collaborators and teams against the subject's audience, repairs what it can. Health line: `links N, wider M, repaired K`. |

### 4.3 The personal instruction

The instruction `onboard` installs at user level gains four short rules:

1. **Triggers, not session start.** Before brainstorming or designing a feature, writing a spec
   or plan, preparing a call, or answering anything about a client, the product or a past
   decision: run `link.py resolve`. When linked, read the subject's brief and answer questions
   with the `query` skill run from the workspace root (workspace, brain and crew projects).
   A plain coding task never triggers it.
2. **Three rings.** Ring 1: the linked subject and its parent (`part_of`), in full. Ring 2:
   the company layer, meaning the brain's pages, lessons and standards; always allowed,
   because everyone reads the brain. Ring 3: anything else the person can read, such as
   another client's material or their private notes; only when the person names it, and then
   for thinking only. Nothing from ring 3 is written into the codebase or routed to this
   subject's home. A `query` run for this work searches rings 1 and 2 unless the person
   widens it.
3. **Writing into the codebase.** `readers: inside`: write freely (secrets never). `readers:
   wider`: only what would pass the release scrub for that audience: requirements, decisions,
   constraints. Never the left-out kinds (prices, pay, credentials, other clients' data,
   personal details) and never private notes. Point to the space for the rest.
4. **Capture.** Entries take `subject: <id>` from the link. Pointers use one form:
   `ref: <owner/repo>:<path>@<commit>` (`@HEAD` for a file not committed yet) or a pull request
   URL. After writing a spec, plan or design file, or opening a pull request, in a linked
   codebase: one entry with what it is, why, and the pointer.

### 4.4 Capture and distill

- `capture` documents the `ref:` line. The validator accepts it in journal entries.
- `distill` lists `ref:` pointers as evidence on the subject's candidate; status pages link them.
- A pointer carries a location, not content, so it may travel to the home. The scrub still
  reads its path.

### 4.5 Routing

Records from a codebase session go to the subject's home like any record: router at once where
P2 applies, otherwise after the window. The session never writes into the home itself.

### 4.6 The repository feed

Once a codebase is linked, the home's sweep reads it as a `repository` source:

- merged pull requests: title, description, author, merge time, permalink;
- added or changed spec-like files (`docs/`, `specs/`, `.kiro/specs/`, decision records,
  `*design*.md`): a record with a permalink at the merge commit;
- never the space's own machinery pull requests (existing rule).

The records carry the home's audience and pass the classification gate like any other. For a
product company the brain's sweep reads the product repository.

### 4.7 The block in an owned codebase

Where the company owns the repository, `link.py stamp` opens one pull request in it that adds
or replaces a short section of its `AGENTS.md`:

```
## Where this code's knowledge lives
This code belongs to the subject harbor-voice-agent. Its knowledge lives in the space
northwind/harbor, not in this repository. Your personal workspace instruction tells you how
to reach it. Write here only what this repository's readers may see.
```

It replaces older instructions about pinned brains and in-repo wikis. It is never added to a
repository the company does not own. Nothing else of the machinery goes into a codebase.

Not in the first build: it ships with cloud sessions (section 8), which extend the same block.
The personal instruction covers every local session.

### 4.8 The lesson loop

Ring 3 is where context could be lost: a lesson learned for one client that would help
another. The loop moves such lessons into ring 2 so they stop needing a request.

1. The person reaches across by name ("didn't we solve this for Beacon?"). The session reads
   it and uses it to think. The Harbor spec and the Harbor space get none of Beacon's detail.
2. The journal entry for the current subject gains one line, `lesson: <the general point>`,
   with `from: <other subject>`. The `from:` subject's material stays in the person's
   workspace.
3. Distill proposes a company-grain lesson page for the brain from that line. The release
   scrub removes client names and other left-out kinds, and the classification gate applies
   as for any company page.
4. Once signed, the lesson sits in the brain, ring 2, and every later session pulls it in
   without being asked.

A lesson proposed twice from different subjects is merged into one candidate. A lesson the
signer rejects is not proposed again from the same `from:` subject.

### 4.9 Planned extension: links scoped to folders (monorepos)

Not in the first version. Build it when a company has several teams, each with its own subject,
in one repository. Until then the repo-wide link covers every case, including a monorepo whose
apps all belong to one subject.

The gap it closes: with one link per repository, every session in a monorepo gets the same
subject. A session in the billing folder then pulls the whole product's brief, its notes are
filed under the product rather than billing, and every pull request goes to one subject.

The shape: a link may name folders.

```yaml
repos:
  - repo: lumen/app
    paths: ["apps/billing/**"]   # subject: billing
  - repo: lumen/app
    paths: ["apps/search/**"]    # subject: search
  - repo: lumen/app              # subject: product, everything else and the parent
```

Rules:
1. Each folder has exactly one subject; the most specific match wins.
2. One repo-wide link covers everything else, and is the parent (`part_of`) of the folder
   subjects.
3. The session picks the subject by the files it works on, not by the terminal's folder. When
   it touches nothing specific, or several areas at once, it uses the repo-wide subject.
4. `detect` proposes folder links from the repository's `CODEOWNERS` and its top-level folders
   (`apps/`, `services/`, `packages/`) matched against subjects; a clear match is linked at once.
5. The repository feed tags a pull request with the subject of the folders it touches; one that
   spans two areas goes to both.
6. Readers stay repo-wide: the platform cannot restrict reading to a folder, so a subteam's
   private information in a shared monorepo meets the wider-readers rule.

## 5. The flows

**Context in.** Trigger → `resolve` → brief (one small file the daily status already writes) →
`query` from the workspace root over rings 1 and 2 for specific questions → answer. Ring 3 only
when the person names it. No search runs at session start.

**Notes out.** Session → the person's daily note (`subject:` and `ref:`) → sweep and distill →
router or window → the home's status page and candidates → signed pages.

**Code activity.** Codebase → the home's repository feed → records → the same status page.

**Into the code.** The session writes specs and docs into the codebase, filtered by `readers`.

## 6. Two company shapes

| | Agency (many client codebases) | Product company (one SaaS repo) |
|---|---|---|
| Subject | one per client project | `product`, home the brain |
| Links | several codebases per subject | one |
| Readers | often wider (client engineers, other developers) | usually inside (everyone is staff); contractors decide |
| Feed reads into | the project space | the brain |
| What the rule changes | a lot: client context stays out of shared code | little: today's habits, with a scrub only for wider readers |

A monorepo shared by several teams, each with its own subject, needs links scoped to folders:
the planned extension in 4.9.

## 7. Self-repair

| Situation | What happens |
|---|---|
| Session in an unlinked repo | `detect` links a clear match at once when its owner is trusted, else asks once, naming the owner, with a recommendation |
| Repo renamed or moved | `check` follows `repo_id` and updates the name |
| Repo deleted or access lost | `check` marks a link it found before `gone` (one it never found, such as a client's private repository, stays); `resolve` treats it as unlinked; no owner alert |
| New collaborator widens the readers | `check` flips `readers` to `wider`; the stricter rule applies from the next session. Already-written files are not rewritten |
| Two subjects claim one repo | `check` keeps the older link and turns the other into one question |
| `gh` unavailable | `resolve` still works from the registry; `scan`, `detect` and `check` retry next run |

## 8. Deferred: cloud sessions

Not in this version (D13). A cloud session today has no user-level instruction and no sync
script, so it would lose its notes, conflict on the shared daily note if it pushed, find nothing
that merges its branch, and see empty brain and project folders. Until this is built, plain
coding works in the cloud and brainstorming and specs happen locally; the docs say so in one
line.

The scope for later, by what the session starts with:

- **Workspace plus codebase (the way to start):** the workspace rules gain a cloud exception
  (commit only your own journal file to a branch and open a pull request); each cloud session
  writes `journal/<yyyy>/<mm>/<date>.cloud-<session>.md`; the sync routine merges a pull request
  that touches only `journal/` once its checks pass; the session fills in the brain and project
  folders before context work when its access allows.
- **An owned codebase alone:** the block in 4.7 gains one line telling the session to clone the
  person's workspace beside it, which turns it into the case above.
- **A codebase alone with no workspace reachable:** decisions go into the pull request
  description under `## Decisions and open questions`, filtered by the readers rule; the
  repository feed carries them to the home.

## 9. Out of scope

- The setup check that passes on a run log the template ships (separate fix).
- A search index over the spaces (the `_meta/search.yaml` engine stays as it is).
- Rewriting history of files already in codebases.

## 10. Testing

On the fixture company, with one linked codebase and one unlinked:

- `resolve` returns the subject in under a second with no network; exit 3 when unlinked.
- `detect` links a clear match of a trusted owner, asks once on a tie or an untrusted owner, never asks twice;
  `scan` links at once, because the host listed the repository for a trusted owner.
- `check` follows a rename by `repo_id`; flips `readers` when a collaborator is added.
- Behaviour tests: a brainstorm in the codebase reads the brief; a bug fix does not; a session
  with `readers: wider` leaves a planted price and another client's name out of a spec.
- Rings: a brainstorm finds a brain lesson without being asked; another client's meeting is
  not read unless named; when named, none of its detail reaches the spec or the home.
- Lesson loop: a named reach across produces one `lesson:` line and one brain candidate with
  the client's name removed; a rejected lesson is not proposed again.
- Capture: a spec written in the codebase produces one entry with `subject:` and `ref:`;
  distill lists the pointer; the status page links it.
- The repository feed files a merged pull request and a changed spec as records in the home,
  and skips machinery pull requests.

## 11. Open questions

None. Deferred: cloud sessions (section 8) with the block in owned codebases (4.7), and links
scoped to folders (4.9).
