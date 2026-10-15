# Understand first: briefs, research, placement and checked pages

Status: approved by the owner, 2026-10-15 ("write the spec, and implement the things we
discussed"). Date: 2026-10-15.
Builds on: `2026-10-14-judgment-routing-design.md` (the router) and
`2026-10-14-history-backfill-design.md` (a year of history). Changes how the router and distill
work: both understand a subject before they place or write anything about it.

## 1. Why

Distill turns evidence into the pages people sign, and signed pages are what travels and what
answers lean on first. Two candidates for the same client, a day apart, showed what goes wrong:

- **Written before understood.** The first draft carried six bracketed questions for the owner
  ("who are these two people?", "what would this project do?", a contact's full name). Every
  answer was findable in mail, calls and documents the agent could read. The owner's reply: "you
  can figure out those questions yourself, that's your job". The rewrite, after research, was
  far better.
- **Rich is not the same as right for the page.** The rewrite also carried an ownership remark
  (never-leaves), a contact's private situation (a claim about a named person) and prices (the
  high-stakes list): lines that hold a page at the door or do not belong on a company page.
- **No source per sentence.** One "Sources:" line at the end; the signer cannot tell which line
  rests on which record, or which is stale.
- **Wrong evidence sticks.** A candidate's evidence list only ever grows (refreshed in place,
  merged), and distill may not drop an id, so evidence about another subject stays attached and
  is signed over.
- **Status inside pages.** "As of the 9th nothing is signed; a call is set for the 13th" is stale
  within days. The vision: status is rebuilt from evidence; only lasting conclusions are signed.

The owner's direction: "it's VERY VERY important that an agent first understands the project
before it actually puts information into certain pages": first what the subject is, then the
research the new content needs, then where it goes and which pages change. Tokens are not the
constraint: "rather high quality".

## 2. The owner's decisions (2026-10-14 and 15)

1. **Understand, research, then place**, for the router and for distill alike.
2. **Research before asking.** No "[unknown]" and no question until every readable source is
   exhausted; a question that remains carries the agent's best guess.
3. **The archive stays out for now.** The owner wants to see how a space performs on its own
   records. `routing.archives` (default off) keeps archive-derived pages out of every
   understanding step; one switch turns it on for a later comparison.
4. **Quality over token cost.** No search cap, no shortened reading, a second agent checks.
5. **A pilot first.** One client with two projects is backfilled end to end with this design and
   compared with the page the owner already has, before the full backfill.

## 3. Approaches considered

- **A. Better instructions only.** Cheap; leaves wrong evidence stuck, no source per line, no
  memory of what was learned, so every run re-researches and contradicts the last.
- **B. Understand per record.** Each record triggers its own research: thorough but slow, and
  two records about one subject reach different understandings.
- **C. Understand per subject, kept in a brief (chosen).** Records are grouped by subject; the
  agent builds the subject's understanding once, keeps it as a private brief, extends it with
  research, and places every record and writes every page from it.

## 4. Design

### 4.1 The flow

For each run (a sweep's new records, a backfill slice, a pilot):

| Step | Who | What |
|---|---|---|
| 0. Triage | router | name the candidate subjects of each record: identifiers, names, aliases, mail domains, the registry's suggestions; an unknown name is a research item |
| a. Understand | router, then distill | per subject: load its brief, or build it by reading everything the space holds on it, in full |
| b. Research | router, then distill | close the gaps the new records open: search this space, the spaces it can read, the connectors; export what is found as records |
| c. Place | router | per record: which spaces, which parts, tier, what it informs (as in the routing design) |
| c'. Write | distill | per subject page: which lines change, each with its sources, in the page's shape |
| d. Check | a second agent | every placement and every new page line against its source |

Records are processed per subject, and within a subject per episode (4.4), oldest first.

### 4.2 The subject brief

A private working understanding of one subject, kept by the space that understands it.

- **Where:** `_meta/state/private/briefs/<subject>.md`. Private: never staged, never travels,
  never signed. It is derived and disposable: deleting it costs a rebuild, nothing else.
- **What:** frontmatter `{subject, built_at, updated_at, records_read}`, then the sections of
  `_meta/templates/brief.md`: What it is; People (name, organisation, role, every address, chat
  handle and recording speaker label seen for them); Names and aliases; How it started;
  Timeline (dated lines, each with record ids); Where it stands (with its date); Sensitive
  (prices, terms, ownership, people's private situations: known, never published); Open
  questions (what research could not answer); Research log (searches made, what they found).
- **Built** the first time a subject is touched: `python _meta/tools/brief.py <root> pack
  <subject>` writes `_meta/state/private/briefs/<subject>.pack.json` (4.6), and the agent reads
  every page the pack lists, in full, then writes the brief. **Extended** on every later touch:
  new records and research results are folded in; a line that newer evidence replaces is moved
  to the timeline with its date, never silently dropped. **Rebuilt** on request or when the
  pack's evidence count has more than doubled since `built_at`.
- **Used by** the router (placement), distill (pages), status questions and call prep (the
  query skill reads the brief first, then the newest records).

### 4.3 Research

- **Sources, in order:** this space's witness pages and journal; the spaces it can read
  (mounts: signed pages, status pages); the connectors within `space.yaml` sources (mail
  search by sender, domain and terms; chat search; the call recorder's search; documents). The
  archive only when `routing.archives` is on.
- **Stopping rule:** a gap is closed when a record answers it, or when a search finds nothing
  new. No fixed number of searches.
- **Lossless:** every record a search finds is exported into its feed in the sweep's record
  shape (and gets a witness page from the next `sweep.py`); what was looked up is kept, not only
  consulted. Each carries `research: <subject id>`, kept on its page: `route.py pending` lists it
  for that subject and `brief.py pack` counts it as evidence (`researched`), because what answers
  a subject's question often never names the subject (the first enquiry, a thread on next steps).
- **Questions last:** a gap that stays open goes to the brief's Open questions; it becomes a
  question to the owner only when a page cannot be written without it, pre-answered with the
  agent's best guess.

### 4.4 Episodes

One event often arrives as several records: a calendar entry, one or two recordings, a
follow-up mail, a chat thread. `route.py pending` gives every record an `episode` id:

- records with the same `identifiers.thread` share an episode;
- a call joins an event when its `identifiers.calendar` is the event's id, or when it starts
  within 60 minutes of the event's start on the same day and shares at least one participant
  address other than the owner's own;
- a mail joins an event when it is dated from the event's start to three days after it and
  shares at least two participant addresses other than the owner's own with the event;
- two calls in one episode are flagged `same_meeting: true` (a second recording);
- anything else is its own episode (`th-<thread>` or `rec-<record id>`).

Records are listed per episode, oldest first; the router reads an episode as one event.

### 4.5 The clean map, and lessons

- **Clean map first.** Before a subject's backfill, check its registry entries: duplicates
  merged (a project and a client registered as one thing, or one thing registered twice),
  umbrellas set (`part_of`), aliases complete. Aliases and people found during research are
  added through the existing paths (the registry's `aliases`; a person subject through the
  librarian), each with the record that shows it.
- **Lessons from corrections.** `_meta/lessons.md`, one line per lesson:
  `- <YYYY-MM-DD> <route|distill|all> [<subject>]: <lesson> (from: <the owner's words or verdict>)`.
  Written by the sign skill when the owner's verdict corrects content (an `edit` with a reason,
  a `no` with a reason, a pushback in a session) and by any session that receives such a
  correction. Read first by the router, distill and the brief pack. A lesson is never re-asked.

### 4.6 `brief.py`

```
python _meta/tools/brief.py <root> pack <subject> [--out <path>]
python _meta/tools/brief.py <root> path <subject>
```

`pack` writes JSON: `subject` (the registry entry, its umbrella and its parts), `brief` (the
brief's path and whether it exists), `pages` (its signed pages here and at its home through the
mounts), `status` (its status page here or at its home), `evidence` (every witness page and
journal entry in this space that the subject or one of its parts is routed or attached to,
names in `mentions`, or whose title or text names its title or an alias as a whole word: path,
record id, at, source, kind, title, characters), `lessons` (the lines of `_meta/lessons.md`
for this subject or `all`), `excluded` (pages left out and why). With `routing.archives` off,
any page `provenance.is_archive_derived` flags is excluded, and so is the subject's open
candidate when it is being rebuilt (`--rebuild`). `path` prints the brief's path.

### 4.7 Distill, understand-first

- **Before writing:** pack and brief (4.2), research (4.3), lessons (4.5).
- **Shape per kind:** each page follows `_meta/templates/pages/<kind>.md` (clients, projects,
  people, deals, decisions): sections and what belongs in each. Lasting facts only: what the
  subject is, how it started, scope, people and roles, agreements, decisions, how it ended.
  Where it stands lives on the status page; a page may say "see status".
- **A source on every line.** Every sentence or bullet outside `## New evidence` ends with
  `[src: <record id>, ...]`, ids from the candidate's `evidence`. Newer evidence replaces older;
  `## Disagreements` is only for records of the same moment that conflict.
- **Written for where it lands.** Each line is checked against the never-leaves list and the
  destination's left-out list before it is written; a sensitive fact is softened ("currently
  unavailable") or left in the brief, never placed on a page bound for an audience that may not
  read it.
- **Set aside, never silently drop.** Evidence that is not about the subject moves from
  `evidence` to `set_aside: [{id, why}]` in the candidate's frontmatter; `distill.py` keeps
  set-aside ids out on every later refresh.
- **Rebuild.** `python _meta/tools/distill.py <root> --rebuild [--subject <id>]` writes the
  candidate from scratch: no old body, the evidence as it is now, no inherited set-aside list.
  Used after a backfill, or when a candidate's evidence is known to be wrong.
- **Check.** `python _meta/tools/distill.py <root> verify <candidate>` lists every problem: a
  line without a source, a source not in `evidence`, a bracketed unknown or question mark
  placeholder (never-leaves and left-out matter is the second agent's judgement since
  2026-10-15, judged gates). Then a second agent reads the
  candidate against each cited record and fixes or removes any line its source does not
  support; `python _meta/tools/distill.py <root> checked <candidate> --by <name>` stamps
  `checked: {by, at}`. A candidate reaches Needs you only when `verify` is clean and it is
  checked.
- **No archive** unless `routing.archives` is on: archive-derived pages are not evidence.

### 4.8 Router, understand-first

Same flow before each placement: triage, the subject's brief (built or extended), research for
what the records leave open, then the decision files of the routing design. `route.py pending`
adds to its output `lessons`, `briefs` (subject id to brief path, for every suggested subject
with a brief) and `archives` (the switch), and each record's `episode` and `same_meeting`. The
second agent checks a sample of each run's decisions against their records (at least one in
five, all `window` and all `whole` routes) before `route.py record`.

### 4.9 Measuring it

- **Answer tests first.** Before a subject's backfill, its questions go into the brain's answer
  tests ("what is <project>", "who are our contacts at <client>", "what have we done for
  <client>"), with expected facts taken from a version the owner trusts, kept out of every
  understanding step until the run is done.
- **The weekly sample** (vision: five silent landings) also draws from router decisions and new
  page lines; the share found wrong is on the health line.

### 4.10 The pilot

One client with two projects, done exactly as the full backfill will be: clean map, a count of
records per month, a year of records re-exported in full, sweep, briefs and research, routing,
distill rebuilt, verify and check, landing. The result is compared with the page the owner
already has, line by line: found, missing, wrong, and what came only from the archive. A pilot's
decisions carry `scope:` (its subjects): an internal call that also covers other projects is
decided for the pilot's subjects only and stays pending for the rest.

## 5. What changes in the machinery

| Where | Change |
|---|---|
| `_meta/tools/provenance.py` (new) | `is_archive_derived(fm)` |
| `_meta/tools/brief.py` (new) | `pack`, `path` (4.6) |
| `_meta/templates/brief.md`, `_meta/templates/pages/<kind>.md` (new) | the brief's sections; page shapes |
| `spaceyaml.py` | `routing.history_since`, `routing.archives` (default off) |
| `route.py` | episodes; `lessons`, `briefs`, `archives` in pending; the history rules (history spec) |
| `distill.py` | `set_aside`; `--rebuild`; `verify`; `checked`; no archive evidence unless on |
| `registry.py`, `status.py` | `period`, `client`, `crew`, `work` on subjects; the projects page shows them, unconfirmed marked (history spec) |
| skills | route, distill, sweep, seed, status, query, sign: the flow above |
| routines | the sweep chain names the brief and the check |

## 6. Testing

- `brief.py pack`: lists evidence routed, mentioned, or named by alias; excludes archive-derived
  pages with archives off, includes them with archives on; lessons filtered by subject.
- `route.py pending`: episode joins (calendar id; time and participant; mail within three days
  with two shared participants); `same_meeting` on two recordings; lessons, briefs and archives
  in the output.
- `distill.py`: set-aside ids stay out after a refresh; `--rebuild` drops the old body and the
  merged list; `verify` flags a line without a source, an unknown source and a bracketed
  placeholder, and passes a clean page; `checked`
  stamps; archive-derived evidence is ignored with archives off.
- Behaviour scenarios for distill and route gain: a placeholder question is researched, not
  asked; a sensitive fact stays in the brief.

## 7. Out of scope

- The archive as a research source (switch off until the owner compares).
- Mail attachments the mail connector cannot open (the browser route needs the owner's browser
  extension).
- A registry proposal decision from the router (new subjects go through the librarian).
