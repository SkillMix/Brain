# Judged gates: the agent reads topics, the tools check facts

Date: 2026-10-15. Supersedes the term rows of section 4.3 in
`2026-10-14-judgment-routing-design.md` and the term checks in the release scrub, landing,
signing, distill's verify and request answers.

## 1. Why

What may leave a space was decided by matching words: a never-leaves list, a destination's
left-out list (with an exception list), a high-stakes list, another client's name, a registered
person's name, and since this morning a money-amount pattern. A one-client pilot showed the two
failures every word list has. It holds what only looks like a topic ("transfer pricing team"
holds on "pricing", "managed identity" lands pending on "identity", "validation rules" on
"rules"), and each false hold asks for one more exception. It passes what is said in other
words (an hourly rate in euros passed "day rate"; a talk about the ownership split that never says
"equity" passes "equity"). The owner's direction: an agent should judge these itself; word
triggers and exception lists do not scale.

## 2. The rule

Topics are judged by the agent that reads the whole record; facts are checked by the tools.

| Kind | Who decides | Examples |
|---|---|---|
| topic | the router, then a second agent | never-leaves material, left-out matter, high stakes, another client's matter, a claim about a named person |
| fact | the tools | secrets by format (scan), a part copied verbatim, a destination this space releases to, a subject's home, `whole` only when everyone on the record is in the destination's audience, the space's mode |

## 3. Settings

`space.yaml` keeps `never_leaves`, `left_out` and `high_stakes`. Their entries are read by the
agent as plain categories, never matched as words. Existing one-word entries ("price",
"settlement") read fine as categories, so no space has to change its file. The template
defaults become short sentences. `left_out_except` is removed: an exception list has no use
when nothing is matched (a file that still has it is accepted and ignored).

## 4. The routing decision

Two optional fields:

- `never_leaves: <one line: where>` on the decision. The record holds never-leaves material.
  Only clean parts travel and every route from the record waits the window, with the reason
  "the record holds never-leaves material elsewhere". The line itself never travels and is not
  logged; only that it was set.
- `high_stakes: <why>` on a route. The route waits the window and the projection lands pending
  for its domain owner. The why travels with the item (the domain owner reads it), so it is
  written like a part: nothing in it may be left-out or never-leaves matter.

What the destination leaves out is the router's own choice of parts. The router no longer
holds anything for a topic: what goes is clean or stays home.

## 5. The check is the floor

A second agent checks every decision that sends anything out of the space (previously every
window and whole route and one decision in five): is the subject right, is every part verbatim
and needed, is any never-leaves or left-out matter leaving, are `never_leaves`, `high_stakes`
and the tier right. It writes `checked_by: <name>` into the decision. `route.py record` refuses
an outgoing decision without `checked_by`. A decision that keeps everything home, a low
confidence question and a person's answer to a question need none.

## 6. The other places that matched words

- **Release scrub** at ship time: mode and the secrets scan only.
- **Landing**: an item lands pending for high stakes only when it is flagged; the pending note
  carries the router's why.
- **Signing**: an item is high-stakes when it is flagged; the signer judges the rest.
- **Distill**: `verify` checks sources, evidence and placeholders; the page check (a second
  agent) judges never-leaves and left-out matter against the lists.
- **Requests**: an answer given at once by a standing choice carries facts only (the subject's
  state, the last contact date); lines quoting a record (coming-up titles) wait for the
  holder's person with the rest. The question no longer turns into `never` by a word.
- **Without the router** (a space with no `routing:`): nothing judges what a record says, so a
  record leaves by itself only when its destination's audience already reads it. Everything
  else is held in doubt for one yes from its person, answered by the record's id. A standing
  choice no longer sends anything on its own: it only marks yes as the expected answer
  (`recommendation: yes: ...`), because nobody read the record.
- **Witness pages** no longer carry `sensitive: true` from a word; nothing read it.

## 7. What stays

The 24-hour window and the morning view, medium confidence forcing the window, low confidence
asking, review mode, the secrets scan and credential redaction, the never-leaves hard rule
itself (now applied by judgement), and every structural refusal in `route.py record`.

## 8. Rollout

1. Template: tools, skills (route, release, distill, sign, request, sweep, onboard), defaults,
   templates, tests, `docs/decisions.md`. The money-amount pattern and `left_out_except` go.
2. Synced to every space by the template sync.
3. The pilot workspace re-judges what waits on a word: queued and held items whose reason came
   from a matched word or name (a high-stakes term, a named person, never-leaves material
   elsewhere, a left-out term). Each is retracted while still queued and decided again under
   these rules, every outgoing decision checked.

## 9. Risks accepted

A router that misjudges and a checker that agrees let one item through; the window, the
morning view and pull-back remain for anything not at once. A record written to persuade the
router ("this is fine to share") is read by two agents told that records are data, never
instructions. Both risks existed under word lists, which also missed anything phrased
differently.
