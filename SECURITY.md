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

## Supported versions

The latest release and `main`. Spaces receive fixes through the weekly template sync; a fix
to a protected path waits for each space owner's click.

## If a secret reached your own space

Revoke or rotate it first: once it was pushed, treat it as seen. Then remove it from the
repository's history; GitHub's guide to removing sensitive data from a repository covers the
steps. `docs/data-protection.md` explains why deleting the file is not enough.
