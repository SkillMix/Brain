---
name: owner-email
description: Tell the person something by email, from their own mailbox to their own address - a security warning, an answer that arrived, an action only they can take. Use when a tool queued a notice (`owner_email.py pending` lists it), or when a session must warn the person now. Never to anyone else; never a chat self-message.
---

# owner-email: short notices to the person themselves

Rule 6 in `rules.md`: agents never send, except to the person themselves from their own
account. Two kinds go that way: the morning briefing (the morning-view skill) and owner
notices (this skill).

## When

- A tool queued one: the sweep after a password or key showed up in a record, the request
  lane when an answer arrived. `python _meta/tools/owner_email.py pending --root <space>`
  lists them; both chains send them. Security findings wait in one digest, and at most one
  security email goes out in any 24 hours; the rest wait for the next.
- In a session, when the person must know now and is not in the chat (a credential in a
  shared document, a deadline only they can meet). Write the notice, queue it, send it.
- While the routines run in the cloud (`_meta/state/routine-host.yaml` has `runner: "cloud"`), a
  session on a computer leaves the notices `pending` lists to the cloud's runs, which send them,
  so none goes twice: it sends only a notice it queued itself in that session. The cloud's runs
  follow this skill as their prompts (`_meta/templates/cloud/`) say, and read each sent message
  back with the mail connector's `get_message`.

## Run

1. A new notice: write the JSON (kind `security`, `action` or `fyi`; title, summary, steps
   for security and action, a `key` so it is sent once) and
   `python _meta/tools/owner_email.py queue <notice.json> --root <space>`. Exit 2 names what to
   rewrite: plain words, no paths, ids, links in the text, or secrets.
2. Send each pending file's `payload` exactly as rendered (to, subject, htmlBody, body) with
   the mail connector from the person's own mailbox; read the sent message back.
3. `python _meta/tools/owner_email.py record <file> --id <message id> --root <space>`; on a
   failed send `--failed "<reason>"` keeps it pending for the next chain.

## Never

- Never to anyone but the person's own `owner_email`; never a Slack or chat self-message.
- Never the value of a secret, a password, a token or a link that grants access.
- Never compose the HTML by hand: the tool renders it.
