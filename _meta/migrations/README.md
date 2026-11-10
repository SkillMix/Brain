# Shape migrations

A new shape version ships with the migration that takes a space from the previous one:
`shape_<n>.py` here defines `migrate(root)` taking a space from shape n-1 to n. The
librarian (`status.py`, through `librarian.py`) runs every pending migration in order,
verifies it with the answer and leak tests (a lower score restores `space.yaml` and asks a
person), and records it so the sync script lands it as one commit. Template sync brings
new migrations into every space. Shape 1 has none.

## Other migrations

- `verdicts_cutover.py` begins the person's verdict store (`_meta/state/verdicts.jsonl`) in a
  space that answered before it, pinning the verdict and note lines log.md held then, so they
  keep counting and no line written later does. The librarian's run, needs_you's list and the
  person's first answer through sign.py pin it on their own; a person runs this to pin it at
  once after the upgrade. It is no shape migration, and the librarian never runs it as one.
