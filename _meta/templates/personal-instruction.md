<!-- space:personal-instruction -->
## Your workspace

Your workspace is `{workspace_path}`. Journal there, never in the repository the session runs in.

At the start of a session show what needs the person: run
`{python} {workspace_path}/_meta/tools/needs_you.py {workspace_path}` and show the result once.

Capture durable context to today's daily note
`{workspace_path}/journal/<yyyy>/<mm>/<yyyy>-<mm>-<dd>.md`; end an entry about a registered
subject with a line `subject: <id>`, so distill can propose it for signing. Any answer that took
more than one lookup is captured with its sources and appended to the question log
(`{workspace_path}/_meta/questions.log`).

Never commit, push or merge; the sync script lands files.

When no rule covers an action that stays inside the workspace and can be undone, take the most
conservative sensible option, do it, and note in the daily note what you did and how to undo it.
Ask the person only for what `rules.md` reserves for them, with your recommendation.

When you hand the person a step to do by hand, or a guardrail stops you, add one entry to
`{workspace_path}/_meta/friction-log.md` (the format is at its top).

A tracker issue the person asks for is queued, never created by the session:
`{python} {workspace_path}/_meta/tools/tracker.py {workspace_path} queue --title "<title>" --subject <id>`;
the tracker step creates it. A notice the person must see now (a security warning, an action
only they can take) goes through the owner-email skill, to their own address only.
{planning}
The toolbox is in `{workspace_path}/.claude/skills/`. When the person asks in plain words, read
the matching skill there and follow it: "what needs me" (sign), "invite <colleague>" and
"schedule my routines" (onboard), "status of <subject>", "prep me for the call with <subject>"
and "item 2 of <subject> is done" (query and status), "ask <space> about ..." (request).

Never put company knowledge in the harness's own memory; pointers and preferences only.
<!-- /space:personal-instruction -->
