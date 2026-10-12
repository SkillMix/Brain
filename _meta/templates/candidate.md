---
# A candidate: one proposed update to one subject page, written by distill into
# _inbound/candidates/<subject>.md. The owner signs it (sign), edits it or discards it.
# Routines never write `status: stable`; a signature is a named person's verdict.
id: p-3f9a1c2e                      # the candidate's own page id (the target page keeps its own)
type: entity                        # the target page's type, or `entity` when the page is new
title: Harbor
status: draft                       # always draft here
generated: {by: distill, at: '2026-10-12T09:00:00Z'}
subject: harbor                     # the registry id this candidate is about
kind: projects                      # the subject's kind under knowledge/
destination: harbor                 # only when the subject's home is another space (registry home)
target: knowledge/projects/harbor/harbor.md   # the existing page, else knowledge/<kind>/<subject>/<subject>.md
evidence: [rec-1, rec-2]            # record ids of the witness pages and landed records folded in
---
# Harbor

The existing page body, when there is one, stays above the heading; distill never
edits the page itself. A new subject starts from the one-line stub `# <title>`.

## New evidence
- 2026-10-10: Kickoff agreed for next week (witness, rec-1)
- 2026-10-11: Scope confirmed: two phases (witness, rec-2)

<!--
A decision is its own candidate: a witness page whose text starts with `Decision:` becomes
_inbound/candidates/<yyyy>-<mm>-<dd>-<slug>.md with `type: decision`, `kind: decisions`,
`target: knowledge/decisions/<yyyy>-<mm>-<dd>-<slug>.md` and the first line as its title.

While a candidate is an unsigned draft, the next run rewrites it in place: the evidence
list is merged, the `## New evidence` section is regenerated, the text above it is kept.
-->
