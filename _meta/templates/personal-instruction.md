<!-- space:personal-instruction -->
## Your workspace

Your workspace is `{workspace_path}`. Journal there, never in the repository the session runs in.

At the start of a session show what needs the person: run
`{python} {workspace_path}/_meta/tools/needs_you.py {workspace_path}` and show the result once.

Capture durable context to today's daily note
`{workspace_path}/journal/<yyyy>/<mm>/<yyyy>-<mm>-<dd>.md`. Any answer that took more than
one lookup is captured with its sources and appended to the question log
(`{workspace_path}/_meta/questions.log`).

Never commit, push or merge; the sync script lands files.

The toolbox is in `{workspace_path}/.claude/skills/`. When the person asks in plain words, read
the matching skill there and follow it: "what needs me" (sign), "invite <colleague>" and
"schedule my routines" (onboard), "status of <subject>" (query), "ask <space> about ..." (request).

Never put company knowledge in the harness's own memory; pointers and preferences only.
<!-- /space:personal-instruction -->
