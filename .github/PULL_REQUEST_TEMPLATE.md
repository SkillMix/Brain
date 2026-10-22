**What** this changes, in one or two sentences:

**Why**:

**Tests run** (see CONTRIBUTING.md):

- [ ] `python -m pytest _meta/tools/tests -q`
- [ ] `python _meta/tools/validate.py . --template`
- [ ] `python _meta/tools/behaviour.py . --all` (a tool or skill changed)
- [ ] `python _meta/tools/onboard.py --fixture <empty dir>` (setup, routing, release or signing changed)

**Touches protected paths?** (`.github/`, `.claude/`, `_meta/tools/`, `rules.md`, `identity/`, `space.yaml`): no / yes, which and why

- [ ] A line in `docs/decisions.md` for any choice the docs did not already make
- [ ] No real company, client, product or person names; no secrets
