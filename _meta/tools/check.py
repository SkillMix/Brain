"""Check: the content checker (shape 1). Keeps content true, on purpose.

Runs after distill in every sweep chain. It reads what moved: the subjects with
evidence since `_meta/state/check-watermark.yaml` and the pages they touch; once a
scaled day (`window_pass` in that file) it adds every signed page whose window
closes within seven scaled days. Named checks, one function each: `disagree`,
`newer_evidence`, `past_window`, `person_claim`, `named_number` (each has its
docstring). Every hit is one item `_inbound/checks/<chk-id>.md` with evidence, a
recommendation in words and a route (signer, subject owner, domain owner, space
owner). The checker never edits a page. Labels compare by their last word.

Verdicts in `_meta/state/check-verdicts.jsonl` (`no` means noise) give `hit_rate`;
under 0.3 over 30 scaled days with ten or more verdicts the run drops `past_window`
and `named_number` and records that in its run line.

    python check.py [root]
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import distill  # noqa: E402
import pages  # noqa: E402
import registry as registry_mod  # noqa: E402
import spaceyaml  # noqa: E402

ROUTINE = "check"
CHECKS_DIR = Path("_inbound") / "checks"
VERDICTS = Path("_meta") / "state" / "check-verdicts.jsonl"
LABEL_RE = re.compile(r"([A-Za-z][A-Za-z ]{1,30}?)\s*[:=]\s*(\d{4}-\d{2}-\d{2}|\d[\d.,]*\s?%?)")
ROLE_RE = re.compile(r"^\s*(?:[-*]\s*)?role\s*:\s*(.+?)\s*$", re.I | re.M)
ALL_CHECKS = ("disagree", "newer_evidence", "past_window", "person_claim", "named_number")
NARROWABLE = ("past_window", "named_number")
WINDOW_PASS_HOURS = 24
CLOSING_DAYS = 7
NOISE_FLOOR = 0.3
MIN_VERDICTS = 10
RATE_DAYS = 30


@dataclass
class Page:
    rel: str
    fm: dict
    body: str
    subject: str | None
    kind: str | None

    @property
    def signed(self) -> bool:
        return str(self.fm.get("status") or "") == "stable"

    @property
    def stamp(self) -> dt.datetime | None:
        gen = self.fm.get("generated") or {}
        return distill.stamp_of(self.fm.get("verified_at") or self.fm.get("signed_at")
                                or (gen.get("at") if isinstance(gen, dict) else None))

    def labels(self) -> dict:
        return labelled_values(self.body)


@dataclass
class Hit:
    check: str
    page: str
    subject: str | None
    label: str
    evidence: list
    recommendation: str
    route: list
    hit_id: str = ""
    path: str = ""


@dataclass
class CheckReport:
    hits: list = field(default_factory=list)      # the items written this run
    checks_run: list = field(default_factory=list)
    pages_read: int = 0
    disabled: list = field(default_factory=list)
    window_pass: bool = False
    cost_tokens: int = 0
    batch_lines: int = 0
    conflict_notes: int = 0


# ----- reading -----------------------------------------------------------------
def labelled_values(text: str) -> dict:
    """label key (its last word) -> (full label, value); the first occurrence wins."""
    out: dict = {}
    for match in LABEL_RE.finditer(text):
        label = " ".join(match.group(1).lower().split())
        value = match.group(2).replace(" ", "").rstrip(".,")
        if label and value:
            out.setdefault(label.split()[-1], (label, value))
    return out


class Pages:
    """Pages under knowledge/ (never sources), read once each."""

    def __init__(self, root: Path, reader: distill.Reader):
        self.root, self.reader, self.loaded = root, reader, {}

    def load(self, path: Path) -> Page | None:
        rel = path.relative_to(self.root).as_posix()
        if rel in self.loaded:
            return self.loaded[rel]
        try:
            fm, body = self.reader.read(path)
        except Exception:
            return None
        if not fm:
            return None
        parts = rel.split("/")
        kind = subject = None
        if len(parts) >= 4 and parts[0] == "knowledge":
            kind, subject = parts[1], parts[2]
        if fm.get("subject"):
            subject = str(fm["subject"])
        page = Page(rel, fm, body, subject, kind)
        self.loaded[rel] = page
        return page

    def _walk(self, base: Path):
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(self.root).as_posix()
            if pages.is_page_path(path) and not rel.startswith("knowledge/sources/"):
                page = self.load(path)
                if page:
                    yield page

    def of_subject(self, sid: str) -> list:
        base = self.root / "knowledge"
        found = []
        if base.is_dir():
            for kind_dir in sorted(base.iterdir()):
                if kind_dir.is_dir() and kind_dir.name != "sources" and (kind_dir / sid).is_dir():
                    found.extend(self._walk(kind_dir / sid))
        return found

    def all(self) -> list:
        base = self.root / "knowledge"
        return list(self._walk(base)) if base.is_dir() else []


# ----- windows -----------------------------------------------------------------
def _window_real_days(page: Page, space: spaceyaml.Space) -> float | None:
    days = pages.window_days(page.fm, space.windows)
    if days is None:
        return None
    return space.scale(days * 24).total_seconds() / 86400


def is_past_window(page: Page, space, now) -> bool:
    window, age = _window_real_days(page, space), pages.age_days(page.fm, now)
    return window is not None and age is not None and age > window


def closes_soon(page: Page, space, now) -> bool:
    window, age = _window_real_days(page, space), pages.age_days(page.fm, now)
    if window is None or age is None:
        return False
    return age >= window - space.scale(CLOSING_DAYS * 24).total_seconds() / 86400


# ----- verdicts ----------------------------------------------------------------
def read_verdicts(root) -> list:
    path = Path(root) / VERDICTS
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line) if line.strip() and not line.startswith("#") else None
        except ValueError:
            row = None
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _verdicts_in(root, space, days: float, now: dt.datetime) -> list:
    start = now - space.scale(float(days) * 24)
    rows = []
    for row in read_verdicts(root):
        at = distill.stamp_of(row.get("at"))
        if at is not None and start <= at <= now:
            rows.append(row)
    return rows


def hit_rate(root, days: float, now: dt.datetime | None = None) -> float | None:
    """Share of verdicts in the last `days` scaled days that were not `no`; None without verdicts."""
    space = spaceyaml.load(root)
    rows = _verdicts_in(root, space, days, now or space.now())
    if not rows:
        return None
    return real_share(rows)


REAL = ("yes", "edit", "retire")


def real_share(rows: list) -> float:
    """The checker's hit rate: the share of verdicts that said the hit was real (yes, edit or
    retire). The health line and the narrowing use this one formula."""
    return sum(1 for r in rows if str(r.get("verdict")) in REAL) / len(rows) if rows else 0.0


def narrowed(root, space, now) -> list:
    rows = _verdicts_in(root, space, RATE_DAYS, now)
    if len(rows) < MIN_VERDICTS:
        return []
    rate = real_share(rows)
    return list(NARROWABLE) if rate < NOISE_FLOOR else []


# ----- routing and items -------------------------------------------------------
def hit_id(check: str, page: str, label: str) -> str:
    return "chk-" + hashlib.sha1(f"{check}|{page}|{label}".encode("utf-8")).hexdigest()[:8]


def route_for(page: Page, reg: registry_mod.Registry, space) -> list:
    names = [page.fm.get("signed_by")]
    if page.subject:
        names.append(reg.owner_of(page.subject))
        domain = reg.domain_of(page.subject)
        if domain is not None:
            names.append(reg.owner_of(domain.id))
    names.append(space.owner)
    route: list = []
    for name in names:
        text = str(name or "").strip()
        if text and text not in route:
            route.append(text)
    return route


def _date(moment) -> str:
    return f"{moment:%Y-%m-%d}" if moment else "an unknown date"


def write_hit(root: Path, hit: Hit, now: dt.datetime) -> Hit:
    hit.hit_id = hit_id(hit.check, hit.page, hit.label)
    path = root / CHECKS_DIR / f"{hit.hit_id}.md"
    hit.path = path.relative_to(root).as_posix()
    title = f"{hit.check}: {hit.page}"
    fm = {"id": "p-" + hit.hit_id[4:], "type": "review", "title": title, "status": "draft",
          "generated": pages.generated(ROUTINE, now), "check": hit.check, "check_id": hit.hit_id,
          "page": hit.page}
    if hit.subject:
        fm["subject"] = hit.subject
    fm.update({"evidence": list(hit.evidence), "recommendation": hit.recommendation,
               "route": list(hit.route)})
    pages.write(path, fm, f"# {title}\n\n{hit.recommendation}\n")
    return hit


# ----- the named checks (one function each) ------------------------------------
def check_disagree(signed: list, reg, space) -> list:
    """Two signed pages of one subject give different values for the same label."""
    hits = []
    ordered = sorted(signed, key=lambda p: (p.stamp or dt.datetime.min.replace(tzinfo=dt.timezone.utc), p.rel))
    for older, newer in itertools.combinations(ordered, 2):
        old_labels, new_labels = older.labels(), newer.labels()
        for key in sorted(set(old_labels) & set(new_labels)):
            (label_a, value_a), (label_b, value_b) = old_labels[key], new_labels[key]
            if value_a == value_b:
                continue
            text = (f"the page says {label_a}: {value_a}; {newer.rel} of {_date(newer.stamp)} "
                    f"says {label_b}: {value_b}; I think {value_b}. Still {value_a}?")
            hits.append(Hit("disagree", older.rel, older.subject, key, [newer.rel], text,
                            route_for(older, reg, space)))
    return hits


def check_newer_evidence(signed: list, evidence: list, reg, space) -> list:
    """Evidence newer than a signed page gives a different value for a label the page carries."""
    hits = []
    for page in signed:
        page_labels, stamp = page.labels(), page.stamp
        if not page_labels:
            continue
        seen = set()
        for ev in sorted(evidence, key=lambda e: (e.arrived, e.record)):
            if stamp is not None and ev.arrived <= stamp:
                continue
            for key, (label, value) in labelled_values(ev.body).items():
                if key in seen or key not in page_labels or page_labels[key][1] == value:
                    continue
                seen.add(key)
                text = (f"the page says {page_labels[key][0]}: {page_labels[key][1]}; {ev.record} of "
                        f"{_date(ev.at)} says {label}: {value}; I think {value}. "
                        f"Still {page_labels[key][1]}?")
                hits.append(Hit("newer_evidence", page.rel, page.subject, key, [ev.rel], text,
                                route_for(page, reg, space)))
    return hits


def check_past_window(signed: list, reg, space, now) -> list:
    """A signed page is older than its window and its subject is active (or has none)."""
    hits = []
    for page in signed:
        subject = reg.get(page.subject) if page.subject else None
        if page.subject and (subject is None or subject.state != "active"):
            continue
        if not is_past_window(page, space, now):
            continue
        age, window = pages.age_days(page.fm, now), pages.window_days(page.fm, space.windows)
        text = (f"the page was last verified on {_date(page.stamp)}, {int(age)} days ago; its window is "
                f"{window} days; I think it needs a look. Still true?")
        hits.append(Hit("past_window", page.rel, page.subject, "window", [page.rel], text,
                        route_for(page, reg, space)))
    return hits


def _departed(reg, page) -> bool:
    """A signed page about a person the registry marks as left: the daily pass reads it."""
    subject = reg.get(page.subject) if page.subject else None
    return subject is not None and subject.kind == "people" and subject.state == "archived"


def check_person_claim(people_pages: list, reg, space) -> list:
    """A page under knowledge/people/<id>/ states a role the registry contradicts."""
    hits = []
    for page in people_pages:
        subject = reg.get(page.subject) if page.subject else None
        if subject is not None and subject.state == "archived" and str(page.fm.get("status")) == "stable":
            text = (f"the registry says {subject.title} has left (archived); this page still describes "
                    f"them as current. I think it needs a line that they left. Still current?")
            hits.append(Hit("person_claim", page.rel, page.subject, "left", ["_meta/subjects.yaml"],
                            text, route_for(page, reg, space)))
            continue
        registered = str((subject.extra if subject else {}).get("role") or "").strip()
        if not registered:
            continue
        stated = str(page.fm.get("role") or "").strip()
        if not stated:
            match = ROLE_RE.search(page.body)
            stated = match.group(1).strip() if match else ""
        if not stated or stated.lower() == registered.lower():
            continue
        text = (f"the page says role: {stated}; the registry says {registered}; "
                f"I think {registered}. Still {stated}?")
        hits.append(Hit("person_claim", page.rel, page.subject, "role", ["_meta/subjects.yaml"],
                        text, route_for(page, reg, space)))
    return hits


def check_named_number(signed: list, space) -> list:
    """One label, different values across two subjects' signed pages. Informational."""
    hits = []
    ordered = sorted(signed, key=lambda p: (p.stamp or dt.datetime.min.replace(tzinfo=dt.timezone.utc), p.rel))
    for a, b in itertools.combinations(ordered, 2):
        if not a.subject or not b.subject or a.subject == b.subject:
            continue
        labels_a, labels_b = a.labels(), b.labels()
        for key in sorted(set(labels_a) & set(labels_b)):
            if labels_a[key][1] == labels_b[key][1]:
                continue
            text = (f"{a.rel} ({a.subject}) says {labels_a[key][0]}: {labels_a[key][1]}; {b.rel} "
                    f"({b.subject}) says {labels_b[key][0]}: {labels_b[key][1]}; informational, "
                    f"for the space owner.")
            hits.append(Hit("named_number", a.rel, a.subject, key, [b.rel], text,
                            [space.owner] if space.owner else []))
    return hits


# ----- the weekly review batch (vision section 13) ------------------------------------
REVIEW_DAYS = 7
CONFLICTS_IN = Path("_inbound") / "conflicts"
CANDIDATES_DIR = Path("_inbound") / "candidates"


def _open_batch(root: Path):
    """The newest review batch that still has unanswered lines, or None."""
    for path in sorted((root / CANDIDATES_DIR).glob("review-*.md"), reverse=True):
        try:
            fm, _ = pages.read(path)
        except Exception:
            continue
        if fm.get("review_batch") and any(not l.get("answered") for l in fm.get("lines") or []):
            return path
    return None


def _evidence_since(root: Path, reader, subject, since) -> list:
    out = []
    for ev in distill.gather_evidence(root, reader):
        if subject in ev.subjects and (since is None or ev.arrived > since):
            out.append(f"{ev.at:%Y-%m-%d} {ev.title or ev.record}")
    return out[-5:]


def batch_lines(root: Path, space, reg, store: "Pages", reader, now) -> list:
    """Every signed page past its window in an active subject, every draft older than its
    window, every contested page: one line each, with the evidence since it was verified."""
    lines = []
    for page in store.all():
        subject = reg.get(page.subject) if page.subject else None
        if page.subject and (subject is None or subject.state != "active"):
            continue
        reason = None
        if page.fm.get("contested"):
            notes = []
            for note_id in page.fm.get("contested") or []:
                note = root / CONFLICTS_IN / f"{note_id}.md"
                if note.exists():
                    notes.append(" ".join(pages.read(note)[1].split())[:200])
            reason = "contested: " + ("; ".join(notes) or "a conflict note arrived")
        elif page.signed and is_past_window(page, space, now):
            age = pages.age_days(page.fm, now)
            reason = (f"past its window: last verified {_date(page.stamp)}, {int(age or 0)} days ago, "
                      f"window {pages.window_days(page.fm, space.windows)} days")
        if reason:
            lines.append({"page": page.rel, "id": page.fm.get("id"), "title": page.fm.get("title"),
                          "signed": page.signed, "reason": reason,
                          "evidence": _evidence_since(root, reader, page.subject, page.stamp),
                          "recommendation": "yes, still true" if not page.fm.get("contested")
                          else "edit: take the newer value, or yes to keep the page"})
    for path in sorted((root / CANDIDATES_DIR).glob("*.md")):
        if path.name.startswith("review-"):
            continue
        try:
            fm, _ = reader.read(path)
        except Exception:
            continue
        if not fm or fm.get("released_from") or fm.get("landing"):
            continue
        age = pages.age_days(fm, now)
        window = pages.window_days(fm, space.windows)
        if age is None or window is None:
            continue
        if age > space.scale(window * 24).total_seconds() / 86400:
            lines.append({"page": path.relative_to(root).as_posix(), "id": fm.get("id"),
                          "title": fm.get("title"), "signed": False,
                          "reason": f"a draft waiting {int(age)} days, longer than its window of {window}",
                          "evidence": [], "recommendation": "yes to sign it, or no to drop it"})
    for n, line in enumerate(lines, 1):
        line["n"] = n
    return lines


def write_batch(root: Path, space, reg, store: "Pages", reader, now) -> int:
    """One review candidate a scaled week, when there is anything to review and no open batch."""
    mark = distill.read_watermark(root, ROUTINE)
    last = distill.stamp_of(mark.get("review_at"))
    if last is not None and now - last < space.scale(REVIEW_DAYS * 24):
        return 0
    if _open_batch(root) is not None:
        return 0
    lines = batch_lines(root, space, reg, store, reader, now)
    if not lines:
        return 0
    stamp = f"{now:%Y-%m-%d}"
    path = root / CANDIDATES_DIR / f"review-{stamp}.md"
    n = 1
    while path.exists():
        path = root / CANDIDATES_DIR / f"review-{stamp}-{n}.md"
        n += 1
    body = [f"# Weekly review {stamp}", "",
            "Each line is a page past its window, a draft that waited too long, or a page another "
            "space contested. Answer per line: yes (still true; a draft is signed), edit with the "
            "new text, or no (retire the page, drop the draft).", ""]
    for line in lines:
        ev = f" Evidence since: {'; '.join(line['evidence'])}." if line["evidence"] else ""
        body.append(f"{line['n']}. {line['title']} (`{line['page']}`): {line['reason']}.{ev} "
                    f"I recommend: {line['recommendation']}.")
    pid = "p-" + hashlib.sha1(path.name.encode("utf-8")).hexdigest()[:8]
    fm = {"id": pid, "type": "review", "title": f"Weekly review {stamp}: {len(lines)} pages",
          "status": "draft", "generated": pages.generated(ROUTINE, now), "review_batch": True,
          "lines": [{k: v for k, v in l.items() if k != "evidence"} for l in lines]}
    pages.write(path, fm, "\n".join(body) + "\n")
    mark["review_at"] = pages.iso(now)
    distill.write_watermark(root, ROUTINE, mark)
    return len(lines)


# ----- conflict notes (vision section 13) -----------------------------------------------
CONFLICT_LOG = Path("_meta") / "state" / "conflict-notes.jsonl"
QUEUE_DIR = Path("_outbound") / "queue"


def _mounted_pages(space, sid: str) -> list:
    """(mount name, mount's space name, Page) for every signed page of `sid` in a mounted ancestor."""
    out = []
    for mount in space.mounts:
        name = str(mount.get("name", ""))
        mroot = space.mount_root(name)
        if mroot is None:
            continue
        try:
            owner_space = spaceyaml.load(mroot).name
        except Exception:
            continue
        store = Pages(mroot, distill.Reader())
        for page in store.of_subject(sid):
            if page.signed:
                out.append((name, owner_space, page))
    return out


def conflict_notes(root: Path, space, reg, moved: dict, now) -> int:
    """Newer evidence against a page another space owns: a short note queued to that space."""
    done = {str(r.get("id")) for r in read_verdicts(root)}  # never shadow a verdict id
    sent = set()
    log = root / CONFLICT_LOG
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                sent.add(json.loads(line).get("hit"))
            except ValueError:
                continue
    count = 0
    for sid, evidence in sorted(moved.items()):
        for mount_name, owner_space, page in _mounted_pages(space, sid):
            if owner_space == space.name:
                continue
            for hit in check_newer_evidence([page], evidence, reg, space):
                hid = hit_id("conflict", f"{mount_name}:{hit.page}", hit.label)
                if hid in sent or hid in done:
                    continue
                note_id = "p-" + hid[4:]
                labels = page.labels()
                said = labels.get(hit.label, ("", ""))
                body = (f"Conflict note for [{page.fm.get('title')}](id:{page.fm.get('id')}): the page says "
                        f"{said[0]}: {said[1]}; evidence of {now:%Y-%m-%d} from {space.name} gives a "
                        f"different value: {hit.recommendation}\n")
                fm = {"id": note_id, "type": "source", "title": f"Conflict note: {page.fm.get('title')}",
                      "status": "draft", "generated": pages.generated(ROUTINE, now), "class": "window",
                      "destination": owner_space, "subject": sid,
                      "stage_after": pages.iso(now + space.window()), "listed": False,
                      "evidence": [str(e) for e in hit.evidence], "from_space": space.name,
                      "kind": "document", "conflict_note": True, "page": page.fm.get("id")}
                pages.write(root / QUEUE_DIR / f"{note_id}.md", fm, body)
                with log.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps({"hit": hid, "id": note_id, "at": pages.iso(now),
                                             "destination": owner_space, "page": page.fm.get("id")}) + "\n")
                sent.add(hid)
                count += 1
    return count


# ----- the run -----------------------------------------------------------------
def run(root, now: dt.datetime | None = None) -> CheckReport:
    root = Path(root)
    space = spaceyaml.load(root)
    now = now or space.now()
    reg = registry_mod.Registry(root, space)
    reader = distill.Reader()
    store = Pages(root, reader)
    report = CheckReport(disabled=narrowed(root, space, now))
    report.checks_run = [c for c in ALL_CHECKS if c not in report.disabled]
    mark = distill.read_watermark(root, ROUTINE)
    since, last_pass = distill.stamp_of(mark.get("at")), distill.stamp_of(mark.get("window_pass"))
    report.window_pass = last_pass is None or (now - last_pass) > space.scale(WINDOW_PASS_HOURS)

    moved = distill.by_subject(distill.new_since(distill.gather_evidence(root, reader), since))
    subject_pages = {sid: store.of_subject(sid) for sid in sorted(moved)}
    in_scope: dict = {p.rel: p for plist in subject_pages.values() for p in plist}
    if report.window_pass:
        for page in store.all():
            if page.signed and (closes_soon(page, space, now) or _departed(reg, page)):
                in_scope.setdefault(page.rel, page)
    scoped = sorted(in_scope.values(), key=lambda p: p.rel)
    signed = [p for p in scoped if p.signed]

    found: list = []
    if "disagree" in report.checks_run:
        for sid, plist in subject_pages.items():
            found += check_disagree([p for p in plist if p.signed], reg, space)
    if "newer_evidence" in report.checks_run:
        for sid, plist in subject_pages.items():
            found += check_newer_evidence([p for p in plist if p.signed], moved[sid], reg, space)
    # window findings are not items of their own: they bundle into the weekly review batch
    if "past_window" in report.checks_run:
        report.batch_lines = write_batch(root, space, reg, store, reader, now)
    report.conflict_notes = conflict_notes(root, space, reg, moved, now)
    if "person_claim" in report.checks_run:
        found += check_person_claim([p for p in scoped if p.kind == "people"], reg, space)
    if "named_number" in report.checks_run:
        found += check_named_number(signed, space)

    # sign may record the verdict under the chk- id or under the item's page id (p- + same hex)
    answered = {str(r.get("check_id")) for r in read_verdicts(root)}
    seen: set = set()
    for hit in found:
        hid = hit_id(hit.check, hit.page, hit.label)
        if hid in seen or {hid, "p-" + hid[4:]} & answered or (root / CHECKS_DIR / f"{hid}.md").exists():
            continue
        seen.add(hid)
        report.hits.append(write_hit(root, hit, now))

    distill.write_watermark(root, ROUTINE, {
        "at": pages.iso(now), "window_pass": pages.iso(now if report.window_pass else last_pass)})
    report.pages_read, report.cost_tokens = reader.pages, reader.cost_tokens
    distill.append_run(root, ROUTINE, space, now, report.cost_tokens, {
        "hits": len(report.hits), "checks_run": report.checks_run, "pages_read": report.pages_read,
        "disabled": report.disabled, "window_pass": report.window_pass,
        "batch_lines": report.batch_lines, "conflict_notes": report.conflict_notes})
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path(".")
    report = run(root)
    print(f"check: {len(report.hits)} hits, {report.pages_read} pages read, {report.cost_tokens} tokens; "
          f"checks: {', '.join(report.checks_run)}; disabled: {', '.join(report.disabled) or 'none'}; "
          f"window pass: {'yes' if report.window_pass else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
