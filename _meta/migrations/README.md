# Shape migrations

A new shape version ships with the migration that takes a space from the previous one:
`shape_<n>.py` here defines `migrate(root)` taking a space from shape n-1 to n. The
librarian (`status.py`, through `librarian.py`) runs every pending migration in order,
verifies it with the answer and leak tests (a lower score restores `space.yaml` and asks a
person), and records it so the sync script lands it as one commit. Template sync brings
new migrations into every space. Shape 1 has none.
