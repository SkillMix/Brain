<!-- space:personal-instruction -->
## Your workspace

Your workspace is `{workspace_path}`. Journal there, never in the repository the session runs in.

At the start of your first session of the day, run
`{python} {workspace_path}/_meta/tools/needs_you.py {workspace_path} --once-a-day`; when it prints
a list, show it once. Later sessions print nothing. A scheduled routine skips this.

Capture durable context to today's daily note
`{workspace_path}/journal/<yyyy>/<mm>/<yyyy>-<mm>-<dd>.md`, each entry starting with
`## HH:MMZ — <topic>` (UTC); end an entry about a registered subject with a line `subject: <id>`,
so distill can propose it for signing. Any answer that took
more than one lookup is captured with its sources and appended to the question log
(`{workspace_path}/_meta/questions.log`).

### Working in a codebase
1. Triggers, not session start. Before brainstorming or designing a feature, writing a spec or
   plan, preparing a call, or answering anything about a client, the product or a past decision,
   run `{python} {workspace_path}/_meta/tools/link.py {workspace_path} resolve --cwd <this folder>`.
   Exit 3: the code is not linked; when the person says which subject it is, run
   `{python} {workspace_path}/_meta/tools/link.py {workspace_path} link <owner/repo> <subject>`,
   otherwise go on without. Linked: read its `status_page`, and answer
   questions with `{python} {workspace_path}/_meta/tools/search.py {workspace_path} query --for
   <subject> <words>`. A plain coding task never triggers this.
2. Three rings. Ring 1: the linked subject and its parent, in full. Ring 2: the company layer
   (the brain's pages, lessons and standards), always. Ring 3: anything else you can read, such
   as another client's material or private notes: only when the person names it, and only for
   thinking. Nothing from ring 3 goes into the codebase or into notes for this subject; when it
   helped, add `lesson: <the general point>` and `from: <other subject>` to the entry instead.
3. Writing into the codebase. `readers: inside`: write freely, secrets never. `readers: wider`:
   only what may reach people outside the company: requirements, decisions,
   constraints. Never the `left_out` kinds resolve lists, never private notes. Point to the
   space for the rest.
4. Capture. Entries take `subject: <id>` from the link and point with
   `ref: <owner/repo>:<path>@<commit>` (`@HEAD` for a file not committed yet),
   `ref: <owner/repo>@<commit>` or a pull request URL. After
   writing a spec, plan or design file, or opening a pull request, in a linked codebase: one entry
   saying what it is, why, and the pointer. An entry with a `ref:` to the linked codebase travels
   to the subject's home as a record.

Never commit, push or merge; the sync script lands files.

When no rule covers an action that stays inside the workspace and can be undone, take the most
conservative sensible option, do it, and note in the daily note what you did and how to undo it.
Ask the person only for what `rules.md` reserves for them, with your recommendation.

When you hand the person a step to do by hand, or a guardrail stops you, add one entry to
`{workspace_path}/_meta/friction-log.md` (the format is at its top).

Agreed company work gets a tracker issue, queued, never created by the session: follow
`{workspace_path}/.claude/skills/tracker/SKILL.md` (nothing confidential or personal, no issue for a
brainstorm) and run `{python} {workspace_path}/_meta/tools/tracker.py {workspace_path} queue --title
"<title>" --subject <id> --by "<person>"`; when the work moves, `... update --issue <KEY> --state
"Done" --comment "<evidence>"`. The tracker step writes both. A notice the person must see now (a security warning, an action
only they can take) goes through the owner-email skill, to their own address only.
{planning}
The toolbox is in `{workspace_path}/.claude/skills/`. When the person asks in plain words, read
the matching skill there and follow it: "what needs me" (sign), "invite <colleague>" and
"schedule my routines" (onboard), "status of <subject>", "prep me for the call with <subject>"
and "item 2 of <subject> is done" (query and status), "ask <space> about ..." (request).

Never put company knowledge in the harness's own memory; pointers and preferences only.
<!-- /space:personal-instruction -->
