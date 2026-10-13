---
title: Rules
---
# Rules: the constitution of this space

The brain's copy binds every space below it; a space may add rules, never loosen these.
Section numbers are stable; tools cite them.

## 1. Hard rules

1. Secrets never enter a repository: no keys, tokens, passwords, access links. The scan
   blocks them; a hit is unstaged and counted, never quoted.
2. What is on the never-leaves list never leaves its space, by any path, at any badge level.
3. Only a person signs. `status: stable` is written by `sign` while executing a named
   person's verdict; a routine or agent that writes it is a defect.
4. Agents never widen their own authority. A badge level is raised only by a human space
   owner; evidence lowers it for four weeks.
5. One writer each: only the platform writes `main`; each append-only file is merged by
   union; each outside system is written by one routine, never by sessions.
6. Agents never send, post, spend or delete. Drafts go to `outbox/`; the person fires them.
   The one exception is the morning email: the person's own mail, sent from their own
   account to themselves, with the payload exactly as the morning view rendered it.
7. Agents edit files; the sync script commits; the platform merges. No session runs a git
   write.
8. `personal/`, `academic/` and `_scratch/` are outside every tool, sweep and commit. The
   question log never leaves its space; releases carry pointers to evidence, never the
   evidence itself.

## 2. The high-stakes list (closed)

Waits 24 hours and lands pending for the domain owner, whatever the release path:
identity and rules; anything that binds the company (pricing rules, contract terms,
commitments); policy (HR, security, legal); claims about a named person; changes to someone
else's signed page.

## 3. Badge floors

`send_outward` and `spend` never rise above `propose`. `release` never rises above
`after_window` for records whose audience would widen. The template defaults are in
`space.yaml`; the brain's `floors:` may make any of them stricter for every space below it,
never looser. An agent row's `badge:` only lowers. Evidence lowers a path for four weeks
(a pull-back or a failed sample pauses the standing choice or source audience that let an
item through); every other level is lowered or raised only by a person in `space.yaml`.

## 4. Left-out defaults for a project destination

Prices, fees and other commercial terms; a person's employment, role, pay or immigration;
credentials and access links; client data files; attachments and raw transcripts; other
clients. A passage that mixes these keeps only its project facts. The list is a setting in
the destination's `space.yaml`.

## 5. Freshness windows (days)

snapshot and status 7; process, directive and review 90; concept and entity 180; policy and
decision 365. A page may set `review_every`. A page unanswered four weeks past its window is
confidence low, derived from its dates.

## 6. The release rule

A release waits only when it widens who can see something. At once: content its audience
can already read, content its holder approved (by name or by a standing choice for that
destination), retractions and signals. Otherwise after one window and one morning view.
Doubt about the class means the window; doubt about privacy means the item stays home and
is shown as a count. External disclosure is not internal permission.

## 7. Budget

`budget.tokens_per_day` is declared and shown in the health line, not enforced. When an
owner lowers it, shed in this order: the checker's window pass, then the checker's breadth,
then search. Never the sweep, never the tests.

## 8. Protected paths

Pull requests touching `protected:` paths wait for the space owner's one click; everything
else lands when the checks pass.

## 9. The trust root

A signature is a commit in the signer's own space, made under their platform identity by
the sync script running with their credentials; nothing else can produce one. The
fingerprint (the sha256 of the approved text) travels with every release, and the merge
gate of the receiving space checks it against the body before the page lands. At the home a
routine signed release lands silently; a high-stakes one, a change to someone else's signed
page, a disagreement between two releases and a release whose body does not match its
fingerprint land pending. Each week the domain owner checks a sample of what landed
silently; a pulled-back release or a failed sample pauses the path that let it through for
four weeks, and a releaser whose samples fail too often lands pending until it recovers.
Agents act with the person's own grants, never with a company-wide token.

## 10. People without a workspace

A person without a workspace has no capture and no sweep: their sessions are not recorded
and nothing is swept on their behalf. What they know reaches a space through the releases
of people who have a workspace and through a project's own sources (its channel, tracker
and repository). Nobody else's workspace stands in for theirs.
