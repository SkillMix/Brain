<!--
Page shape: a deal (knowledge/deals/<id>/). Lasting facts only: what was on offer and to whom,
how it started, scope, people and roles, agreements, decisions, how it ended. Where it stands
is the status page's (live/subjects/<id>.md), never this page's.
Line format: every sentence or bullet under a heading ends with `[src: <record id>, ...]`,
ids from the candidate's `evidence`; a paragraph ends with one marker. Headings and the
`## New evidence` list carry none. Newer evidence replaces older.
Leave out a section with nothing lasting in it; never a placeholder or a question.
Never on the page: amounts, prices, rates, discounts, terms, a person's private situation
(the brief keeps them). `python _meta/tools/distill.py <root> verify <candidate>` checks the lines.
-->
# <deal name>

## What it is
What is on offer and to whom, with a link to the client's page. [src: <id>]

## How it started
Where the lead came from: when, through whom, and what was asked. [src: <id>]

## Scope
- What the offer covers and what it leaves out, one bullet each. [src: <id>]

## People and roles
- One bullet per person: name, organisation, role in the deal (who decides, who leads ours). [src: <id>]

## Agreements
- A step both sides took (a proposal sent, a letter of intent signed), dated, never an amount. [src: <id>]

## Decisions
- A go or no-go, dated, linked to its decision page. [src: <id>]

## How it ended
Won (with a link to the project it became), lost (and the reason given) or dropped, and when. [src: <id>]

## Where it stands: see the [status page](id:<status page id>)
