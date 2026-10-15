<!--
Page shape: a client (knowledge/clients/<id>/). Lasting facts only: what the client is, how
it started, its people and their roles, the work, agreements, decisions, how it ended. Where
it stands is the status page's (live/subjects/<id>.md), never this page's.
Line format: every sentence or bullet under a heading ends with `[src: <record id>, ...]`,
ids from the candidate's `evidence`; a paragraph ends with one marker. Headings and the
`## New evidence` list carry none. Newer evidence replaces older.
Leave out a section with nothing lasting in it; never a placeholder or a question.
Never on the page: prices, rates, terms, ownership, a person's private situation (the brief
keeps them). `python _meta/tools/distill.py <root> verify <candidate>` checks the lines.
-->
# <client name>

## What it is
What the organisation does, where it works and how large it is, in one short paragraph. [src: <id>]

## How it started
The first contact: when, through whom, and what was asked. [src: <id>]

## People and roles
- One bullet per contact: name, organisation, role, what they do in the work with us. [src: <id>]

## Work
- One bullet per project or deal for this client: a link to its page and one line on what it is. [src: <id>]

## Agreements
- A lasting arrangement (a framework or confidentiality agreement, how work is ordered), dated, never an amount. [src: <id>]

## Decisions
- A decision that shapes the relationship, dated, linked to its decision page. [src: <id>]

## How it ended
When and why the work stopped; left out while it goes on. [src: <id>]

## Where it stands: see the [status page](id:<status page id>)
