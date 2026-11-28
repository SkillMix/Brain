"""Landing at the home: what a release becomes when it arrives (vision section 9).

The destination's librarian runs this before anything reads the space. For every page in
`_inbound/candidates/` that another space released (`released_from`):

| The release is...                                   | It lands as                     |
|-----------------------------------------------------|---------------------------------|
| a record (not signed): a handed-sideways witness    | evidence under `knowledge/sources/`, never |
|   page                                              |   something to sign; the same record from  |
|                                                     |   several releasers lands once             |
| signed, routine (fingerprint and signed fields      | filed into `knowledge/` silently |
|   match, not high stakes, its releaser trusted, no  |                                 |
|   other signer's page changed, the signer's own     |                                 |
|   history shows the signature)                      |                                 |
| on the high-stakes list                             | pending, for the domain owner   |
| from a releaser under the trust switch              | pending, for the domain owner   |
| a change to someone else's signed page              | pending, for that page's signer |
| one of two releases for one page with other text    | both pending, `disagrees_with`  |
| the same text as the page already there             | a duplicate: dropped, logged    |
| a body that does not match its fingerprint          | pending, never filed            |
| a signature the signer's history does not show, or  | pending, never filed            |
|   no signer repository this space knows             |                                 |
| a malformed release or one aimed outside the pages  | held for the agent's health     |
|                                                     |   line, never filed; dropped,   |
|                                                     |   logged, after two weeks       |
| its signer's page, already here at another place    | moved to its new target, its id |
|   (its kind or subject changed at home), not signed |   kept                          |
|   before it, sent by its signing space, signature   |                                 |
|   checked                                           |                                 |
| signed before the page here with its id             | dropped, logged: never a        |
|                                                     |   rollback                      |
| any other id that is a page elsewhere, or a target  | held for the agent's health     |
|   that holds a page with another id (a record too)  |   line, never filed             |
| signed in a repository this run may not ask (a      | left as it is, for a run that   |
|   cloud run it is not attached to)                  |   can ask                       |
| a retraction from the release's sender              | that release withdrawn: never   |
|                                                     |   lands, or kept deprecated and |
|                                                     |   no longer evidence            |

What a release names comes from another space, so nothing in it is used as it stands:

- Its id must be a page id, its `released_from` and `signed_in` space names, its `subject` and
  `kind` slugs, a conflict note's `page` and a retraction's `retracts` page ids, and every name
  single-line text, and it is never a review batch (`review_batch`, `lines`: only this space's
  check writes one, and its lines name pages here for a verdict); a release that fails waits
  `pending: malformed release`, for the space owner, and no line is written from its values.
- Where it lands is `sign.dest_of`: a page file under `knowledge/` (a record under
  `knowledge/sources/` only), in a kind this space has, never through a link, never at a
  path the gate guards (a `CLAUDE.md`, a `.claude/`); anything else waits `pending: bad
  target`. A signature binds an id, not a place, so a signed release lands only where its id
  is not a page already (a fresh scan of `knowledge/`) or at that very page: a release whose
  id is a page elsewhere waits `pending: its id is already the page at ...`, and one whose
  target holds a page with another id waits `pending: another page is at its target`. The one
  exception is the same page moved: when the page there is stable, carries the id, was signed
  by the release's signer in its signing space no later than the release (`_older`), the
  release comes from that signing space or from the space the page came from, names no
  `target` of its own and its new place is free, it moves there (the old file goes) once the
  signature checks; signed by someone else, it waits for that page's signer, as in place. A
  release signed before the page here never overwrites or moves it: it is dropped, logged.
  These
  holds, a malformed release and a bad target are the agent's (`_hold_for_agent`: a `health`
  line on Needs you), never a question to the person; they stay `pending`, so no pull request files one unseen. A
  conflict note marks only a page a fresh scan of `knowledge/` finds under that id.
- The trust root is pinned here, never by the release (rules.md section 9). The signing
  space (`signed_in`, else `released_from`) must be one this space knows: itself, a mount,
  a space its registry homes subjects in or a person's registered workspace. Its repository
  comes from this space's own data (`release.trust_repo`: a mount, the space's own registry
  subject, or the `workspace_repo` onboarding records for each person's workspace); for a
  person's workspace with no repository recorded, only a repository under that person's own
  registered GitHub login (`match.github`, the login the gate requires of the account that
  opened the release) is accepted, and with no login either the release waits pending with
  what to add to the registry. The release's `signed_repo`
  must name that same repository, `signed_by` must be one of that space's people (below),
  exactly, and that repository's `log.md` must hold the whole line
  `- <ISO> fingerprint <id> <sha256> by <signer>` and, for its `signed_fields`, the line
  `- <ISO> fields <id> <sha256> by <signer>`. The people who sign in a space are its
  owner and audience list, the person whose workspace it is, and for the page's own subject,
  when it is homed there, the owners and crew of that subject and its umbrellas
  (`registry_people`, read from this space's own registry and the brain's only). A release that names no `signed_repo`, or that
  was signed before its fields were bound (no `signed_fields`), is the upgrade's backlog
  (`backlog_problem`): it is checked against the repository this space pins, and lands only
  when that repository's log holds its fingerprint line and the signer's own page there
  carries the same signature and fields; else it is pending for a person (a space in `mode:
  fixture`, which is offline, files one with no `signed_repo` unchecked and holds one with no
  `signed_fields`). A `signed_fields` that does not match its frontmatter is pending as a
  fingerprint mismatch is.
- A change to a signed page lands silently only from that page's own signer and space (a page
  that names no `signed_in` was signed where it `landed_from`, else here). The
  trust switch holds a release whose releaser or signing space is under it. A retraction
  withdraws a release only when it names the same releasing space and the same `released_by`.
- Every line written to `log.md` is one line: a line break inside a value becomes a space.

Once a scaled week `sample_item` draws up to five silent landings, at-once arrivals and
request answers sent under a standing choice of the week for the domain owner (else the space owner); `apply_sample` records the verdict in
`_meta/state/samples.jsonl`, sends a content-free `sample_failed` signal to the releasing
space for each failed one (that space pauses the path that let it through), and
`recompute_trust` puts a releaser whose failed share over four scaled weeks is above 0.2
(five samples or more) under the trust switch (`_meta/state/trust.yaml`): its signed
releases land pending until the share recovers. Nothing here writes `stable` of its own: a
silent landing keeps the releaser's signature exactly as it arrived.

One record arriving twice. When colleagues sweep the same mail, message or meeting, each room
may release it, with another id and, for a call, a transcript with other errors, so the exact
`record_key` above misses it. `duplicates` lists the likely pairs for an agent to judge, and
never compares text: two records here, at least one released from another space (a page under
`knowledge/sources/`, or a record still waiting in `_inbound/candidates/`), that share a subject
(copies for two different subjects each count once there, so they never count twice), with the
same original id once written one way (`_id_key`: `slack:C0B:17.68` is `C0B/17.68`), or mail
from one sender in one minute (every recipient's copy), or two calls on one day starting within
TWIN_CALL_MINUTES, whatever their titles. One record is not yet a duplicate: two routers may
have sent different verbatim parts of it. `mark` marks a copy whose text its main already holds
`duplicate_of: {id, by, at}`: it stays where it is, distill counts it once through its main
(`distill.counted`) and Needs you does not ask to file it. `keep-both` records a pair that stays
two pieces of evidence (two records, or copies that each hold what the other lacks) in
`_meta/state/duplicates.jsonl`, the pages untouched, so it is not listed again. `undo` takes a
mark back and records the pair as kept. Nothing is deleted.

    python landing.py [root] duplicates                  the likely pairs, as JSON
    python landing.py [root] mark <main> <copy> --by NAME
    python landing.py [root] keep-both <a> <b> --by NAME
    python landing.py [root] undo <copy> --by NAME
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import random
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ghrest  # noqa: E402
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

CANDIDATES = Path("_inbound") / "candidates"
LANDED = Path("_meta") / "state" / "landed.jsonl"
SAMPLES = Path("_meta") / "state" / "samples.jsonl"
TRUST = Path("_meta") / "state" / "trust.yaml"
SIGNALS_OUT = Path("_outbound") / "signals"
SOURCES = Path("knowledge") / "sources"
DUPLICATES = Path("_meta") / "state" / "duplicates.jsonl"   # pairs an agent read and found to be two records
TWIN_CALL_MINUTES = 30   # two recordings of one meeting start this close, on one day
STRIP = ("class", "stage_after", "listed", "listed_at", "destination", "from_space", "landing",
         "disagrees_with", "for", "target", "recommendation", "depends", "deadline", "approved_by",
         "approved_at", "page", "health", "held_at")
# What the agent does about a release held because only a bug or an attack makes it (a malformed
# release, a bad target, an id that is a page already, a page in the way or one that cannot be
# read): nothing of it is filed, the person is not asked, and the health line says so
# (`needs_you.landing_health`, whose list of these marks is the same).
AGENT_WORK = ("nothing of it is filed and nobody needs to answer it; it is read again on every landing, "
              "lands once its sender sends it again as it should be, and is dropped, logged, after two weeks")
AGENT_HOLD_DAYS = 14   # scaled days: a hold only a bug or an attack makes never sits on the health line forever
# A person's workspace whose repository the brain has not verified yet: the workspace's note names
# it and classify.apply_note checks it with the host, so the release waits for the agent's work.
NO_REPO_YET = "no repository is known here for"
REPO_WORK = ("it lands once this space knows that space's repository: a person's workspace names it in "
             "its next note and the brain's agent checks it; a project's comes with its registry entry")
SAMPLE_SIZE = 5
TRUST_SHARE = 0.2
TRUST_MIN = 5
TRUST_DAYS = 28


class _Unreachable:
    """The type of UNREACHABLE: signer_log's answer for a repository this session may not ask (a
    cloud run it is not attached to), neither the signer's history nor its absence."""


UNREACHABLE = _Unreachable()


@dataclass
class LandingReport:
    silent: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    disagreements: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    records: list = field(default_factory=list)
    record_duplicates: list = field(default_factory=list)
    withdrawn: list = field(default_factory=list)
    stale: list = field(default_factory=list)   # a signature older than the page here: dropped, logged


def record_key(fm: dict, body: str) -> str:
    """One record, whoever released it: its title, its time to the minute and its text."""
    at = str(fm.get("at") or "")[:16]
    return pages.fingerprint(f"{str(fm.get('title') or '').strip().lower()}|{at}|{pages.normalize(body)}")


def _file_record(root: Path, path: Path, fm: dict, body: str, now: dt.datetime, report, here: dict) -> bool:
    """A record handed sideways is evidence at its home: a witness page under
    `knowledge/sources/<yyyy>/<mm>/`, a draft forever, never an item to sign. The same record
    released by several people lands once; the others are named under `also_from`. A record
    whose id is already another page here (`here`, id -> path under `knowledge/`) is not filed:
    it would write over that page, or give one id two pages; False, and the caller holds it."""
    releaser = str(fm.get("released_from") or "")
    pid = pages.check_page_id(str(fm.get("id") or path.stem), what="record id")   # land() checked it too
    try:
        when = pages.parse_iso(fm.get("at") or fm.get("released_at"))
    except (TypeError, ValueError, OverflowError):
        when = now
    # the file name is the checked id and the folder its date: confined under knowledge/sources/
    target = pages.confine(root, f"knowledge/sources/{when:%Y}/{when:%m}/{pid}.md",
                           zones=("knowledge/sources/",), guarded=True, what="record")
    folder = target.parent
    key = record_key(fm, body)
    for other in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            ofm, obody = pages.read(other)
        except Exception:
            continue
        if ofm.get("record_key") == key:
            also = sorted(set(ofm.get("also_from") or []) | {releaser})
            if releaser != ofm.get("landed_from") and also != (ofm.get("also_from") or []):
                pages.write(other, dict(ofm, also_from=also), obody, root=root)
            path.unlink()
            _append(root / "log.md", f"- {pages.iso(now)} landed duplicate record {pid} of "
                                     f"{other.relative_to(root).as_posix()} from {releaser}")
            report.record_duplicates.append(pid)
            return True
    if pid in here or target.exists():   # another record, or a signed page, under this id
        return False
    page_fm = {k: v for k, v in fm.items() if k not in STRIP}
    page_fm.update(status="draft", landed_at=pages.iso(now), landed_from=releaser, record_key=key)
    pages.write(target, page_fm, body, root=root)
    path.unlink()
    rel = target.relative_to(root).as_posix()
    _append(root / "log.md", f"- {pages.iso(now)} filed record {pid} at {rel} from {releaser}")
    _append(root / LANDED, json.dumps({"at": pages.iso(now), "id": pid, "target": rel, "from": releaser,
                                       "by": None, "subject": fm.get("subject"),
                                       "class": fm.get("release_class"), "record": True}))
    report.records.append(pid)
    here[pid] = rel
    return True


def _apply_retraction(root: Path, path: Path, fm: dict, now: dt.datetime, report) -> None:
    """A retraction that arrived withdraws the release it names, wherever that went here: still
    a candidate, it is marked and never lands; filed, it stays as it is, deprecated, and stops
    counting as evidence. Only the sender's own release is withdrawn, and only when the
    retraction names the person who released it (`released_by`); a filed signed page only when
    it was signed in the sender's space (`signed_in`, the gate's test), else it is marked
    contested and kept. Nothing is deleted but the
    retraction itself, which is a message, not a record. Both names are the ones the releases
    state, so this narrows a retraction from a space that took another's name; it does not
    prove the sender."""
    target_id, sender = str(fm.get("retracts") or ""), str(fm.get("released_from") or "")
    if not pages.is_page_id(target_id):   # land() holds a malformed one; never a path from it
        path.unlink()
        _append(root / "log.md", f"- {pages.iso(now)} retraction from {sender}: it names no page id")
        return
    found = root / CANDIDATES / f"{target_id}.md"
    if not found.exists():
        base = root / "knowledge"
        found = next((p for p in sorted(base.rglob(f"{target_id}.md")) if pages.is_page_path(p)), None) \
            if base.is_dir() else None
    try:
        tfm, tbody = pages.read(found) if found is not None else ({}, "")
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        found, tfm, tbody = None, {}, ""
    # only the sender's own release, and only as the person who released it: both as the
    # releases state them, so this narrows a spoofed retraction without proving the sender
    same_sender = str(tfm.get("landed_from") or tfm.get("released_from") or "") == sender
    same_person = not tfm.get("released_by") or str(tfm.get("released_by")) == str(fm.get("released_by") or "")
    if found is not None and same_sender and not same_person:
        path.unlink()
        _append(root / "log.md", f"- {pages.iso(now)} retraction of {target_id} from {sender}: "
                                 f"released by {fm.get('released_by')}, not by {tfm.get('released_by')}; kept")
        return
    if found is not None and same_sender and found.parent != root / CANDIDATES and claims_signature(tfm) \
            and str(tfm.get("signed_in") or "") != sender:
        # signed in another space than the sender's (a draft he released, signed here): his word
        # withdraws nothing the gate would hold; the page is marked contested for its signer's batch
        rid = str(fm.get("id") or path.stem)
        contested = [str(x) for x in (tfm.get("contested") or [])]
        if rid not in contested:
            pages.write(found, dict(tfm, contested=contested + [rid]), tbody, root=root)
        path.unlink()
        _append(root / "log.md", f"- {pages.iso(now)} retraction of {target_id} from {sender}: "
                                 f"signed in {tfm.get('signed_in')} by {tfm.get('signed_by')}; kept, contested")
        return
    if found is not None and same_sender:
        if not pages.withdrawn(tfm):
            update = {"retracted": True}
            if found.parent != root / CANDIDATES:
                update.update(status="deprecated", retracted_by=fm.get("released_by") or sender,
                              retracted_at=pages.iso(now))
            pages.write(found, dict(tfm, **update), tbody, root=root)
        _append(root / "log.md", f"- {pages.iso(now)} withdrew {target_id} at "
                                 f"{found.relative_to(root).as_posix()}: {sender} retracted it")
        report.withdrawn.append(target_id)
    else:
        _append(root / "log.md", f"- {pages.iso(now)} retraction of {target_id} from {sender}: "
                                 "nothing here carries it")
    path.unlink()


CONFLICTS = Path("_inbound") / "conflicts"


def _contested_page(root: Path, page) -> Path | None:
    """The page a conflict note contests: found by a fresh scan of `knowledge/` (never the
    committed index, which a pull request can rewrite), confined there, and carrying that id
    itself. None when there is no such page here."""
    import ids
    if not pages.is_page_id(page):
        return None
    rel = ids.scan(root, roots=("knowledge",)).get(page)
    if not rel or rel.startswith("knowledge/sources/"):
        return None
    try:
        target = pages.confine(root, rel, zones=("knowledge/",), guarded=True, what="contested page")
        pfm, _ = pages.read(target)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        return None
    return target if str(pfm.get("id") or "") == page else None


def _land_conflict(root: Path, path: Path, fm: dict, body: str, now: dt.datetime) -> bool:
    """A conflict note marks its page contested (frontmatter only: the signed body and its
    fingerprint stay untouched) and waits under `_inbound/conflicts/` for the weekly batch. Only
    a page under `knowledge/` that carries the note's `page` id is marked (`_contested_page`)."""
    note_id = str(fm.get("id") or path.stem)
    target = _contested_page(root, fm.get("page"))
    if target is not None:
        pfm, pbody = pages.read(target)
        contested = [str(x) for x in (pfm.get("contested") or [])]
        if note_id not in contested:
            pages.write(target, dict(pfm, contested=contested + [note_id]), pbody, root=root)
    dest = root / CONFLICTS / pages.check_name(path.name, what="conflict note file name")
    pages.write(dest, fm, body, root=root)
    path.unlink()
    _append(root / "log.md", f"- {pages.iso(now)} conflict note {note_id} from {fm.get('released_from')} "
                             + (f"contests {fm.get('page')}" if target is not None
                                else f"contests {fm.get('page')}: no such page here"))
    return True


def _jsonl(path: Path) -> list:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append(path: Path, line: str) -> None:
    """Append one line, never through a link (pages.append_text). The line is made one line
    first (`sign.one_line`): a value from a release holding a line break can never start a
    line of its own in `log.md`, the trust root, and pass for a signature or a verdict."""
    import sign
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lead = "" if not text or text.endswith("\n") else "\n"
    pages.append_text(path, lead + sign.one_line(line).rstrip() + "\n")


def signed_ok(fm: dict, body: str) -> bool:
    """Stable, signed by someone, its body the one its fingerprint names, and its signed fields
    (when it carries them) the ones `signed_fields` names."""
    import sign
    return (str(fm.get("status") or "") == "stable" and bool(fm.get("signed_by"))
            and fm.get("fingerprint") == pages.fingerprint(body) and sign.fields_problem(fm) is None)


def claims_signature(fm: dict) -> bool:
    return str(fm.get("status") or "") == "stable" or bool(fm.get("fingerprint"))


# what a release says in one line of text: never a mapping, never a line break (it would start
# a line of its own wherever it is written)
ONE_LINE_FIELDS = ("released_by", "signed_by", "signed_at", "released_at", "signed_repo", "fingerprint",
                   "signed_fields", "release_class")


def malformed(fm: dict, stem: str) -> str | None:
    """What in a release cannot be used as it stands (its id, its sender's name, a slug, a page
    id, a line of text), or None. The answer names the field, never its value."""
    if not pages.is_page_id(fm.get("id") or stem):
        return "its id is not a page id"
    if fm.get("review_batch") or fm.get("lines"):
        # a review batch names pages here for a verdict: only this space's check writes one
        return "a review batch is never released"
    if not pages.is_slug(fm.get("released_from")):
        return "`released_from` is not a space name"
    for key in ("signed_in", "subject") + (("kind",) if claims_signature(fm) else ()):
        if fm.get(key) not in (None, "") and not pages.is_slug(fm.get(key)):
            return f"`{key}` is not a slug"
    # a conflict note's `page` names the page it contests, so it must be a page id; on any other
    # release `page` is ignored (STRIP drops it on filing): a release signed before sign.py
    # stopped sending it carries the path of the page in its sender's space, still in flight
    keys = (("page",) if fm.get("conflict_note") else ()) + ("retracts",)
    for key in keys:
        if fm.get(key) not in (None, "") and not pages.is_page_id(fm.get(key)):
            return f"`{key}` is not a page id"
    for key in ONE_LINE_FIELDS + (() if claims_signature(fm) else ("kind",)):
        value = fm.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (dict, list)) or not pages.is_record_id(str(value)):
            return f"`{key}` is not one line of text"
    return None


def pending_releasers(root: Path) -> set:
    path = root / TRUST
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(x) for x in (data.get("pending") or [])} if isinstance(data, dict) else set()


def domain_owner(reg, space, subject) -> str:
    if subject and pages.is_slug(subject):
        domain = reg.domain_of(str(subject))
        if domain is not None:
            return reg.owner_of(domain.id) or space.owner
        return reg.owner_of(str(subject)) or space.owner
    return space.owner


def _target(root: Path, space, fm: dict, path: Path, now: dt.datetime) -> Path:
    """Where a signed release lands, confined (sign.dest_of is the one rule), or UnsafePath."""
    import sign
    return sign.dest_of(root, fm, path, now, kinds=space.all_kinds)


def _mark(path: Path, fm: dict, body: str, **fields) -> None:
    """Mark a release where it waits. A mark for a person never carries a `health` line, whatever
    the release said: only `_hold_for_agent` writes one, so no sender hides its release that way.
    Likewise `disagrees_with`: only the disagreement mark writes it, so a stale or forged list goes."""
    fields.setdefault("health", None)
    fields.setdefault("disagrees_with", None)
    changed = {k: v for k, v in fields.items() if fm.get(k) != v}
    if changed:
        new = dict(fm, **changed)
        for key in ("health", "disagrees_with"):
            if new.get(key) is None:
                new.pop(key, None)
        pages.write(path, new, body)


def _hold_for_agent(root: Path, path: Path, fm: dict, body: str, mark: str, space, now,
                    work: str = AGENT_WORK) -> None:
    """Hold a release that only a bug or an attack produces: `landing: pending: ...` as for any
    hold (so no pull request files it unseen, `gate.page_reasons`), for the space owner, with a
    `health` line the agent reads instead of a question to the person (`needs_you.health_work`),
    and `held_at`, when it was first held. Nothing else is written: the release itself is the
    record. One its sender has not sent again within AGENT_HOLD_DAYS is dropped, logged, so the
    health line never grows forever; a `held_at` the sender wrote can only drop its own release
    sooner, never keep it later than now. A wait for a repository (REPO_WORK) is never dropped:
    the agent's own work ends it."""
    try:
        since = min(pages.parse_iso(fm.get("held_at")), now)
    except (TypeError, ValueError, OverflowError):
        since = now
    if work == AGENT_WORK and now - since > space.scale(AGENT_HOLD_DAYS * 24):
        path.unlink()
        _append(root / "log.md", f"- {pages.iso(now)} dropped {path.stem} from {fm.get('released_from')}: "
                                 f"held since {pages.iso(since)} ({mark}) and never sent again")
        return
    _mark(path, fm, body, landing=mark, health=work, held_at=pages.iso(since), **{"for": space.owner})


def stakes(fm: dict) -> str | None:
    """Why the sender judged an item high stakes (an older flag carries no why); None when it
    was not flagged. No word in it decides: the router or a person judged it."""
    value = fm.get("high_stakes")
    if not value:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else "flagged"


def signer_log(repo: str, cache: dict) -> str | _Unreachable | None:
    """The signer's own `log.md`, read with this person's grants (the host's command line); None
    when it cannot be read; UNREACHABLE when this session may not ask that repository at all (a
    cloud run it is not attached to)."""
    if repo in cache:
        return cache[repo]
    import subprocess
    try:
        res = subprocess.run(["gh", "api", f"repos/{repo}/contents/log.md", "-H", "Accept: application/vnd.github.raw"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if res.returncode == 0:
            cache[repo] = res.stdout
        else:
            cache[repo] = UNREACHABLE if ghrest.NOT_ENABLED.search(f"{res.stderr}\n{res.stdout}") else None
    except Exception:
        cache[repo] = None
    return cache[repo]


def signing_space(space, reg, name: str, subject=None) -> tuple[str | None, set]:
    """The repository of the space called `name` and the people who sign in it, as this space's
    own data knows them, never as a release says: this space itself (its origin remote, its
    owner and audience); else a mount (its `repo`, and the owner and audience of the mounted
    space.yaml on disk); and for either, from the registry this space trusts
    (`registry_people`), the person whose registered workspace it is and, for the page's
    `subject`, the explicit owners and crew of that subject and its umbrellas when it is homed
    there (a walled classification's crew for its home too). With `subject` None, the crew of
    every subject homed there (what the gate asks: who could the registry make a signer). The
    repository is `release.trust_repo`'s: a mount, the registry subject that is that space
    itself, or the `workspace_repo` of the person whose workspace it is, never a subject merely
    homed there (a client's `match.repo` names the client's code)."""
    import release
    import sign
    names: set = set()

    def people_of(other) -> None:
        names.add(other.owner)
        if isinstance(other.audience, list):
            names.update(str(a) for a in other.audience)

    if name == space.name:
        people_of(space)
        repo = sign._own_repo(space.root)
    else:
        found = space.space_root(name)
        # a corridor's space.yaml is both sides'; a mount the trusted registry does not know
        # (a corridor under a custom name, whatever its space.yaml now says) names no signer
        if found is not None and not space.is_corridor(name) and (
                release._is_brain(space, name) or (hasattr(reg, "vouched") and reg.vouched(name))):
            try:
                people_of(spaceyaml.load(found))
            except Exception:
                pass
        repo = release.trust_repo(space, name)
    names |= registry_people(reg, name, here=name == space.name, subject=subject)
    names.discard("")
    names.discard("None")
    return repo, names


def _trusted(reg):
    """The registry as a trust decision reads it (`Registry.trusted_view`: its own entries and
    the brain's, never a channel's or a project mount's, which both sides write)."""
    return reg.trusted_view() if hasattr(reg, "trusted_view") else reg


def _homed_in(reg, subject, name: str, here: bool) -> bool:
    """The subject is homed in the space `name`: its home, the home the registry gives it, the
    home of a walled classification it carries, or (`here`) this registry holds it with no home."""
    c = subject.extra.get("classification") if isinstance(subject.extra.get("classification"), dict) else {}
    try:
        home = reg.home_of(subject.id)
    except Exception:
        home = None
    walled_here = c.get("class") == "walled" and str(c.get("home") or "") == name
    held_here = here and home is None and subject.id in getattr(reg, "subjects", {})
    return subject.home == name or home == name or walled_here or held_here


def _crew_names(subject, titles: dict) -> set:
    crew = list(subject.crew or []) if isinstance(subject.crew, list) else [subject.crew] if subject.crew else []
    return {titles[str(p)] for p in crew if str(p) in titles}


def registry_people(reg, name: str, here: bool = False, subject=None) -> set:
    """The people this registry gives the space called `name`: the person whose registered
    workspace it is and, for a page about `subject` homed there (`_homed_in`), the explicit
    owners and the crew of that subject and of its umbrellas, and the crew of the walled
    classification that governs it when its home is that space, by their registered names. The
    crew of one subject never signs a page about another. A crew member who signs in a project
    space is one of its people, though the brain mounts no project space; with `here` (the
    space is this one), a subject its own registry holds with no home elsewhere counts as homed
    here (a domain the brain holds). `subject` None is the gate's question instead: the people
    of every subject homed there. `subject` "" (a page about no subject) adds no one. Only the
    registry this space trusts is read (`_trusted`): its own and the brain's, whose changes wait
    for their owner (`gate`); never a corridor's, which the asker writes too."""
    if reg is None:
        return set()
    reg = _trusted(reg)
    every = [s for s in reg.everywhere() if s.state != "archived"]
    titles = {s.id: s.title for s in every if s.kind == "people"}
    out = {s.title for s in every if s.kind == "people" and str(s.extra.get("workspace") or "") == name}
    if subject is None:   # the union: everyone the registry could make a signer of `name`
        for one in every:
            if _homed_in(reg, one, name, here):
                out |= ({str(one.owner)} if one.owner else set()) | _crew_names(one, titles)
                c = one.extra.get("classification") if isinstance(one.extra.get("classification"), dict) else {}
                if c.get("class") == "walled" and str(c.get("home") or "") == name:
                    out |= {titles[str(p)] for p in c.get("crew") or [] if str(p) in titles}
        return out
    page_subject = reg.get(str(subject)) if subject and pages.is_slug(subject) else None
    if page_subject is None or page_subject.state == "archived" or not _homed_in(reg, page_subject, name, here):
        return out
    for one in [page_subject, *reg.ancestors_of(page_subject.id)]:
        if one.state != "archived":
            out |= ({str(one.owner)} if one.owner else set()) | _crew_names(one, titles)
    try:
        governing = reg.classification(page_subject.id) or {}
    except Exception:
        governing = {}
    if governing.get("class") == "walled" and str(governing.get("home") or "") == name:
        out |= {titles[str(p)] for p in governing.get("crew") or [] if str(p) in titles}
    return out


def workspace_logins(reg, name: str) -> set:
    """The GitHub logins (`match.github`, lower case) of the one person whose registered
    workspace is the space `name`; empty when no one's is, or when several people name it. Only
    the registry this space trusts is read (`_trusted`)."""
    people = [s for s in (_trusted(reg).everywhere() if reg is not None else [])
              if s.kind == "people" and s.state != "archived" and str(s.extra.get("workspace") or "") == name]
    if len(people) != 1:
        return set()
    got = people[0].match.get("github") if isinstance(people[0].match, dict) else None
    return {str(v).strip().casefold() for v in (got if isinstance(got, list) else [got])
            if isinstance(v, str) and v.strip()}


def _is_workspace(reg, name: str) -> bool:
    """`name` is a registered person's workspace, in the registry this space trusts."""
    return reg is not None and any(s.kind == "people" and s.state != "archived"
                                   and str(s.extra.get("workspace") or "") == name
                                   for s in _trusted(reg).everywhere())


def owned_by_login(given, reg, name: str) -> str | None:
    """The release's `signed_repo` as `https://github.com/<login>/<name>` when the space `name`
    is a person's registered workspace, no repository is recorded for it, and that repository
    sits under the person's own GitHub login as this registry gives it; else None. The login is
    this space's own data (a change waits for the owner's click, `gate.registry_reasons`), the
    same login the gate requires of the account that opened the release, and only that account,
    or someone it gave write access, can write the `log.md` of a repository it owns."""
    logins = workspace_logins(reg, name)
    url = registry_mod.clone_url(given) if isinstance(given, str) else None
    slug = _github_slug(url) if url else None
    if not logins or slug is None:
        return None
    return url if slug.split("/", 1)[0].casefold() in logins else None


def _github_slug(repo) -> str | None:
    """`owner/name` of a GitHub repository reference, the form `gh api` asks; None otherwise."""
    url = registry_mod.repo_url(repo) or ""
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9][\w-]*/[\w.-]+)", url)
    return match.group(1) if match else None


def _has_line(text: str, word: str, pid, digest, signer) -> bool:
    """The signer's history holds the whole line `- <ISO> <word> <id> <sha256> by <signer>`:
    matched line by line and in full, so `by Ada` never matches `by Ada Okafor`."""
    line = re.compile(r"- \S+ " + word + " " + re.escape(str(pid)) + " " + re.escape(str(digest))
                      + " by " + re.escape(str(signer)))
    return any(line.fullmatch(row.strip()) for row in text.splitlines())


def signer_problem(fm: dict, cache: dict, space, reg=None):
    """The trust root (rules.md section 9), pinned by this space and never by the release: None
    when the signing space's own history shows this signature; UNREACHABLE when this session
    may not ask that repository (a cloud run it is not attached to: the release waits as it is
    for a run that can); else why it lands pending. The signing space is `signed_in`, else the
    releaser. Its repository and its people are `signing_space`'s (for a person's workspace no
    repository is recorded for, a `signed_repo` under that person's own registered GitHub login,
    `owned_by_login`; with neither, it waits for the agent until the brain records the
    workspace's repository, `NO_REPO_YET`); the release's `signed_repo` must name that
    repository, `signed_by` must be one of those people, exactly, and that
    repository's `log.md` must hold the whole fingerprint line and, for the signed fields, the
    whole `fields` line. A release that names no `signed_repo` is pending, except in a space
    in `mode: fixture` (offline: there is no repository to ask)."""
    given = fm.get("signed_repo")
    if not given:
        if space is not None and space.mode == "fixture":
            return None
        return "it names no signer repository"
    if space is None:
        return "no space to check the signer against"
    reg = reg if reg is not None else registry_mod.Registry(space.root, space)
    signing = str(fm.get("signed_in") or fm.get("released_from") or "")
    if not pages.is_slug(signing):
        return "it names no signing space"
    expected, people = signing_space(space, reg, signing, subject=str(fm.get("subject") or ""))
    if not expected and signing != space.name:
        # a person's workspace no repository is recorded for: its own login vouches, or nothing
        expected = owned_by_login(given, reg, signing)
    if not expected:
        return _no_repo(reg, signing)
    if not registry_mod.same_repo(given, expected):
        return f"it names a repository that is not {signing}'s"
    signer = str(fm.get("signed_by") or "")
    if signer not in people:
        return f"its signer is not one of the people who sign in {signing}"
    slug = _github_slug(expected)
    if slug is None:
        return f"{signing}'s repository is not one this run can read"
    text = signer_log(slug, cache)
    if text is UNREACHABLE:
        return UNREACHABLE
    if text is None or not _has_line(text, "fingerprint", fm.get("id"), fm.get("fingerprint"), signer):
        return "the signer's history does not show this signature"
    if fm.get("signed_fields") and not _has_line(text, "fields", fm.get("id"), fm.get("signed_fields"), signer):
        return "the signer's history does not show its signed fields"
    return None


def _no_repo(reg, signing: str) -> str:
    """Why a release from `signing` waits when this space pins no repository for it. For a
    registered person's workspace the answer starts with NO_REPO_YET: the agent's wait, which
    lands it once the brain has verified that workspace's repository (classify.py)."""
    if _is_workspace(reg, signing):
        return (f"{NO_REPO_YET} {signing} yet: its workspace names it in its next workspace note, and "
                "the brain's agent checks it before it records `workspace_repo`")
    return f"{NO_REPO_YET} {signing}"


def signer_file(repo: str, rel: str, cache: dict) -> str | _Unreachable | None:
    """One committed file of the signer's repository (its default branch), read like its log
    (`signer_log`): its text, None when it cannot be read, UNREACHABLE when this session may not
    ask that repository. `rel` is a page path `sign.check_target` accepts, or the id index."""
    key = (repo, rel)
    if key in cache:
        return cache[key]
    import subprocess
    try:
        res = subprocess.run(["gh", "api", f"repos/{repo}/contents/{rel}", "-H", "Accept: application/vnd.github.raw"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if res.returncode == 0:
            cache[key] = res.stdout
        else:
            cache[key] = UNREACHABLE if ghrest.NOT_ENABLED.search(f"{res.stderr}\n{res.stdout}") else None
    except Exception:
        cache[key] = None
    return cache[key]


def _page_path(value) -> str | None:
    """A path in the signer's repository a signed page may sit at, or None: never a record, a
    hub or anything outside `knowledge/` (`sign.check_target`)."""
    import sign
    try:
        return sign.check_target(value, record=False) if isinstance(value, str) else None
    except pages.UnsafePath:
        return None


def signer_page(repo: str, fm: dict, cache: dict):
    """(frontmatter, body) of the page with the release's id as the signer's repository holds it
    now; None when it cannot be found or read; UNREACHABLE when this session may not ask. It is
    found at the release's `page` (a release signed before sign.py stopped sending it names its
    path there), else through the signer's own id index (`_meta/state/ids.yaml`); either way it
    must carry that id itself."""
    pid = str(fm.get("id") or "")
    rel = _page_path(fm.get("page"))
    if rel is None:
        index = signer_file(repo, "_meta/state/ids.yaml", cache)
        if index is UNREACHABLE:
            return UNREACHABLE
        try:
            data = yaml.safe_load(index) if isinstance(index, str) else None
        except yaml.YAMLError:
            data = None
        ids = data.get("ids", data) if isinstance(data, dict) else None
        rel = _page_path(ids.get(pid)) if isinstance(ids, dict) else None
    if rel is None:
        return None
    text = signer_file(repo, rel, cache)
    if text is UNREACHABLE or text is None:
        return text
    try:
        cfm, cbody = pages.split(text)
    except (ValueError, yaml.YAMLError):
        return None
    return (cfm, cbody) if str(cfm.get("id") or "") == pid else None


def backlog_problem(fm: dict, cache: dict, space, reg=None):
    """The trust root for a release signed before its fields were bound (no `signed_fields`) or
    released naming no `signed_repo`, the upgrade's backlog: None when it lands as signed, else
    as `signer_problem`. The repository is the one this space pins for the signing space (never
    the release's: a `signed_repo` it names must be that one), the signer one of its people, its
    log must hold the whole fingerprint line (and the fields line when it has one), and the
    signer's own page, as that repository holds it now (`signer_page`), must carry this very
    signature: the same id, text fingerprint, signer, time, title, type, subject, kind and stakes.
    Only the signer's repository writes that page, so the fields no digest binds are the signer's."""
    import sign
    if space is None:
        return "no space to check the signer against"
    reg = reg if reg is not None else registry_mod.Registry(space.root, space)
    signing = str(fm.get("signed_in") or fm.get("released_from") or "")
    if not pages.is_slug(signing):
        return "it names no signing space"
    given = fm.get("signed_repo")
    expected, people = signing_space(space, reg, signing, subject=str(fm.get("subject") or ""))
    if not expected and signing != space.name and given:
        expected = owned_by_login(given, reg, signing)
    if not expected:
        # a person's workspace the brain has not verified a repository for yet: the agent's wait
        # (classify records `workspace_repo`), retried on every landing, never a question
        return _no_repo(reg, signing) if given or _is_workspace(reg, signing) else "it names no signer repository"
    if given and not registry_mod.same_repo(given, expected):
        return f"it names a repository that is not {signing}'s"
    signer = str(fm.get("signed_by") or "")
    if signer not in people:
        return f"its signer is not one of the people who sign in {signing}"
    slug = _github_slug(expected)
    if slug is None:
        return f"{signing}'s repository is not one this run can read"
    text = signer_log(slug, cache)
    if text is UNREACHABLE:
        return UNREACHABLE
    if text is None or not _has_line(text, "fingerprint", fm.get("id"), fm.get("fingerprint"), signer):
        return "the signer's history does not show this signature"
    if fm.get("signed_fields") and not _has_line(text, "fields", fm.get("id"), fm.get("signed_fields"), signer):
        return "the signer's history does not show its signed fields"
    found = signer_page(slug, fm, cache)
    if found is UNREACHABLE:
        return UNREACHABLE
    what = "its signed fields carry no digest" if not fm.get("signed_fields") else "it names no signer repository"
    if found is None:
        return f"{what}, and the signer's own page could not be read to compare"
    cfm = found[0]
    keys = [k for k in sign.SIGNED_FIELDS if k != "signed_in"] + ["fingerprint", "status"]
    same = all(sign._plain(cfm.get(k)) == sign._plain(fm.get(k)) for k in keys)
    same = same and str(cfm.get("signed_in") or signing) == signing
    return None if same else f"{what}, and the signer's own page says otherwise"


def _older(fm: dict, old_fm: dict) -> bool:
    """The release was signed before the page here: an older signature, still in its signer's
    log, never rolls a page back to older text or an older place. A release whose signing time
    cannot be read counts as older; a page here that names none (or none that reads), as older
    than any release."""
    try:
        signed = pages.parse_iso(fm.get("signed_at"))
    except (TypeError, ValueError, OverflowError):
        return True
    try:
        return signed < pages.parse_iso(old_fm.get("signed_at")) if old_fm.get("signed_at") else False
    except (TypeError, ValueError, OverflowError):
        return False


def _movable(root: Path, elsewhere: str, pid: str, fm: dict, signing: str, space) -> tuple:
    """(the page already here under the release's id, why it may not move): why is None when the
    release may move it to its new target (a page whose kind or subject changed in its home moves
    there): a signed page under `knowledge/` (never a record), carrying that id itself, signed by
    the release's signer in the release's signing space, no later than the release (`_older`),
    and released by its signing space or by the space it came from (`landed_from`).
    Else why: "signer" (someone else signed the page here: its signer decides), "older" (an older
    signature: dropped, never a rollback), "sender" (another space re-sends it) or "no". The
    signature itself is checked before anything moves (`land`)."""
    if elsewhere.startswith("knowledge/sources/"):
        return None, "no"
    try:
        old = pages.confine(root, elsewhere, zones=("knowledge/",), guarded=True, what="page to move")
        old_fm, _ = pages.read(old)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, pages.UnsafePath):
        return None, "no"
    if str(old_fm.get("id") or "") != pid or str(old_fm.get("status")) != "stable":
        return None, "no"
    signed_where = str(old_fm.get("signed_in") or old_fm.get("landed_from") or space.name)
    if str(old_fm.get("signed_by") or "") != str(fm.get("signed_by") or "") or signed_where != signing:
        return old, "signer"
    if _older(fm, old_fm):
        return old, "older"
    releaser = str(fm.get("released_from") or "")
    if releaser not in (signing, str(old_fm.get("landed_from") or "")):
        return old, "sender"
    return old, None


def verified(fm: dict, cache: dict, space=None, reg=None) -> bool | str:
    """`signer_problem` as a verdict: True when the signer's history shows the signature,
    "unknown" when this run may not ask it, False otherwise (no space to pin it against
    included)."""
    problem = signer_problem(fm, cache, space, reg)
    if problem is UNREACHABLE:
        return "unknown"
    return problem is None


def land(root, space: spaceyaml.Space | None = None, reg=None, now: dt.datetime | None = None) -> LandingReport:
    root = Path(root)
    pages.refuse_copy(root)
    space = space or spaceyaml.load(root)
    now = now or space.now()
    import classify   # registry notes first: what lands below reads the registry as they left it
    if classify.land_notes(root, space, now)["applied"] or reg is None:
        reg = registry_mod.Registry(root, space)
    report = LandingReport()
    distrusted = pending_releasers(root)
    signer_cache: dict = {}
    groups: dict = {}
    for path in sorted((root / CANDIDATES).glob("*.md")):   # retractions first: what they name never lands
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("released_from") and fm.get("release_class") == "retraction" and not malformed(fm, path.stem):
            try:
                _apply_retraction(root, path, fm, now, report)
            except pages.UnsafePath:   # the page it names sits behind a link: left as it is
                continue
    here = None   # id -> path under knowledge/, scanned once, when a release gets that far
    for path in sorted((root / CANDIDATES).glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if not fm.get("released_from"):
            continue
        if "health" in fm:   # only `_hold_for_agent` writes one, below: never the sender's own
            fm = {k: v for k, v in fm.items() if k != "health"}
            pages.write(path, fm, body)
        problem = malformed(fm, path.stem)
        if problem:   # held, for the agent's health line; nothing is built from its values
            _hold_for_agent(root, path, fm, body, f"pending: malformed release ({problem})", space, now)
            report.pending.append(path.stem)
            continue
        pid = str(fm.get("id") or path.stem)
        if pages.withdrawn(fm):
            path.unlink()
            _append(root / "log.md", f"- {pages.iso(now)} dropped {pid} from {fm.get('released_from')}: "
                                     "retracted before it landed")
            report.withdrawn.append(pid)
            continue
        try:
            if fm.get("conflict_note"):
                if _land_conflict(root, path, fm, body, now):
                    report.conflicts.append(pid)
                continue
            if not claims_signature(fm) and str(fm.get("type")) == "source":
                # a high-stakes record waits for the domain owner's yes; it is never filed silently.
                # Marked every run, so a mark the sender pre-wrote (its own `for`, reason or rival) goes
                term = stakes(fm)
                if term:
                    mark = f"pending: high stakes ({term})"
                    if fm.get("landing") != mark:
                        report.pending.append(pid)
                    _mark(path, fm, body, landing=mark,
                          **{"for": domain_owner(reg, space, fm.get("subject"))})
                    continue
                if here is None:
                    import ids
                    here = ids.scan(root, roots=("knowledge",))
                if not _file_record(root, path, fm, body, now, report, here):
                    _hold_for_agent(root, path, fm, body, "pending: its id is already a page here", space, now)
                    report.pending.append(pid)
                continue
        except pages.UnsafePath as exc:   # it would write where it may not: held, nothing written
            _hold_for_agent(root, path, fm, body, f"pending: bad target ({exc})", space, now)
            report.pending.append(pid)
            continue
        if not claims_signature(fm):
            continue
        try:
            dest = _target(root, space, fm, path, now)
        except pages.UnsafePath as exc:   # aimed outside the pages: held, never written
            _hold_for_agent(root, path, fm, body, f"pending: bad target ({exc})", space, now)
            report.pending.append(pid)
            continue
        groups.setdefault(dest.relative_to(root).as_posix(), []).append((path, fm, body, dest))
    for target, group in sorted(groups.items()):
        prints = {fm.get("fingerprint") for _, fm, body, _ in group if signed_ok(fm, body)}
        if len(group) > 1 and len(prints) > 1:
            ids_ = [str(fm.get("id") or p.stem) for p, fm, _, _ in group]
            for path, fm, body, _ in group:
                me = str(fm.get("id") or path.stem)
                # frontmatter only (needs_you names the others): a body change would break the
                # fingerprint on the next run and fail gate.marks_only for the owner's sync push
                _mark(path, fm, body, landing="pending: disagreement",
                      disagrees_with=[i for i in ids_ if i != me],
                      **{"for": domain_owner(reg, space, fm.get("subject"))})
                report.disagreements.append(me)
            continue
        for path, fm, body, dest in group:
            pid = str(fm.get("id") or path.stem)
            owner = domain_owner(reg, space, fm.get("subject"))
            if not signed_ok(fm, body):
                _mark(path, fm, body, landing="pending: fingerprint mismatch", **{"for": owner})
                report.pending.append(pid)
                continue
            if not fm.get("signed_fields") and space.mode == "fixture":   # offline: nothing to compare
                _mark(path, fm, body, landing="pending: its signed fields carry no digest", **{"for": owner})
                report.pending.append(pid)
                continue
            # signed before its fields were bound, or released naming no repository: the signer's
            # own committed page vouches for its fields (`backlog_problem`), else a person looks
            legacy = not fm.get("signed_fields") or (not fm.get("signed_repo") and space.mode != "fixture")
            term = stakes(fm)
            if term:
                _mark(path, fm, body, landing=f"pending: high stakes ({term})", **{"for": owner})
                report.pending.append(pid)
                continue
            releaser = str(fm.get("released_from") or "")
            signing = str(fm.get("signed_in") or releaser)
            if releaser in distrusted or signing in distrusted:
                who = releaser if releaser in distrusted else signing
                _mark(path, fm, body, landing=f"pending: trust ({who} lands pending for now)",
                      **{"for": owner})
                report.pending.append(pid)
                continue
            # an id is one page in one place: a release that would put a second copy of it
            # elsewhere, or write over a page with another id (a genuine signature replayed at
            # another target, which the signature does not bind), waits for a person
            if here is None:
                import ids
                here = ids.scan(root, roots=("knowledge",))
            elsewhere = here.get(pid)
            moving = None   # the page this release moves to its new target, keeping its id
            theirs = None   # the page here under this id that someone else signed: its signer decides
            if elsewhere is not None and elsewhere != target:
                # only to the place its signed kind and subject name (never a `target` it carries,
                # which no signature binds), and never onto a file that is there already
                found, why = (None, "no") if fm.get("target") or dest.exists() else _movable(
                    root, elsewhere, pid, fm, signing, space)
                if why == "older":
                    _stale(root, path, pid, releaser, elsewhere, now, report)
                    continue
                if why == "signer":
                    theirs = found
                elif why is None:
                    moving = found
                else:
                    _hold_for_agent(root, path, fm, body, f"pending: its id is already the page at {elsewhere}",
                                    space, now)
                    report.pending.append(pid)
                    continue
            if dest.exists():
                try:
                    old_fm, _ = pages.read(dest)
                except (OSError, UnicodeError, ValueError, yaml.YAMLError):
                    _hold_for_agent(root, path, fm, body, "pending: the page there cannot be read", space, now)
                    report.pending.append(pid)
                    continue
                if str(old_fm.get("id") or "") != pid:
                    _hold_for_agent(root, path, fm, body, "pending: another page is at its target", space, now)
                    report.pending.append(pid)
                    continue
                if str(old_fm.get("status")) == "stable":
                    if old_fm.get("fingerprint") == fm.get("fingerprint"):
                        path.unlink()
                        _append(root / "log.md", f"- {pages.iso(now)} landed duplicate {pid} of {target} "
                                                 f"from {releaser}")
                        report.duplicates.append(pid)
                        continue
                    if _older(fm, old_fm):   # an older signature replayed: never a rollback
                        _stale(root, path, pid, releaser, target, now, report)
                        continue
                    # a signed page changes silently only for its own signer, signing where it did;
                    # a page that does not say where it was signed was signed where it came from
                    # (`landed_from`), else here
                    signed_where = str(old_fm.get("signed_in") or old_fm.get("landed_from") or space.name)
                    if str(old_fm.get("signed_by")) != str(fm.get("signed_by")) or signed_where != signing:
                        _mark(path, fm, body, landing="pending: someone else's signed page",
                              **{"for": str(old_fm.get("signed_by"))})
                        report.pending.append(pid)
                        continue
            problem = (backlog_problem(fm, signer_cache, space, reg) if legacy
                       else signer_problem(fm, signer_cache, space, reg))
            if problem is UNREACHABLE:   # the signer's history cannot be asked here: left as it is
                continue
            if problem and (moving is not None or theirs is not None):   # a move only on a signature that checks
                _hold_for_agent(root, path, fm, body, f"pending: its id is already the page at {elsewhere}",
                                space, now)
                report.pending.append(pid)
                continue
            if theirs is not None:   # a genuine signature on someone else's page, moved: as in place
                _mark(path, fm, body, landing="pending: someone else's signed page",
                      **{"for": str(pages.read(theirs)[0].get("signed_by"))})
                report.pending.append(pid)
                continue
            if problem and problem.startswith(NO_REPO_YET):   # the agent's to settle (classify.py)
                _hold_for_agent(root, path, fm, body, f"pending: {problem}", space, now, work=REPO_WORK)
                report.pending.append(pid)
                continue
            if problem:
                _mark(path, fm, body, landing=f"pending: {problem}", **{"for": owner})
                report.pending.append(pid)
                continue
            page_fm = {k: v for k, v in fm.items() if k not in STRIP}
            page_fm.update(landed_at=pages.iso(now), landed_from=releaser)
            try:
                pages.write(dest, page_fm, body, root=root)
            except pages.UnsafePath as exc:   # a link appeared on the way: held, nothing written
                _hold_for_agent(root, path, fm, body, f"pending: bad target ({exc})", space, now)
                report.pending.append(pid)
                continue
            if moving is not None:   # the same page, moved: its old file goes, so its id stays one page
                try:
                    moving.unlink()
                except OSError:   # the old file stays: so does the release, and the new copy goes
                    dest.unlink(missing_ok=True)
                    _hold_for_agent(root, path, fm, body, f"pending: its id is already the page at {elsewhere}",
                                    space, now)
                    report.pending.append(pid)
                    continue
            path.unlink()
            here[pid] = target
            if moving is not None:
                _append(root / "log.md", f"- {pages.iso(now)} moved {pid} from {elsewhere} to {target}: "
                                         f"its signer's release from {releaser} names the new place")
            _append(root / "log.md", f"- {pages.iso(now)} landed {pid} silently at {target} from {releaser}")
            _append(root / LANDED, json.dumps({"at": pages.iso(now), "id": pid, "target": target,
                                               "from": releaser, "by": fm.get("signed_by"),
                                               "subject": fm.get("subject"),
                                               "class": fm.get("release_class")}))
            report.silent.append(pid)
    return report


def _stale(root: Path, path: Path, pid: str, releaser: str, where: str, now, report) -> None:
    """Drop a release signed before the page here carrying its id: the newer signature stands,
    and nothing is lost but an old copy. Logged."""
    path.unlink()
    _append(root / "log.md", f"- {pages.iso(now)} dropped {pid} from {releaser}: signed before "
                             f"the page at {where}")
    report.stale.append(pid)


# ----- the weekly sample and the trust switch -------------------------------------------
def _week(space, moment: dt.datetime) -> int:
    return int(moment.timestamp() // space.scale(7 * 24).total_seconds())


def sample_item(root, space=None, reg=None, now: dt.datetime | None = None, given: dict | None = None) -> dict | None:
    """One `sample` item per scaled week from the week's silent landings and at-once arrivals."""
    root = Path(root)
    space = space or spaceyaml.load(root)
    reg = reg or registry_mod.Registry(root, space)
    now = now or space.now()
    week = _week(space, now)
    item_id = f"sample-{week}"
    if item_id in (given or {}) or any(r.get("week") == week for r in _jsonl(root / SAMPLES)):
        return None
    since = now - space.scale(7 * 24)
    pool = []
    for row in _jsonl(root / LANDED):
        try:
            if pages.parse_iso(row.get("at")) >= since:
                pool.append({"id": row["id"], "title": row.get("target"), "from": row.get("from"),
                             "subject": row.get("subject")})
        except (KeyError, TypeError, ValueError):
            continue
    for path in sorted((root / CANDIDATES).glob("*.md")):
        try:
            fm, _ = pages.read(path)
            at = pages.parse_iso(fm.get("released_at"))
        except Exception:
            continue
        if fm.get("released_from") and fm.get("release_class") == "at_once" and at >= since:
            pool.append({"id": str(fm.get("id") or path.stem), "title": fm.get("title"),
                         "from": fm.get("released_from"), "subject": fm.get("subject")})
    for row in _jsonl(root / "_meta" / "state" / "requests.jsonl"):
        # what this space answered at once under a standing choice is sampled too
        try:
            fresh = pages.parse_iso(row.get("at")) >= since
        except (TypeError, ValueError):
            continue
        if fresh and row.get("outcome") == "answered" and str(row.get("choice") or "").endswith(":at_once"):
            pool.append({"id": str(row.get("id")), "title": f"answer to {row.get('from')} ({row.get('class')})",
                         "from": "a standing choice", "subject": None})
    if not pool:
        return None
    picked = random.Random(week).sample(pool, min(SAMPLE_SIZE, len(pool)))
    lines = "; ".join(f"{n}) {p['title']} from {p['from']}" for n, p in enumerate(picked, 1))
    owner = domain_owner(reg, space, picked[0].get("subject"))
    return {"id": item_id, "kind": "sample",
            "text": f"Check what arrived without asking you this week, for {owner}: {lines}; a no pauses, "
                    "for four weeks, the path that let each failed one through",
            "recommendation": "yes if each reads right; no with the numbers that did not (no: 2)",
            "default": "counted as fine", "depends": 0, "for": owner,
            "payload": json.dumps(picked)}


def _numbers(note: str, count: int) -> list:
    out = []
    for part in str(note or "").replace(",", " ").split():
        if part.isdigit() and 1 <= int(part) <= count:
            out.append(int(part))
    return out


def apply_sample(root, item: dict, verdict: dict, now: dt.datetime | None = None) -> list:
    """Record a sample verdict; each failed item sends a content-free signal to its releaser."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    picked = json.loads(item.get("payload") or "[]")
    verb = verdict.get("verb")
    failed_numbers = []
    if verb in ("no", "edit"):
        failed_numbers = _numbers(verdict.get("note"), len(picked)) or (
            list(range(1, len(picked) + 1)) if verb == "no" else [])
    failed = [picked[n - 1]["id"] for n in failed_numbers]
    _append(root / SAMPLES, json.dumps({"at": pages.iso(now), "week": _week(space, now),
                                        "items": [p["id"] for p in picked], "failed": failed,
                                        "releasers": {p["id"]: p.get("from") for p in picked},
                                        "by": verdict.get("by")}))
    for n in failed_numbers:
        row = picked[n - 1]
        if str(row.get("id", "")).startswith("rq-"):
            # a request answer this space sent at once: pause the standing choice that sent it
            import release
            for entry in _jsonl(root / "_meta" / "state" / "requests.jsonl"):
                if entry.get("id") == row["id"] and str(entry.get("choice") or "").endswith(":at_once"):
                    release.pause(root, f"request_choice:{entry.get('from')}:{entry.get('class')}", now,
                                  "failed sample", row["id"], space)
            continue
        if not row.get("from"):
            continue
        signal = {"id": pages.new_id(), "destination": row["from"], "at": pages.iso(now),
                  "kind": "sample_failed", "item": row["id"], "subject": row.get("subject"),
                  "note": "a sampled release did not read right; pause the path that let it through"}
        target = root / SIGNALS_OUT / f"{signal['id']}.yaml"
        pages.write_text(target, yaml.safe_dump(signal, sort_keys=False), root=root)
    recompute_trust(root, now=now)
    return failed


def retract(root, page_id: str, by: str, why: str = "", now: dt.datetime | None = None) -> Path:
    """The subject's owner (or the domain owner) withdraws a landed release at the home: the
    page is deprecated with who and why, and a content-free signal tells the releaser, whose
    path pauses as after a failed sample. Nothing is deleted. Refused (ValueError) inside a
    routine or a cloud run (`routine.in_routine`): the withdrawal is the owner's word."""
    import ids
    import routine   # here and not at the top: only the person's withdrawal asks it
    running = routine.in_routine()
    if running:
        raise ValueError(f"refused: {running}; a routine never withdraws what a person signed or released (rules.md section 1)")
    root = Path(root)
    pages.refuse_copy(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    if not str(by or "").strip():
        raise ValueError("a retraction names the person who withdraws it")
    pages.check_page_id(page_id)
    rel = ids.table(root).get(page_id)
    if rel is None:
        raise KeyError(f"{page_id} is not a page here")
    # the index is data: only a page under knowledge/ that carries this id itself is withdrawn
    path = pages.confine(root, rel, zones=("knowledge/",), guarded=True, what="retracted page")
    fm, body = pages.read(path)
    if str(fm.get("id") or "") != page_id:
        raise KeyError(f"{page_id} is not a page here (the index points at another page)")
    if not fm.get("landed_from") and not fm.get("released_from"):
        raise ValueError(f"{page_id} did not arrive by a release; correct it like any page")
    fm.update(status="deprecated", retracted_by=by, retracted_at=pages.iso(now), retracted_why=why or None)
    pages.write(path, fm, body, root=root)
    releaser = fm.get("landed_from") or fm.get("released_from")
    _append(root / "log.md", f"- {pages.iso(now)} retracted {page_id} at home by {by}" + (f": {why}" if why else ""))
    signal = {"id": pages.new_id(), "destination": releaser, "at": pages.iso(now), "kind": "sample_failed",
              "item": page_id, "subject": fm.get("subject"),
              "note": "withdrawn at the home by its owner; pause the path that let it through"}
    target = root / SIGNALS_OUT / f"{signal['id']}.yaml"
    pages.write_text(target, yaml.safe_dump(signal, sort_keys=False), root=root)
    return path


# ----- one record arriving twice ------------------------------------------------------
def _id_key(value, kind) -> str | None:
    """A record's original id written one way, so rooms that write it differently agree: a
    calendar's `＠version` cut off (an event's plain `@` too: its id is one in every calendar),
    letters and digits only, every separator alike, and a leading word of letters alone dropped
    (a source's name, as in `slack:` or `gcal-`)."""
    text = re.split("[＠﹫]", str(value or ""))[0]
    text = unicodedata.normalize("NFKC", text).casefold()
    if kind == "event":
        text = text.split("@")[0]
    tokens = re.findall(r"[0-9a-z]+", text)
    while len(tokens) > 1 and tokens[0].isalpha():
        tokens.pop(0)
    return "|".join(tokens) or None


def _records(root: Path) -> dict:
    """Page id -> (path, frontmatter, body) of every record here that can pair: a page under
    `knowledge/sources/` (a split page by its first part) and a released record still waiting in
    `_inbound/candidates/`. A withdrawn one is no record any more."""
    base = root / SOURCES
    filed = [p for p in sorted(base.rglob("*.md")) if pages.is_page_path(p)] if base.is_dir() else []
    waiting = sorted((root / CANDIDATES).glob("*.md"))
    found: dict = {}
    for path in filed + waiting:
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        pid = fm.get("id")
        if str(fm.get("type")) != "source" or not pages.is_page_id(pid) or pages.withdrawn(fm) \
                or str(fm.get("part") or 1) != "1" or (path.parent.name == "candidates" and not fm.get("released_from")):
            continue
        found.setdefault(pid, (path, fm, body))
    return found


def _checked(root: Path) -> set:
    """The pairs an agent kept as two pieces of evidence (`keep-both`, `undo`), each as a frozenset of ids."""
    return {frozenset(str(p) for p in row["pages"]) for row in _jsonl(root / DUPLICATES)
            if isinstance(row.get("pages"), list) and len(row["pages"]) == 2}


def duplicate_pairs(root) -> list:
    """The likely pairs of one record arriving twice (the module's last section), each
    {a, b, a_path, b_path, why}, for an agent to read and judge. Text is never compared."""
    root = Path(root)
    found = {pid: rec for pid, rec in _records(root).items() if not pages.duplicate_of(rec[1])}
    checked = _checked(root)
    here = spaceyaml.load(root).name
    subjects = {pid: set(registry_mod.routed_subjects(fm, here)) for pid, (_, fm, _) in found.items()}
    groups: dict = {}   # (why, key) -> page ids
    calls: dict = {}    # a day -> [(start, page id)]
    for pid, (_, fm, _) in found.items():
        kind = str(fm.get("kind") or "")
        evidence = fm.get("evidence") if isinstance(fm.get("evidence"), list) else []
        for rid in [fm["record"]] if fm.get("record") else evidence:
            key = _id_key(rid, kind)
            if key:
                groups.setdefault(("the same original id", key), []).append(pid)
        try:
            at = pages.parse_iso(fm.get("at")).astimezone(dt.timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
        if kind == "mail" and fm.get("from"):
            key = f"{registry_mod.address_of(fm['from'])}|{at:%Y-%m-%dT%H:%M}"
            groups.setdefault(("mail from one sender in one minute", key), []).append(pid)
        elif kind == "call":
            calls.setdefault(at.date(), []).append((at, pid))
    pairs: dict = {}

    def pair(a: str, b: str, why: str) -> None:
        a, b = sorted((a, b))
        arrived = any(found[p][1].get("released_from") or found[p][1].get("landed_from") for p in (a, b))
        apart = subjects[a] and subjects[b] and not subjects[a] & subjects[b]   # each counts once, for its own
        if a != b and arrived and not apart and frozenset((a, b)) not in checked:
            pairs.setdefault((a, b), {"a": a, "b": b, "a_path": found[a][0].relative_to(root).as_posix(),
                                      "b_path": found[b][0].relative_to(root).as_posix(), "why": why})

    for (why, _), members in groups.items():
        for a, b in itertools.combinations(dict.fromkeys(members), 2):
            pair(a, b, why)
    for day in calls.values():
        for (start, a), (end, b) in itertools.combinations(sorted(day), 2):
            if end - start <= dt.timedelta(minutes=TWIN_CALL_MINUTES):
                pair(a, b, f"two calls {int((end - start).total_seconds() // 60)} minutes apart")
    return [pairs[k] for k in sorted(pairs)]


def _verdict(root, by, ids: tuple):
    """(root, now, who, the records here) for a verdict on `ids`, each a record here, or ValueError."""
    root = Path(root)
    pages.refuse_copy(root)
    if not str(by or "").strip():
        raise ValueError("a verdict names who gave it (--by)")
    found = _records(root)
    for pid in ids:
        if pid not in found:
            raise KeyError(f"{pages.check_page_id(pid)} is no record here")
    if len(set(ids)) != len(ids):
        raise ValueError("a pair is two records")
    return root, spaceyaml.load(root).now(), str(by).strip(), found


def mark_duplicate(root, main: str, copy: str, by: str, now: dt.datetime | None = None) -> Path:
    """`copy` holds nothing `main` does not: marked `duplicate_of: {id, by, at}`, never deleted.
    A record marked a duplicate of it follows it to `main`, so a mark always names a record that
    counts."""
    root, today, by, found = _verdict(root, by, (main, copy))
    now = now or today
    if pages.duplicate_of(found[main][1]):
        raise ValueError(f"{main} is itself a duplicate of {pages.duplicate_of(found[main][1])}: name that one as main")
    mark = {"id": main, "by": by, "at": pages.iso(now)}
    for opath, ofm, obody in found.values():
        if pages.duplicate_of(ofm) == copy:
            pages.write(opath, dict(ofm, duplicate_of=dict(mark)), obody, root=root)
    path, fm, body = found[copy]
    pages.write(path, dict(fm, duplicate_of=mark), body, root=root)
    _append(root / "log.md", f"- {pages.iso(now)} marked {copy} a duplicate of {main} by {by}")
    return path


def _kept(root: Path, verdict: str, a: str, b: str, by: str, now: dt.datetime) -> None:
    _append(root / DUPLICATES, json.dumps({"at": pages.iso(now), "verdict": verdict, "pages": sorted((a, b)),
                                           "by": by}, ensure_ascii=False))


def keep_both(root, a: str, b: str, by: str, now: dt.datetime | None = None) -> None:
    """A pair the agent read that stays two pieces of evidence (two records, or copies that each
    hold what the other lacks): it goes to DUPLICATES, both pages untouched, and is not listed again."""
    root, today, by, _ = _verdict(root, by, (a, b))
    now = now or today
    _kept(root, "keep-both", a, b, by, now)
    _append(root / "log.md", f"- {pages.iso(now)} kept {a} and {b} both as evidence, by {by}")


def undo(root, copy: str, by: str, now: dt.datetime | None = None) -> Path:
    """Take a mark back: the record is again exactly as it was before it and counts as itself,
    and the pair is recorded as kept, so it is not listed again."""
    root, today, by, found = _verdict(root, by, (copy,))
    now = now or today
    path, fm, body = found[copy]
    main = pages.duplicate_of(fm)
    if not main:
        raise ValueError(f"{copy} is not marked a duplicate")
    pages.write(path, {k: v for k, v in fm.items() if k != "duplicate_of"}, body, root=root)
    _kept(root, "undo", main, copy, by, now)
    _append(root / "log.md", f"- {pages.iso(now)} took back the mark on {copy} (a duplicate of {main}) by {by}")
    return path


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("land")
    r = sub.add_parser("retract")
    r.add_argument("id")
    r.add_argument("--by", required=True)
    r.add_argument("--why", default="")
    sub.add_parser("duplicates")
    for name, ids in (("mark", ("main", "copy")), ("keep-both", ("a", "b")), ("undo", ("copy",))):
        p = sub.add_parser(name)
        for arg in ids:
            p.add_argument(arg)
        p.add_argument("--by", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "retract":
        print(retract(args.root, args.id, args.by, args.why))
    elif args.cmd == "land":
        print(land(args.root))
    elif args.cmd == "duplicates":
        print(json.dumps(duplicate_pairs(args.root), indent=1, ensure_ascii=False))
    else:
        try:   # a refused verdict is one line for the agent, never a traceback
            if args.cmd == "mark":
                print(mark_duplicate(args.root, args.main, args.copy, args.by))
            elif args.cmd == "keep-both":
                keep_both(args.root, args.a, args.b, args.by)
            else:
                print(undo(args.root, args.copy, args.by))
        except (KeyError, ValueError) as exc:
            print(f"refused: {exc.args[0] if exc.args else exc}", file=sys.stderr)
            return 1
    return 0


def recompute_trust(root, now: dt.datetime | None = None) -> list:
    """Releasers whose failed share over four scaled weeks is above the threshold."""
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    since = now - space.scale(TRUST_DAYS * 24)
    totals: dict = {}
    for row in _jsonl(root / SAMPLES):
        try:
            if pages.parse_iso(row.get("at")) < since:
                continue
        except (TypeError, ValueError):
            continue
        for item, releaser in (row.get("releasers") or {}).items():
            if not releaser:
                continue
            seen, bad = totals.get(releaser, (0, 0))
            totals[releaser] = (seen + 1, bad + int(item in (row.get("failed") or [])))
    pending = sorted(r for r, (seen, bad) in totals.items() if seen >= TRUST_MIN and bad / seen > TRUST_SHARE)
    path = root / TRUST
    current = pending_releasers(root)
    if pending or current or path.exists():
        pages.write_text(path, yaml.safe_dump({"pending": pending, "computed_at": pages.iso(now),
                                               "totals": {r: list(v) for r, v in sorted(totals.items())}},
                                              sort_keys=False), root=root)
    return pending


if __name__ == "__main__":
    sys.exit(main())
