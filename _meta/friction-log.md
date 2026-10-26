# Friction log

Every time an agent in this space has to hand something to the person, or a guardrail stops
it, it adds one entry here. The point is diagnosis: read this file, spot the patterns, and fix
them (an allow rule, a skill change, a tool) so the person is asked less often.

## What gets an entry

- **The person had to run or do it.** Any time an agent asks the person to run a command, click
  something, approve something, or finish a step by hand, except the deliberate verbs of the
  person (read, sign, say no, send what is in the outbox, answer a question only they can).
  Those are the system working as designed, not friction.
- **A guardrail stopped it.** A permission prompt that was refused, a denied tool, a hook's
  refusal, a sandbox block, or a connector that refused an action. Log it even when you worked
  around it.

Plain tool bugs that never reached the person and never hit a guardrail stay out. So does
anything secret: name the kind of thing, never a password, token, key or link that opens
something.

## How to write one

Append at the bottom, newest last. Never edit or delete an earlier entry. One block:

```
## {yyyy-mm-dd HH:MM}Z — {short title}
- **Who:** chat session | routine name
- **Kind:** permission refused | denied tool | hook or sandbox | connector refused | the person had to do it
- **Trying to:** what the agent was doing, in one line
- **What happened:** the refusal or the prompt, in a few words or a short quote
- **Asked of the person:** what they had to do, or "nothing, worked around: ..."
- **Fix idea:** an allow rule, a skill change, a tool, or "unknown"
- **Status:** open | worked around | fixed {date}
```
