# Tests as data

Three kinds of test live here. None of them needs a model; all of them run in the
template's checks and, where it says so, in every space's daily chain.

| Kind | File | Runner | Asks |
|---|---|---|---|
| Answers | `answers.yaml` | `python _meta/tools/tests_run.py . answers` | Can this space answer its real questions? |
| Leaks | `leaks.yaml` | `python _meta/tools/tests_run.py . leaks` | Can an audience read what it must not? |
| Behaviour | `behaviour/<skill>.yaml` | `python _meta/tools/behaviour.py . --all` | Does a tool do only what it may? |

Every run appends one line to `_meta/state/tests.jsonl`:
`{"at", "kind": answers|leaks|behaviour, "passed", "failed", "skipped", "details": [...]}`.
The runners exit 1 on any failure.

## Answers

`answers.yaml` holds `questions:`, each with `question`, `subject`, `expect` (substrings the
answer must contain, matched without regard to case), `asker` and `via`.

- `walk` reads what a person walking the index would reach: the subject's folder under its
  kind (`knowledge/<kind>/<subject>/`, the kind looked up in the registry, ancestors
  included), its status page `live/subjects/<subject>.md`, and the pages the subject's hub
  links by id (`[text](id:p-...)`, resolved through `_meta/state/ids.yaml` here and in the
  mounts).
- `search` greps every `.md` under `knowledge/`, `live/` and `work/`, plus the mounts'
  `knowledge/` and `live/` when they are present on disk.
- `both` passes when either does.

A question runs in the asker's space. It runs here when the asker is this space's name, its
owner, its directory name, or one of `owner`, `anyone`. When the runner is given roots
(`--root ada=../ada`), a question whose asker names one runs against that root. Any other
asker is skipped with a note, so one file can serve several spaces. The template ships two
examples with `asker: example`, a name no space has: they show the format and run nowhere
until you put your own name on them.

To add a question: write it the way a colleague would ask it, name the subject, list the
two or three facts a correct answer must contain, and say who asks. About thirty per space
is the target; they double as the cold start's "done when".

## Leaks

`leaks.yaml` holds `leaks:`, each with `audience_root`, `must_not_find` and `reason`. The
runner resolves the audience root (a name from `--root name=path`, a mount name, or a path
relative to this space), then greps every tracked text file under it that is content: it
skips `.git/`, the machinery (`_meta/tools/`, `_meta/tests/`, `_meta/templates/`,
`.claude/`, `.github/`, `docs/`), the files that name the terms themselves (`space.yaml`
with its left-out and never-leaves lists, `rules.md`, `AGENTS.md`), the two logs
`_meta/state/tests.jsonl` and `_meta/state/runs.jsonl`, and the private, gitignored zones
(`personal/`, `academic/`, `_scratch/`, `_meta/state/feed/`). Everything else counts,
including queue items, candidates and the morning view's mail payload under
`_meta/state/`. Any substring found fails the entry and names the file. An audience root
that resolves nowhere is skipped with a note, never passed.

Write one entry per audience that must not see something: a project space and the
left-out terms, the brain and what stays in a person's space, a colleague and another
person's messages.

## Behaviour

A scenario runs one tool in a copy of the space and holds it to invariants. The file:

```yaml
name: sweep writes witness pages only
module: _meta/tools/sweep.py             # optional; else the first .py in the command
command: [python, _meta/tools/sweep.py, "{root}"]
setup: {files: {relpath: content}}       # written into the copy first; a mapping is dumped as YAML
env: {SPACE_NOW: "2026-10-05T09:00:00Z"} # the virtual clock makes the run deterministic
expect:
  exit: 0
  writes_only_under: [knowledge/sources/, _outbound/]      # _meta/state/ and log.md always may
  never_contains: {"knowledge/sources/**": ["status: stable"]}
  must_exist: ["knowledge/sources/**/*.md"]
  must_not_exist: ["_scratch/dest/**/*.md"]
```

`python` means this interpreter; `{root}` is the copy's path (`{root_posix}` inside code).
The copy has no `.git`, so no scenario can land anything. Every created, changed or deleted
file must sit under an allowed prefix; `never_contains` is how a scenario asserts that
`status: stable` never appears outside `knowledge/` (and never in `live/` at all), and that
nothing reaches a destination it must not. `behaviour.py --all` skips a scenario whose tool
is not present yet and says so.

The shipped scenarios, one per routine: `sweep`, `distill`, `check`, `status`, `release`,
`sign`, `morning-view`, `sync`. The prompt half of a skill (what the agent does around the
tool) is checked by the agent that changes the skill, against the same scenario file.

## The fixture company

`fixture/` is Lantern Works: three people, two projects, a handful of records, all invented.
`company.yaml` describes it; `feed/<person>.jsonl` holds each person's records in the feed
shape; `answers.yaml`, `leaks.yaml` and `verdicts.yaml` are the tests and the signatures of
a seven-day run on a fast clock. `onboard --fixture <dir>` builds it, runs the chains and
checks it end to end, so the flow cannot rot and a newcomer can watch the system work
before connecting anything real. `python _meta/tools/chain.py virtual --days 7 --roots ...`
drives the clock by hand.

## Forbidden names

`forbidden-names.sha256` lists the sha256 of lowercased tokens that must never appear in
the template: the names of a real company, its people, clients and projects. Neither the
names nor the list are in this repository: the `checks` workflow writes the list here from
the `FORBIDDEN_NAMES_SHA256` secret for the run, and `.gitignore` keeps it out of commits.
`validate.py . --template` checks every token against it; without the file there is nothing
to compare.
