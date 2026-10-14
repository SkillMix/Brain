---
name: capture
description: The personal instruction's behaviour - from any repository a session runs in, journal durable context into the person's own space's daily note, show Needs you once at session start, log answered questions, and never commit. Always on for a person with a personal space; never in a project space of its own.
---

# capture: follows the person, not the repository

A session's audience is one person, so its evidence enters at that person's space. Nobody
opens their vault for the system to learn from them; the sync script lands what you write.

## When

Every substantive session, in whatever repository it runs, when the user-level instruction
installed by `onboard` (from `_meta/templates/personal-instruction.md`) names the person's
space on this machine. Not for an ordinary greeting; not in a project space, which has no
journal.

## Run

No tool. The instruction gives `<space root>`; then:
1. Session start, once: `python _meta/tools/needs_you.py <space root>` and show the list in
   your first reply ("three things need you: ..."). Answers go through the sign skill.
2. During the session, append to the daily note
   `<space root>/journal/<yyyy>/<mm>/<yyyy>-<mm>-<dd>.md` (today on the person's clock;
   when new, start it with a `# <yyyy>-<mm>-<dd>` heading): one dated line per durable
   item: a decision and its reason, a fact learned, a commitment, a position, a correction
   to a procedure, and every action of yours with how to undo it. Start each entry with
   a `## HH:MMZ — <topic>` heading and, when you know your session's id, a marker
   `<!-- session: <id> -->`: the transcript adapter counts a session as captured by either.
   When the entry is about a subject in the registry (this space's or a mounted
   `_meta/subjects.yaml`), end it with a line `subject: <id>`: distill attaches the entry to
   that subject and proposes it for signing. An entry that names a desk folder
   `work/<kind>/<id>/` attaches the same way; one without either stays a note.
   Read the file first; append; never rewrite what is there.
3. A question answered with more than one lookup: its answer and sources as one entry, plus
   the question log entry (the query skill).
4. Stop. The sync script commits within 15 minutes; distill proposes from the journal at
   the next sweep; the person signs.

## What the tool already did

`onboard` installed the instruction with the space path; `sync.py` lands the journal
(append-only, merged by union); `distill.py` reads every entry that names a subject and lists
it, by title only, as evidence on that subject's candidate.

## Judgment left to you

What will still matter tomorrow. When unsure, one line costs nothing and never leaves the
person's space on its own; a secret costs everything and never goes anywhere.

## Never

- Never journal into the repository the session runs in unless it is the person's own
  space; never into a project space (no daily note exists there).
- Never commit, push or merge; never write `stable`.
- Never a secret, key, token or access link, even quoted (hard rule 1).
- Never keep company knowledge in the harness's own memory: pointers and preferences only;
  what you learn goes through capture.
- Never overwrite the daily note: other sessions append to it too.

## Scheduled invocation

None. Capture is the session's own behaviour. `transcripts.py scan` (in the sweep chain of a
personal space whose `space.yaml` names a `transcripts` folder) measures how many sessions
capture reached; below one half over four weeks the librarian proposes its switch, and with
the switch on, missed sessions become witness pages in this personal space only.

## Behaviour scenarios

`_meta/tools/tests/test_agents_md.py` (a `project` profile renders no capture rule) and
`_meta/tools/tests/test_onboard.py` (the instruction block is installed and read back;
`offboard` removes it).
