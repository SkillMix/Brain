# Cloud routines: the sweep and daily chains run in the cloud

Status: proposed, 2026-10-27, revised the same day after a builder check and an adversarial
review. Builder: the plan at `docs/design/2026-10-27-cloud-routines-plan.md`.
Supersedes, once accepted: decisions "Chains run beside the sync script" (2026-10-12) and the
part of "Routines never run git" (2026-10-13) that says a routine never starts the sync script
for its own files. Keeps: "The sync task ships" (2026-10-12), so releases still leave from the
person's computer.

## 1. Intent

A person's routines should run when the person's computer is closed. Today the sweep chain
(three times a day) and the daily chain (once a day) run on the person's computer, as the
desktop app's scheduled tasks or as OS tasks running `claude -p`. A closed lid, a sleeping
laptop or a closed app means no sweep and no morning briefing, and nothing says why until the
person looks.

Claude Code can run a prompt on a schedule in the cloud (scheduled tasks, earlier called
routines; the agent tool is `RemoteTrigger`). Such a run gets fresh clones of the attached
repositories and the person's claude.ai connectors, and it ends when the session ends.

The chains left the cloud on 2026-10-12 for one reason: "a cloud run that commits nothing
leaves its feed exports, candidates and run lines in a checkout that is thrown away". The
earlier cloud era failed on the same thing: "landing, not running, is the failure". The second
blocker, Gmail attachments, is gone: a read-only mail connector with an attachment reader now
exists. This design makes a cloud run land its own files, through the same sync script and the
same pull request checks a computer uses, carries the computer's own safety profile into the
cloud, and keeps on the computer only what has to be there.

## 2. Goals, non-goals, anti-goals

Goals
- G1. The sweep and daily chains run as cloud scheduled tasks and need no computer to be on.
- G2. A cloud run lands its own files: it commits once at the end through `sync.py`, pushes a
  `cloud/<chain>` branch and opens a pull request that the space's own checks merge.
- G3. Nothing gets weaker than the local runner: the same allow list for the shell, the same
  connector grant, the same send guard, no push to `main`, no secret pushed, no private zone
  pushed, no edit to the machinery, mail only to the person.
- G4. One place runs a workspace's routines. Moving them to the cloud and back is one sentence,
  pausing still works, and a run that did nothing shows up.
- G5. The sync script stays on the computer every 15 minutes for what only the computer has:
  attended sessions' edits, mount pins, the release leg and requests, template sync, the
  transcripts scan, and adopting a cloud pull request nothing could merge.

Non-goals (this version)
- Reading the person's history in parts (the backfill) in the cloud. History stays on the
  computer, so moving routines to the cloud waits until the history is read, and a history read
  cannot start while they run there.
- Unsure records with their content across runs in the cloud. A cloud sweep keeps their ids
  only (4.8), so nothing is lost and nothing private is pushed.
- Attended cloud sessions (the codebase-link design, section 8). Unchanged.
- Making the cloud the onboarding default. Onboarding still schedules on the computer; the cloud
  is a sentence the person can say afterwards ("run my routines in the cloud").
- Advancing mount pins in the cloud. Mounts are read at their pins, as CI reads them (4.8).

Anti-goals: this fails even if it runs, when
- a cloud run's output is lost or lands only when the computer wakes;
- the cloud and the computer both run a chain, so mail is read twice or two briefings arrive;
- a mail anyone can write gets a cloud run to send, edit machinery, push to `main` or merge a
  pull request that waits for the person;
- `_meta/state/private/`, `_meta/state/feed/`, `personal/`, `academic/` or a secret reaches
  GitHub.

## 3. What a live cloud run showed (2026-10-27)

A one-off probe run, attached to a workspace and two of its mounts, measured the platform
(VERIFIED unless marked):

| Question | Answer |
|---|---|
| Runtime | Ubuntu, Python 3.11 with PyYAML 6, git 2.43, gh 2.89, UTC clock, `CLAUDE_CODE_REMOTE=true`, 4 CPUs, 15 GB. No setup script needed. |
| Layout | Each attached repository is a shallow clone at `/home/user/<repo>` on `main`; the session starts in `/home/user`. |
| Mounts | `git submodule update --init -- <mount>` works in about 2 s for a mount whose repository is attached; a mount whose repository is not attached fails (needs `add_repo`). |
| Push | `git push origin HEAD:refs/heads/cloud/<name>` works; the commit author is the cloud's own identity. |
| GitHub identity | The pull request the run opened was opened by the person's own GitHub account (`author_association: OWNER`). The run acts on GitHub as the person, with the person's rights on every attached repository. |
| Pull requests | `gh api -X POST repos/<o>/<r>/pulls ... -F draft=false` opens a ready pull request. `gh pr ...` (GraphQL) is refused: only REST works. |
| Deny rules | The trigger's `disallowed_tools` are enforced: `Edit(_meta/tools/**)` refused the edit; `Bash(git push --dry-run origin HEAD:main*)` refused the command, also inside a compound command. A Bash rule matches the command's text only. |
| Connectors | The tool prefix is the connection's `name` (`mcp__Gmail-readonly__read_gmail_attachment`). A connection's `permitted_tools` did not narrow the tools: all 30 of the mail connector's tools were offered with `permitted_tools: [send_message, get_message]`. Narrowing has to be done with `disallowed_tools` by exact tool name, and a hook. |
| Mail send | `send_message` to the person's own address ran unattended; read back with labels SENT and INBOX. |
| Attachment text | `read_gmail_attachment` returns the text inline, 40,000 characters per call with an offset for more. |
| Schedule | `CRON_TZ=<IANA zone> m h * * *` is accepted; `next_run_at` follows the zone. |
| Trigger fields | `environment_variables` is refused on triggers. The body takes `enabled_plugins`, `extra_marketplaces`, `auto_mode_allow` and `auto_mode_soft_deny` (seen, not exercised). |
| CI merge | The space's own `checks` ran on the pull request opened from the cloud, `arm` armed auto-merge, and it merged 65 s after it opened, with no computer involved. |
| Branch protection | The workspace's `main` requires `checks` but has `enforce_admins: false`, so the person's own token, and so a cloud run, can push to `main` past the checks. |
| Secrets in the run | `GH_TOKEN` and `GITHUB_TOKEN` are set by the platform's proxy. A repository not attached to the task answers "GitHub access to this repository is not enabled for this session". |

Facts from the earlier cloud era (2026-09 to 10-12 run logs): fires start 1 to 14 minutes
late; a run that did nothing still reports "succeeded"; tool results over the size limit are
saved to a file the run can read; the GitHub tools `mcp__github__*` exist in every run.

From the docs: in a session with several repositories, only `enabledPlugins` and
`extraKnownMarketplaces` are read from each repository's `.claude/settings.json`; permission
rules and hooks are not. So the trigger's own fields and a plugin it enables are the only places
a multi-repository run carries its safety profile (4.7).

## 4. Design

### 4.1 Shape

Two scheduled tasks per person, created by the agent with `RemoteTrigger`:
- **sweep**: three times a day; connectors to read the person's work apps, and the tracker.
- **daily**: once a day; the mail connector's send and read-back tools, held by the send guard.

Each task attaches every space the person owns that runs chains today (`chain.py spaces`), plus
every repository those spaces mount, so mounts initialise. The sweep task also attaches the
codebases its spaces link (`_meta/state/links.json`) and their `repo` sources, so the repository
feed can read them. What a run still cannot reach is named, never read as gone: the link check
and the landing check treat "access to this repository is not enabled" as a host they could not
ask. The record of the move keeps the repositories attached, and the computer's sync names a
space the person owns that is not among them (a space minted later) as one Needs-you item.

The task's prompt is short: who the person is, which clone is the workspace, and "follow
`_meta/templates/cloud/<chain>-chain.md` in the workspace clone". The steps live in the
repository, so template sync updates them and the prompt on claude.ai never drifts. They sit
outside `_meta/templates/routines/`, whose every file the build turns into a task on the
computer, and they are protected paths (4.7).

`cloud.py trigger` renders the task body from the space files (sources, schedule, tool lists,
deny rules, plugin, prompt). Python cannot call `RemoteTrigger`; the agent passes the body to it.

### 4.2 A cloud run, step by step

1. **Begin** (`cloud.py begin --chain <c>` in the workspace clone). It finds the space clones in
   `/home/user` the person owns and initialises their mounts (attached repositories only, one at
   a time, each at its pin). It reads the routine host record: runner not `cloud`, or paused, and
   the run stops at once, writing and landing nothing (a run that steps aside leaves no line, as
   on the computer). Otherwise it:
   - **carries** the previous run of this chain into every clone (`sync.carry`). When
     `cloud/<chain>` still exists on `origin` and its head is not a pull request the person
     closed unmerged, it is merged in now, before anything is exported, deepening the shallow
     clone until the two share a commit. So the cursors and watermarks already hold what the
     earlier run read, and the new run never writes the same witness pages, queue items,
     tracker issues or notices again. A carry that conflicts stops the run: it never works on
     top of files it could not merge.
   - writes the run's guard profile (4.7) outside the repositories, from the grant in the host
     record;
   - prints the roots and, for the sweep, the export cursor of every source.
2. **Export** (sweep): as the sweep skill says, from the cursor `sweep.py <root> --cursor
   <source>` prints: the feed file's last record when the file exists, else the watermark's mark
   for that feed, else 90 days back. The feed files stay in the clone and are never pushed.
3. **Tools**: `chain.py sweep <root> --cloud` and `chain.py daily <root> --cloud` (4.8).
4. **Judgment**: route, distill and check exactly as today.
5. **Tracker** (sweep, workspace only), **owner notices** (both) and **the briefing** (daily,
   workspace only).
6. **Run line**: the agent's own line, as today.
7. **Land**: `sync.py --root <root> --cloud <chain>` for every root, workspace last (4.3).

Every command uses absolute paths (`python3 /home/user/<repo>/_meta/tools/<tool>.py <root> ...`),
one plain command at a time, so the guard's allow list (4.7) holds it.

### 4.3 Landing: `sync.py --cloud <chain>`

The sync script stays the only code that runs git. In a cloud run the agent starts it once per
space at the end. `--cloud <chain>` changes these things:
- The branch is `cloud/<chain>` (no machine hash). The earlier run's branch was already carried
  by `begin`, so the push drops nothing. The push is leased to the branch head `begin` saw; a
  rejected lease (another run pushed meanwhile) fetches, carries and pushes again once.
- **Protected paths never land from the cloud.** The cloud makes no machinery change, so any
  change under the protected paths (the gate's list and the routine profile's `PROTECTED_EDITS`)
  is left out of the commit and becomes one security item on Needs you ("a cloud run changed
  <k> protected files; they were not landed"). There is no `cloud/<chain>-protected` branch.
- Its own run line is written before the commit, so it lands with the run. It says what it
  staged and whether it carried an earlier run; what happened after the push is printed for the
  run log, and the next run's line shows it again (`carried`).
- Untracked files need no minimum age. Nothing about pins, the release leg or `watch_chains`
  runs. Failing answer tests are still set aside before the push, as on the computer.
- A file the secret scan holds back is not lost with the clone: in the cloud, each line the scan
  hits is replaced by `[a line held back: <kind>]` and the page lands. A file over 1 MB or not
  text is named in the run line.
- **Quiet runs.** A change that is only run lines, watermarks and `stale.yaml` pushes the branch
  without opening a pull request; a later run carries it, and a pull request opens once a run has
  a real change or the branch's first unmerged commit is older than six hours. So a quiet day
  costs one pull request per space, not four.
- The pull request opens through REST.
- After the push it waits for the merge, at most nine minutes per call. A conflict with `main`
  is merged again with the merge rules (4.5a) and pushed, at most three times. Checks that failed
  get the computer's repair once: answer tests the failed log names are set aside for CI and the
  branch is pushed again. When it has not merged in time, it is left open. The next cloud run
  carries it, and the computer adopts it (4.5).

Everything else is the computer's code path: the same candidate rules (private zones,
submodules and nested checkouts never staged; `_meta/state/feed/` joins the private zones), the
same secret scan, `merge origin/main` with the merge rules written into the space's own git, and
never a push to `main`.

### 4.4 REST instead of GraphQL

`gh pr list/view/checks/create` call GraphQL, which a cloud run refuses. `sync.py`,
`needs_you.py` and `link.py` move to `gh api` REST calls. They behave the same on a computer, and
nothing is lost silently: `needs_you` currently returns an empty list when `gh pr list` fails,
and it will now say so. The repository feed in the sweep skill reads pull requests through the
REST search (`gh api search/issues?q=...`) and `pulls/<n>/files`, not `gh pr`.

### 4.5 The computer's leg

`sync.py` on the computer keeps everything it does today, and adds, for a workspace whose
runner is `cloud`:
- **Transcripts.** The transcripts scan reads the person's local session files, so it moves from
  the sweep chain to the sync. It runs at most every six hours, when `transcripts.path` exists
  on this computer, and rewrites `sessions.yaml` only when a session row changed. A cloud sweep
  never runs it. The scan never blanks `sessions.yaml` when the path is missing; that is a bug
  today.
- **Template sync**, weekly, as the daily chain runs it today: its machinery changes need the
  person's click, and a cloud run never makes one.
- **Not started**, after the merge rather than before, so a computer that slept reads the
  cloud's run lines before it says a chain did not start.
- **Adopting.** A `cloud/<chain>` pull request still open after 30 minutes, conflicted or red,
  gets merged into the computer's checkout with the merge rules. It then rides the computer's own
  sync pull request, which the computer repairs as it repairs its own. When that lands, GitHub
  marks the cloud request merged (unless the computer's push was split for protected paths; then
  the next cloud run replaces it). A conflict the rules cannot resolve is aborted and becomes one
  Needs-you item.
- **Spaces not attached.** A space the person owns on this computer that the move did not attach
  is one Needs-you item ("say 'run my routines in the cloud' again to add it").

### 4.5a Merges that never wedge

GitHub's own merge knows no merge rules, so every merge the platform cannot do is done by a
sync, in the cloud or on the computer, with rules the sync writes into the space's own git:
- **Append-only files** (journal, logs, `*.jsonl`, the host record): union, as today.
- **Watermarks** (`_meta/state/*-watermark.yaml`): the later mark wins, key by key (a small
  merge driver in `sync.py`), so neither side's progress is undone.
- **Generated pages and state** (`live/**`, `_meta/state/ids.yaml`, `_meta/state/stale.yaml`):
  this checkout's side; the next status run writes them again from the files.
- **Items a person answered or a release shipped** (`_inbound/**`, `_outbound/**`): when one side
  deleted the file and the other changed it, the deletion wins. The person's answer or the
  shipping is the later fact.
- Anything else stops the merge, as today.

Two writers are also kept apart at the source:
- **Requests between spaces** stay with the computer's release leg, which already answers them
  every 15 minutes; the cloud daily chain leaves them out.
- **A sweep, a distill or the queued owner notices in a session** on the computer: while the
  routines run in the cloud, the sweep, distill and owner-email skills leave them to the cloud (a
  session still sends a notice it queued itself), so mail is never read twice and no notice is
  sent twice.

### 4.6 Host, gate, pause and proof

`routine-host.yaml` takes a third runner, `cloud`, with the machine `m-cloud` (the record's
machine pattern, which no computer's id can take), the two task ids, the person's zone, the
repositories the tasks attach and the connector grant (4.7). On the computer, `routine.py`
already steps aside for any runner but `os`, so OS-task runs stop by themselves. The app's own
scheduled tasks never read the record, so the computer's prompts gain a first step,
`chain.py gate <workspace>`, that stops a run whose computer is not the host or that is paused;
and moving to the cloud turns the app's tasks off (the agent does it with the scheduled-tasks
tools; when those are not available, it is the one hand step).

A pause works as it does today. `onboard.py --pause` writes `paused_until`, the sync lands it,
and the next cloud run's `begin` reads it on `main` and stops. Away from the computer, the
person (or a session on the phone) pauses by disabling the two tasks.

`onboard.py --routines-cloud --sweep <id> --daily <id> --timezone <zone>` claims the host for the
cloud after two proof runs. `--routines-here` takes it back: the cloud tasks are disabled first,
and the OS tasks come back. History that is not yet read refuses the move to the cloud with one
sentence, and while the routines run in the cloud a history read cannot start (`backfill.py`
refuses on a computer that is not the host; its sentence says "run my routines here" first).

**A proof run** happens before the claim, so `begin --proof` skips only the runner check. It
exports nothing, runs no chain tool, creates no tracker issue and sends nothing, so it never
overlaps the computer's own runs. It tries what the profile must refuse (4.7, section 6), writes
what happened into its run line, and lands that line through `sync.py --cloud`. The move is
refused unless both tasks' proof lines on `main` show every refusal. The task is created with a
proof prompt and changed to the real one when it is enabled.

### 4.7 The safety profile in the task

The repository's settings do not reach a multi-repository run, and the run acts on GitHub as the
person. So the task carries the computer's routine profile, in four layers:

1. **The guard plugin** (`spaces-guard`, in the template under `_meta/plugins/`, offered by the
   space's own marketplace file `.claude-plugin/marketplace.json`; the task enables it with
   `enabled_plugins` and `extra_marketplaces`). Its hooks are the computer's: on Bash the
   `bash_guard` allow list (one plain call of a routine tool, or a read-only `gh api` GET, nothing
   else; `python3 -c`, other programs and compound commands are refused); on every connector tool
   the grant by exact name, so a tool a vendor adds later is refused; on Task only the
   general-purpose agent; on each send tool the `send_guard`, which refuses any address but the
   person's. The hooks read the profile `begin` wrote (`routine.routine_settings`, the same
   function the computer uses); before `begin` has written it, the only command they let through
   is `cloud.py begin` itself. Outside a cloud run (`CLAUDE_CODE_REMOTE` not `true`) they let
   everything through, so a session on the computer that enabled the plugin is unchanged.
2. **`disallowed_tools`**, enforced by the platform:
   - `Edit` on every protected path (`routine.PROTECTED_EDITS` with a `//**/` prefix, and
     `identity/**` in each clone), which covers Write and NotebookEdit;
   - every built-in tool the computer's profile denies (`reader.DENIED` that a routine does not
     use, among them `WebFetch`, `WebSearch`, `RemoteTrigger`, `CronCreate`, `SendMessage`,
     `PushNotification`), and `mcp__github`;
   - every tool of each attached connection that is not granted, by exact name;
   - the shell patterns the computer denies (`python -m`, `git --output`, the tool scripts a
     routine never runs) and, as text-only backstops, `git`, `gh` writes and GraphQL.
3. **`auto_mode_soft_deny`**: the same limits in plain words (never push to `main`, never merge a
   pull request, never send mail to anyone but the person, never edit machinery, never change
   `.gitignore`), for the platform's own check.
4. **On GitHub**: `main` gets `enforce_admins: true` (onboarding sets it for new spaces; the move
   to the cloud checks it on every attached space), so not even the person's token pushes to
   `main` past the checks or merges a red pull request. The gate's protected list always includes
   a minimum, whatever `space.yaml` says: the machinery, `CLAUDE.md`, `AGENTS.md`, `.gitignore`,
   `.gitattributes`, `.gitmodules`, `_meta/templates/` and `_meta/plugins/`. A change there waits
   for the person's click. Two consequences are settled so nothing lands half-done or waits for
   no reason:
   - A new or re-addressed mount waits as one unit with its `.gitmodules` entry. A pin move of a
     mount already on `main` still lands on green.
   - An `AGENTS.md` that is exactly what `agents_md.py` renders from the same checkout counts as
     part of a template release, so a release still lands on its own.

The grant is the computer's, read from the same verdicts (`apps.yaml`) when the tasks are made:
read tools for read apps; the tracker's create and update tools; for mail, `send_message` and
`get_message` only, and only while the send guard holds. The sweep's read-only mail connector
serves its mail and attachments. Owner notices go out from whichever task runs first after they
are queued; both tasks hold the send tool behind the same guard. `record-send` still refuses a
read-back whose recipients are not the person, as a second check.

What is left, and said so: a merge of a green protected pull request through the REST API by
code the guard let run. The guard's allow list leaves no such code, `enforce_admins` keeps the
pull request's checks in force, and section 7 names the review rule that would close it.

### 4.8 State a cloud run does not keep

| State | In the cloud |
|---|---|
| `_meta/state/feed/` (gitignored, and a private zone of the sync) | Rebuilt from the cursor each run, never pushed. |
| `_meta/state/private/` | Briefs, packs and payloads are rebuilt within the run. |
| Unsure records | Their ids, source and time (no content) in `_meta/state/unsure-ids.jsonl`; the morning view counts them and a session on the computer can fetch each by id. |
| Attachments a run could not read | Their record id and name in `_meta/state/attachments-unread.jsonl`; `sweep.py --attachments` lists them again until read. |
| Transcripts | Scanned by the computer's sync (4.5). |
| The attachment backlog of older records | Read by attended runs on the computer. |
| Outbox reconcile | Needs the mail feed, so in the cloud it runs after the sweep's export, not in the daily chain. |
| Release, requests, template sync | Not in the cloud: the computer's sync runs them (decision 2026-10-12, 4.5a). |
| Mount pins | Read at their pin, as CI reads them. Pins move with the computer's sync; 4.11 names a pin older than three days. |

`chain.py ... --cloud` applies the table:
- The sweep skips the transcripts scan and adds outbox reconcile.
- The daily chain skips template sync, release, the two request steps and outbox reconcile, and
  leaves the link check's GraphQL calls out.

### 4.9 Schedules

The task cron is `CRON_TZ=<the person's IANA zone> <m> <h> * * *`, taken from the same hours the
prompts state, with minutes off the hour (the platform starts top-of-hour runs late). The person
keeps local hours across daylight saving. The zone is given when the tasks are made (the agent
reads it from the computer) and kept in the host record.

### 4.10 Moving to the cloud: the sentence

"Run my routines in the cloud" (onboard skill):
1. `onboard.py --routines-cloud --check`: the history is read, the apps' grant exists, every
   space and mount has a GitHub repository, and each has `enforce_admins` on (the agent turns it
   on when the person says yes; it is the person's repository setting).
2. `cloud.py trigger --chain sweep|daily --proof` renders each body. The agent creates both
   disabled, runs each once, and reads its run log, its proof line and the pull request it
   opened.
3. On two green proofs it updates each task to the body `cloud.py trigger` renders without
   `--proof`, enables both, turns off the computer's chain tasks, and runs `onboard.py
   --routines-cloud --sweep <id> --daily <id> --timezone <zone>`, which reads the proof lines and
   refuses without them.
4. One sentence names the two task links and how to pause.

### 4.11 A run that did nothing

Each cloud `begin` reads the age of the other chain's last line on `main` in every root (the
sweep reads the daily's, the daily the sweep's). Older than twice the longest schedule gap, and
not paused, means one Needs-you item ("the cloud sweep has not run since …"). The daily `begin`
also reads each mount's pin: one older than three days means the computer's sync has not run,
and the brain the cloud reads has stopped moving; that is one Needs-you item too. The computer's
`not_started` keeps working whenever the computer is on. A run's own proof is its run line on
`main`, never the platform's "succeeded".

## 5. Rules and decisions

- rules.md rule 7 becomes: "Agents edit files. The sync script commits: on the computer every
  15 minutes, and once at the end of a cloud routine run. The platform merges. No session runs
  a git write itself."
- The generated `AGENTS.md` text changes with it.
- New decision rows: chains run in the cloud by choice; the cloud lands through `sync.py
  --cloud`; REST only; transcripts and template sync move to the computer's sync; `runner:
  cloud`; the profile lives in the task and the guard plugin; merges never wedge; the cloud
  never lands a protected change; `enforce_admins` on.

## 6. Proof before it counts

A cloud chain counts as working only when all of these hold:
- a proof run of each task opened a `cloud/<chain>` pull request that merged on green;
- its run lines are on `main`;
- the guard was active: a shell command outside the allow list (`python3 -c "print(1)"`) was
  refused;
- a protected-path edit was refused;
- a connector tool outside the grant was refused (for the daily task, a mail tool such as
  `trash_message`);
- for the daily task, a send to an address that is not the person's was refused.

`onboard --check` reads the task ids from the host record and the last run lines from `main`.

## 7. Left to the builder, and later

- A required review on protected paths. GitHub's code-owner review would close the last gap in
  4.7, but the person cannot approve a pull request their own token opened, so it needs a second
  identity for the cloud or for the person's approvals.
- Advancing mount pins in the cloud, with a rule for two runs that moved one pin.
- Making the cloud the onboarding default, once cloud runs have been green for a while.

My calls, for the owner to check: two tasks per person, not one per space; landing through
`sync.py --cloud`, not a second lander; the earlier run is carried at the start of a run; the
cloud carries the computer's own guard as a plugin, and the move is refused until a proof shows
it holds; `enforce_admins` on; the cloud never lands a protected change, so template sync moves
to the computer's sync; merges resolve by file kind; requests stay on the computer; quiet runs
push without a pull request; history stays on the computer; unsure records keep their ids only;
mounts are read at their pins; `CRON_TZ` with the person's zone; a proof run before the claim.
