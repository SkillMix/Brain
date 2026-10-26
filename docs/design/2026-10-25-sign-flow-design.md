# Sign flow: answer back to back, never asked twice: design

Status: approved 25 October 2026, built for the release after 0.2.0.

## 1. Intent

The person signs pages in sittings. They want to sit down and answer questions back to back,
without waiting minutes after each page while the agent works. They also want a question that a
later sweep or distill can answer to be answered or reworded on its own, and never put to them.

### How a sitting went before

For each page:

1. The person picks the page ("which should come next?").
2. **Wait 1, the long one.** The agent reads the draft, checks every line against the records,
   finds what the records cannot settle, and writes a few questions.
3. The person answers.
4. **Wait 2.** The agent writes the edit, moves amounts into a `commercial.md` desk file,
   writes the daily note, builds the read-back.
5. The person types `submit`.
6. **Wait 3.** `sign.py apply`, the validator, the daily note, then "which should come next?".

### Why it was slow

- Wait 1 repeated work distill had already done. Distill researches every subject and writes
  what it could not settle under "Open questions" in the subject's brief
  (`_meta/state/private/briefs/<subject>.md`). The sign pass did not use them: it did the
  research again, live.
- Briefs are private state and gitignored, so they exist only on the machine that ran distill.
  A sign pass on another machine has no brief to read.
- Questions were free text with no id and no state. Nothing rechecked them when new records
  arrived, questions raised in a sign pass were stored nowhere, and an answer reached the next
  distill only because the daily note counts as evidence. The same question could be asked
  twice.
- The way the person wants to sign (show what the page says, ask the few questions only they
  can answer, fold the answers into an edit, read back the final text) lived only in one
  harness's memory, not in the sign skill. The skill still described a two-line brief per
  item, which the person had already turned down as too thin to sign from. Anyone else using
  the template got the old behaviour.

## 2. Goals, non-goals, anti-goals

### Goals

- **G1 Questions ready on arrival.** When the person sits down, every candidate's questions are
  already prepared. In the normal case there is no research wait before the first question.
- **G2 No idle wait between pages.** The write-up of an answered page runs in the background
  while the person answers the next page's questions.
- **G3 Questions are tracked items.** Each has an id and a state. Every distill run rechecks the
  open ones against new records: settled ones are answered from the record, partly settled ones
  are reworded, ones that no longer matter are dropped. A sign pass asks only what is still open.
  Nothing settled, by a record or by the person, is ever asked again.
- **G4 What is signed is what was read back.** `submit` signs exactly the text the person was
  shown, checked by fingerprint, even though that text was written in the background.
- **G5 No "which next?".** The agent picks the order. The person can still say "skip" or "do X
  next".

### Non-goals

- Changing who signs, the verbs, or the word `submit`.
- Answering questions by email reply (`reply.py`). Question ids make that possible later; not
  now.
- Changing what distill writes on a page, or making distill itself faster.
- Changing check items, routing questions or release items. This design covers the questions
  that come with a candidate page.

### Anti-goals: failure even if it works

- **Rubber-stamping.** The person signs text they have not seen, or gets one long pile of
  read-backs at the end and approves it unread.
- **Being asked what the records answer.** The person is asked something a record already
  says.
- **A wrong automatic answer landing unseen.** A question settled from a record that does not
  actually say it, and the person never sees the settled line.
- **Two writers on one file.** A background helper writing into `knowledge/`, the daily note, a
  candidate or a desk file.
- **Working only in Claude Code.** The core (prepared questions, the recheck, fingerprint
  pinning) must work in any harness. Background helpers speed things up; without them the flow
  runs the same steps one after another.

## 3. Constraints

- `rules.md` section 1: only a person signs, and `status: stable` is written only by `sign.py`
  for a named person (hard rule 3); one writer per file, append-only files merged by union
  (hard rule 5); agents never commit (hard rule 7); `_scratch/` is outside every tool, sweep and
  commit (hard rule 8).
- Questions can carry sensitive content (they ask about fees and terms). They stay in the space
  that holds the candidate. Nothing in `_inbound/` or `_meta/state/` travels.
- The sign pass can run on a different machine from distill, so the questions must live in
  committed state, not in the private brief.
- Each space distills and signs its own candidates (a person's workspace and the brain both
  have briefs and candidates). The question store is per space.
- The template is public, and 0.1.0 and 0.2.0 are released. Neither touches the sign flow: the
  sign skill is unchanged, `sign.py` has no fingerprint pin and there is no question store. This
  design targets the next release after 0.2.0.
- 0.2.0 adds lesson candidates (`lesson-<slug>.md`, landing under
  `knowledge/topics/lessons/`): a lesson carries its text alone, so it has no questions, and an
  edit of one also rewrites its title and target.

## 4. Design

### 4.1 The question item

A new append-only ledger, `_meta/state/sign-questions.jsonl`, one event per line, merged by
union like the other `jsonl` state files. (Not `_meta/questions.log`, which logs the person's own
questions.) A question's current state is the fold of its events.

Each question carries:

- `id`: stable, `q-` plus 8 hex. Rewording keeps the id.
- `subject`, and `page`: the candidate id it belongs to.
- `text`: the question in plain words, as the person will read it.
- `why`: what the page cannot say without the answer.
- `recommendation`: the best guess, written as the line the page would get ("add: Harbor moved
  its go-live to 3 November").
- `searched`: where it was looked for, the terms, when.
- `status`: `open`, `settled` (a record answered it), `answered` (the person answered it),
  `parked` (the person does not know or skipped it: never asked again, but every recheck still
  tries to settle it from records), `dropped` (no longer matters).
- When closed: `source` (the record id), or `by` plus `answer` (the person's words, short),
  or `why` for a drop.

Events: `asked`, `reworded`, `settled`, `answered`, `parked`, `dropped`, each with `at` and who
wrote it.

A question belongs to its **subject**, not to one candidate. `page` names the candidate it was
last asked for. A subject gets a new candidate id after each signature; its open and parked
questions carry over. A page may be signed while questions are still open; they stay open for
the subject's next candidate.

**Sensitive content.** The ledger follows the same rules as the page it serves: the
destination's `left_out` and `never_leaves` lists. The brain has `audience: everyone`, so
anything committed there is readable by every colleague, and stays in git history. A question
or answer in one of those categories (a fee, rate, term, someone's pay or ownership) is not
written word for word. The question reads "Beacon's terms: recorded?", and the answer event
says only "answered by Ada Okafor, kept in Ada Okafor's workspace". The answer itself goes where
it went before: a desk file in the answerer's own workspace, such as
`work/<kind>/<subject>/commercial.md`. `sign_questions.py` refuses a line that matches the
destination's lists, the way `verify` refuses a line without a source (as built, see section
10).

A new tool, `_meta/tools/sign_questions.py`, is the one way to read and write the ledger:
listing a subject's or page's open questions; adding, rewording, settling, answering and
dropping one; and saying whether a subject has records newer than its questions' last check.

The brief keeps its research notes. Its "Open questions" section lists question ids with their
searches; the ledger is where a question to the person lives. The brief template changes to say
so. The free-text Open questions already in the briefs are converted on the subject's next
distill run, and once at rollout (section 9).

### 4.1a Citing an answer

An `answered` or `settled` question is a record a page line can cite: `[src: q-1a2b3c4d]`.
Distill gathers the subject's answered questions as evidence, and `verify` accepts their ids
(as built, see section 10). The background write-up (4.3 step 5) must pass `verify` before it is
read back.

This also fixes an existing problem: a brain page that cites an entry of a person's daily note.
A brain page about Harbor can cite an entry of Ada Okafor's journal; that id is not in the
page's `evidence`, and the brain cannot read her journal, so the page's next refresh fails
`verify`. Cited as the answered question, the same fact passes.

### 4.2 Distill: prepare and recheck

Two new steps in the distill skill, for each candidate the run wrote or refreshed:

- **Recheck** (before research). List the subject's open questions. For each, read the records
  that arrived since it was last checked, including daily-note entries:
  - a record answers it: mark it `settled` with that record as `source`, and put the answer on
    the page with its `[src: ...]` like any other line;
  - a record answers part of it: reword it, keeping the id;
  - the page no longer makes the claim the question was about: drop it, with why;
  - otherwise it stays open.
- **Ask** (after research). Each gap that only the person can answer, or that blocks a line,
  becomes a question through the tool, with its recommendation. At most four per page, most
  important first. The rest stay in the brief.

The second agent's check (distill's Check step) also reads each question settled this run
against its source record. A settle the record does not support is reopened. This is the guard
against a wrong automatic answer.

### 4.3 The sign sitting

The sign skill gets the working method that lived in harness memory, then the new flow:

1. **Needs you** as before. A candidate's line shows how many questions it has ("Harbor: sign?
   (3 questions)").
2. **Order.** The agent picks: by deadline, then by how much depends on it, as Needs you
   already sorts. It does not ask which page is next.
3. **Per page:** what the page says (every claim, or its text), where it lands and who reads
   it, then its open questions from the ledger. Questions settled since the last sitting are
   not asked.
4. **Answers are recorded as they come**, as `answered` events with the person's words. This
   is a write before `submit`, and the skill says so explicitly: an answer is the person's
   statement of fact, not a signature. The verdicts file is still the first signing write.
5. **Background write-up.** Once a page's questions are answered, a helper writes its edited
   text to `_scratch/sign/<page id>.md`, plus a word-for-word list of changes and the text of
   any side file (such as a `commercial.md` desk file). The helper writes only there. The main
   agent writes the daily note, the desk files and the verdicts. Meanwhile the person is already
   on the next page.
6. **Rolling read-backs.** When a write-up is done, the main agent shows its read-back at the
   next break, after the person has finished the current page's questions and before the next
   page's: the changes word for word, the path to the full text, its fingerprint, and one line
   per question a record settled ("settled by Harbor's mail of 2 October: the go-live moved to
   3 November"). A read-back is never held back for a pile at the end.
7. **Submit and stop.** `submit` signs every page read back and not yet signed (each with its
   own word: section 9). If the person says "stop", the agent says it needs a moment, waits for
   the running write-ups, reads them back, and asks for `submit`. Anything not yet answered
   keeps its questions in the ledger.
8. **Fallback prefetch.** If a page has no prepared questions, or the subject has records newer
   than the last recheck, a helper prepares or rechecks that page while the person answers the
   current one. This was the person's own first idea, a pipeline, kept for when distill has not
   caught up.
9. **Without background helpers** (another harness), steps 5 and 8 run in line, in the same
   order. Slower, never different.

### 4.4 Fingerprint pinning in `sign.py`

- `read-back <id> --body <file>` prints the landing text and fingerprint of an edited body
  exactly as `apply` would compute them.
- The verdicts file takes a mapping per item:
  `p-1a2b3c4d: {verb: yes, fingerprint: <hex>}` or
  `p-1a2b3c4d: {verb: edit, body_file: _scratch/sign/p-1a2b3c4d.md, fingerprint: <hex>}`.
- `apply` recomputes the fingerprint of what it is about to sign and refuses the item on a
  mismatch, so a candidate that distill refreshed after the read-back is never signed unseen.
  A refused item is reported and the others go ahead.
- **The base is pinned too.** Distill runs three times a day and can refresh a candidate during
  a sitting: a settled answer, new `evidence`, `high_stakes`, or a new `destination`. The body
  fingerprint alone would miss that, and an edit's body comes from the write-up, not the
  candidate. So each signing verdict also carries `base`: a fingerprint of the candidate as the
  write-up read it (body plus `destination`, `high_stakes` and `target`). `apply` refuses the
  item when the base has changed; that page is written up and read back again. Distill is not
  locked during a sitting.
- For verbs that sign (`yes`, `edit`, `always`) on candidates and on leaving items whose body
  is signed, a missing fingerprint is refused. `no` and `later` need none.
- The question keys (4.1) go into the fields stripped on landing.
- Before, `needs_you` fingerprinted a candidate's full body, `## New evidence` included, while
  `read_back` and `apply` fingerprinted the landing text. They are made the same.

### 4.5 Needs you and the morning view

The question count is shown on the candidate's line. Questions are not separate items, and the
morning email stays counts-only.

## 5. Decided with the person

- Prepare the next page's questions while answering the current one: the person's idea, kept as
  the fallback in 4.3 step 8.
- Questions are prepared at distill time, there is a background writer during the sitting, and
  the fingerprint is pinned.
- Questions go stale: when a later sweep or distill can answer one, it is answered
  automatically or reworded, and not asked.
- The person still sees the final text and types `submit`. On "stop" the agent waits, reads
  back, and asks for `submit`.

## 6. Calls made in the design (the person's to overrule)

- Background helpers inside the sitting's conversation, not separate sessions.
- Rolling read-backs, one `submit` for everything read back so far.
- Questions a record settled are shown in the read-back, one line each.
- The ledger in `_meta/state/` (committed, append-only); the brief keeps the research notes.
- Answers recorded as they are given, before `submit`.
- At most four questions per page.
- Questions belong to the subject and carry over to its next candidate; a `parked` state for
  "I don't know" (4.1).
- Sensitive questions and answers follow the destination's left-out rules; the answer stays in
  the answerer's workspace (4.1).
- Answered questions are citable records (4.1a).
- The base is pinned as well as the body (4.4).
- The working method moves from harness memory into the sign skill; the memory entry becomes a
  pointer.

## 7. Left to the builder

The id scheme, the exact ledger line shape, CLI names and flags, scratch file naming, how the
main agent decides when there is a break for a read-back, and the test layout. Tests to cover at
least: the ledger fold and union-merge safety (a duplicated line changes nothing), a stable id
through a reword, a settle that the check reopens, `apply` refusing a fingerprint mismatch and a
missing fingerprint, `edit` with `body_file` signing exactly that text, Needs you showing the
count and hiding settled questions, and a behaviour scenario in which a new record settles an
open question and the next sitting does not ask it.

## 8. Building it

Built in the template, and reaching spaces by template sync. The three tools it touches
(`sign.py`, `needs_you.py`, `distill.py`) can differ between the template and a space that
changed them. Template sync keeps any file a space changed, so before building, the builder
diffs the copies and carries any real local fix into the template. Otherwise those spaces would
never receive the new version.

## 9. Open questions, answered 25 October 2026

1. **Rollout.** It goes in the next release after 0.2.0. Every open candidate gets its
   questions prepared in one run at rollout, once per space, rather than as distill next
   touches each subject: `distill.py <root> prepare`, then distill's Recheck and Ask for every
   page it lists. It is a large token run, so it runs overnight as a one-off.
2. **A word per page.** Each read-back takes its own word from the person (yes, no or later)
   before `submit`. `submit` signs only the pages that have their word; it never signs a
   read-back the person did not answer.

## 10. As built

Where the build differs from the design above:

- **The lists are categories, not words.** 4.1 had `sign_questions.py` refuse a line that
  matches the destination's never-leaves or left-out lists. Those lists are categories, not
  words to match, as `distill.py verify` already treats them. The tool enforces structure only: a
  question asked with `--sensitive` is answered with `--kept-in <where>`, a pointer, and never
  with its text. Judging content stays with the agent and the second agent's check.
- **Answers are cited, not gathered.** 4.1a had distill gather answered questions as evidence.
  An answered or settled question is cited because `verify` accepts its `q-` id for the same
  subject; it is not added to the candidate's `evidence`.
- **The cap and the count follow the subject.** A subject holds at most four open questions,
  and the Needs-you count is the subject's open questions, not one page's. Distill gives a
  subject's next candidate a new id and its questions carry over, so a cap or count per page
  would miss them. A subject has one candidate at a time, so the sitting still says "at most
  four per page".
- **Batch lines that name a draft.** A line of the weekly review batch whose yes, edit or
  always would sign a draft is refused under the pin. The person signs that draft by its own id
  after its read-back, and the line, answered after, closes.
- **The write-up file is the page's body only.** 4.3 step 5 had the helper write a list of
  changes and the text of any side file beside the edited text. The file
  `_scratch/sign/<page id>.md` holds the body alone, so that `verify --body` can check it; the
  list of changes comes back in the helper's report, and desk files are the main agent's.
- **No question keys on a candidate.** 4.4 had the question keys stripped on landing. Questions
  live only in the ledger, so a candidate carries none and there is nothing to strip.
- **The pin covers every signed body.** Every page whose yes signs its text (a candidate that
  is not a record or a review batch, and a leaving or high-stakes release that carries a
  fingerprint) prints a `base:` line in its read-back, and `apply` checks it. `read-back` takes
  `--body <id>=<file>`, the file resolved inside the space like a verdict's `body_file`.
- **The pin is enforced at the command line.** The Python API, `sign.apply(...,
  require_pin=False)`, keeps the old behaviour for the tools and tests that call it.
- **An emailed yes is bare.** A yes on a page from an email reply (`reply.py`) carries no pin,
  so the command line refuses it; the page is read back and signed with its pin.
- **`distill.py prepare`** lists the pages whose questions need preparing, one line each
  (candidate, subject, and "never checked" or "new records since the last check"), and writes
  nothing. `sign_questions.py <root> checked <subject> --by <name>` marks a subject's
  questions as looked over.
- **The open questions** were answered on 25 October 2026 (section 9): one preparation run at
  rollout, and a word from the person for each read-back before `submit`.
