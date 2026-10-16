# Classification gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A client, project or deal sends nothing out of the space its material came in until its owner's agent has classified it (company, walled or owner), with the classification and obligation facts on its entry in the brain registry.

**Architecture:** One new tool, `_meta/tools/classify.py`, holds the gate, the decision verbs and the registry notes. Notes are `kind: registry` signals addressed to the space whose registry holds the subject (the brain), shipped at once on the existing signal lane and applied there by `classify.land_notes`, called from `release.ship` (every sync) and `landing.land` (every status pass). Every exit (route, sweep, release, distill, subject backfill) asks `classify.gate`.

**Tech Stack:** Python 3.10+, PyYAML, pytest (`_meta/tools/tests`), behaviour scenarios (`_meta/tests/behaviour`).

**Spec:** `docs/superpowers/specs/2026-10-16-classification-gate-design.md`

## Global Constraints

- No company, client or person names in any file, test or commit; fixture names are the invented Lantern Works ones. `no_company_names.py` stays clean.
- Owner-facing prose plain and short, no em dashes.
- Obligation entries carry `kind` (one of `confidentiality`, `named_people`, `data_processor`, `other`), `date` (YYYY-MM-DD, optional), `seen_in`, `at`, `evidence` (record ids). No free-text field anywhere on the obligation path; the registry check refuses other keys.
- Gated kinds: `clients`, `projects`, `deals`, and any subject `part_of` one of them.
- Classes widest to narrowest: `company`, `walled`, `owner`. States: `asking`, `settled`, `open`. Only `settled` opens the gate.
- `classification_default` in the brain's `space.yaml`: `open` (default when absent) or `closed`.
- The ask deadline and the first-release wait are one window (`space.window()`).
- Tools never run git, never write `status: stable`, never send; landing goes through `sync.py --only`.
- Do not touch `sync.py` or `.github/` (another session owns them).

## Review Focus

1. A subject's client is `open` (re-opened) while the project itself is `settled`: the project is held too. Test in Task 2.
2. A note arrives at the brain for a subject the brain does not hold, or from a space whose person is not the decider: refused and logged, the registry unchanged. Test in Task 3.
3. An item already in the queue before the template sync (no gate when it was queued) for an unclassified subject: the ship backstop holds it and the next ship after classification puts it back. Test in Task 5.
4. A person answers yes to a held-in-doubt line while unclassified items are in `held/`: those items are not offered and not released. Test in Task 6.
5. An obligation note with extra wording (`fact`, `terms`) or a date that is not a date: refused by the tool and by the registry check. Test in Task 4.

---

### Task 1: Registry fields and their check

**Files:**
- Modify: `_meta/tools/registry.py` (constants after `INFERRED_KEYS`; `Registry.check`)
- Test: `_meta/tools/tests/test_classify.py` (new)

**Interfaces:**
- Produces: `registry.GATED_KINDS = ("clients", "projects", "deals")`, `registry.CLASSES = ("company", "walled", "owner")`, `registry.CLASS_STATES = ("asking", "settled", "open")`, `registry.OBLIGATION_KINDS = {"confidentiality": "confidentiality agreement", "named_people": "named people only", "data_processor": "data processing agreement", "other": "other obligation"}`, `registry.OBLIGATION_KEYS = ("kind", "date", "seen_in", "at", "evidence")`. The fields live in `Subject.extra` (`classification`, `obligations`; people: `workspace`).

- [ ] **Step 1: Write the failing tests** `test_registry_checks_a_classification`, `test_registry_refuses_obligation_wording`, `test_registry_checks_a_workspace_slug`: a subject with `classification: {class: shared}` -> an error naming `class`; `state: done` -> error; a settled `walled` without `home` or `crew` -> error; an obligation `{kind: confidentiality, date: "2026-04-14", fact: "x"}` -> an error naming `fact`; `date: "April"` -> error; `kind: nda` -> error; a people entry `workspace: "Not A Slug"` -> error; the valid shapes -> no error.
- [ ] **Step 2: Run** `python -m pytest _meta/tools/tests/test_classify.py -q -p no:cacheprovider`; expect the three to fail.
- [ ] **Step 3: Implement** the constants and the checks in `Registry.check` (own subjects only; messages `registry: \`<id>\` classification ...`, `registry: \`<id>\` obligation ...`).
- [ ] **Step 4: Run** the tests; expect pass. Run `test_registry.py`; expect pass.

### Task 2: The gate

**Files:**
- Create: `_meta/tools/classify.py`
- Test: `_meta/tools/tests/test_classify.py`

**Interfaces:**
- Consumes: Task 1 constants.
- Produces:
  - `gated(reg, sid) -> bool`
  - `subject_owner(reg, sid) -> str | None`: the subject's own `owner`, else the nearest gated ancestor's; never a domain's, never the space owner.
  - `effective(reg, sid) -> dict | None`: the strictest `settled` classification on the chain (subject, then gated ancestors; ties: the nearest), with `subject` (whose it is) added; None when none, or when any classification on the chain is `asking` or `open`.
  - `brain_space(space) -> Space | None` (the space itself for `profile: brain`, else the `brain` mount); `company_owner(space) -> str`; `default(space) -> "open" | "closed"`.
  - `person_id(reg, name) -> str` (a people subject whose title matches, else the slug of the name); `workspaces(reg) -> dict[str, str]` (people id -> workspace, not archived).
  - `decider(space, reg, sid) -> str`: `"self"` (this space's owner is the subject owner), `"stand_in"` (owner known, has no workspace, this space's owner is the company owner), `"other"`, `"no_owner"`.
  - `gate(space, reg, sid, destination) -> tuple[str, str] | None`: None when `destination` is this space, the subject is not gated, or the effective class allows `destination`; `("kept", why)` for class `owner`; `("unclassified", why)` otherwise (no settled classification, or `destination` is not the classification's `home`).
  - `first_release(root, space, reg, sid) -> bool`: effective class `company` and no line in `_meta/state/released.jsonl` with this `subject` at or after the classification's `at`.

- [ ] **Step 1: Write the failing tests**: `test_gate_holds_an_unclassified_project`, `test_gate_frees_a_company_subject_to_its_home_only`, `test_gate_keeps_an_owner_subject_home`, `test_a_project_follows_its_client_and_the_strictest_wins` (client walled + project company -> walled, home the client's), `test_an_open_client_closes_its_settled_project` (Review Focus 1), `test_subject_owner_ignores_domain_and_space_owner`, `test_decider_self_stand_in_other_and_no_owner`, `test_first_release_until_one_release_since_classification`. Registries are built with `conftest.make_space(..., subjects=[...])`; a brain and a workspace mounting it as `brain` (path relative).
- [ ] **Step 2: Run**; expect fail (module missing).
- [ ] **Step 3: Implement** the functions above in `classify.py` with a module docstring that lists the CLI of Task 4.
- [ ] **Step 4: Run**; expect pass.

### Task 3: Registry notes and the brain applying them

**Files:**
- Modify: `_meta/tools/classify.py`, `_meta/tools/release.py` (`ship`: call `classify.land_notes` first; `kind: registry` signals never re-addressed), `_meta/tools/landing.py` (`land`: call `classify.land_notes` first), `_meta/tools/status.py` (`moved_signals`: skip `kind: registry`)
- Test: `_meta/tools/tests/test_classify.py`

**Interfaces:**
- Consumes: Task 2.
- Produces:
  - `NOTE_KINDS = ("owner", "workspace", "ask", "classification", "obligation", "checked", "crew")`
  - `note(root, sid, kind, data, now=None) -> Path | None`: when this space's own registry holds `sid`, applies at once through `apply_note` and returns None; else writes `_outbound/signals/<id>.yaml` `{id, kind: registry, note, subject, destination: <holder space name>, at, by: <space owner>, from: <space name>, data}`.
  - `apply_note(root, space, reg, signal: dict, now) -> tuple[str, str]`: `("applied", "")` or `("refused", why)`; saves the registry when applied. Rules: `workspace` sets `workspace: <from>` on the people entry whose title is `by`; `owner` only when the subject has no own `owner`; `ask` and `classification` only from `by` == `subject_owner`, or the company stand-in (`subject_owner` has no `workspace` and `by` is this registry's space owner), or any `by` carrying `data.approved_by` for a subject without an owner (the backlog, which also sets `owner`); a `classification` looser than the previous settled class, `company` after `open`, or `company` under `closed` needs `data.approved_by`; `classification` sets `home` (company: this space's name; walled: `data.home`; owner: removed); `obligation` appends, and a settled `company` becomes `open` (`reopened_at`); `checked` and `obligation` move the sender's person from `not_yet_checked` (and from `asked` to `answered` while asking) and add `from` to `checked_with`; `crew` appends the person to `crew_proposed` on a walled classification.
  - `land_notes(root, space=None, now=None) -> dict`: applies every `_inbound/signals/*.yaml` with `kind: registry`, appends one line per note to `_meta/state/registry-notes.jsonl` and `log.md`, removes the signal file; returns `{"applied": n, "refused": n}`.

- [ ] **Step 1: Write the failing tests**: `test_a_note_ships_to_the_brain_and_lands_in_its_registry` (workspace writes an `owner` note, `release.ship(ws, offline_roots={brain: path})`, then `landing.land(brain)`: the brain entry has the owner, the signal file is gone, one notes-log line); `test_a_registry_signal_is_never_readdressed` (the subject's home is a project space: the note still goes to the brain); `test_the_brain_refuses_a_classification_from_a_non_decider` (Review Focus 2: registry unchanged, outcome `refused`); `test_a_note_for_a_subject_the_brain_does_not_hold_is_refused`; `test_status_ignores_registry_signals` (`status.moved_signals` empty).
- [ ] **Step 2: Run**; expect fail.
- [ ] **Step 3: Implement** `note`, `apply_note`, `land_notes`; the three call sites.
- [ ] **Step 4: Run** these and `test_release.py test_landing.py test_status.py`; expect pass.

### Task 4: The verbs, the pending pack and the Needs-you items

**Files:**
- Modify: `_meta/tools/classify.py`
- Test: `_meta/tools/tests/test_classify.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces (CLI in brackets):
  - `announce(root, now=None) -> Path | None`: a `workspace` note when the brain's people entry for this space's owner lacks `workspace: <this space>` and none is waiting in `_outbound/signals/`.
  - `ask(root, sid, now=None) -> dict` [`ask <sid>`]: refused (`ClassifyError`) unless `decider` is `self` or `stand_in`; `asked` = `workspaces(reg)` minus this space's person; with nobody to ask returns `{"asked": [], "ready": True}` and writes no note; else an `ask` note `{asked, ask_until: now + window}`.
  - `decide(root, sid, cls, reason, evidence=(), crew=(), home=None, by=None, owner=None, now=None) -> dict` [`decide <sid> --class C --reason R [--evidence ID...] [--crew ID...] [--home NAME] [--by NAME]`]: refused for a non-decider (unless `by` and the subject has no owner); `walled` needs `crew` and `home`; `walled`/`owner` under `open` need `evidence` or a recorded obligation; when approval is needed (Task 3 rules) and `by` is None, writes a proposal `_meta/state/classify-proposals/<sid>.yaml` and the item `classify-decide-<sid>` instead of a note; else a `classification` note with `checked_with` (this workspace plus the asked who answered) and `not_yet_checked` (people without a workspace, plus the asked who did not answer). A `walled` decision whose `home` this space cannot reach writes the item `classify-mint-<sid>` (deadline one window, default "the space is made on <date>") and queues an owner notice through `owner_email.queue`.
  - `set_owner(root, sid, person, evidence, now=None)` [`owner <sid> --person NAME --evidence ID...`]
  - `obligation(root, sid, kind, date=None, evidence=(), works_on=False, now=None)` [`obligation <sid> --kind K [--date YYYY-MM-DD] --evidence ID... [--works-on]`]: `kind` from `OBLIGATION_KINDS`, `date` parsed with `dt.date.fromisoformat`, at least one evidence id.
  - `checked(root, sid, works_on=False, now=None)` [`checked <sid> [--works-on]`]; `works_on` on a walled subject whose crew lacks this person also writes a `crew` note.
  - `pending(root, now=None) -> dict` [`pending`, also runs `announce` and writes `_meta/state/private/classify-pending.json`]: `decide` (subjects this space decides, each with `next`: `ask`, `wait` or `decide`, `needs_person`, `obligations`, `asked`, `answered`, `ask_until`, `previous`), `check` (subjects asking this person, or naming them under `not_yet_checked`), `owner_unknown` (subjects with no owner that this space holds `unclassified` items for; writes item `classify-owner-<sid>`), `crew` (walled subjects of mine with `crew_proposed`; writes item `classify-crew-<sid>-<person>`), `mint_due` (mint items past their deadline without a `no`), `default`. Subjects listed in an open backlog (Task 8) are left out of `decide`.
  - `apply_verdicts(root, now=None) -> dict` [`apply`]: reads `status.verdicts(root)` for `classify-*` items in `_inbound/needs-you/classify.yaml`: `classify-owner-<sid>` (yes: the suggested person; edit: the named person) -> `set_owner`; `classify-decide-<sid>` yes -> `decide(..., by=<who answered>)`, no -> proposal dropped; `classify-crew-...` yes -> `decide` walled with the crew plus the person, `by` the answerer; `classify-mint-<sid>` no -> the plan is dropped; answered items leave the file.
  - `ClassifyError(ValueError)`.
- Items file: `_inbound/needs-you/classify.yaml`, `{items: [{id, kind: question, text, recommendation, default, deadline, depends, payload}]}`, written only by this tool.

- [ ] **Step 1: Write the failing tests**: `test_ask_names_every_workspace_but_mine_and_a_deadline`; `test_decide_waits_for_answers_until_the_deadline` (`pending` says `wait`, then `decide` after the deadline with the silent workspace under `not_yet_checked`); `test_a_non_owner_workspace_never_classifies`; `test_closed_default_needs_the_owner_for_company` (an item `classify-decide-<sid>`, no note; after a `yes` verdict in `log.md` and `apply_verdicts` the note is written with `approved_by`); `test_open_default_needs_a_reason_to_wall`; `test_obligation_carries_kind_and_date_only` and `test_obligation_refuses_wording_and_bad_dates` (Review Focus 5); `test_owner_unknown_asks_who_handles_it_once`; `test_walled_without_a_space_plans_a_mint_after_a_window`; `test_announce_sends_the_workspace_note_once`.
- [ ] **Step 2: Run**; expect fail.
- [ ] **Step 3: Implement** the verbs, `pending`, `apply_verdicts`, `main` (argparse subcommands as listed).
- [ ] **Step 4: Run**; expect pass.

### Task 5: The gate at every exit

**Files:**
- Modify: `_meta/tools/route.py` (`record`, `pending`, `_withdraw`), `_meta/tools/sweep.py` (`run`), `_meta/tools/distill.py` (`run`), `_meta/tools/release.py` (`ship`)
- Test: `_meta/tools/tests/test_classify_flow.py` (new)

**Interfaces:**
- Consumes: `classify.gate`, `classify.first_release`.
- Produces:
  - `route.record`: a leaving route whose `gate` answers writes the projection to `_outbound/held/` (`reason` the gate's, `why`; `kept: true` for `kept`), no signal, outcome `held` in the log and in `out["held"]`; an open gate with `first_release` forces `tier: window`, adds the why "the first release since <sid> was classified company" and `first_release: <sid>`.
  - `route.pending(root, subject=...)`: for a subject the gate holds toward its home, returns the pack with `records: []` and `waits: "<sid> waits for its classification"`; the CLI prints it.
  - `route._withdraw`: also withdraws router items held `unclassified`.
  - `sweep.run` (no router): before the release rule, a gated subject is held with the gate's reason (shallow body), no signal.
  - `distill.run`: no subject candidate and no decision candidate for a subject whose home is another space while the gate holds it; notes count `unclassified`.
  - `classify.requeue(root, space, reg, now) -> dict`, called by `release.ship` after `_requeue_mode_held`: every held `unclassified` item whose gate is now open goes back to the queue (a window item gets a fresh window; `first_release` applies); a `kept` answer marks it `kept: true`; a settled classification whose home differs re-routes it (`route.retract(..., space=<old>, queued_only=True)` for a router item, else the destination re-addressed). Returns `{"requeued": n, "kept": n, "rerouted": n}`.
  - `release.ship`: the backstop: an item whose gate answers is held (reason, why, `kept`); an at-once item of a first-release subject becomes a window item; a non-registry signal of a held subject waits.

- [ ] **Step 1: Write the failing tests**: `test_route_holds_an_unclassified_projection_without_a_signal`; `test_route_first_release_waits_the_window`; `test_no_subject_backfill_before_classification`; `test_sweep_holds_an_unclassified_subject`; `test_distill_writes_no_candidate_for_a_held_home`; `test_ship_holds_an_item_queued_before_the_gate` and `test_ship_puts_it_back_after_classification` (Review Focus 3); `test_a_walled_decision_reroutes_held_items`.
- [ ] **Step 2: Run**; expect fail.
- [ ] **Step 3: Implement** the wiring.
- [ ] **Step 4: Run** these plus `test_route.py test_sweep.py test_distill.py test_release.py`; fix the existing tests whose registries now need a classification by adding a settled `classification` to the subject (no assertion changes).

### Task 6: Needs you, the morning view and sign

**Files:**
- Modify: `_meta/tools/needs_you.py` (`doubt_count`, `_doubt`, `_queue`, `--doubt` output), `_meta/tools/morning_view.py` (`_mark_listed`), `_meta/tools/sign.py` (`index`, a `first_release` handler)
- Test: `_meta/tools/tests/test_classify_flow.py`

**Interfaces:**
- Produces: `needs_you.unclassified_pages(root) -> list` (held, reason `unclassified`, not kept); `doubt_count` = doubt pages + unclassified pages; the line reads `held in doubt: N` plus `, M wait for a classification` when M > 0; `doubt_items` unchanged (only reason `doubt`). Queue items with `first_release` fold into one `leaving` item per subject, id `first-<sid>`, text `<title or sid>: the first N item(s) since it was classified company leave for <destination> at <earliest stage_after>`, payload `_outbound/queue/`. `_mark_listed` lists every queue item of that subject. `sign.index` knows `first-<sid>`; its verdict applies to each of those items as a leaving item's verdict does.

- [ ] **Step 1: Write the failing tests**: `test_doubt_line_counts_unclassified_but_does_not_offer_them` (Review Focus 4: `sign.apply` with `yes` on the unclassified item id skips it); `test_first_release_is_one_line`; `test_the_morning_view_lists_every_first_release_item`; `test_no_on_the_first_release_line_pulls_them_all_back`.
- [ ] **Step 2: Run**; expect fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** these plus `test_needs_you.py test_morning_view.py test_sign.py`; expect pass.

### Task 7: Onboarding and mint

**Files:**
- Modify: `_meta/templates/interview.yaml` (question `classification_default`, company, kind `choice`, choices `[open, closed]`, default `open`), `_meta/tools/onboard.py` (`run` company case writes `classification_default` into the brain's `space.yaml`; `fill_registry` gets `workspaces: dict[slug, space name]` and writes `workspace`, and classifies a project given its own space `walled` with `home` its id, `crew` its crew ids, `reason: "given its own space at onboarding"`, `decided_by` the top owner, `state: settled`, `at`, `checked_with: []`, `not_yet_checked: []`; the person case calls `classify.announce`), `_meta/tools/mint.py` (no `mint-<name>-home` item when `classify.effective` already names `home: <name>`)
- Test: `_meta/tools/tests/test_classify_flow.py`

- [ ] **Step 1: Write the failing tests**: `test_onboarding_asks_the_classification_default_once`; `test_a_project_with_its_own_space_is_walled_at_onboarding` (offline company onboard of a small answers dict); `test_people_with_a_workspace_carry_it`; `test_a_person_onboarding_later_announces_their_workspace`; `test_mint_needs_no_home_item_after_a_walled_classification`.
- [ ] **Step 2: Run**; expect fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** these plus `test_onboard.py test_mint.py test_fixture_e2e.py`; expect pass.

### Task 8: Existing installs: the one list

**Files:**
- Modify: `_meta/tools/classify.py`
- Test: `_meta/tools/tests/test_classify.py`

**Interfaces:**
- Produces: `backlog(root, rows: list[dict], now=None) -> Path` [`backlog FILE`]: rows `{subject, owner, class, reason, evidence?, home?, crew?}` for registered gated subjects without a settled classification; copies them to `_meta/state/classify-backlog.yaml` and writes the one item `classify-backlog` (text: one numbered line per subject, `<n>. <title>: <class>, owner <owner>: <reason>`; recommendation `yes: classify them as listed`; default `nothing about them leaves`). In `apply_verdicts`: yes applies every row (`decide(..., by=<answerer>, owner=<row owner>)`); `edit` takes lines `<n> <class>` separated by `;` and applies the rest as listed; no drops the list. It never changes a `home` except through those classifications.

- [ ] **Step 1: Write the failing tests**: `test_backlog_is_one_item_with_a_line_per_subject`; `test_backlog_yes_classifies_every_row_with_the_owner`; `test_backlog_edit_changes_named_lines_only`; `test_backlog_leaves_homes_alone_until_answered`.
- [ ] **Step 2: Run**; expect fail. **Step 3: Implement. Step 4: Run**; expect pass.

### Task 9: The three stories end to end

**Files:**
- Test: `_meta/tools/tests/test_classify_stories.py` (new): a brain, three workspaces (owner, colleague, late colleague) built with `conftest.make_space`, offline shipping with `offline_roots`, the brain landing each note with `landing.land`.

- [ ] **Step 1: Write the tests**: `test_owner_holds_the_obligation_and_a_colleague_finds_the_client_first` (the colleague's routes are held, nothing in its queue or `released.jsonl` for the client; the owner asks, the colleague answers `checked`, the owner decides `walled`; the colleague's held items are re-routed, none ever went to the brain); `test_a_late_colleague_holds_an_obligation_the_owner_never_saw` (owner decides `company` with the late colleague under `not_yet_checked`; the late workspace's `pending` lists the check; its `obligation` note re-opens the subject; every space's routes for it are held; the owner's `decide walled` needs no person; `decide company` writes a question); `test_the_company_owner_decides_for_an_owner_without_a_workspace` (`for:` names the owner; a third workspace's `decide` is refused).
- [ ] **Step 2: Run**; fix what the stories find in Tasks 2 to 8.

### Task 10: Skills, scenarios, rules, decisions, full suite

**Files:**
- Create: `.claude/skills/classify/SKILL.md`, `_meta/tests/behaviour/classify-1.yaml` (pending writes only `_meta/state/private/`, `_outbound/signals/`, `_inbound/needs-you/`), `classify-2.yaml` (an obligation note: one signal file, no `fact`), `classify-3.yaml` (a non-owner `decide` exits 1 and writes nothing)
- Modify: `.claude/skills/route/SKILL.md` (step 0: `classify.py pending`, then the classify skill; an obligation bullet under judgment; never route a held subject by hand), `.claude/skills/seed/SKILL.md` (after the backfill sweep: the classify skill's checks), `.claude/skills/mint/SKILL.md` (a walled classification's plan whose window passed without a no counts as the person's ask), `.claude/skills/onboard/SKILL.md` (the default question), `.claude/skills/sign/SKILL.md` (the doubt line's waiting part; the first-release line), `rules.md` section 6 (one sentence), `docs/decisions.md` (one row; the 2026-10-12 row marked replaced), existing behaviour scenarios whose registries need a classification.
- [ ] **Step 1:** Write the skill and scenarios; edit the others.
- [ ] **Step 2:** `python _meta/tools/validate.py . --template` (or the template's check), `python _meta/tools/no_company_names.py .`, `python _meta/tools/behaviour.py . run` (or `test_behaviour.py`), all clean.
- [ ] **Step 3:** Full suite in the background from `_meta/tools`: `<venv> -m pytest -q -p no:cacheprovider`; expect the earlier pass count plus the new tests, no failures.
- [ ] **Step 4:** A fresh reviewer reads the whole diff against the spec; fix what it finds.

### Task 11: Land, sync, the live list

- [ ] Land: `<venv> _meta/tools/sync.py --root <template checkout> --only <every changed file> --message "chore: sync" --min-age-minutes 0`; wait for the merge.
- [ ] Sync every space: `<space>/_meta/tools/template_sync.py <space> --guarded --from <template repository>` for each live space the brief names (the brain, every workspace, every project and channel); each guarded run green.
- [ ] The one-off list in the owner's workspace: classify every registered gated subject (agent judgment with evidence), `classify.py <owner's workspace> backlog <rows>`, and confirm the item shows on `needs_you.py`.
