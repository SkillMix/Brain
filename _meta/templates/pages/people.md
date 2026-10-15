<!--
Page shape: a person (knowledge/people/<id>/). Work facts only: who they are at work, how we
know them, the roles they hold in our work, how they work with us. A claim about a named
person is high-stakes: every line rests on a record that says it, never on an inference.
Line format: every sentence or bullet under a heading ends with `[src: <record id>, ...]`,
ids from the candidate's `evidence`; a paragraph ends with one marker. Headings and the
`## New evidence` list carry none. Newer evidence replaces older.
Leave out a section with nothing lasting in it; never a placeholder or a question.
Never on the page: health, family, pay, employment or immigration status, private addresses
or numbers, opinions about the person (the brief keeps what the work needs to know).
`python _meta/tools/distill.py <root> verify <candidate>` checks the lines.
-->
# <full name>

## Who they are
Their organisation and role, as they or their organisation state it. [src: <id>]

## How we know them
The first contact: when, through whom, and in which piece of work. [src: <id>]

## Roles in our work
- One bullet per client, project or deal: a link to its page, their role there, from when to when. [src: <id>]

## Working with them
- A lasting preference they stated themselves (language, channel, the hours they keep). [src: <id>]
