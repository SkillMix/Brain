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

When `~/.spaces/setup.yaml` lists a setup phase that is not `done` (a `history` marked
`in_background: true` counts as done: the routines read it from there), run
`{python} {workspace_path}/_meta/tools/start.py resume --json` at the start of a session; when its
`next` names a phase, offer to carry on the setup, and on yes follow
`{workspace_path}/.claude/skills/onboard/SKILL.md` where it says a setup is partway (the
permission mode is asked again, through the app's card).

In the Claude desktop app, while the person's history is being read
(`{workspace_path}/_meta/state/backfill.yaml` lists a part not `done`) and `~/.spaces/setup.yaml`
says `usage_account: same`, read the app's usage tool at the start of a session and record it,
saying nothing in chat, only on the computer that runs the routines
(`{workspace_path}/_meta/state/routine-host.yaml` names this computer, or there is no such file):
`{python} {workspace_path}/_meta/tools/backfill.py {workspace_path} reading five_hour=<pct>,week=<pct>,five_hour_resets=<iso>,week_resets=<iso>,extra=<0|1>`
Skip it outside the desktop app, in a scheduled routine, when the tool's `plan.status` is
`not_applicable` or `unavailable`, and when `usage_account` is not `same`. When the command refuses,
say nothing either.

Agreed company work gets a tracker issue, queued, never created by the session: follow
`{workspace_path}/.claude/skills/tracker/SKILL.md` (nothing confidential or personal, no issue for a
brainstorm) and run `{python} {workspace_path}/_meta/tools/tracker.py {workspace_path} queue --title
"<title>" --subject <id> --by "<person>"`; when the work moves, `... update --issue <KEY> --state
"Done" --comment "<evidence>"`. The tracker step writes both. A notice the person must see now (a security warning, an action
only they can take) goes through the owner-email skill, to their own address only.
{planning}
The toolbox is in `{workspace_path}/.claude/skills/`. When the person asks in plain words, read
the matching skill there and follow it: "what needs me" (sign), "invite <colleague>",
"schedule my routines", "pause my routines", "resume my routines" and "run my routines here"
(onboard), "status of <subject>", "prep me for the call with <subject>"
and "item 2 of <subject> is done" (query and status), "ask <space> about ..." (request).

Never put company knowledge in the harness's own memory; pointers and preferences only.
<!-- /space:personal-instruction -->
