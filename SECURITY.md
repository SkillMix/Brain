# Security policy

## Report a vulnerability privately

Use GitHub's private vulnerability reporting: open this repository's **Security** tab and
choose **Report a vulnerability** ([direct link](../../security/advisories/new)). Please do not
open a public issue, pull request or discussion for it.

Say what you found, how to reproduce it (the fixture company is a safe place: `python
_meta/tools/onboard.py --fixture <empty dir>`), and what it exposes. We answer in the advisory,
and agree there on a fix and on when it is disclosed.

## What we want to hear about

The template promises that records stay in the space they came into until the rules let them
go, that no agent signs or sends for a person, and that no secret reaches a repository.
Anything that breaks one of those counts, for example:

- a path by which material leaves a space without passing the release rule, the
  classification gate or the second agent's check (`rules.md` sections 1 and 6);
- a page turning `stable` without a person's verdict, or a signature that does not match the
  text that landed;
- a secret the scan lets into a commit, or a scan hit that quotes the secret;
- a record's text acting as an instruction that makes an agent send, sign, release or run
  something;
- a workflow or tool that exposes a token, or writes outside its own space;
- a tool that runs code from a feed file, a page or a pull request it should only read.

Report problems in git, `gh`, Python, Claude Code or a connector to their own projects.

## Prompt injection from swept records

The routines read what anyone can write: mail, chat, call transcripts, calendar invitations,
shared documents and their attachments. They run unattended (`routine.py` hands the prompt to
`claude -p` with nobody to answer a permission prompt), with the person's `gh` sign-in and
connectors. A record that reads like an instruction must not become an action.

- **What a routine may do.** Read the space and its mounts; write pages, feed files, the
  journal, `_inbound/`, `_outbound/` and `outbox/`; run the tools its chains name
  (`ROUTINE_TOOLS` in `_meta/tools/routine.py`), each by its exact path; read pull requests
  and git history. It sends only the owner's own notices and briefing, to the owner.
- **What the routine profile blocks.** Each run adds a profile (`claude --settings`, written
  again every run to `~/.spaces/routines/`). It refuses edits, in every space on the machine, to
  `_meta/tools/`, `_meta/migrations/`, `_meta/tests/`, `_meta/templates/`, the routine prompts,
  `.claude/`, `.github/`, `.git/`, `.gitmodules`, `.mcp.json`, `rules.md`, `space.yaml`,
  `identity/`, `CLAUDE.md`, `AGENTS.md` and Python test configuration, plus `~/.claude/` and
  `~/.spaces/`. It refuses edits to the files only a tool writes (`TOOL_WRITTEN`): the queue,
  the held items and the signals, the requests and answers waiting to leave, the routing,
  release, request and classification ledgers, the conflict notes sent, the registry, the
  lessons, the librarian's proposals, each space's own `log.md`, the person's verdict store
  (`_meta/state/verdicts.jsonl`), the tools' health rows (`_meta/state/health/`) and the brain's
  mount-key requests and record. A desk folder's `log.md` is a desk note: answers count only from
  the verdict store. It refuses a `.pth` or `.lnk` anywhere, and any other program (`.exe`,
  `.bat`, `.cmd`, `.ps1`, `.js` and the like) in a space's root, under `_meta/`, `.claude/` or
  `.github/`, or in a folder on PATH; a chart's `.js` or a script in a desk folder or an
  attached codebase is allowed, since nothing here starts a program from a space by its name.
  It refuses every other tool script by name, `python -m` and git's `--output`, and every
  command that executes a person's word: `sign.py`, `route.py answer` or `--out`,
  `sign_questions.py answer`, `status.py close`, and any `chain.py` verb but its four chains.
  Each text rule wants a space before its verb (and a space after it, or the command's end), so
  a root or subject whose path or id holds the word runs its chains; the computer's hook reads
  the words as the shell will. Each of these tools also refuses itself, before it writes
  anything, inside a routine or a cloud run (`routine.in_routine`: the routine marker
  `SPACES_ROUTINE`, or a cloud run's marker `~/.spaces/routines/cloud.json`), whatever spelling of
  the command reached it. `routine.in_routine` looks for that marker in the home that `HOME`
  names and, on Linux and macOS, also in the account's own home and the cloud's home
  (`/home/user`), so a command that sets `HOME` does not hide it. A cloud task carries no hook,
  so its profile also refuses a quote, backslash, `$`, `?`, `[` or `{` in a command of
  `librarian.py`, `chain.py`, `route.py`, `status.py`, `landing.py` or `release.py`, and a tab
  beside a verb, the withdrawals among them (`landing.py retract`, `release.py retract` and
  `pull-back`) (`cloud.PERSON_ONLY_SPLICE`), and any shell command that names the marker
  (`cloud.MARKER_DENY`). `sign_questions.py`, whose questions the distill step writes in quotes,
  gets the tab rules only: in the cloud its `answer` rests on the tool's own refusal on the
  run's marker. These text rules do not bound a cloud shell: it runs any program (`python3 -c`,
  a script written to `/tmp`), which can call a tool in-process past its refusal or remove the
  marker by a glob. It also pushes as its person, to any branch name it picks, so neither the
  author nor the branch tells the gate a cloud run made a change. So every withdrawal is held at
  the gate, whoever pushed it and on whatever branch: a signed page deleted (not moved), made not
  stable or newly withdrawn (`retracted`, `retracted_at`, `retracted_by`), and a new
  `sample_failed` signal for a page the base branch does not hold withdrawn, wait for the owner's
  click (`gate.withdrawal_reasons`); the computer's sync puts the person's own withdrawal on
  `sync/<machine>-protected`, one click. A withdrawal lands on green only when the same pull
  request deletes a retraction candidate the base branch holds for that page, from the space the
  page was signed in (`signed_in`, which the signature binds) and by the person who released it;
  the gate checks that deletion, not that landing applied it. Landing withdraws a signed page on
  that same test only: a retraction from a space that released a draft signed elsewhere, or only
  relayed the page, leaves the page as it is, marked `contested`. A cloud run holds its person's
identity (the platform's proxy sets `GH_TOKEN`), so the login that opened a pull request vouches
for no signature. Without more, a prompt-injected cloud run can therefore sign, re-sign or answer
as its person: this is the one limit the gate cannot close by itself. The opt-in bound: once the
base branch holds the protected `.github/allowed_signers`, the gate accepts a new or changed
signature on a stable page (or, from the owner's login, a change to what readers act on beside
it, such as `supersedes`), a person's answer in the verdict store or its deletion, or a
`fingerprint` or `fields` line in `log.md` (the history other spaces' landing reads as the
signature), only when every commit of the pull request that changed it verifies against a key that
file lists (`gate._key_signed`).
The key stays on the person's computer (`sync.py --signing-key` makes it and lists its public
half, which lands on one click), and the computer's sync signs its commits with it; anything else
waits for the click. A space without the file keeps the limit above; taking the file away again
is a protected change. Landing's
filing of a release main holds unmarked is the one exception, and only as landing files it: at the
candidate's own target, with its keys, never as a decision; and a cloud run's sync leaves out a
signature or an un-marked release it wrote (`sync.Run.forged_signature`, `gate.marks_only`). This
bounds a cloud run only while the proxy's token lacks the repository's Administration permission,
which could turn branch protection off: check it once with `gh api
repos/<owner>/<name>/branches/main/protection` from a cloud session (a 403 is the answer you want).
A second GitHub login for the cloud would not help while the proxy's token is in the run. A routine
  runs without `SPACES_LOCAL_CLONES`, so a release clone is made only from
  `https://github.com/<owner>/<name>` (the switch is for tests and offline fixtures). Deny rules
  win over any allow rule in any settings file, so a space's own profile cannot widen them.
- **The edit guard.** On the computer a hook checks every edit a routine makes. A page whose
  frontmatter does not parse is not provably signed (a reader cannot parse it either), so a
  routine may repair it, as long as what its edit leaves parses. A signature or
  `status: stable` key counts only where YAML starts a key (a line's start or a flow mapping),
  so a title or summary that holds the words is no signature, and such a line is refused only
  where it can land in the leading frontmatter. The guard matches curly and straight quotes as
  Claude Code does and reads the new string both as given and with its quotes curled. The
  setup's probe runs the edit and person-only guards once in the routines' own shell and Python
  (`routine.guards_check`) and fails, naming that Python, when they refuse a harmless call, for
  example when PyYAML is importable only from the user's own site-packages, which `-I` leaves
  out; setup then installs PyYAML into `~/.spaces/venv` and the routines use that Python.
- **What the space's profile blocks.** `.claude/settings.json` allows each tool the skills name,
  one rule per script (`python` and `python3`), never the whole folder, so a script written into
  `_meta/tools/` asks before it runs; `template_sync.py` asks too, as it copies tools from a path.
  An attended session runs the commands that carry out a person's word without a prompt:
  `sign.py apply` (with the route and librarian answers it files), `sign_questions.py answer`
  and `status.py close` follow the person's own word in the chat (`submit` for a signature),
  and a click after it would only repeat it. `librarian.py apply` is no person's word: it
  applies only a change the librarian itself derives from the tree as it is now, every move
  checked, and a change of owner still waits for the owners' yes, so the daily chain applies
  what a fresh second agent approved; it is not among the opt-in rules, since a routine loads
  your `settings.local.json` too and an ask rule would refuse it there. `chain.py virtual` is
  the fixture's clock.
  The boundary is elsewhere: the routine profile refuses all of them, and each tool refuses
  itself inside a routine or a cloud run. What is left is an attended session that reads
  swept records and is talked into one of them; the sign skill runs `apply` only after the
  exact word `submit`, on the pins its read-back printed. To be asked anyway, add these rules
  to your own `.claude/settings.local.json` (`routine.ATTENDED_ASK_OPT_IN`; a space comes
  before each verb, so they meet no routine's command). A quoted or globbed spelling can slip
  past a text rule, so they are a speed bump, not a boundary:

  ```json
  {
    "permissions": {
      "ask": [
        "Bash(*_meta/tools/sign.py* apply*)",
        "Bash(*_meta/tools/sign.py* --verd*)",
        "Bash(*_meta/tools/chain.py* virtual*)",
        "Bash(*_meta/tools/chain.py* --verd*)",
        "Bash(*_meta/tools/route.py* answer*)",
        "Bash(*_meta/tools/sign_questions.py* answer q-*)",
        "Bash(*_meta/tools/status.py* close *)",
        "Bash(*_meta/tools/landing.py* retract *)",
        "Bash(*_meta/tools/release.py* retract *)",
        "Bash(*_meta/tools/release.py* pull-back *)"
      ]
    }
  }
  ```

  Attended sessions may still edit any file without a prompt: the person is there, and the
  machinery reaches the remote only through a protected pull request.
- **What stays the person's.** Review every pull request that touches a protected path before
  you merge it: a tool, a workflow or the profile changed there runs on every machine that
  syncs. Give each connector the narrowest scope it needs, and no send or delete rights a
  routine does not use. In an attended session that reads swept records, read what the agent
  is about to edit and run; to be asked before any edit to the machinery, add `ask` rules such
  as `Edit(/_meta/tools/**)` to your own `.claude/settings.local.json`.
- **Late answers.** A second computer still on older tools writes answers to `log.md` only. After
  the verdict store's cut-over such a line never counts by itself: it becomes one Needs-you item
  that names the item it answers, and counts only after the person it names confirms it, and
  only when its note was shown whole (at most 500 characters, with nothing the page would hide);
  otherwise the person answers the first item again.
- **What stays a risk.** `_outbound/queue`, `held`, `signals`, `requests` and `answers`, and
  the routing, release and request logs, are written by tools only, and the routine profile
  denies agent edits there; release.py still trusts the `class`, `listed` and `approved_by`
  fields of a queue file it did not verify, so a Python tool tricked into writing one would
  be believed. The never-leaves and left-out lists are enforced by agent judgment (the router
  and its second check), the window and the morning view, not by a deterministic word filter:
  a router that misjudges a record can queue it, and the window and the morning view are
  where the person sees it before it leaves.

## Invitations and templates

An invitation sentence is text anyone can write and send. Setup treats it that way.

- **The template.** Setup installs the official template, the repository the root `README.md` names. A link to any other
  repository is shown to the person (its owner, its creation date, whether it is a fork) and used
  only on their explicit yes. The release's `SHA256SUMS` sits beside the zip, so it proves the
  download is whole, not who made it. The weekly template sync reads the template a space
  records where only the owner's click lands a change (`space.yaml`'s `template:`, else
  `_meta/state/template-origin.yaml`), else the one GitHub says the repository was made from,
  else the official one; never the provenance file a run writes (it can only keep a space on
  the official template while GitHub cannot be asked), and a local folder only when the person
  names it with `--local`. A colleague who joins a brain gets the template that brain records
  (`_meta/state/template-version.yaml`, whose change the gate holds), read only after their yes
  to the brain.
- **The brain.** Before setup reads an invitation, joins anything or creates a repository, the
  person confirms the brain by its exact name (`start.py signins --confirm-brain`), after being
  shown its owner, its age, whether they are a member, and where their workspace would go. The
  question is asked of everyone, members of the brain's organisation included, and nothing is
  read from that brain before the yes: anyone who may create repositories in an organisation the
  person belongs to could make a look-alike brain whose invitation names their login. Only a
  folder on this computer needs no yes; a network share (`\\host\share`, `//host/share`) is never
  one, and is refused without being contacted.
- **The organisation.** After the yes to a brain, setup accepts a pending invitation only from
  that brain's organisation, and reads it back as active. An invitation from any other
  organisation is the person's own click on GitHub's page. A workspace goes in the brain's
  organisation only when the person is an active member of it, else in their own account.
- **The invitation file.** It lives in the brain, where anyone with push access, or a misled
  routine, can change it. Setup takes from it only the person's name, their login and the crew
  projects. A crew project mounts only under `mounts/<name>`, from a GitHub repository in the
  brain's own account or from the address the brain's own registry pins for that project
  (id == home == name, `match.repo`, never a people subject), whose change waits for the owner's
  click; the invitation's address is only compared (online, a crew project is left out when
  that cannot be told, and the agent's health list says so). Never under the name `brain`.
  The briefing address comes from the invitation only
  when it is an address the person is already known by. The never-leaves list, the feeds'
  audiences and the workspace's account are never taken from it.

- **The colleague's login.** `onboard --invite` writes the colleague's GitHub login into the
  invitation and their registry row in one change, which waits for the owner's click: the login
  the owner gave (`--login`), else the one organisation member whose public profile address
  (GitHub lists only verified addresses there) is the registered address. A display name is
  never used: anyone can set it.

What stays a risk: a person who confirms a look-alike brain, or says yes to a look-alike
template, gets what they confirmed. Check the brain's name with the colleague who invited you,
by a channel you know is theirs.

## Secrets a repository can see

Anyone who can push to a repository can read every Actions secret that repository sees. A
branch that edits a workflow runs that edited workflow with the secrets, and the gate holds only
the merge, not the run. The same holds for an agent session that opens such a pull request.

So the checks read their mounts with read-only deploy keys the agents make, one per mounted
repository, held in the consuming repository's own secret `MOUNT_DEPLOY_KEYS` (for each mount a
`mount <owner>/<name>` line and its private key, with a `pending <owner>/<name> <date>` line
before a key the brain's agent has still to attach). It is a repository secret, never an
organisation secret.

- **Who may hold a key.** A key is attached only when everyone who can push to the consuming
  repository can already read the mounted one (`onboard.writers_can_read`, which fails closed).
  The one exception is the brain on a workspace in the person's own account. No key reads what a
  writer of the consumer could not already read.
- **Made by the agents.** The agent makes the key pair on the computer (the private half goes
  only through stdin to `gh secret set`, never to a file in the repository or a command line)
  and attaches the public half where it administers the mount. Every space's daily chain keeps
  them (`onboard.keys_upkeep`): it makes keys for a new mount and for spaces built before keys.
- **Asked of the brain.** For a mount the agent does not administer, a colleague's workspace
  asks the brain's agent with a `key` registry note. The brain keeps the request in
  `_meta/state/private/key-requests/`, which is never staged, and refuses any request git
  tracks, since that one came in a pull request. Before attaching, it checks again that the
  person is registered and not archived, that the note came from their registered workspace,
  that the consumer is their `workspace_repo` (or in their own account when none is registered
  yet), that the mount is the brain or a project they are crew on, that their login reads it,
  and that the consumer's writers read it. What is only "not yet" (the workspace's own note in
  the same batch, a login waiting for the owner's click) waits; it is never dropped.
- **The record.** The brain records every outcome in `_meta/state/mount-keys-attached.yaml`
  (attached, refused or removed, with the key's fingerprint). The workspace reads it through its
  brain mount and trusts only an outcome for the fingerprint it sent; it asks again after a
  refusal or a week with no answer, and only then shows the token row. While the brain is unread
  only through a key marked pending, and before that key's date (14 days after the request), the
  mounts step warns and the cross-space checks are skipped; after that date it fails as before.
- **Removed when access goes.** The brain's daily run removes a recorded key whose person is
  archived, is off that crew, or no longer reads the mount. Offboarding removes the leaver's keys
  on every repository the record lists.
- **The token fallback.** When no key can read the brain, the checks read a fine-grained personal
  access token, `BRAIN_READ_TOKEN` (the checks also read `MOUNT_READ_TOKEN`, under the same
  rules): a repository secret of each repository, never an organisation secret; Contents:
  read-only on the brain repository alone, with an expiry; never a classic token. `onboard.py`
  flags an organisation secret of either name for removal. A token that reads more (the walled
  projects, other people's workspaces) hands all of it to every colleague, which `rules.md`
  section 9 forbids.
- **The checkout.** The address list is the default branch's `.gitmodules`, a protected path.
  Any plain `https://github.com/<owner>/<name>` address in it is read, pinned in that form.
  ssh is used only as `ssh://git@github.com/<owner>/<name>.git` built from that address, with
  GitHub's ed25519 host key pinned in the workflow (`StrictHostKeyChecking=yes`,
  `IdentitiesOnly`, no agent). Key files live in the runner's temporary folder for that step
  only. If GitHub rotates its ed25519 host key, update the pinned line in `checks.yml`; until
  then the token fallback reads the mounts.

What stays a risk: a deploy key does not expire. A writer added to a consuming repository after
its key was attached is not checked again until the consumer makes its keys again (weekly at
most while a mount is left out, otherwise at the next rebuild). The routine profile refuses a
routine's own edit of a key request, but a request written on the brain owner's computer some
other way would be believed as far as the checks above let it: it is still bound to the
person's own workspace repository and to mounts they read.

## The secrets scan and the sync

- **What the scan reads.** The sync scans every file before it is staged, and the checks scan
  every pull request with the same `scan.py`. A file of any size is read; past 25 MB it is read
  in overlapping 4 MB chunks, so a token across a chunk border is still found. A file is
  `unscanned`, and never lands, only when it cannot be read, is an archive that unpacks past
  max(25 MB, 50 times its size) or past 2 GB, nests more than 3 archives deep, or is not read
  within 10 minutes. A computer run scans for 10 minutes at most; a file it did not reach stays
  on the computer until a later run reads it.
- **Text among binary bytes.** Platform token shapes, JWTs and private keys are searched in
  every byte. The password, IBAN, labelled-key, URL-credential and Authorization checks run on
  text: on text files, on text members of archives, on PDF text, and in a mostly binary file (a
  tar, a SQLite file, a mail with an attachment) on the lines that read as text. ASCII85 PDF
  streams are decoded and inflated, then read with every check; an encoded stream body is never
  read as text, so its noise is no password.
- **Credential file names.** A `.key` file that is a zip with no PEM header in its first 4 KB (a
  Keynote deck) is not refused by its name; it is still scanned like any other file.
- **Files left local.** The sync records each file it kept back (path, why, since when) in
  `_meta/state/private/left-local.json`, which never leaves the computer; the run line and the
  health page only count them. After 7 days one Needs-you item asks the person whether an agent
  may help move the secret out or export the file again; the matched text is never recorded or
  shown.
- **Held changes ride their own branch.** The sync pushes every path the gate would hold, not
  only protected paths, on its own branch, `sync/<machine>-protected`, for the owner's click; a
  cloud run uses `cloud/<chain>-protected`. A security item on Needs you (a new one, or one whose
  text changed under the same id), a registry trust field or a changed `log.md` line waits alone,
  and the rest lands on green. Outside a personal space the verdict store lands only rows pushed
  from the login of the person each names (or the owner's), and the request ledgers only the
  owner's push; a subject's owner or crew signs only pages filed under that subject, never a
  decision (nor a `type: decision` page or one that `supersedes` another, wherever it is filed),
  and never a page moved in under an id another subject's page carries; a signed page it did not
  push keeps the keys readers act on (`gate.READ_KEYS`: `supersedes`, `type`, `subject` and the
  like, and who released it: `landed_from`, `released_from`, `released_by`) unless the owner's
  login pushes the change; a signed page is withdrawn (deleted, unsigned or retracted) only with
  the owner's click, or by the retraction of the space it was signed in. A cloud run, which pushes
  as its owner, is not bound by these (see what the routine profile blocks, above). In a
  project space a crew member's login is the one its space.yaml lists beside the audience (`logins`); a release into it lands from the login
  listed for the audience member its branch names, a release from it into the brain from the
  brain owner's login or one of its owner's and crew's. Nothing under a private zone the sync never
  stages (`sync.PRIVATE_ZONES`: a subject's brief, an app grant) lands from a pull request. The
  owner's login is the registry's, or the one onboard writes into CODEOWNERS. The gate in the checks
  still judges every pull request; this routing only decides which branch a change rides. A
  cloud run carries an earlier run's held change only from its own commit history, never from
  what is on that branch, so a notice someone else pushes there is replaced, not sent on.
- **The lock.** The sync refreshes its lock before each file is scanned and scans the index
  again right before the commit, so a file staged during a long scan cannot be committed
  unscanned.

## Trust between spaces

- **A workspace's repository.** The repository whose `log.md` vouches for a person's signatures
  (`workspace_repo`) is set once, by the brain's agent, from the workspace's own note, only
  after the host confirms three things: a login in the person's registered `match.github` has
  admin on it; it sits under one of those logins, or every account with push access is one of
  them; and its default-branch `space.yaml` names that workspace and that person. An existing
  value never changes this way. A retry reads the repository from the notes-log row of the
  applied note, never from the health file, which only shows the wait.
- **Who signs in a space.** A page's signers are the space's owner and audience, the person
  whose workspace it is, and, when the page's subject is homed there, the owners and crew of that
  subject, its umbrellas and its walled classification. Only this space's own registry and the
  brain's are read (`Registry.trusted_view`), never a channel's or a project mount's. Landing,
  release and the gate count the same set. Names alone prove nothing: the trust root is still the
  whole fingerprint and fields lines in the `log.md` of the repository this space pins.
- **Moves and replays.** A release signed before the page here never overwrites or moves it; it
  is dropped and logged. A move needs the same signer and signing space, a stable page carrying
  that id, no target field of its own, a free destination, a sender that is the signing space or
  the page's own source, and a signature that checks. Landing removes any `health` a release
  arrives with, and Needs you counts only landing's own two health texts, so a sender can
  neither hide a release from the person nor put its own words on the page. Holds that are the
  agent's age out after 14 scaled days, logged.
- **Release holds.** A held item's text never changes in place. A record the scan refused is
  exported again and routed again, so the new text is judged and gated as new, and the held copy
  is set aside, never leaving. `requeue` and every ship put back only the holds that fixed
  themselves (the scan is there, the registry names a home, no draft waits), unchanged, through
  every gate. A missing signature is never assumed: the item goes back to the person as an
  unsigned draft that keeps its destination.
- **Backlog releases.** A release without `signed_fields` or `signed_repo` is checked against the
  pinned repository's log and the signer's current committed page there, found through the
  release's page field or the signer's id index, both limited by `sign.check_target`. A mismatch
  or an unreadable page stays pending for a person; a workspace whose repository is not verified
  yet waits for the agent.
- **The corridor.** An asker's profile comes from this space's own registry and the brain's
  first (fields gated by the owner's click), then the asker's own `space.yaml`, then the
  strictest choice; a corridor's registry is never read for it. A re-addressed request is a new
  request with its own `asked` line, follows a subject at most twice, and only where the trusted
  registry names its home.
- **Conflict notes.** A note whose quoted records are not cleared for the other space carries no
  value, wording, record id, path or title, and gets a random id, because the full note's id
  digests the value. The question moves to one line of the holder's weekly batch.
- **Email replies.** A failed reply is never applied. The owner gets at most one "not applied"
  notice a day, to their own address, and only for mail whose From is exactly theirs, not
  marked spam, with answer lines; the notice quotes nothing from the mail.
- **Codebase links.** A stranger's repository is never linked on the host's word alone: push
  access and committer addresses are both chosen by the repository's owner, and an
  organisation's `is_verified` is not tied to its email or site. The link question shows what
  the host says; after one yes, that owner's later repositories link on their own. Only
  `match.github`, a trust field, makes an owner trusted; `repo_owner` attaches nothing and
  grants nothing.

What stays a risk: a colleague whose pull request to the brain can append a forged notes-log
row could name a repository for a person's retry, but only one the person alone controls passes
the host check.

## Settings a public deployment must set

The tools set what they can on the repositories they create: auto-merge, merge commits only, and
branch protection on `main` requiring the `checks` job, admins included. Some settings belong to
the host account, and only an administrator of that account can make them. Set each of these
before you rely on the template's promises:

- **Branch protection on `main`**, in every space and in the template:
  - require the `checks` status check, and do not allow bypassing it, administrators included;
  - forbid force pushes and deletion of `main`.
- **No code-owner review in a space.** A space requires status checks only. The gate decides
  which pull requests wait for the owner's click, and only those it lets through are armed to
  merge. Do not turn on "require review from code owners" there: an approved template release
  arrives as a pull request opened with the owner's own token, GitHub never lets the author
  approve their own pull request, and so every release would wait for an admin bypass in every
  space, which undoes "one yes, every space takes it". GitHub does not enforce the hold itself:
  a held pull request is simply never armed, and an agent holding the owner's token could still
  merge it. What stops that is the rule that agents never merge (`rules.md`), not a repository
  setting.
- **Code-owner review in the template is optional.** The template's pull requests land on
  green, its machinery included, so agents can build and fix it on their own. Turn on "require
  review from code owners" there only when a second maintainer reviews what the first one's
  agents write. The holds that remain in the template (a link, a case twin, a name another
  system reads as another path) are hygiene, not a boundary: a pull request that changes the
  gate lands on green too, so the release tag is the template's only security gate. Keep
  `.github/CODEOWNERS` in step with the gate's protected paths
  (`gate.MINIMUM_PROTECTED`, `_meta/state/invitations/` among them) either way: it says who
  decides.
- **Tag protection for `v*`**, a ruleset that lets only maintainers create or move release tags.
  This is the one human gate on the template: a maintainer reads what changed since the last
  release, then tags it. The release workflow publishes a `vX.Y.Z` or `vX.Y.Z-rcN` tag
  only when its commit is on the default branch. The gate checks that a space's files are a
  tagged release on main, byte for byte (`gate.release_match`); the owner's yes to that tag's
  full commit is checked by the template sync on the owner's computer. Without this ruleset anyone who can push can make a release, and
  so can a workflow that landed on green with a write token. The release workflow checks the
  ruleset before it publishes (`template_archive.py ruleset`): no active tag ruleset on
  `refs/tags/v*` that restricts creations and updates, or one the workflow's own token can
  bypass, fails the job and nothing is published. Give apps and GitHub Actions no bypass.
  A space still reads the tag itself through git, so the check makes a missing ruleset loud at
  the next release; it does not stop a tag pushed while the ruleset is off.
- **A branch ruleset for `cloud/*` and `sync/*`** in each space, that lets only the owner's
  identity (the one the cloud runs and the computer's sync push as) create, update or force-push
  them. Their pull requests are opened under the owner's name, and the `-protected` ones are
  the ones the owner clicks. Without it the sync still notices a push that is not its own and
  replaces it with a security item (`split_cloud`, `foreign_protected`), but only on its next
  run. Branch rulesets are not available for private repositories on GitHub Free.
- **No write access for outside collaborators.** Give the brain and project repositories
  write access only to members of the organisation's teams, and review the outside collaborators
  list (`gh api repos/<owner>/<repo>/collaborators?affiliation=outside`). Anyone with write
  access can run a workflow with the repository's secrets and push branches the sync merges.
- **Organisation base permission "No permission"**, so members read only the repositories their
  teams grant. A workspace in the organisation holds its owner's raw mail and chat.
- **Private vulnerability reporting**, turned on in the repository's security settings, so the
  reporting route at the top of this page works.
- **Actions**: allow only actions from GitHub and verified creators, and keep "Allow GitHub
  Actions to create and approve pull requests" off unless the checks workflow needs it.

When someone leaves, the brain owner says so once, in a session. Then
`offboard.py <brain> --person <slug> --online --remove` removes the leaver's access through
GitHub's API (collaborator access, team and organisation memberships, outside-collaborator
access, organisation-approved tokens where the API allows, and the leaver's deploy keys, those
the key record lists included) and reads each back. It refuses to run inside a routine
(`routine.in_routine`), since removing an organisation membership deletes private forks; it
never removes the account it runs as and never grants. The leaver's own workspace deletes its
mount secrets before it is archived. Only the leaver's claude.ai cloud task and a token they
made by hand stay rows for a person. See `docs/data-protection.md`, "Removing a person".

## Supported versions

The latest release and `main`. Spaces receive fixes through the weekly template sync: each
release asks each space's owner once, and on that yes it lands in every space they own with no
further click.

A space with `template_updates: main` follows the template's `main` instead of its releases.
That is a dev-only mode, for a space that tests the template. The template's `main` is agents'
work that no person reviewed (only the release tag is reviewed), and the sync copies it with no
yes and runs it in the next routine. Never set it in a space that holds real records.

## If a secret reached your own space

Revoke or rotate it first: once it was pushed, treat it as seen. Then remove it from the
repository's history; GitHub's guide to removing sensitive data from a repository covers the
steps. `docs/data-protection.md` explains why deleting the file is not enough.
