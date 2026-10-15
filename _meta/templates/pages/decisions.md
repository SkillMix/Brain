<!--
Page shape: a decision (knowledge/decisions/<yyyy>-<mm>-<dd>-<slug>.md). One decision per page:
what was decided, why, who decided and when, what it changes. A decision page is never edited
to say something else: a later decision that replaces it gets its own page and is linked here.
Line format: every sentence or bullet under a heading ends with `[src: <record id>, ...]`,
ids from the candidate's `evidence`; a paragraph ends with one marker. Headings and the
`## New evidence` list carry none.
Leave out a section with nothing lasting in it; never a placeholder or a question.
Never on the page: prices, rates, terms, ownership, a person's private situation (the brief
keeps them). `python _meta/tools/distill.py <root> verify <candidate>` checks the lines.
-->
# <the decision, as one short sentence>

## The decision
What was decided, in one or two sentences, with a link to the subject it is about. [src: <id>]

## Why
The question or event that called for it, and the reasons given. [src: <id>]

## Options considered
- One bullet per option that was on the table, and why it was not taken. [src: <id>]

## Who and when
Who decided, in which meeting, mail or chat, and on which date. [src: <id>]

## What it changes
- One bullet per consequence: work that starts or stops, a page or agreement it touches. [src: <id>]

## Replaced by
A later decision that replaces this one, linked to its page; left out while it holds. [src: <id>]
