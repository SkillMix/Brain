# The classification gate: nothing about a client leaves before its owner's agent classifies it

Date: 2026-10-16. Supersedes the decisions row of 2026-10-12 "Project spaces at onboarding"
(every client and project without its own space homed at the brain, unchecked).

## 1. Why

Onboarding points every client, project and deal that has no space of its own at the brain,
and nothing checks whether one is an exception. A client under a confidentiality agreement had
its records routed and distilled into the brain, readable by the whole company, and had to be
moved out by hand with a history rewrite. The evidence that a client is an exception is spread
over several people's workspaces, which are set up at different times: a colleague may never
have seen the agreement, or may onboard weeks later. So the decision is one per subject, made by
the agent of the person who holds the relationship, never waiting for workspaces that do not
exist yet, and tightened later when a newcomer's records show an obligation.

## 2. The gate

A subject of a gated kind (`clients`, `projects`, `deals`, and anything `part_of` one of them)
sends nothing out of the space where its material came in until it has a settled
classification. Checked by one function, `classify.gate`, at every exit:

- `route.py record`: a leaving route is recorded, its projection written to `_outbound/held/`
  with reason `unclassified`, no signal. The router does not redo the record.
- `sweep.py` (spaces without the router): the same hold before the release rule.
- `release.py ship`: the backstop for anything already queued, approved or signed; and the
  place where held items go back to the queue once the gate opens (section 9).
- `distill.py`: no candidate destined for another space (no distill at a home).
- `route.py pending --subject`: no subject backfill; it says the subject waits for its
  classification.

Held `unclassified` items count in the one "held in doubt" line; they are not answered item by
item, because a person's yes there would only meet the gate again. They move by themselves when
the subject is classified.

The gate fails closed: a subject or umbrella the registry cannot resolve (an empty brain mount),
or a registry that cannot be read, lets nothing about a subject leave. A `whole` route passes
the gate for every other gated subject whose identifiers the record carries; parts the router
chose leave for their own subject. A registry note and a failed-sample signal carry no material
toward a home and are not gated. A company or walled class names the home of everything it
covers, parts included.

## 3. The classification

One per subject, stored on its entry in the registry every space reads through the brain:

```yaml
classification:
  class: company | walled | owner
  state: asking | settled | open      # only settled opens the gate; open = re-opened
  home: <space>                        # walled: the crew's space
  crew: [<people ids>]                 # walled
  reason: <one line, facts only>
  evidence: [<record ids>]             # pointers, never text
  decided_by: <person whose agent decided>
  for: <the subject's owner, when the company owner's agent stood in>
  at: <ISO time>
  checked_with: [<workspace names consulted>]
  not_yet_checked: [<people ids without a workspace, or who did not answer in time>]
obligations:                           # facts any agent saw (section 7): kind and date only
  - {kind: confidentiality | named_people | data_processor | other, date: <YYYY-MM-DD>,
     seen_in: <workspace>, at: <ISO>, evidence: [<record ids>]}
```

A part inherits: a project of a classified client follows the client, and the strictest class
on the chain wins (owner, then walled, then company); any `asking` or `open` on the chain closes
the gate. People entries gain `workspace: <space name>` once that person's workspace exists.

## 4. Who decides

- The subject's owner: `owner` on the subject or its nearest gated umbrella (never a domain's
  owner, never the space owner as fallback). An agent sets a missing owner when the evidence
  shows who sold or signed (`classify.py owner`); it never changes an owner already set.
- The owner's own workspace classifies. If that person has no workspace yet, the company
  owner's workspace (the brain's owner; onboarding creates it first) decides in their place,
  with `for:` naming them.
- No owner known: everything about the subject stays held, and the person whose workspace holds
  the material gets one Needs-you question, "who handles <subject>?".
- Any other workspace never classifies: `classify.py decide` refuses, and so does the brain
  when the note arrives.

## 5. Asking the other workspaces

The owner's agent runs `classify.py ask <subject>`: the classification goes to `asking`, naming
every registered person who has a workspace (not the asker), with a deadline one window out.
Each of those workspaces lists it in `classify.py pending`, its agent reads its own records
about the subject and answers in facts only: `classify.py checked <subject>` (looked, saw no
obligation) or `classify.py obligation ...` (kind and date: "confidentiality agreement, signed
<date>"), never the document. The owner's agent decides when every one answered or the deadline passed; a
workspace that did not answer moves to `not_yet_checked`. Nobody without a workspace is waited
for: every registered person without one is listed under `not_yet_checked`, because nobody can
know who is involved before their records are read.

How the asks and answers travel is the one place this differs from the brief, which named the
request lane. Three ways were weighed; the owner chose the first on 2026-10-16:

1. **Registry notes through the brain (chosen).** Asks, answers, owners, obligations and
   classifications are small YAML notes on the existing signal lane, addressed to the brain and
   applied by the brain itself on its next sync (15 minutes). One lane, one source of truth,
   and these facts must end up in the shared registry anyway.
2. The request lane with the brain standing in as corridor for one new class. Reuses requests,
   but an answer lands as a witness page the router then reads as a new record, an owner email
   per answer has to be switched off, and the answer still has to be copied into the registry.
3. A corridor repository per pair of workspaces, the request lane as it is: about ten
   repositories for five people, and none exist today between personal workspaces.

## 6. Late workspaces check back

`classify.py pending` also lists every classification that names this workspace's person under
`not_yet_checked`. The route skill runs it first in every sweep chain, and the seed skill after
the backfill sweep, so a new workspace's first sweep checks its records against them. A check
writes `checked` or an obligation; either removes the person from `not_yet_checked`. A late
check only tightens by itself: an obligation re-opens a `company` subject (section 7) and its
owner's agent then re-decides. A colleague outside a walled subject's crew who holds its
records keeps them home and is proposed for the crew, a question for the subject's owner.
Loosening always needs the subject's owner, the person, never their agent alone.

## 7. Obligations as facts

Any agent that sees an obligation (a confidentiality agreement or clause, a client asking for
named people only, acting as the client's data processor) writes it with `classify.py
obligation`, judged from the whole record, never a word list. The line in the registry is the
obligation's kind and its date only ("confidentiality agreement, signed 2026-04-14"): never its
terms, amounts, the people on the other side or any wording from the document. The tool takes a
kind from a closed list and a date, and has no free-text field; the evidence stays record ids.
A new obligation on a `company`
subject sets its state to `open`: its routes are held everywhere until its owner's agent
settles it again. Tighter (walled, owner) needs no person; `company` again needs the owner's yes.

## 8. The company default

One interview question at onboarding, stored in the brain's `space.yaml`:
`classification_default: open | closed` (default `open`; a brain without it reads `open`).

- `open`: `company` unless there is a reason; `walled` or `owner` needs a reason and evidence
  (a project that already has its own space is walled for its crew, as at onboarding).
- `closed`: `walled` unless someone opens it; `company` needs the subject owner's yes.

## 9. Acting on the class

- `company`: home the brain. Held items go back to the queue.
- `walled`: home the named crew's space. When it does not exist yet, the deciding workspace gets
  a mint plan as a veto-window item and an owner notice ("on <date> I make <space> for <subject>
  with <crew>; say no to stop"); after the window the agent runs the existing mint flow. The crew
  is who works on it (its repository, channel, recurring meetings, tracker), never one mail's
  recipients. Platform grants stay the person's (mint's team item). Held items for the old home
  are withdrawn and the records go back to the router for the new home.
- `owner`: stays home. Held items are kept home for good.

## 10. The safety net

Until a workspace has made its first release of a subject since it was classified `company`,
every projection for it waits the window, and the morning view shows them as one line per
subject ("<subject>: the first N items since it was classified company leave for the brain at
<time>"). A no on that line pulls them all back.

## 11. Existing installs

After the template sync nothing about a registered client, project or deal leaves any space
until it is classified. The agent classifies every registered one once and puts the result on
the owner's Needs you as one list with a recommendation per subject (owner, class, reason); a
yes applies it, an edit corrects lines. It changes no home itself. Subjects that already live
in their own crew space are recommended `walled` with that space as home.

## 12. What changes

- New: `_meta/tools/classify.py` (gate, pending, ask, decide, owner, obligation, checked,
  backlog, and the brain's `land_notes`), `.claude/skills/classify/SKILL.md` with three
  behaviour scenarios, tests.
- `route.py`, `sweep.py`, `release.py`, `distill.py`: the gate; `release.py` and `landing.py`
  apply registry notes in a brain; signals with `kind: registry` are never re-addressed and never
  flag a status page.
- `registry.py`: checks the new fields. `needs_you.py`, `morning_view.py`, `sign.py`: the
  unclassified count, the first-release line and its pull-back.
- `onboard.py` and `interview.yaml`: the default question; a project given its own space at
  onboarding is classified `walled` (crew and home from the answers, decided by the top owner);
  people with a workspace get `workspace`; a person's onboarding sends its `workspace` note.
- `mint.py`: skips its "set home in the parent's registry" item when the classification set it.
- Skills: route (step 0 and obligations), seed, mint, onboard, sign, status. `rules.md` section
  6 gains one sentence. `docs/decisions.md`: one row.

## 13. Tests

One per behaviour above, and the three stories end to end on offline spaces: the owner's
workspace holds the obligation while a colleague's finds the client first (nothing leaves before
the owner's classification); the colleague onboards after a `company` classification and holds
an obligation the owner never saw (the classification re-opens, the owner's agent tightens it);
the subject's owner has no workspace (the company owner's agent decides, a third workspace is
refused). The fixture's projects get their classification from onboarding, so the fixture run
and the behaviour scenarios stay green.

## 14. Risks accepted, and what is not here

A subject's name, class and obligation facts (the kind and date of each, nothing else) are
readable company-wide in the brain registry; every space needs them to apply the gate. A client whose existence itself is confidential is not
covered by this slice. The brain trusts a note's sender as the platform trusts the author of the
pull request that carried it. A new client's first material reaches the brain about two windows
after it was first seen (the ask, then the first-release window). Later cards: facts instead of
mails for wider audiences, a move-client tool, a default search scope, retention and erasure.
