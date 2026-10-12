# space-template

A **space** is a git repository that holds knowledge any agent can read and write under
one set of rules: a settings file (`space.yaml`), pages with permanent ids, a registry of
subjects, and queues for what needs a person. A company is a tree of spaces: one brain
everyone reads, one workspace per person, one space per project with its crew as audience.
This template is every profile of that tree in one repository; `onboard` turns a copy of it
into the profile it needs, and the shape is written down in `docs/shape-1.md`.

## Start a company

1. Create a private repository from this template ("Use this template" on the host, or
   `gh repo create <org>/<name> --template <owner>/space-template --private --clone`).
2. In the clone: `pip install pyyaml`, then `python _meta/tools/onboard.py`.
3. Answer the questions. Every one has a default; press Enter to take it, type `skip` to
   decide later (the question returns on your Needs-you list). The company name, the top
   owner, the domains and their owners, what must never leave, which feeds to connect
   (none is fine), how often to sync.

What the agent does from there: fills `space.yaml` as the brain, drafts the charter and
the spine as pages for you to sign, registers the domains as subjects with their owners,
creates your personal workspace from the same template with the brain mounted, installs
one instruction at user level so every session journals in your workspace, arms auto-merge
and the required check on the host, registers the sync task with the OS scheduler, and runs
the validator and the first status pass. Every step is recorded in
`_meta/state/onboard.yaml` with the command that proves it took.

What only you can do: sign in to your own mail, calendar and chat when you connect a feed,
and give the organization-level grants on the host. The tool lists these with their read-back
checks; run `python _meta/tools/onboard.py --check` afterwards and it tells you what is
still missing without changing anything.

The morning email comes from the daily routine (`morning-view`): it renders
`live/needs-you.md` and a short briefing addressed to `owner_email` in `space.yaml`, counts
only, sent from your own mail account. Nothing is signed by an agent; you sign with `sign`.

Nothing has to be connected to start. A files-only brain and workspace work on day one;
each connector is one adapter, switched on later from the Needs-you list.

## Join as a colleague

Create your own repository from the template and point it at the company's brain:

    python _meta/tools/onboard.py --brain https://github.com/<org>/<brain>

Your repository becomes a personal workspace with the brain mounted read-only; kinds, rules
and identity come from there. About fifteen minutes, mostly the sign-ins.

## Start a project from a desk seed

When something on your desk gains a second reader, mint it:

    python _meta/tools/mint.py . --seed work/projects/<subject>/<effort> --name <project> --crew "Ada Okafor" "Ben Liu"

The seed's pages become the project's first drafts, the subject's home moves to the new
space (its id does not), and the platform team is proposed on your Needs-you list.

## Try it first on the fixture company

    python _meta/tools/onboard.py --fixture <some-empty-dir>

Builds Lantern Works offline (a brain, three workspaces, two projects) from
`_meta/tests/fixture/`, runs a week of sweeps on a fast clock, applies the owners'
verdicts, then runs the answer and leak tests and prints one table. No account, no network.

## What runs

- **Routines** (each a tool under `_meta/tools/` and a skill under `.claude/skills/`):
  `sweep` → `distill` → `check` three times a day; `status` → `release` → `morning-view`
  once a day. They write files and queue items; they never sign, send, post or spend.
- **The sync task**: `_meta/tools/sync.py` every 15 minutes through the OS scheduler
  (`schtasks` on Windows, cron elsewhere). It commits what sessions left, opens the pull
  request, and the host merges it when `checks` is green. Nothing else runs git.
- **Needs you**: `python _meta/tools/needs_you.py .` shows what waits for a person.

## Rules

`rules.md` is the constitution: the hard rules, the high-stakes list, the badge floors.
`docs/shape-1.md` states what any agent may assume about a space and must never do.
`docs/decisions.md` records why things are the way they are.

## License

MIT, see `LICENSE`.
