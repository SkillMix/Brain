"""Landing at the home: what a release becomes when it arrives (vision section 9).

The destination's librarian runs this before anything reads the space. For every page in
`_inbound/candidates/` that another space released (`released_from`):

| The release is...                                   | It lands as                     |
|-----------------------------------------------------|---------------------------------|
| a record (not signed): a handed-sideways witness    | evidence under `knowledge/sources/`, never |
|   page                                              |   something to sign; the same record from  |
|                                                     |   several releasers lands once             |
| signed, routine (fingerprint matches, not high      | filed into `knowledge/` silently |
|   stakes, its releaser trusted, no other signer's   |                                 |
|   page changed)                                     |                                 |
| on the high-stakes list                             | pending, for the domain owner   |
| from a releaser under the trust switch              | pending, for the domain owner   |
| a change to someone else's signed page              | pending, for that page's signer |
| one of two releases for one page with other text    | both pending, `disagrees_with`  |
| the same text as the page already there             | a duplicate: dropped, logged    |
| a body that does not match its fingerprint          | pending, never filed            |
| a retraction from the release's sender              | that release withdrawn: never   |
|                                                     |   lands, or kept deprecated and |
|                                                     |   no longer evidence            |

Once a scaled week `sample_item` draws up to five silent landings, at-once arrivals and
request answers sent under a standing choice of the week for the domain owner (else the space owner); `apply_sample` records the verdict in
`_meta/state/samples.jsonl`, sends a content-free `sample_failed` signal to the releasing
space for each failed one (that space pauses the path that let it through), and
`recompute_trust` puts a releaser whose failed share over four scaled weeks is above 0.2
(five samples or more) under the trust switch (`_meta/state/trust.yaml`): its signed
releases land pending until the share recovers. Nothing here writes `stable` of its own: a
silent landing keeps the releaser's signature exactly as it arrived.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

CANDIDATES = Path("_inbound") / "candidates"
LANDED = Path("_meta") / "state" / "landed.jsonl"
SAMPLES = Path("_meta") / "state" / "samples.jsonl"
TRUST = Path("_meta") / "state" / "trust.yaml"
SIGNALS_OUT = Path("_outbound") / "signals"
STRIP = ("class", "stage_after", "listed", "listed_at", "destination", "from_space", "landing",
         "disagrees_with", "for", "target", "recommendation", "depends", "deadline", "approved_by",
         "approved_at", "page")
SAMPLE_SIZE = 5
TRUST_SHARE = 0.2
TRUST_MIN = 5
TRUST_DAYS = 28


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


def record_key(fm: dict, body: str) -> str:
    """One record, whoever released it: its title, its time to the minute and its text."""
    at = str(fm.get("at") or "")[:16]
    return pages.fingerprint(f"{str(fm.get('title') or '').strip().lower()}|{at}|{pages.normalize(body)}")


def _file_record(root: Path, path: Path, fm: dict, body: str, now: dt.datetime, report) -> None:
    """A record handed sideways is evidence at its home: a witness page under
    `knowledge/sources/<yyyy>/<mm>/`, a draft forever, never an item to sign. The same record
    released by several people lands once; the others are named under `also_from`."""
    releaser = str(fm.get("released_from") or "")
    pid = str(fm.get("id") or path.stem)
    try:
        when = pages.parse_iso(fm.get("at") or fm.get("released_at"))
    except (TypeError, ValueError):
        when = now
    folder = root / "knowledge" / "sources" / f"{when:%Y}" / f"{when:%m}"
    key = record_key(fm, body)
    for other in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            ofm, obody = pages.read(other)
        except Exception:
            continue
        if ofm.get("record_key") == key:
            also = sorted(set(ofm.get("also_from") or []) | {releaser})
            if releaser != ofm.get("landed_from") and also != (ofm.get("also_from") or []):
                pages.write(other, dict(ofm, also_from=also), obody)
            path.unlink()
            _append(root / "log.md", f"- {pages.iso(now)} landed duplicate record {pid} of "
                                     f"{other.relative_to(root).as_posix()} from {releaser}")
            report.record_duplicates.append(pid)
            return
    page_fm = {k: v for k, v in fm.items() if k not in STRIP}
    page_fm.update(status="draft", landed_at=pages.iso(now), landed_from=releaser, record_key=key)
    target = folder / f"{pid}.md"
    pages.write(target, page_fm, body)
    path.unlink()
    rel = target.relative_to(root).as_posix()
    _append(root / "log.md", f"- {pages.iso(now)} filed record {pid} at {rel} from {releaser}")
    _append(root / LANDED, json.dumps({"at": pages.iso(now), "id": pid, "target": rel, "from": releaser,
                                       "by": None, "subject": fm.get("subject"),
                                       "class": fm.get("release_class"), "record": True}))
    report.records.append(pid)


def _apply_retraction(root: Path, path: Path, fm: dict, now: dt.datetime, report) -> None:
    """A retraction that arrived withdraws the release it names, wherever that went here: still
    a candidate, it is marked and never lands; filed, it stays as it is, deprecated, and stops
    counting as evidence. Only the sender's own release is withdrawn; nothing is deleted but the
    retraction itself, which is a message, not a record."""
    target_id, sender = str(fm.get("retracts") or ""), str(fm.get("released_from") or "")
    found = root / CANDIDATES / f"{target_id}.md"
    if not found.exists():
        base = root / "knowledge"
        found = next((p for p in sorted(base.rglob(f"{target_id}.md")) if pages.is_page_path(p)), None)             if target_id and base.is_dir() else None
    tfm, tbody = pages.read(found) if found is not None else ({}, "")
    if found is not None and str(tfm.get("landed_from") or tfm.get("released_from") or "") == sender:
        if not pages.withdrawn(tfm):
            update = {"retracted": True}
            if found.parent != root / CANDIDATES:
                update.update(status="deprecated", retracted_by=fm.get("released_by") or sender,
                              retracted_at=pages.iso(now))
            pages.write(found, dict(tfm, **update), tbody)
        _append(root / "log.md", f"- {pages.iso(now)} withdrew {target_id} at "
                                 f"{found.relative_to(root).as_posix()}: {sender} retracted it")
        report.withdrawn.append(target_id)
    else:
        _append(root / "log.md", f"- {pages.iso(now)} retraction of {target_id} from {sender}: "
                                 "nothing here carries it")
    path.unlink()


CONFLICTS = Path("_inbound") / "conflicts"


def _land_conflict(root: Path, path: Path, fm: dict, body: str, now: dt.datetime) -> bool:
    """A conflict note marks its page contested (frontmatter only: the signed body and its
    fingerprint stay untouched) and waits under `_inbound/conflicts/` for the weekly batch."""
    import ids
    target = ids.table(root).get(str(fm.get("page") or ""))
    note_id = str(fm.get("id") or path.stem)
    if target and (root / target).exists():
        pfm, pbody = pages.read(root / target)
        contested = [str(x) for x in (pfm.get("contested") or [])]
        if note_id not in contested:
            pages.write(root / target, dict(pfm, contested=contested + [note_id]), pbody)
    dest = root / CONFLICTS / path.name
    pages.write(dest, fm, body)
    path.unlink()
    _append(root / "log.md", f"- {pages.iso(now)} conflict note {note_id} from {fm.get('released_from')} "
                             f"contests {fm.get('page')}")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(("" if not text or text.endswith("\n") else "\n") + line.rstrip("\n") + "\n")


def signed_ok(fm: dict, body: str) -> bool:
    return (str(fm.get("status") or "") == "stable" and bool(fm.get("signed_by"))
            and fm.get("fingerprint") == pages.fingerprint(body))


def claims_signature(fm: dict) -> bool:
    return str(fm.get("status") or "") == "stable" or bool(fm.get("fingerprint"))


def pending_releasers(root: Path) -> set:
    path = root / TRUST
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(x) for x in (data.get("pending") or [])} if isinstance(data, dict) else set()


def domain_owner(reg, space, subject) -> str:
    if subject:
        domain = reg.domain_of(str(subject))
        if domain is not None:
            return reg.owner_of(domain.id) or space.owner
        return reg.owner_of(str(subject)) or space.owner
    return space.owner


def _target(fm: dict, path: Path, now: dt.datetime) -> str:
    import sign  # target_of is the one rule for where a page lands
    return sign.target_of(fm, path, now)


def _mark(path: Path, fm: dict, body: str, **fields) -> None:
    changed = {k: v for k, v in fields.items() if fm.get(k) != v}
    if changed:
        pages.write(path, dict(fm, **changed), body)


def stakes(fm: dict) -> str | None:
    """Why the sender judged an item high stakes (an older flag carries no why); None when it
    was not flagged. No word in it decides: the router or a person judged it."""
    value = fm.get("high_stakes")
    if not value:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else "flagged"


def signer_log(repo: str, cache: dict) -> str | None:
    """The signer's own `log.md`, read with this person's grants (the host's command line)."""
    if repo in cache:
        return cache[repo]
    import subprocess
    try:
        res = subprocess.run(["gh", "api", f"repos/{repo}/contents/log.md", "-H", "Accept: application/vnd.github.raw"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        cache[repo] = res.stdout if res.returncode == 0 else None
    except Exception:
        cache[repo] = None
    return cache[repo]


def verified(fm: dict, cache: dict) -> bool | None:
    """The trust root (rules.md section 9): a release names the repository it was signed in;
    the signer's own history must carry the fingerprint line for this page. None when the
    release names no repository (offline, a fixture): nothing to check against."""
    repo = fm.get("signed_repo")
    if not repo:
        return None
    text = signer_log(str(repo), cache)
    if text is None:
        return False
    return f" fingerprint {fm.get('id')} {fm.get('fingerprint')} by {fm.get('signed_by')}" in text


def land(root, space: spaceyaml.Space | None = None, reg=None, now: dt.datetime | None = None) -> LandingReport:
    root = Path(root)
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
        if fm.get("released_from") and fm.get("release_class") == "retraction":
            _apply_retraction(root, path, fm, now, report)
    for path in sorted((root / CANDIDATES).glob("*.md")):
        try:
            fm, body = pages.read(path)
        except Exception:
            continue
        if fm.get("released_from") and pages.withdrawn(fm):
            pid = str(fm.get("id") or path.stem)
            path.unlink()
            _append(root / "log.md", f"- {pages.iso(now)} dropped {pid} from {fm.get('released_from')}: "
                                     "retracted before it landed")
            report.withdrawn.append(pid)
            continue
        if fm.get("released_from") and fm.get("conflict_note"):
            if _land_conflict(root, path, fm, body, now):
                report.conflicts.append(str(fm.get("id") or path.stem))
            continue
        if fm.get("released_from") and not claims_signature(fm) and str(fm.get("type")) == "source":
            # a high-stakes record waits for the domain owner's yes; it is never filed silently
            if str(fm.get("landing") or "").startswith("pending"):
                continue
            term = stakes(fm)
            if term:
                pid = str(fm.get("id") or path.stem)
                _mark(path, fm, body, landing=f"pending: high stakes ({term})",
                      **{"for": domain_owner(reg, space, fm.get("subject"))})
                report.pending.append(pid)
                continue
            _file_record(root, path, fm, body, now, report)
            continue
        if not fm.get("released_from") or not claims_signature(fm):
            continue
        groups.setdefault(_target(fm, path, now), []).append((path, fm, body))
    for target, group in sorted(groups.items()):
        prints = {fm.get("fingerprint") for _, fm, body in group if signed_ok(fm, body)}
        if len(group) > 1 and len(prints) > 1:
            ids_ = [str(fm.get("id") or p.stem) for p, fm, _ in group]
            by_id = {str(fm.get("id") or p.stem): fm for p, fm, _ in group}
            for path, fm, body in group:
                me = str(fm.get("id") or path.stem)
                others = [i for i in ids_ if i != me]
                lines = "\n".join(f"- {i}, released from {by_id[i].get('released_from')} by "
                                   f"{by_id[i].get('signed_by')}, says otherwise; the domain owner may pick."
                                   for i in others)
                if "## Disagreements" not in body:
                    body = body.rstrip("\n") + "\n\n## Disagreements\n\n" + lines + "\n"
                _mark(path, fm, body, landing="pending: disagreement", disagrees_with=others,
                      **{"for": domain_owner(reg, space, fm.get("subject"))})
                pages.write(path, pages.read(path)[0], body)
                report.disagreements.append(me)
            continue
        for path, fm, body in group:
            pid = str(fm.get("id") or path.stem)
            owner = domain_owner(reg, space, fm.get("subject"))
            if not signed_ok(fm, body):
                _mark(path, fm, body, landing="pending: fingerprint mismatch", **{"for": owner})
                report.pending.append(pid)
                continue
            term = stakes(fm)
            if term:
                _mark(path, fm, body, landing=f"pending: high stakes ({term})", **{"for": owner})
                report.pending.append(pid)
                continue
            releaser = str(fm.get("released_from") or "")
            if verified(fm, signer_cache) is False:
                _mark(path, fm, body, landing="pending: the signer's history does not show this signature",
                      **{"for": owner})
                report.pending.append(pid)
                continue
            if releaser in distrusted:
                _mark(path, fm, body, landing=f"pending: trust ({releaser} lands pending for now)",
                      **{"for": owner})
                report.pending.append(pid)
                continue
            dest = root / target
            if dest.exists():
                old_fm, old_body = pages.read(dest)
                if str(old_fm.get("status")) == "stable":
                    if old_fm.get("fingerprint") == fm.get("fingerprint"):
                        path.unlink()
                        _append(root / "log.md", f"- {pages.iso(now)} landed duplicate {pid} of {target} "
                                                 f"from {releaser}")
                        report.duplicates.append(pid)
                        continue
                    if str(old_fm.get("signed_by")) != str(fm.get("signed_by")):
                        _mark(path, fm, body, landing="pending: someone else's signed page",
                              **{"for": str(old_fm.get("signed_by"))})
                        report.pending.append(pid)
                        continue
            page_fm = {k: v for k, v in fm.items() if k not in STRIP}
            page_fm.update(landed_at=pages.iso(now), landed_from=releaser)
            pages.write(dest, page_fm, body)
            path.unlink()
            _append(root / "log.md", f"- {pages.iso(now)} landed {pid} silently at {target} from {releaser}")
            _append(root / LANDED, json.dumps({"at": pages.iso(now), "id": pid, "target": target,
                                               "from": releaser, "by": fm.get("signed_by"),
                                               "subject": fm.get("subject"),
                                               "class": fm.get("release_class")}))
            report.silent.append(pid)
    return report


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
            "text": f"Check what arrived without asking you this week, for {owner}: {lines}",
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(signal, sort_keys=False), encoding="utf-8", newline="\n")
    recompute_trust(root, now=now)
    return failed


def retract(root, page_id: str, by: str, why: str = "", now: dt.datetime | None = None) -> Path:
    """The subject's owner (or the domain owner) withdraws a landed release at the home: the
    page is deprecated with who and why, and a content-free signal tells the releaser, whose
    path pauses as after a failed sample. Nothing is deleted."""
    import ids
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    if not str(by or "").strip():
        raise ValueError("a retraction names the person who withdraws it")
    rel = ids.table(root).get(page_id)
    if rel is None:
        raise KeyError(f"{page_id} is not a page here")
    path = root / rel
    fm, body = pages.read(path)
    if not fm.get("landed_from") and not fm.get("released_from"):
        raise ValueError(f"{page_id} did not arrive by a release; correct it like any page")
    fm.update(status="deprecated", retracted_by=by, retracted_at=pages.iso(now), retracted_why=why or None)
    pages.write(path, fm, body)
    releaser = fm.get("landed_from") or fm.get("released_from")
    _append(root / "log.md", f"- {pages.iso(now)} retracted {page_id} at home by {by}" + (f": {why}" if why else ""))
    signal = {"id": pages.new_id(), "destination": releaser, "at": pages.iso(now), "kind": "sample_failed",
              "item": page_id, "subject": fm.get("subject"),
              "note": "withdrawn at the home by its owner; pause the path that let it through"}
    target = root / SIGNALS_OUT / f"{signal['id']}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(signal, sort_keys=False), encoding="utf-8", newline="\n")
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
    args = ap.parse_args(argv)
    if args.cmd == "retract":
        print(retract(args.root, args.id, args.by, args.why))
    else:
        print(land(args.root))
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"pending": pending, "computed_at": pages.iso(now),
                                        "totals": {r: list(v) for r, v in sorted(totals.items())}},
                                       sort_keys=False), encoding="utf-8", newline="\n")
    return pending


if __name__ == "__main__":
    sys.exit(main())
