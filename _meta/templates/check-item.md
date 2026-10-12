---
# A check item: one hit of one named check, written by the checker into
# _inbound/checks/<check_id>.md. It is a question already answered as well as the evidence
# allows; the person answers yes, edit, no or retire (sign writes the verdict to
# _meta/state/check-verdicts.jsonl with this check_id). The checker never edits the page.
id: p-7b2d9e04                      # page id: `p-` plus the eight hex characters of the check id
type: review
title: 'newer_evidence: knowledge/projects/harbor/terms.md'
status: draft                       # always draft here
generated: {by: check, at: '2026-10-12T09:00:00Z'}
check: newer_evidence               # disagree | newer_evidence | past_window | person_claim | named_number
check_id: chk-7b2d9e04              # sha1(check, page, label)[:8]; a re-run never reopens an open item, nor one
                                    # whose verdict names this check_id or the page id above
page: knowledge/projects/harbor/terms.md   # the page the hit is about
subject: harbor                     # omitted when the page has no subject
evidence: [knowledge/sources/2026/10/rec-1.md]   # page paths or record ids that back the recommendation
recommendation: 'the page says standard discount: 15%; rec-1 of 2026-10-11 says discount: 20%; I think 20%. Still 15%?'
route: [Ben Liu, Chloe Martin, Ada Okafor]   # signer, subject owner, domain owner, space owner; blanks and repeats removed
---
# newer_evidence: knowledge/projects/harbor/terms.md

the page says standard discount: 15%; rec-1 of 2026-10-11 says discount: 20%; I think 20%. Still 15%?

<!--
Labels are matched with `([A-Za-z][A-Za-z ]{1,30}?)\s*[:=]\s*(\d{4}-\d{2}-\d{2}|\d[\d.,]*\s?%?)`
and compared by their last word, so `standard discount` and `discount` are one label.
`named_number` is informational and routes to the space owner only. A run whose hit rate over
30 scaled days is under 0.3 (with ten or more verdicts) drops past_window and named_number
and says so in its run line.
-->
