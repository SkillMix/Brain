---
# A witness page: one feed record, as this space saw it. sweep.py writes one per record
# at knowledge/sources/<yyyy>/<mm>/<record-id>.md. Witness pages are drafts, indexed at
# once and quotable as "witness, unverified"; they are never signed and never leave
# this space themselves (what travels is a scrubbed queue item that points back at the
# record id as evidence). The comments explain each key; the sweep writes them all.
id: p-1a2b3c4d                         # permanent page id (pages.new_id()); links use it
type: source                           # always `source` under knowledge/sources/
title: 'Re: Harbor go-live'            # the record's subject line, else "<kind> from <sender>"
status: draft                          # never `stable`: witness pages are not signed
generated: {by: sweep, at: '2026-10-12T09:00:00Z'}
record: m-0001                         # the feed record id; the file is named after it
source: mail                           # the feed it came from (space.yaml `sources[].name`)
kind: mail                             # mail | call | message | event | document
at: '2026-10-12T08:14:00Z'             # when the record happened (the watermark advances on this)
from: Chloe Martin
to: [Ada Okafor]                       # recipients, when the record has any
# participants: [Ada Okafor, Ben Liu]  # calls only; people outside the crew make the release a doubt
subject: harbor                        # the one subject whose `match:` identifier the record
                                       # carries; absent when it matched none or several
mentions: [beacon]                     # subjects named in the text: hints only, never attachment
audience: owner                        # who could already read the record: owner | crew | everyone
identifiers: {thread: t-1, labels: [Harbor]}   # the fixed identifiers the sweep matched on
# attachments: [brief.pdf]             # names only: attachments are pointed to, never copied
# sensitive: true                      # written when a never-leaves term appears; such a record
                                       # is held and sends no signal
---
Go-live moves to 3 November. Please confirm.
