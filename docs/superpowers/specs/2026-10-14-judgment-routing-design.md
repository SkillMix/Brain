# Lossless capture and judgment routing: design

Status: approved by the owner, 2026-10-14 (section 7 records his answers). Date: 2026-10-14.
Replaces: the decision "evidence attaches by `match:` identifiers only; mentions are hints"
(docs/decisions.md, 2026-10-11 and 2026-10-13), the release-rule limit that only standing choices
and already-covered audiences go at once (rules.md section 6), and the export caps of 2026-10-14
(8,000 characters, call summaries without the transcript).

## 1. Why

A colleague who joined a client project today found its status page nearly empty. The owner's
workspace holds 55 witness pages about that project. Before the 2026-10-14 patch 5 were tied to the
project's subject; after it re-checked the older pages, 13 are: calendar 3 of 3, calls 4 of 7,
chat 3 of 13, mail 2 of 5, requests 1 of 1, session notes 0 of 26 (the largest untouched group).
Tied is not delivered: a tied page still has to pass the release rule. Two causes, both
structural:

- **Capture is lossy.** Mail arrives as a 200-character preview, documents as an excerpt, calls as
  a summary, calendar entries without their description, attachments not at all. What is not
  captured cannot be routed later.
- **Routing is mechanical.** A record reaches a space only when it carries an exact identifier
  (a chat channel, a tracker project, a repository). Calls, unlabelled mail and calendar entries
  carry none, so they stay in the workspace whatever they are about. The 2026-10-14 patch
  (mail domain and meeting-title keywords) widens the net but is still a lookup, not judgment.

The owner's direction, in his words: an agent should "read everything, from FULL mails, to the
transcripts", "decide on its own whether it should go to a specific space or whether it stays in
the workspace", "go in depth", because "we can't afford losing information".

## 2. The owner's decisions (2026-10-14, given in the alignment round)

1. **Capture is lossless.** Full mail bodies, full call transcripts with speakers, full document
   text, attachment text, chat threads with their files and AI notes (huddle notes, canvases),
   calendar descriptions. No character caps. Witness pages stay private to the workspace.
2. **The agent picks the tier per record.** Ordinary project work (a client writing to the team
   about technical matters) goes at once. Anything more sensitive waits the 24-hour veto window
   and shows in the morning view with its reason. Never-leaves terms stay a hard stop; a route the
   agent is not sure of becomes a question on Needs you.
3. **Raw content travels, not summaries.** A record about one topic whose people are all inside
   the destination's audience (its crew, and the client's own people for a client project) goes
   whole. A record with several topics sends each destination its relevant parts verbatim: whole
   transcript segments, whole mail passages. A summary never replaces the original; the agent may
   add a one-line note on why the part is there.
4. **Personal and academic records are not stored, only counted.** The reader classifies at
   capture; those records never enter the workspace repository; the run line counts them.
5. **Backfill depth.** A person's first run reads 90 days. A project gets everything back to its
   first mention.
6. **Mail attachments** are read without downloading files: the mail connector's attachment
   reader where the session has one, else the attachment's preview in the mail web app through
   the browser; where both fail, a once-a-day batch the owner approves.
7. **The veto window is 24 hours** (the default `window_hours`).
8. The 2026-10-14 patch (template PR 37) stays as a step forward; this design replaces its
   transcript ban and caps.

## 3. Approaches considered

- **A. Agent judgment, tool-enforced gates (recommended).** An agent reads each record in full
  with the registry and the destination pages and writes a routing decision; a deterministic
  tool (`route.py`) validates it, applies the gates, writes the projections and the log. This is
  the pattern the space already uses for the tracker and for signing: judgment in the agent,
  writes and rules in a tool that tests can run without a model.
- **B. Agent writes queue items directly.** Fewer moving parts, but no deterministic gate between
  a model's choice and a release, nothing a test can pin, and no single log of decisions.
- **C. Smarter matching without an agent** (scored mentions, domains, keywords). Cheap and
  testable, but it is what failed: it cannot tell a client's technical question from a pricing
  aside in the same thread, and the owner asked for judgment.

## 4. Design

### 4.1 Capture (the export step)

The sweep skill's export becomes lossless. Per kind:

| Kind | One record is | `text` holds |
|---|---|---|
| mail | one message (its `thread` groups the conversation) | the full body, quoted history included |
| call | one meeting | the recorder's summary, action items and key points, then the full transcript, one line per utterance: `[hh:mm:ss] Speaker: words` |
| message | one chat thread (root and replies) | every message with author and time; files read with the chat connector's file reader; AI notes and canvases as their own `document` records |
| event | one calendar entry | the description; guests as `participants` |
| document | one file version, or one attachment (`<message id>/<file name>`) | the full text |

- **Private at the door.** Before writing a record the reader decides `company`, `personal`,
  `academic` or `unsure`. Only `company` records are written to the feed; `personal` and
  `academic` are counted in the run line (`notes.skipped: {personal: n, academic: n}` per source)
  and never written anywhere. An `unsure` record is kept out of the repository but not dropped: it
  waits in `_meta/state/private/unsure.jsonl`, a local private zone the sync script never stages
  (like `personal/`), and the owner's morning email lists its subject line, source and date as a
  numbered item. One word in the reply brings it in (`yes`: it is written to its feed as a
  company record and routed) or lets it go (`no`: removed, counted as personal). Unanswered items
  stay listed for 14 days, then go, counted in the run line. The morning email's notice and
  payload files move into that private zone, since they now carry such subject lines.
- **Size.** A witness page above 900 KB is split into parts (`<record id>#2`, `#3`, ...) on line
  boundaries, because the sync script never lands a file over 1 MB.
- **Secrets** stay as today: a key or password is exported as `[redacted]`, the record carries
  `redacted: true`, and the person gets one security notice.
- **Deepening old pages.** A re-exported record whose page exists and has shorter text replaces
  that page's body (same page id, `refreshed_at` stamped). A record already routed is routed
  again (see 4.6).
- **Attachments**, in this order: the mail connector's attachment reader (one session read a PDF's
  text this way on 2026-10-14); else the browser, opening the message and reading the attachment
  in the mail web app's preview, never downloading; else the attachment goes on a daily list on
  Needs you, and the owner's yes for that list lets the browser download those files once, read
  them and delete the downloads. Each attachment record says how it was read (`read_via:
  connector | preview | approved_batch`).

### 4.2 Routing (judgment)

A new step in the sweep chain, after `sweep.py`: the **router**.

1. `route.py <root> pending` lists every witness page without a routing decision, grouped by
   thread or meeting, oldest first, each with a context pack: the record (full text), the spaces
   this workspace can release to (name, profile, purpose line, audience, left-out terms), the
   registry (subjects, homes, people with their addresses), each candidate subject's current
   status page and open items (read through the mounts), and the shortcuts: subjects suggested
   by exact identifiers and by the 2026-10-14 domain and title keys.
2. The agent reads the record in full and writes one decision per record:

```yaml
record: <record id>
decision: private | route
confidence: high | medium | low
why: <one or two sentences>
routes:                      # empty when private
  - space: <destination space>
    subject: <subject id>
    tier: at_once | window
    why: <why this space, and why this tier>
    content: whole | parts
    parts:                   # with content: parts; each a verbatim span of the witness page
      - text: <exact passage or transcript segment>
        note: <optional one line: why this part is here>
    informs:                 # where it belongs in the destination (4.4)
      - kind: open_item | decision | person | page
        text: <one line>
        owner: <name, optional>
        due: <YYYY-MM-DD, optional>
        direction: we owe | waiting on them | internal   # open items
        closes: <open item id, optional>
```

3. `route.py <root> record <file>` validates and applies it (4.3), appends the decision to
   `_meta/state/routing.jsonl` and stamps the witness page (`routing: {decided_at, decision,
   routes: [<space>/<subject>]}`). The workspace's own status pages then count the page for every
   subject it was routed to: routed subjects replace "identifier only" in `status.py` and
   `distill.py`.

Every witness page is routed, whatever its source: session notes (the workspace's summaries of
the person's own agent sessions) included, since they carry no identifier at all and are the
largest group that never reached a project. Exact identifiers become shortcuts: the pack proposes them, the agent confirms or overrides,
and its reason is logged. Depth over thrift: the router reads whole records and the destination
pages, and its token cost goes into its run line.

### 4.3 Gates (deterministic, in `route.py record`)

> Superseded in part on 2026-10-15: the term rows below (never-leaves, left-out, high-stakes
> words) are now the router's judgement, checked by a second agent. See
> `2026-10-15-judged-gates-design.md`.

A decision is refused (nothing written, the reason printed, the agent fixes it) when:

- a destination is not a space this workspace releases to, or the subject's home is not that space;
- a part is not a verbatim span of the witness page (compared after collapsing whitespace);
- `content: whole` is chosen but someone on the record is outside the destination's audience
  (people resolved through the registry; the client's own mail domain, `match.domain`, counts as
  inside for its project).

Otherwise each route becomes a projection, then:

| Condition | Result |
|---|---|
| a never-leaves term in the projected content | held at home (`never_leaves`), no signal |
| a never-leaves term elsewhere in the record only | `window`, reason "the record holds never-leaves material elsewhere" |
| a left-out term of the destination in the projected content | held (`left_out`); the agent should have left that passage out |
| `confidence: low` | no projection; a question on Needs you with the agent's recommendation |
| `confidence: medium` | `window` whatever the tier |
| a high-stakes term in the projected content | `window`, lands pending (the closed list stays a floor) |
| a review period, only when `routing.review_until` is set (off by default, 4.6) | `window` |
| otherwise | the agent's tier: `at_once` ships with the next sync (within 15 minutes); `window` ships after 24 hours and one morning view, which shows the agent's reason |

The release scrub at ship time is unchanged (never-leaves, left-out, mode, secrets scan). The
left-out defaults lose `attachment(s)` and `transcript(s)`: they name a form, not a kind of
information, and the owner decided that verbatim parts travel. Each space's own list changes by
its owner's click (`space.yaml` is protected).

A Needs-you answer (`route <record> to <space>`, `keep private`) is applied by `route.py answer`,
which records the decision with `by: <person>`.

**Reversal.** The original stays in the workspace. `route.py retract <record> [--space <name>]`
queues a retraction for every projection already shipped (the existing retraction path) and logs
it; a projection still in the queue is withdrawn.

### 4.4 Where it lands in the destination

A projection is a record handed sideways: `landing.py` files it under the destination's
`knowledge/sources/` as today, carrying `informs`, `at`, `from`, `to` and the thread. Then:

- **Status page** (`status.py`): every `informs` open item is a line under Open, by direction,
  with owner and due date and the projection's id; `closes:` closes the named item; due dates
  feed Coming up. The thread view of mail (who wrote last) stays.
- **Candidates** (`distill.py`): `decision` informs become a decision candidate; `person` informs
  a people-page candidate (claims about a named person stay high-stakes, so they land pending);
  `page` informs an update candidate for that page. Candidates are drafts the destination's owner
  signs; nothing writes `stable`.
- A record dump is never the product: the projection carries only what that space needs.

### 4.5 Failure handling

- A connector that cannot be reached: export nothing for it, name it under `unreachable`, continue
  (as today). An attachment that cannot be read stays on the daily approval list.
- The router stops mid-run: decisions are per record and idempotent; `pending` lists what is left.
- A refused decision is retried once by the agent; still refused, the record becomes a Needs-you
  question with the refusal reason.
- Budget: the router's cost per run goes into its run line; a run that exceeds the space's
  `budget.tokens_per_day` stops and lists what is left. No other cap.

### 4.6 Backfill and the first proof

- A person's first run: 90 days of every source, full depth, then the router over everything,
  oldest first.
- A project, when it is registered or first routed to: the router searches the workspace's
  witness pages for the subject (names, aliases, domains, shortcuts) back to the first mention,
  and the export re-reads, in full, the records whose pages are shallow.
- **No review period by default.** The router is live from day one: its at-once choices take
  effect at once (the owner's decision, section 7). The setting `routing: {review_until: <date>}`
  stays available for whoever wants every projection to wait the window until a date; onboard
  never sets it.
- **First proof:** the owner's records about the first client project are re-exported in full
  and routed. Expected outcome: the kickoff call (its relevant transcript segments), the client
  mail threads and their open items reach the project, at once where the router chose so and
  after the window where a gate forces it. The six items the 2026-10-14 patch staged may ship as they are;
  the router's projections of the same records follow with the verbatim parts, and the
  destination shows each record once (same title and time).

### 4.7 What changes in the existing machinery

| Piece | Change |
|---|---|
| `.claude/skills/sweep/SKILL.md`, `_meta/templates/routines/sweep-chain.md` | lossless capture (4.1), private-at-the-door counts, attachment order; the router step after `sweep.py` |
| `sweep.py` | witness pages and shortcuts only; it no longer queues releases; page refresh and size split |
| `route.py` (new), `.claude/skills/route/SKILL.md` (new) | `pending`, `record`, `answer`, `retract`; the gates; the routing log |
| `status.py`, `distill.py` | count routed subjects; render and use `informs` |
| `morning_view.py` | show the router's reason for each window item; list unsure records in the email only; notice and payload in `_meta/state/private/` |
| `reply.py` | a `yes` or `no` on an unsure record brings it in or lets it go |
| `sync.py`, `.gitignore` | `_meta/state/private/` is a private zone, never staged |
| `spaceyaml.py`, `rules.md` section 4 and 6, `docs/decisions.md` | left-out defaults; the release rule names the agent's tier; the two decisions replaced |
| `registry.py` | unchanged; `domain` and `keywords` stay as shortcuts |

Capture and the router land together: deeper capture through the old release path would send
whole transcripts out before any judgment.

## 5. Testing

- `route.py`: one test per gate row above; verbatim-span check (exact, whitespace-collapsed,
  refused paraphrase); the `whole` audience check (crew, client domain, an outsider); tiers,
  floors and the optional review period; low confidence becomes a question; `answer` and `retract`; idempotence.
- `sweep.py`: private records never written; page refresh keeps the id; split above 900 KB.
- `status.py` and `distill.py`: `informs` lines, `closes`, due dates in Coming up; candidates per
  `informs` kind.
- The fixture end to end (`_meta/tests/fixture`): a fixed decisions file plays the agent, as the
  verdicts file does for signing; a multi-topic call sends each project only its segments.
- A behaviour scenario for the route skill (paths written, no `stable`, no send).
- The first proof's outcome is checked by hand and reported: what was staged, what was held, why.

## 6. Out of scope

The brain's own sweep (it still sweeps nothing of its own); a project's own sources (they keep
their exact-identifier feeds); changing who may read a space.

## 7. The owner's answers on the open points (2026-10-14)

1. **Never-leaves elsewhere in a record**: the clean parts travel after the window (as in 4.3).
2. **Review mode**: none. "I don't want a review mode for now. Just put it live." The router's
   tiers apply from day one, the first proof included; medium confidence, high-stakes terms and
   never-leaves material elsewhere still force the window, low confidence still asks. The
   `routing.review_until` setting stays in the tool, off unless set.
3. **Quoted history in mail**: kept whole; the router sends only the new passage when the quoted
   part already travelled.
4. **A record the reader cannot classify**: kept out of the repository and listed in his morning
   email, where one word brings it in (4.1), so nothing is lost silently.
5. The six items the 2026-10-14 patch staged ship after his morning view.
