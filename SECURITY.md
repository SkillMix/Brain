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
  `~/.spaces/`. It refuses every other tool script by name, `python -m` and git's `--output`.
  Deny rules win over any allow rule in any settings file, so a space's own profile cannot
  widen them.
- **What the space's profile blocks.** `.claude/settings.json` allows each tool the skills name,
  one rule per script (`python` and `python3`), never the whole folder, so a script written into
  `_meta/tools/` asks before it runs; `template_sync.py` asks too, as it copies tools from a path.
  Attended sessions may still edit any file without a prompt: the person is there, and the
  machinery reaches the remote only through a protected pull request.
- **What stays the person's.** Review every pull request that touches a protected path before
  you merge it: a tool, a workflow or the profile changed there runs on every machine that
  syncs. Give each connector the narrowest scope it needs, and no send or delete rights a
  routine does not use. In an attended session that reads swept records, read what the agent
  is about to edit and run; to be asked before any edit to the machinery, add `ask` rules such
  as `Edit(/_meta/tools/**)` to your own `.claude/settings.local.json`.

## Supported versions

The latest release and `main`. Spaces receive fixes through the weekly template sync; a fix
to a protected path waits for each space owner's click.

## If a secret reached your own space

Revoke or rotate it first: once it was pushed, treat it as seen. Then remove it from the
repository's history; GitHub's guide to removing sensitive data from a repository covers the
steps. `docs/data-protection.md` explains why deleting the file is not enough.
