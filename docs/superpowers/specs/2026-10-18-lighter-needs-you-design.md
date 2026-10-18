# Lighter Needs you: ask by exception

Date: 2026-10-18. Status: draft for the owner's review. No code and no plan until it is approved.

## 1. Why

Needs you is the one list of what needs a person (`_meta/tools/needs_you.py`). Every routine
adds to it, and every item waits for a verdict in a session with read-back and `submit`. After
a history backfill the pilot owner's list held 365 items: 195 high-stakes, 127 leaving, 29 notes
to sign, 9 questions, 3 samples, a pull request and a held-in-doubt line. Every one carried a
recommendation, and all but a handful recommended yes. A list that only moves when someone
signs every day asks for a new daily habit. Most people never form it, and then the software
stops being used.

## 2. The rule

**Zero required daily actions.** Ask only by exception, only where people already work, and
let the queue resolve what evidence can answer.

Three things follow, and the rest of this spec spells them out:

- Every item has a safe outcome for silence and a date when that outcome applies. Doing
  nothing is always a valid answer.
- A person is pushed at most a few asks a day. Each one comes with a recommendation, its
  reason, and what happens if it is ignored. Pulling the full list stays unlimited.
- Evidence and a track record replace asks over time: facts close themselves when the record
  arrives, and an agreement rate that is earned turns into standing choices.

What does not change: only a person signs, the agent never writes `status: stable`, the
never-leaves hard rule, the window and pull-back, the classification gate, judged gates with
a second agent on every outgoing decision, the secrets scan, and the one-click protected pull
request.

## 3. What gets asked

### 3.1 Silence is a safe answer

Every item kind has an explicit default: the outcome when nobody answers. The default never
widens an audience beyond what the rules already allow without a person, and it never deletes
anything. Two families:

- **Low-risk items proceed.** They take their default after their window: a `window` release
  leaves after the morning email listed it, as today; a grounded draft stays usable; a sample
  counts as fine.
- **Risky items stay home for good.** High stakes, doubt and unclassified items never leave on
  silence. When the destination is known and the item holds no never-leaves material, one
  fact line goes to the destination at the item's stale date, so the people there know to
  ask: `<kind> record of <date> about <subject> stayed in <space>; ask <owner>`. A fact line
  carries the kind and the date, never the wording, and it rides the normal release (window,
  scrub, scan) like any other item.

**This changes one thing that ships today.** A high-stakes route is a `window` item. The
morning view lists it, `release.ship` sends it once the window has passed, and it lands
pending for the domain owner (`route.py:549-553`, `release.py:409-421`, `landing.py:251-261`).
Its Needs-you default, "waits for the domain owner", describes the destination, not the home.
Under this spec a high-stakes item leaves only with a yes from its person (`approved_by`, as
`sign.py` records today) or a standing choice (3.6). On silence it stays home and the fact line
goes instead.

Drafts stay usable (section 3.9). An item is asked only when silence would be wrong: its
default would cost something a person cares about (a commitment missed, a deadline before the
default applies, someone blocked, an answer the destination needs this week). The agent
records that judgement on the item as `silence_ok: true|false` with one line of reason.

| Kind | Default on silence | When it applies | Fact line |
|---|---|---|---|
| leaving (`window`) | leaves | at `stage_after`, once listed (as today) | n/a |
| leaving, first release of a subject | leaves | at `stage_after`, once listed | n/a |
| high_stakes | stays home for good (today: leaves and lands pending) | stale date: 7 days after the window | yes |
| doubt | stays home for good | stale date: 14 days | no (destination unsettled) |
| unclassified (doubt line) | stays home until its owner classifies | never on its own (the gate) | no |
| router question (low confidence) | stays in the workspace (as today) | stale date: 14 days | no |
| request | expires unanswered (as today, 24 hours) | as today | n/a |
| mint | the space is made on its date (as today) | as today | n/a |
| candidate: entity, status, timeline page | stays a draft, usable once grounded (3.9) | at once | n/a |
| candidate: policy or decision page | stays a draft, not used as policy | stale date: 30 days | n/a |
| candidate: landing pending for a person | stays pending at the destination (as today) | leaves the list at its stale date, 14 days | n/a |
| question, factual | the resolver answers it, else its stated default | stale date: its deadline, else 14 days | n/a |
| question, permission | its stated default (always the conservative one) | stale date: its deadline, else 14 days | n/a |
| check | the page stays as it is | stale date: 14 days | n/a |
| sample | counted as fine | stale date: 7 days | n/a |
| tracker | nothing is created | stale date: 14 days | n/a |
| outbox | archived silently (as today) | 14 days (as today) | n/a |
| reconnect | the feed stays off | never closes; reported weekly | n/a |
| protected_pr | it waits | never closes; reported weekly | n/a |
| template release | the space stays on its release | never closes; reported weekly | n/a |

The numbers are settings (6.3).

### 3.2 Impact rank, grouping and the shape of an ask

Every item gets `impact: critical | high | medium | low`, judged by the agent that writes or
first reads the item:

- **critical**: the default does harm that cannot be undone within a day. Money or a legal
  commitment, a security exposure, a client deadline within 48 hours, or a person blocked
  right now.
- **high**: the default costs real work, or a commitment, this week.
- **medium**: worth a decision, but a week's delay costs little.
- **low**: housekeeping; the default is fine.

The rank is the agent's judgement, but every fact it leans on is tool-checked before it
counts. A date, a deadline, an amount or a person waiting has to appear in the item's record
(`ground.py` resolves the record; a plain substring check on the cited value). If a cited fact
is missing, the rank drops one level and the item notes why. The agent writes `impact_why`
with the checked facts.

Related items become one decision. The group key is (subject, kind), so
"Harbor: 12 high-stakes records, I recommend keeping the 9 history ones home and sending the
3 current ones" is one ask. Each member keeps its own recommendation. **Yes to all
recommendations** applies each member's own. A group's code covers the member ids and their
fingerprints (section 4.3).

Items that need nothing (`silence_ok: true`) are never pushed and appear only as counts.

Every pushed ask carries four short lines, at most 45 words in all:

1. the recommendation and its reason ("I recommend yes: the client confirmed the date in
   writing on 14 October");
2. why you ("you own Harbor");
3. why now ("the go-live mail goes out Thursday");
4. if ignored ("it stays home and Harbor's crew sees only that a record exists").

### 3.3 Budget: pushed asks only

- **Three a day by default**, shared across every channel. The setting is per person, in that
  person's workspace.
- **Adaptive.** Once a scaled week the budget moves by one. It goes up when every pushed ask
  of the week was cleared within a day and items with `silence_ok: false` still settled
  unasked. It goes down when half or more of the pushed asks sat untouched for two days. The
  floor is 1 and the ceiling is 6. A person who sets a fixed number turns the adjustment off.
- **Critical items bypass it.** They are pushed even when the day's budget is spent. They
  count in the day's total, so the health line shows them.
- **Pulled use is unlimited.** "What needs me", `/sign` and the catch-up session (section
  3.10) show everything, ordered by impact, and none of it counts against the budget.
- **When the real inflow exceeds the budget**, the health line says so: "7 items a day needed
  a decision, budget 3: 4 settled to their default". The fix is better defaults and standing
  choices, not a bigger budget. The weekly summary names the kind that overflows most and
  proposes the standing choice or default change that would absorb it.

**Selection runs over the person's one list, never per space.** Today each space the person
owns on the machine gets its own daily chain and its own `live/needs-you.md`
(`chain.spaces_on_machine`). The briefing goes out from the workspace only, and it shows a
mounted space's items only when they are routed to the person. Under this spec the
workspace's morning view selects from its own `collect()` plus the `collect()` of every space
on the machine that the person owns. The candidates are open items with `silence_ok: false`.
They are ranked by impact, then stale date, then how much depends on the answer, then kind
order (today's `order()`), and the first ones up to the budget are pushed. An ask that was
pushed and not answered keeps its place, but it is not pushed again for 48 hours; an
unanswered ask must not crowd out the next one forever.

A ledger records every push, one line per push: `_meta/state/asks.jsonl` in the person's
workspace, with `{at, item, group, channel, impact, code}`. The ledger is the budget. When the
same ask shows in two channels on one day, it counts once.

### 3.4 Resolving from evidence

The resolver is an incremental pass by subject. It visits only open items whose subject
received a new record since its watermark, `_meta/state/resolve.yaml` (per subject: the
newest record id and time it has read).

- **Factual questions close** with the answering record cited. Example: "Is the Harbor
  statement of work signed?" closes on the e-signature service's completion mail. The item
  records `resolved_by: <record id>` and one line of what the record says.
- **Superseded items close as superseded.** A newer item on the same subject and kind replaces
  an older one: a later draft of the same agreement, a newer check on the same page. The old
  item records `superseded_by: <id>`.
- **Recommendations update.** When new evidence changes what the agent would recommend, it
  rewrites `recommendation`, its reason and `impact`, stamping `revised_at`. The fingerprint
  changes with them, so an ask already sent with the old code is asked back, never applied
  (section 4.3).
- **Only facts, never permissions.** The resolver never answers a yes to send, sign, delete,
  mint, merge, classify, widen an audience or spend. Such an item can be superseded, or have
  its recommendation updated, but it is never answered. Every item carries
  `resolvable: fact | permission`, set when it is written. Questions say which in their own
  frontmatter, checks and samples are facts, and every other kind is a permission.
- **A second agent checks each resolution** before it lands, the same floor as judged gates:
  is the record about this subject, does it answer this question, is it newer than the
  question. The resolution carries `checked_by`. A disagreement leaves the item open, with a
  note.
- **Visible and reversible.** The morning email counts "N answered themselves". The weekly
  summary lists each one with its record. **Reopen <n>** puts an item back open, with the
  resolution struck and logged. A reopen counts as a disagreement in the resolver's agreement
  rate (3.5) and becomes a lesson (3.6).

Each resolution writes one `log.md` line:
`- <ISO> resolved <id> by resolver from <record id> checked_by <name>`, then
`- <ISO> reopen <id> by <name>` when undone.

### 3.5 Earned trust

Every verdict a person gives on an item that had a recommendation adds one line to
`_meta/state/agreement.jsonl`: `{at, kind, pattern, id, recommended, given, agreed}`. The
pattern is (kind, destination, subject kind). The agreement rate per kind and per pattern is
taken over the last N verdicts. The defaults, both settings, are N = 20 and a threshold of
90%. The resolver's resolutions are a kind of their own: `resolver`, where a reopen means it
disagreed.

The track record gates two things, and nothing else:

1. **Settling itself.** Where an item's recommendation differs from its default and the
   recommendation is not a permission, silence applies the recommendation instead of the
   default, but only for a kind whose rate meets the threshold over at least N verdicts.
   In practice that covers factual questions (the recommended answer is recorded as the
   agent's, marked "settled by track record"), checks, and samples. A permission never settles itself.
2. **Proposing "always".** See 3.6.

Below the threshold or without N verdicts, silence means the default, always. The rate per
kind shows on the health page (3.12).

### 3.6 Learning

- **Always, on an earned record.** When a pattern meets the threshold over N verdicts, the
  agent proposes "always" for it once, as an ask (medium impact, inside the budget, at most
  one such proposal a week). A yes writes the standing choice through today's mechanism
  (`standing_choices` in `space.yaml`, `{destination, kind}`). A no is remembered, and the
  pattern is not proposed again for 90 days.
- **What a standing choice does.** Today it only makes yes the recommendation: `sweep.classify`
  reads it when routing is off, `route.py` ignores it, and an item still waits for its person
  (`sweep.py:344-351`; judged gates, section 6). Under this spec, an item that matches a
  standing choice takes its recommendation on silence when its window ends, instead of its
  default, but only if an agent judged it: a routed decision with `checked_by`. This is the
  only way a permission settles without an answer, and only because the person said "always"
  for that pattern. Without the router nobody read the record, so a standing choice keeps
  today's meaning there: yes becomes the recommendation, and the item still waits (judged
  gates, section 6). The window, the listing and pull-back all still apply. The weekly summary
  counts what each standing choice let through, and "never <n>" there removes the choice.
- **Corrections become lessons.** Every `edit` or `no` with a reason, and every reopen, adds
  one line to `_meta/lessons.md`, in the format of the sign skill's step 6. Today only the
  session agent appends there, and no tool writes it. Email replies count too: the sweep
  chain's agent writes the lesson from the reply's note, marked `(from: email reply)`. The
  router (`route.pending`), the brief (`brief.lessons_for`) and distill read the lessons
  today, and the ranker and resolver will read them too.

### 3.7 Staleness

Every item carries `stale_after`: its deadline when it has one, else the kind's number from
3.1, counted from when the item was written. At that date the item closes to its default and
logs `- <ISO> stale <id> default "<default>"`. The weekly summary reports it with a way back.
An item with no stale date (a reconnect, a protected pull request, a template release) never
closes on its own, and the weekly summary reports it instead.

### 3.8 The weekly "what I decided for you" summary

Once a scaled week, in the daily chain, one email and one page (`live/decided-for-you.md`).
It lists everything the queue closed without a person in the past week, grouped:

- **Answered themselves**: each with the record that answered it.
- **Settled by track record**: each with the rate that allowed it.
- **Gone after the window**: what left, grouped by destination and subject, with counts.
- **Closed as stale**: each with the default that applied.
- **Held by you**: each hold from the morning email.
- **Let through by a standing choice**: counts per choice.
- **Still open, never closes**: reconnects, protected pull requests, template releases.

Every line is numbered and carries its reverse:

- **reopen <n>** for anything resolved, settled or stale;
- **pull back <n>** for anything that left, through today's retraction ("left N no" in
  `reply.py`, with the four-week pause of the path);
- **release <n>** for a hold;
- **never <n>** for a standing choice. The email holds the counts and the five
highest-impact lines; the page holds the full numbered list, and a reply binds against the
page's numbering, which is stored with the email's message id. An optional last line offers
the catch-up: "12 decisions are waiting, about a minute each; type catch me up in a session".

### 3.9 Signing as an upgrade

**What query reads today.** `ground.py` (`tier_of`) gives every page a tier, and every
grounding line carries its tier and age (`.claude/skills/query/SKILL.md`, steps 4 and 6):

| Page | Tier |
|---|---|
| `status: stable` | `signed` |
| `knowledge/sources/` from a tracker, a repository or sent mail | `system` |
| other `knowledge/sources/` | `witness`, shown as "unverified" |
| anything under `_inbound/`, a candidate included | `pending` |
| `live/` | `reported` |
| a `status: draft` page under `knowledge/<kind>/` | `working` |

The walk reads the candidates of a subject's home. Search covers `knowledge/` and `live/` only,
so it never finds a candidate. The skill forbids stating a witness or working line as fact.

**What status reads today.** `status.collect` renders `live/subjects/<id>.md` from witness
and system pages, from candidates (open items under "Waiting for a signature"), from
`knowledge/` pages (signed ones close items, drafts go to the timeline), and from desk
folders. It writes only `status: draft` snapshots, never `stable`. Needs you offers every
candidate as "sign?", with the default "stays a draft".

What changes:

- **Grounded drafts.** A candidate is a grounded draft when it passed distill's `verify`
  (sources resolve, evidence cited, no placeholders) and the page check (a second agent cleared
  it for never-leaves and left-out matter). Distill records that on the page as
  `grounded: {at, checked_by}`.
- **A new tier, `draft`.** `ground.py` gives a grounded draft tier `draft`, between `signed`
  and `pending`. An answer may rest on a `draft` line, as it rests on a signed one, with the
  marker "(draft, not signed)". Search adds grounded drafts under `_inbound/candidates/`. A
  candidate that is not grounded keeps tier `pending`, and is never the only support for an
  answer.
- **Status pages quote grounded drafts** as current state, with the same marker. "Waiting for
  a signature" lists only the pages that need one.
- **Signing is required only for policy and decision pages** (`type: policy`,
  `type: decision`, and the charter). None of them is used as policy, or as a decision in
  force, until signed. `decisions.py` reads signed decisions only, as today.
- **Other candidates stop being asks.** In pulled use they show as one count, "N drafts you
  could sign", and signing them stays one verb in `/sign`.
- **The agent never writes `status: stable`.** Only `sign.py apply`, on the person's
  `submit`, does.

### 3.10 Backfill catch-up

After a backfill (or the first week of a new space), the person gets one guided session of
about 20 minutes, offered once in the morning email and at session start, and started by
"catch me up". The agent walks the grouped decisions by impact, one at a time, a
recommendation first. It stops offering at 20 minutes or when the critical and high groups
are done, whichever comes first, and the person can carry on. The session is pulled use and is
never counted in the budget. Whatever is skipped settles to its default at `catch_up_until`,
7 days after the backfill finished by default, and the weekly summary lists it.

### 3.11 The right person, and away mode

- **Who is asked.** The person today's code already picks, now written on the item as `owner`:
  - `Registry.owner_of` for most items: the subject's owner, then the nearest ancestor's, then
    the space owner;
  - `landing.domain_owner` for a high-stakes landing;
  - `check.py`'s order for a check: signer, subject owner, domain owner, space owner.
  
  `needs_you.routed_items` already brings each person their items from the spaces they mount.
  Nothing about who is asked changes, except that it is written down on the item.
- **Away.** Nothing reads absence today. The calendar is only a feed of witness pages and
  `live/today.md`, and the only automated-mail filter is the no-reply sender. A person counts as
  away when any of these covers the day, checked in this order:
  1. an out-of-office event in their calendar feed (the calendar adapter keeps the event's
     type);
  2. their mailbox's automatic-reply setting, where the mail connector exposes it;
  3. an automatic reply in their sent mail;
  4. the person saying so ("I'm away until the 30th").

  The first three are written by the sweep's adapters, the last by the session. Each goes to
  `_meta/state/private/away.yaml` as `{from, until, source}`. A connector that exposes none of
  the mail signals leaves the calendar and the person's word, and the health page names which
  signals are live. While someone is away:
  - their pushed asks pause; the morning email still goes, without asks, so holds stay
    possible;
  - their items' stale dates move by the days away, so nothing closes to its default behind
    their back (release windows do not move: those items are low risk);
  - critical items go to a **stand-in**, the per-person setting `stand_in`, by default the
    company owner. The stand-in gets the ask in their own channels, marked "for <person>, away
    until <date>", and their verdict is logged `by <stand-in> for <person>`. This extends the
    one stand-in that exists today: in classification, the company owner decides for a holder
    who has no workspace (`classify.decider` returns `stand_in`, and the verdict records
    `for`). No away, deputy or out-of-office concept exists anywhere else yet.
- **Privacy holds for the stand-in.** If the stand-in is not in the item's audience (a walled
  project they are not crew on), they get only the fact line and the deadline: "<person> is
  away; a critical <kind> about <subject> needs a decision by <date>". They can choose to wait
  or reach the person; they never see the content.
- If the stand-in is away too, or is the same person, the critical item becomes the first ask
  on return, and the health line flags it every day until then.

### 3.12 Health

The health page (`live/health.md`, written by `status.run`, already quoted by the morning
email) gains three numbers per person. Today it carries runs, cost, the checker's hit rate,
held in doubt, open questions, candidates, and cross-space requests, but no rate of asks. The
morning email carries one line of the new numbers:

- **asks per day**: pushed asks over the last 7 days, by channel, from the ledger;
- **minutes spent**: in sessions, the time from the first ask shown to the last answer or
  `submit`; by email, 30 seconds per answered ask (a stated estimate, not a measurement);
- **agreement rate**: per kind, from 3.5.

The target is three or fewer asks a day. The health line turns into a warning when the 7-day
average is above the budget, or when the agreement rate of any kind falls below 70% (the
recommendations are wrong and need a lesson, not more asks).

## 4. Where it is asked

### 4.1 Claude Code

- **Session start: one line.** It shows the budget, not the backlog: "Needs you: 2 asks today,
  nothing critical (41 waiting, all with a safe default)." This replaces the session-start
  call to `needs_you.py` with `needs_you.py --line`.
- **Asks in context, one at a time.** When the session touches a subject with an open ask
  (the person asks about it, or the agent works on it), the agent puts that one ask then,
  with its four lines, and takes the answer. An ask already pushed today does not count again.
  The session never opens with a list.
- **Pulled use**, unlimited: "what needs me", `/sign`, "catch me up".

### 4.2 The morning email

The morning email, in this order:

1. **Up to three asks** (the budget), each with its code and four lines.
2. **What leaves tonight**, grouped by subject: "Harbor: 6 updates leave for the Harbor crew at
   18:00". Each group carries **hold <subject>**, and each numbered line **hold <n>**. Silence
   lets it go. That is today's rule: a window item ships only after a morning view listed it.
   A hold is today's `no` on a leaving item. The item moves to `_outbound/held/` with reason
   `pulled` and never leaves by itself. Today, pulled items appear nowhere afterwards. Under
   this spec, the weekly summary lists them, and **release <n>** there puts one back in the
   queue with a fresh window.
3. **The waiting count by impact**: "Waiting, all with a safe default: 0 critical, 2 high, 9
   medium, 30 low."
4. **Closed without you**: answered themselves, settled, and closed as stale, as counts.
5. **One health line.**

**This ends the counts-only briefing.** Today the email carries counts with plain labels, and
"never a title, name, date or path from the items counted" (`.claude/skills/morning-view/SKILL.md`).
The only exception is the unsure records, listed by code. The numbered list is on
`live/needs-you.md`, not in the email. Under this spec the email carries the asks and the
leaving groups by name, within these limits:

- it goes only from the person's own mailbox to their own address (as today);
- an ask's text is its four lines, written for that person, never a record's wording quoted;
- nothing from a space the person cannot read, and nothing never-leaves (a stand-in sees
  only fact lines, 3.11);
- the plain-language check still runs, with no paths, links or ids.

The briefing's word limit rises from 200 to 350 words for `kind: briefing` only
(`owner_email.LIMITS`). Every other notice keeps 200. Codes are short tokens like today's
unsure codes (`3fa2c`), so they pass the check, which refuses ids and words like
"fingerprint" in the text.

### 4.3 A reply binds by itself, safely

A reply from the person's own mailbox binds by itself for: question answers, leaving items
(holds), reopens, pull-backs, and grouped decisions ("yes to all recommendations"; a single
item is a group of one). The
read-back plus `submit` stays only for **signing page text**: a yes on a candidate, or an
`edit` with a corrected body. Those arrive as an `answer` item, as today.

Today `reply.py` accepts a record whose `from` contains the person's address and whose `to`
contains it (substring checks), and whose subject matches `Re: morning briefing`. It maps
numbers to the list of the day **the reply was sent** (`_meta/state/morning/<date>.json`),
writes an `answer` item, and the sign skill reads it back and waits for `submit`
(`.claude/skills/sign/SKILL.md`, "a reply never signs by itself"). A reply sent the next
morning binds to the wrong day's numbering, and the email itself shows counts, not numbered
items.

The rules for a binding reply. Each is a refusal, never a guess:

1. **Verified sender.** The `from` address, parsed, equals the person's `owner_email` (or an
   address listed in `owner_emails`) exactly, and the record shows the person's own mailbox
   sent it: the mail adapter's `sent` label, or an authentication result that passes for the
   address's domain. A message that only claims the address is ignored and noted on the health
   page.
2. **Thread match.** The reply's thread or `In-Reply-To` names the briefing.
   - Today only the message id is recorded (`morning_view.py record-send --id`, which goes
     into `runs.jsonl`).
   - `record-send` gains `--thread`, and the mail adapter keeps each record's thread id and
     `In-Reply-To`.
   - The listing is stored by message id (`_meta/state/morning/<message id>.json`) and never
     looked up by the reply's date.
   - A reply that names no recorded briefing binds nothing and is asked back.
3. **Codes, ids and fingerprints.** Each ask, group and leaving line in the email carries a
   code. The listing maps each code to the item ids and their fingerprints at send time. At
   bind time, every item's current fingerprint must still match. If one changed (a
   recommendation updated, an item resolved), that clause is asked back, not applied.
4. **Unambiguous parse.** Each clause parses to target(s), a verb, and an optional note,
   using today's `CLAUSE` grammar plus `hold`, `reopen`, `pull back`, `catch up` and codes.
   Targets are the email's own numbers or codes. `all` means only the asks in that email.
   These are refused: a target named twice with different verbs, a verb that does not apply
   to the target's kind, an unknown word in a clause, and quoted text.
5. **Anything unclear is asked back.** One owner email, to the person's own address only:
   "I applied 1 yes and hold Harbor. I could not read: 'maybe the second'. Nothing else
   changed." Ask-backs never count in the budget.
6. **Receipt.** Every bound verdict is one `log.md` line,
   `- <ISO> <verb> <id> by <name> via email <message id>`, and the next morning email opens
   with one line: "Yesterday's reply: 1 yes, Harbor held."

`sign.py apply` takes `--via email:<message id>`, and refuses a page-text signature (a
candidate's yes or edit) arriving that way.

### 4.4 The weekly summary

Section 3.8, sent in the daily chain once a scaled week, with the optional catch-up line.

### 4.5 Later, optional: the channel interface only

A channel is anything that pushes asks to a person and reads answers back. This spec defines
only the interface. The Slack bot and voice are not built here.

A channel must:

- `render(asks, leaving, counts, health) -> message`, using the same codes as the email;
- `verify(reply) -> sender_ok, thread_ok`, with rules equivalent to 4.3, items 1 and 2;
- `parse(reply) -> [Clause(target, verb, note)]`, with the grammar and refusals of 4.3,
  items 3 and 4;
- record every push in the ledger, so the budget stays shared;
- reach only the person (or their stand-in, under 3.11).

Two candidates, for later:

- **A Slack bot**: a bot account and an endpoint, or a no-server variant that posts the asks
  in the person's own direct message with the bot and reads thread replies through the
  sweep's Slack feed.
- **Voice**: a scheduled call. Nothing irreversible binds on voice alone; voice binds holds
  and factual answers, and anything else becomes an answer item to confirm in text. No
  confidential content is read aloud: walled and never-leaves content never, and high-stakes
  items by kind and date only.

**Not channels:** the tracker (it tracks work, it never asks for verdicts) and a separate web
page (the onboarding page shows counts and links, read only).

## 5. Background: every step on an existing run

Nothing new is scheduled. Each new step is a step in a chain that already exists
(`_meta/tools/chain.py`).

| Run | Today | Added |
|---|---|---|
| 15-minute sync | `sync.py --release-leg`: `release.ship` sends listed window items once their time has passed | ships the fact lines of 3.1 (ordinary queue items) |
| Sweep chain, 3 times a day | `transcripts.scan`, `sweep.run`, `reply.run`, `distill.run`, `check.run`, `tracker.observe` | after distill: `rank` (new items only), `resolve` (incremental by subject, with its check), `settle` (stale dates, track-record settling); `reply.run` binds under 4.3; the away record from the adapters |
| Daily chain, once a day | `template_sync` (guarded), `status.run` with landing, `access.run`, `request.collect`, `request.answer`, `release.ship`, `outbox_reconcile.run`, `morning_view.run`, the answer and leak tests | `status.run` writes the health numbers; `morning_view.run` selects the asks within the budget and renders the new order; once a scaled week: the summary and the budget adjustment |

The sweep chain and the daily chain are paused in the pilot owner's install. Pausing happens
in the scheduler; there is no pause flag. Unpausing them stays the owner's decision. Nothing here assumes it, but until they run, nothing settles, resolves or leaves
by its window, and the health page says so.

**Token cost bound per run.** Only `rank` and `resolve` (with its check) need a model. Every
other step is a tool and costs no tokens; the email and the weekly page are rendered from
fields written earlier.

- `rank`: only items written since the last run, at most 50 a run, about 500 tokens each (the
  item, its why, the subject's one-line brief, the lessons that name the subject). Bound:
  **25k tokens a run.** Items beyond 50 wait unranked for the next run. An unranked item is
  never pushed, so it is safe.
- `resolve`: at most 10 subjects a run, 5 open resolvable items and 5 new records per subject,
  a record read as its one-line summary plus at most 1,500 tokens of excerpt. Bound: **60k
  tokens a run**. The check reads only the proposed resolution and its record, at most 20 a
  run and 1k each: **20k tokens**.
- **The ceiling per sweep-chain run is 105k tokens**, enforced as a setting
  (`needs_you.tokens_per_run`). Reaching it stops the step and writes a health note. A typical
  run has 0 to 3 subjects with new records and open factual items, so it costs under 10k.

## 6. Data and files

### 6.1 Fields on every item

`needs_you.Item` gains: `impact`, `impact_why`, `silence_ok`, `silence_why`, `resolvable`,
`stale_after`, `group`, `subject`, `owner` (the person asked), `why_you`, `why_now`,
`if_ignored`, `reason` (for the recommendation), `state` (`open`, `asked`, `answered`,
`resolved`, `superseded`, `settled`, `stale`, `reopened`), and `code`. Routine items in
`_inbound/needs-you/*.yaml` may set any of them. Derived items get them from the ranker,
which writes them to `_meta/state/needs-you/ranks.yaml` (one writer), keyed by id and
fingerprint so a changed item is ranked again.

### 6.2 State, one writer each

- `_meta/state/asks.jsonl`: the push ledger (morning view and session asks).
- `_meta/state/agreement.jsonl`: verdicts against recommendations (`sign.py`, `reply.py`).
- `_meta/state/resolve.yaml`: resolver watermarks.
- `_meta/state/needs-you/ranks.yaml`: ranks and ask fields.
- `_meta/state/morning/<message id>.json`: the listing a reply binds to, replacing the
  per-date file.
- `_meta/state/weekly/<message id>.json`: the weekly summary's numbering.
- `_meta/state/private/away.yaml`: away periods (private zone, never staged).

### 6.3 Settings, in the person's workspace `space.yaml`

```yaml
needs_you:
  budget: 3              # pushed asks a day; a number here turns adaptation off
  adaptive: true
  budget_min: 1
  budget_max: 6
  stand_in: null         # default: the company owner
  trust: {window: 20, threshold: 0.9}
  stale_days: {question: 14, check: 14, sample: 7, doubt: 14, tracker: 14,
               high_stakes_after_window: 7, policy_candidate: 30, landing: 14}
  catch_up_days: 7
  tokens_per_run: 105000
owner_emails: []         # other addresses of the same person that may reply
```

The health page gains the three health numbers and the agreement rate per kind (3.12).

## 7. Migration: the pilot owner's 365 items

Measured with `needs_you.py --json` on 18 October:

| Today | Items | After | Asks |
|---|---|---|---|
| leaving | 127 lines: 116 items for three destinations, and 11 first-release lines (one per newly classified subject) | about 14 "leaves tonight" lines, grouped by subject; all gone once their windows close on 18 and 19 October | 0 |
| high stakes, already approved by the owner | 97 | leave Needs you. They wait only for a morning view to list them, and the daily chain is paused. Today they are asked again, because `_queue` lists a high-stakes item whether or not it carries `approved_by` | 0 |
| high stakes, not approved | 98 | 12 subject groups. 84 are first releases of newly classified subjects. By record date, 65 are older than 60 days, 25 are 15 to 60 days old, and 8 are 14 days old or less (in 3 subjects). History stays home with a fact line | 3 groups at most (the recent ones), in the catch-up |
| candidates: entity pages | 20 | grounded drafts, usable at once; one count in pulled use | 0 |
| candidates: landings pending for the person, all flagged high stakes | 9 | one group per subject, in the catch-up; stay pending on silence | 2 to 3 groups |
| questions | 9 | 2 test questions become counts; 2 deletion questions keep their deadline and safe default; 3 setup questions for one new project become one group; 1 visibility question (safe default: stays private); 1 mount fault resolves from evidence when the mount check passes | 2 to 3 |
| samples | 3 | counts; counted as fine after 7 days | 0 |
| protected pull request | 1 | one ask (medium) | 1 |
| held in doubt (11 pages) | 1 line | stay home; a count | 0 |

The result: from 365 lines to **about 8 to 12 grouped decisions** for the one catch-up
session, and **three or fewer pushed asks a day** after it. Everything else settles at its
date, and the first weekly summary reports it with a way back.

The conversion runs once, in the pilot workspace, after the template sync lands the code.
It ranks every open item (a one-time cost of about 365 × 500 tokens, under 200k), writes
`ranks.yaml`, and prints the table above from the live data, so the owner sees the real
numbers before anything settles. It changes no item's outcome on its own. Stale dates for
existing items start on the day of the conversion, not the day the item was written, so
nothing closes the moment the code lands.

One ordering matters. If the daily chain is unpaused before this lands, the 98 unapproved
high-stakes items ship under today's rule once listed, and land pending for their domain
owner at the destination. That is today's behaviour, and the content stays pending there, not
filed. Unpausing first or landing this first is the owner's choice, and the conversion table
shows which items it affects.

## 8. Acceptance

Two kinds of proof, both run in the template's checks with no model. The agent's judgements
(ranks, `silence_ok`, resolutions and their checks) come from a fixture file,
`_meta/tests/fixture/judgements.yaml`, the way `verdicts.yaml` supplies the person's answers
today.

### 8.1 A simulated week in the fixture company

`python _meta/tools/chain.py virtual --days 7 --roots <brain> <ada> <ben> <chloe>` on the
fixture's fast clock (`time_scale: 1440`), with fixture feeds that add, during the week: a
burst of 30 items for one person on day one, a factual question and, on day four, the record
that answers it, a high-stakes record, and an out-of-office event for one person on days 5
to 7. No verdicts are applied. It passes when:

- **pushed asks**: the ledger holds three or fewer pushed asks per person per day, excluding
  critical items, and the burst does not raise it;
- **nothing leaks when nothing is answered**: `leaks.yaml` passes across all spaces, and no
  high-stakes, doubt, unclassified or never-leaves item reached any destination other than as
  a fact line with no wording;
- **evidence closes a fact**: the factual question is `resolved` on day four, with the
  answering record's id and `checked_by`;
- **stale closes to its default**: a question with no answer closes `stale` at its date, and
  its default is logged;
- **away mode**: a critical item for the away person lands in the stand-in's ledger and
  email payload, marked "for <person>", and none of the away person's asks are pushed on
  days 5 to 7.

### 8.2 Behaviour scenarios (`_meta/tests/behaviour/`)

- `needs-you-budget.yaml`: 12 open items with `silence_ok: false` and one critical item. The
  morning view pushes exactly 3 plus the critical one, and writes 4 ledger lines.
- `reply-binds.yaml`: a reply with "1 yes, hold Harbor, 3fa2c no" binds exactly those items,
  and these do not bind: a reply from another sender with the same text, a reply to another
  day's message id, a code whose item's fingerprint changed (that clause is asked back), and a
  yes on a candidate (it becomes an `answer` item, never signed).
- `resolve-fact.yaml`: a factual question and its answering record close it, with
  `resolved_by` and `checked_by`; the same record never answers a permission question.
- `stale-default.yaml`: an item past `stale_after` closes to its default with one log line,
  and appears in the weekly page with "reopen <n>"; reopening restores it.
- `away-stand-in.yaml`: with `away.yaml` covering today, a critical item for a walled
  project goes to the stand-in as a fact line only (the stand-in is not crew), and a medium
  item is not pushed.
- `needs-you-approved.yaml`: a high-stakes queue item with `approved_by` is not on the list.

## 9. Rollout

**Build order.** Five phases. Each one gets its own plan and ships by itself, and each leaves
the queue lighter than the one before:

1. **Silence and budget.** Per-kind defaults and stale dates, the approved high-stakes fix,
   the impact rank and `silence_ok`, grouping, the ledger and budget, the session-start line,
   and the new morning email order. This alone ends the daily habit.
2. **Safe replies.** Codes, the listing by message id, thread and sender checks, binding
   without a read-back, ask-backs, and `sign.py --via`.
3. **Evidence and the weekly summary.** The resolver and its check, superseded items,
   reopen, the weekly summary and page, release and pull back from it.
4. **Trust and drafts.** The agreement ledger, settling by track record, "always" proposals and
   their new meaning, the `draft` tier, grounded drafts in answers and status pages, and the
   catch-up session.
5. **Away mode.** The away record and its sources, paused asks, moved stale dates, the stand-in
   and its privacy rule.

The fixture week (8.1) grows with each phase; each phase's own scenarios (8.2) pass before the
next phase starts.

**Where it lands.**

1. Template: `needs_you.py`, `morning_view.py`, `reply.py`, `sign.py` (`--via`, the agreement
   lines), `owner_email.py` (the briefing limit), `status.py` (health numbers), `chain.py`
   (the new steps), the sweep, sign, morning-view, query and status skills, the CLAUDE.md
   session-start line, `docs/decisions.md`, and the tests above.
2. Synced to every space by the template sync (one release under the releases design).
3. The pilot workspace runs the conversion of section 7, and the owner reads its table before
   unpausing anything.

## 10. Decisions this changes

New rows in `docs/decisions.md`, each naming the row it amends:

- **2026-10-12 Answering by email reply.** A reply binds by itself for everything but
  signing page text (4.3). The read-back and the explicit `submit` stay the signing contract.
- **2026-10-12 High-stakes window items are listed.** They are still listed, but they leave
  only with an approval or a standing choice. On silence they stay home, and a fact line goes
  instead (3.1).
- **2026-10-13 Left yesterday names its path and pulls back by reply.** `left <n> no` and
  `pull back <n>` bind without a read-back.
- **Standing choices** (sign skill, judged gates section 6). A standing choice settles on
  silence for routed, checked decisions that match it (3.6). Without the router it still only
  sets the recommendation.
- **The morning view's counts-only rule** (morning-view skill). The email names asks and
  leaving groups, within 4.2's limits.

Kept as they are: "High stakes never skip the window" (2026-10-13), "No and silence"
(2026-10-12), "One list per person" (2026-10-12), and the classification gate (2026-10-16).

## 11. Risks accepted

- **A wrong default.** A risky item stays home when it should have gone, and its destination
  learns late. The fact line and the weekly summary bound how late.
- **A wrong resolution.** Two agents agree on a wrong answer to a factual question. It is
  visible in the email count and the weekly list, and one reply reopens it. It never grants a
  permission.
- **A compromised mailbox.** It can bind holds and answers as the person. It cannot sign page
  text, and every binding is logged with the message id. That is the same trust the person's
  mailbox has today for pulling back what left.
- **The estimate of minutes spent by email** is an estimate, and the health page says so.
