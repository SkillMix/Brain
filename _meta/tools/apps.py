"""apps: which of the person's connected apps hold work for this company, decided by one read-only
analysis run per app (`reader.py`), and the app card that says so. There is no question about
which apps to read: every claude.ai connector of a known kind is analysed, and each run answers
whose account it is, whether it holds work, and how much there is per month. Apps that are not
work are skipped; a mailbox in someone else's name is counted and never sends. The card is shown
at least ten minutes before any content is copied (`starts_after`).

`group` sorts what the probe saw (`routine.probe_tools`) into apps. The one classifier of what a
tool may do is `reader.is_read_tool` (a read word, no acting word), so the read lists here are the
ones a reader accepts. Writing exists only for the tracker kind (create, update, save: the tracker
skill needs it). Sending is a mail tool whose name says send; reply and forward, and everything
else, are in no list and are refused like any tool that is not allowed.

    import apps
    groups = apps.group(probe["tools"], probe["mcp_servers"])
    verdicts = apps.analyse(groups, person, scratch_folder, known_tools=probe["tools"])
    card = apps.card(verdicts, now)       # {"read": [...], "skip": [...], "starts_after": ...}
    apps.feeds(verdicts)                  # "mail: owner, calendar: owner"

`start.py apps --probe --json` stops after the probe; `start.py apps --json` runs the readers.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import reader
import research

# A claude.ai connector named "claude.ai <Name>" gives tools "mcp__" + reader.server_part(name) + "__<tool>".
SERVER_PREFIX = "mcp__claude_ai_"
CLAUDE_AI = "claude.ai "
KINDS = ("mail", "calendar", "chat", "calls", "documents", "tracker")
VERDICTS = ("work", "personal", "mixed", "someone-else", "reconnect", "blocked")
READ = ("work", "mixed")             # the card reads these
SKIP = ("personal", "someone-else")  # the card skips these, and says why
HEAD_START = 10                      # minutes the card is shown before any content is copied
CONNECTORS_LINK = "https://claude.ai/settings/connectors"
SEP = " · "
ACCOUNT_MAX, WHY_MAX = 120, 200      # what a reader's words may take on the card
NO_TOOLS = ("personal", "someone-else")   # their verdict keeps no tool that sends or writes
UNEXPECTED = "Claude stopped unexpectedly while reading this app."   # never the fault's own text
FINAL = ("work", "mixed", "personal", "someone-else")   # a verdict a run after a limit keeps (R45, I-2)
PROBE_FRESH = dt.timedelta(minutes=30)                # the apps phase's probe the research may use as its own

# An app is known by whole words of its name (lower case). An app with none of them, and no suite
# word below, is not read: add its word to the kind it serves.
APP_WORDS = {
    "mail": ("gmail", "outlook", "mail", "email", "fastmail", "superhuman", "proton"),
    "calendar": ("calendar", "calendly"),
    "chat": ("slack", "teams", "discord", "telegram", "whatsapp", "mattermost", "chat"),
    "calls": ("notetaker", "granola", "otter", "fathom", "gong", "zoom", "tldv", "avoma", "grain"),
    "documents": ("drive", "notion", "dropbox", "box", "confluence", "sharepoint", "onedrive", "docs", "coda", "evernote"),
    "tracker": ("linear", "jira", "asana", "monday", "clickup", "trello", "shortcut", "basecamp"),
}
APP_KIND = {word: kind for kind, words in APP_WORDS.items() for word in words}
# One connector for several kinds: its tool names tell them apart, by whole words.
SUITE_WORDS = ("microsoft", "365", "workspace", "atlassian", "zoho")
TOOL_WORDS = {
    "mail": ("mail", "mails", "email", "emails", "inbox"),
    "calendar": ("calendar", "calendars", "event", "events"),
    "chat": ("chat", "chats", "channel", "channels", "team", "teams"),
    "calls": ("call", "calls", "transcript", "transcripts", "recording", "recordings"),
    "documents": ("file", "files", "document", "documents", "doc", "docs", "drive", "sharepoint", "onedrive",
                  "folder", "folders", "page", "pages", "wiki", "confluence"),
    "tracker": ("issue", "issues", "ticket", "tickets", "jira", "sprint", "sprints"),
}
WRITE_WORDS = ("create", "update", "save")   # a tracker tool containing one writes
SEND_WORD = "send"                           # a mail tool containing it sends

# `reader.read` checks an answer against this (its schema subset): the account the app is signed
# in as, the verdict, items per month for the last 12 months (keys YYYY-MM), and one sentence.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "account": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "per_month": {"type": "object", "additionalProperties": {"type": "integer"}},
        "why": {"type": "string"},
    },
    "required": ["account", "verdict", "per_month", "why"],
    "additionalProperties": False,
}
MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")


class LimitReached(Exception):
    """The plan's limit (or extra usage starting) stopped the analysis. `verdicts` are the apps
    that finished before; `text` is the command line's own words."""

    def __init__(self, verdicts: list, text: str = ""):
        super().__init__(text or "the plan's limit was reached")
        self.verdicts = verdicts
        self.text = text


# ----- grouping the probe's tools into apps ----------------------------------------------
def _name_kinds(app: str) -> tuple:
    """(the kinds an app's name gives all its tools, whether it is a suite whose tool names decide).
    An app is known by the whole lower-case words of its name: `ClickUp` is `clickup`, while
    `ZoomInfo` is not Zoom, `ChatGPT` is not a chat app and `MailChimp` is not a mailbox."""
    words = re.findall(r"[a-z0-9]+", app.lower())
    return {APP_KIND[w] for w in words if w in APP_KIND}, any(w in SUITE_WORDS for w in words)


def _tool_kinds(tool: str) -> set:
    """The kinds a tool's name points to, by its words (`reader.name_words`: cut at capitals too,
    so `getJiraIssue` is get, jira, issue)."""
    words = set(reader.name_words(tool))
    return {kind for kind, vocabulary in TOOL_WORDS.items() if words & set(vocabulary)}


def _display_names(servers) -> dict:
    """The probe's `claude.ai Gmail` as {server part: "Gmail"}: the server part cannot be turned
    back into a name (`monday_com` was `monday.com`), so the probe's own names come first."""
    names = {}
    for server in servers or []:
        name = server.get("name") if isinstance(server, dict) else None
        if isinstance(name, str) and name.startswith(CLAUDE_AI):
            names[reader.server_part(name)] = name[len(CLAUDE_AI):]
    return names


def group(tools: list, servers: list | None = None) -> list:
    """The apps the probe's tools belong to, in the order they appear, each
    {"app", "kinds", "read", "write", "send"}. Only claude.ai connectors of a known kind that have
    a tool that reads are apps. `servers` are the probe's `mcp_servers`: they give the app its
    name (else the server part of its tools, the underscores read as spaces)."""
    names, head = _display_names(servers), SERVER_PREFIX[len("mcp__"):]
    by_app: dict = {}
    for tool in dict.fromkeys(tools or []):
        match = reader.CONNECTOR_TOOL.fullmatch(tool) if isinstance(tool, str) and tool.startswith(SERVER_PREFIX) else None
        if match:
            server = match.group("server")
            app = names.get(server) or server[len(head):].replace("_", " ")
            by_app.setdefault(app, []).append((tool, match.group("tool")))
    result = []
    for app, items in by_app.items():
        by_name, suite = _name_kinds(app)
        kinds_of = {tool: (by_name or (_tool_kinds(part) if suite else set())) for tool, part in items}
        read = [tool for tool, _ in items if reader.is_read_tool(tool)]
        kinds = set().union(*kinds_of.values())
        if not read or not kinds:
            continue
        result.append({"app": app, "kinds": [k for k in KINDS if k in kinds], "read": read,
                       "write": [t for t, part in items if "tracker" in kinds_of[t] and any(w in part.lower() for w in WRITE_WORDS)],
                       "send": [t for t, part in items if "mail" in kinds_of[t] and SEND_WORD in part.lower()]})
    return result


def not_signed_in(servers: list | None) -> list:
    """The claude.ai connectors the probe lists as `needs-auth` that are of a known kind, by app
    name. They bring no tools, and at setup an expired connector cannot be told from one that was
    never signed in: they go on the card as information, never as a need."""
    found = []
    for server in servers or []:
        name = server.get("name") if isinstance(server, dict) else None
        if not (isinstance(name, str) and name.startswith(CLAUDE_AI) and server.get("status") == "needs-auth"):
            continue
        app = name[len(CLAUDE_AI):]
        kinds, suite = _name_kinds(app)
        if (kinds or suite) and app not in found:
            found.append(app)
    return found


# ----- who the work is judged against ----------------------------------------------------
def company_of(journal: dict, company: str | None, emails: list) -> dict:
    """{"company": name or None, "domains": [...]} the reader judges work against: the researched
    company card in the journal (a name, or a mapping with `name` and `domain`/`domains`), else the
    name the person gave; the domains of that card, else the person's own mail domains (a free
    mail provider's is nobody's company)."""
    record = (journal or {}).get("research")
    card = record.get("company") if isinstance(record, dict) else None
    name, domains = (card, []) if isinstance(card, str) else (None, [])
    if isinstance(card, dict):
        name = card.get("name")
        given = card.get("domains") or card.get("domain") or []
        domains = [given] if isinstance(given, str) else [d for d in given if isinstance(d, str)]
    if not domains:
        own = (str(e).split("@", 1)[1].lower() for e in emails or [] if "@" in str(e))
        domains = list(dict.fromkeys(d for d in own if d and not research.is_provider(d)))
    return {"company": name or company or None, "domains": domains}


def _months(today: dt.date) -> tuple:
    """The first and the last of the 12 months that end with this one, as YYYY-MM."""
    first = divmod(today.year * 12 + today.month - 1 - 11, 12)
    return f"{first[0]:04d}-{first[1] + 1:02d}", f"{today.year:04d}-{today.month:02d}"


def reader_prompt(grouped: dict, person: dict, today: dt.date | None = None) -> str:
    """What one reader is asked. It names its own app and no other: it was given that app's read
    tools only."""
    today = today or dt.date.today()
    first, last = _months(today)
    emails = [e for e in dict.fromkeys([*(person.get("emails") or []), person.get("email") or ""]) if e]
    company, domains = person.get("company"), ", ".join(person.get("domains") or [])
    if company:
        judge = (f"Judge work against this company: {company}" + (f" (mail domain {domains})" if domains else "")
                 + ". Items about it, its clients or its colleagues"
                 + (", or sent from or to that domain," if domains else "") + " are work.")
    elif domains:
        judge = ("The company is not known yet. Judge work against the person's own mail domain: "
                 f"{domains}. Items sent from or to it are work.")
    else:
        judge = ("The company is not known yet and the person has no company mail domain: judge from the "
                 "content, and answer \"personal\" when nothing in it concerns a company.")
    return (
        f"You are reading one connected app, {grouped['app']}, to decide whether it holds work for the "
        "company of the person who is setting up. Use only the tools you were given.\n\n"
        f"Today is {today.isoformat()}. The person is {person.get('name') or 'the owner of this computer'}"
        + (f" (their addresses: {', '.join(emails)})" if emails else "") + f".\n{judge}\n\n"
        "Do this:\n"
        "1. Say whose account this is: the address or user name the app is signed in as (\"account\").\n"
        "2. Read the metadata (who, when, subject or title, where it sits) of at most 30 recent items. Open "
        "the full text of at most 5 of them, and only when the metadata cannot settle the question.\n"
        f"3. Count the items in each of the last 12 months, {first} to {last} (\"per_month\": keys like "
        f"\"{last}\", whole numbers). Use the app's own result counts or date filters where it has them; "
        "where it has none, estimate from what you saw.\n"
        f"You have at most {reader.MAX_TURNS} turns: make several tool calls in one turn where you can, and answer "
        "before the turns run out, with estimates where you had no time to count.\n"
        "4. Give the \"verdict\":\n"
        "- \"work\": the account is the person's own and holds the company's work.\n"
        "- \"mixed\": the person's own, with company work and private matters together.\n"
        "- \"personal\": the person's own, but private, family or school matters only, no company work.\n"
        "- \"someone-else\": the account belongs to another person, or is a shared or group account.\n"
        "- \"reconnect\": the app asks for a new sign-in.\n"
        "- \"blocked\": the app refuses access.\n\n"
        "Personal and school content is counted, never quoted or stored. \"why\" is one short sentence "
        "without names, addresses, subjects or quotes from any record.")


# ----- the analysis runs -----------------------------------------------------------------
def _line(text, limit: int) -> str:
    """One line of at most `limit` characters: a reader's words take no more room on the card."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _counts(per_month: dict, today: dt.date) -> dict:
    """The months of the answer that are real: YYYY-MM inside the twelve months the reader was
    asked about (not older, not in the future), a whole number of items, no fewer than none."""
    first, last = _months(today)
    kept = {k: v for k, v in per_month.items()
            if MONTH.fullmatch(k) and first <= k <= last and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
    return dict(sorted(kept.items()))


def _verdict(grouped: dict, answer: dict, today: dt.date) -> dict:
    """The group plus the schema's fields. A mailbox in someone else's name is counted only, and
    a personal app is not read: neither keeps a tool that sends or writes, so no later grant is
    one filter away from them."""
    verdict = {**copy.deepcopy(grouped), "account": _line(answer["account"], ACCOUNT_MAX), "verdict": answer["verdict"],
               "per_month": _counts(answer["per_month"], today), "why": _line(answer["why"], WHY_MAX)}
    if verdict["verdict"] in NO_TOOLS:
        verdict["send"], verdict["write"] = [], []
    return verdict


def _failed(grouped: dict, verdict: str, why: str) -> dict:
    return {**copy.deepcopy(grouped), "account": "", "verdict": verdict, "per_month": {}, "why": why}


def _one(grouped, person, cwd, read, stop, limits, known_tools, claude, today, skipped=None):
    """One app's verdict, or None when it was not started, the person skipped it before its reader
    started (`skipped()`, asked right then), it ran into the limit, or it was `slow` (out of turns
    or time: the app stays unfinished and is read again, R45 I-3). An answer that came with extra
    usage starting is its verdict all the same (R45, I-2). A reader's other error is the app's
    verdict (a sign-in to give again: `reconnect`; anything else: `blocked`, with the error's text),
    never a stop for the others. Any other fault in the run is that app's `blocked` too, with a
    fixed sentence: its own text could repeat what the app held."""
    if stop.is_set() or (skipped is not None and grouped["app"] in skipped()):
        return None
    try:
        answer = read(reader_prompt(grouped, person, today), list(grouped["read"]), VERDICT_SCHEMA, cwd,
                      known_tools=known_tools, claude=claude)
        return _verdict(grouped, answer, today)
    except reader.ReaderError as exc:
        if exc.kind == "limit":
            limits.append(exc.text)
            stop.set()
            answer = getattr(exc, "answer", None)
            return _verdict(grouped, answer, today) if isinstance(answer, dict) else None
        if exc.kind == "slow":
            return None
        return _failed(grouped, "reconnect" if exc.kind == "auth" else "blocked", exc.text)
    except Exception:
        return _failed(grouped, "blocked", UNEXPECTED)


def analyse(groups: list, person: dict, cwd: Path, reader=reader.read, workers: int = 4,
            known_tools: list | None = None, claude: str | None = None, today: dt.date | None = None,
            skipped=None) -> list:
    """One reader per app, `workers` at a time. Each verdict is the group plus the schema's fields.
    `person` is {"email", and optionally "emails", "name", "company", "domains"}: who the account
    is compared with, and what work is judged against. `known_tools` are the probe's tools: every
    connector tool a reader was not given is denied in its run. A reader's `auth` error is the
    verdict `reconnect`, a `slow` one no verdict (the app is read again next time), any other error
    `blocked`; none stops the other apps. The plan's limit does: no new reader starts, and
    LimitReached carries the verdicts that finished, an answer that came with extra usage starting
    among them. `today` is
    the day the 12 months end with (default: today). `skipped` gives the apps the person skipped, and
    is asked again right before each reader starts: such an app is not read and has no verdict (R43)."""
    if not groups:
        return []
    today = today or dt.date.today()
    stop, limits = threading.Event(), []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(groups)))) as pool:
        done = list(pool.map(lambda g: _one(g, person, cwd, reader, stop, limits, known_tools, claude, today, skipped),
                             groups))
    verdicts = [v for v in done if v is not None]
    if stop.is_set():
        raise LimitReached(verdicts, limits[0] if limits else "")
    return verdicts


# ----- the card and the feeds --------------------------------------------------------------
def card(verdicts: list, now: dt.datetime, unsigned=()) -> dict:
    """What the person is shown before any content is copied: the apps that will be read
    (`work`, `mixed`) and the ones skipped with the reason (`personal`, `someone-else`), a line
    each, then the connectors that need a sign-in as information. An app that must be reconnected
    or is blocked is a need, not a line. Nothing is copied before `starts_after`."""
    lines = {"read": [], "skip": []}
    for v in verdicts:
        who = f"{v['app']}{SEP}{v['account'] or 'unknown account'}{SEP}{v['verdict']}"
        if v["verdict"] in READ:
            lines["read"].append(who)
        elif v["verdict"] in SKIP:
            lines["skip"].append(who + (f", {v['why']}" if v["why"] else ""))
    return {**lines, "not_signed_in": [f"{app}{SEP}not signed in" for app in unsigned],
            "starts_after": (now + dt.timedelta(minutes=HEAD_START)).isoformat(timespec="seconds")}


def feeds(verdicts: list) -> str:
    """The kinds of the apps that are read, as the feeds of the person's workspace
    (`mail: owner, calendar: owner`), in the order of KINDS; "" when none is."""
    kinds = {k for v in verdicts if v["verdict"] in READ for k in v["kinds"]}
    return ", ".join(f"{k}: owner" for k in KINDS if k in kinds)


def scratch() -> Path:
    """Where readers (and the probe) run: a folder of their own, never the person's home or a
    repository."""
    folder = Path.home() / ".spaces" / "readers" / "apps"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def kept(record: dict, groups: list) -> list:
    """The verdicts a run of the apps phase keeps from the one before (R45, I-2): a final one (FINAL) of an app the
    probe still sees, its tools as the probe sees them now (none that sends or writes for an app that is not read). An
    app to reconnect, a blocked one, an unfinished one and a new one are read again."""
    now = {g["app"]: g for g in groups if isinstance(g, dict) and g.get("app")}
    out = []
    for v in (record or {}).get("verdicts") or []:
        if isinstance(v, dict) and v.get("verdict") in FINAL and v.get("app") in now:
            fresh = {**v, **{k: copy.deepcopy(now[v["app"]][k]) for k in ("kinds", "read", "write", "send") if k in now[v["app"]]}}
            if fresh["verdict"] in NO_TOOLS:
                fresh["send"], fresh["write"] = [], []
            out.append(fresh)
    return out


def _probe_file() -> Path:
    return scratch() / "probe.json"


def keep_probe(probe: dict) -> None:
    """The apps phase's probe, kept for the research that runs beside it, so it need not ask again (dry run B N-4)."""
    try:
        _probe_file().write_text(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                             "tools": list(probe.get("tools") or [])}), encoding="utf-8")
    except OSError:
        pass   # the research asks again


def recent_probe(now: dt.datetime | None = None) -> list | None:
    """The tools of the apps phase's probe when it is at most PROBE_FRESH old; None when there is none to use."""
    try:
        data = json.loads(_probe_file().read_text(encoding="utf-8"))
        at = dt.datetime.fromisoformat(data["at"])
        tools = data["tools"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(tools, list) or at.tzinfo is None or not (now - PROBE_FRESH <= at <= now + dt.timedelta(minutes=5)):
        return None
    return [t for t in tools if isinstance(t, str)]
