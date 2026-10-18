# Contributing

Thanks for helping. Small, tested pull requests land fastest.

## Set up

Python 3.10 or newer, then `pip install pyyaml pytest`. No model, account or network is
needed to run anything below.

## Before you open a pull request

    python -m pytest _meta/tools/tests -q
    python _meta/tools/validate.py . --template
    python _meta/tools/behaviour.py . --all
    python _meta/tools/onboard.py --fixture <empty dir>

The unit tests cover the tools; `validate.py --template` checks the shape, the links and the
name gate; the behaviour scenarios check that each skill's tool writes only what it may; the
fixture builds Lantern Works end to end and runs its answer and leak tests. The `checks`
workflow runs the same on every pull request, and `platforms` repeats the setup tests on
Linux, macOS and Windows when the setup tools change.

## What a good pull request looks like

- One change per pull request: a fix or a feature with its test.
- For a bug, a test that fails before the fix and passes after it.
- A line in `docs/decisions.md` when you made a choice the docs do not already make: the
  date, the decision, the choice and one line of reason.
- A new skill comes with three behaviour scenarios (`_meta/tests/behaviour/<skill>-<n>.yaml`).
- The tools use the standard library plus PyYAML, nothing else.
- Docs and skills are plain prose: short sentences, concrete words.
- The fixture copies root files such as `README.md` into every space it builds, and the leak
  tests read them. An example in the docs must not quote a fixture fact that some space may
  not see (see `_meta/tests/fixture/leaks.yaml`).

## Cutting a release

Spaces follow the template's releases, not its `main`. A maintainer (a person, not an
agent) cuts one:

1. Add a `## [X.Y.Z] - <date>` section at the top of `CHANGELOG.md`. Its first paragraph is
   the summary each space's owner reads in the question "Update to vX.Y.Z?", so make it one or
   two plain sentences.
2. Once that is merged, tag the merge commit on `main`: `git tag -a vX.Y.Z -m vX.Y.Z` and
   `git push origin vX.Y.Z`. A GitHub release with the same notes is optional.

Never move a published tag. A space's approval names the tag's commit, so a moved tag stops
every update that was approved for it until its owner answers again.

## Names and secrets

- No real company, client, product or person names in any template file, commit message or
  test. Use the fixture's fictional people and companies (Lantern Works, Ada Okafor, Ben
  Liu, Harbor, Beacon) or plain placeholders. `validate.py --template` fails on names from a
  hashed list kept outside the repository; passing it is not proof, so read your diff too.
- No secrets: no keys, tokens, passwords or access links, not even expired ones. The
  secrets scan runs on every pull request.

## License

By contributing you agree that your work is released under the MIT license in `LICENSE`.
