"""send_guard: the routines' send guard. A PreToolUse hook on each send tool a routine's profile allows
(`routine.routine_settings`): it lets a call through only when every address it holds, anywhere, is the person
whose space it is, and it is plain mail, and refuses (exit 2) everything else. A routine sends one thing, the
person's own morning briefing (and the owner notices), from their own mailbox to themselves; a record it read can
ask for anything.

    python -I send_guard.py <person>        # the hook's JSON on stdin; exit 0 allows, exit 2 blocks

It runs as `python -I`, so it can import nothing but the standard library, and it fails closed (Ruling R30):
what it does not know is refused, never let through. It bounds its own time (Ruling R45): a hook Claude Code cuts
off at its timeout lets the call through, so the guard ends itself with exit 2 after LIMIT_SECONDS, well inside
the hook's timeout (`routine.GUARD_TIMEOUT`), and two seconds later faulthandler ends it even inside one long call
(exit 1, which the hook command's `|| exit 2` turns into a refusal). It reads the call in this order, each step in time that grows with
the call's length and no faster, and the first that fails refuses it:
- The size: a call whose `tool_input` is larger than 64 KB (MAX_INPUT) is not read at all. The briefing and the
  owner notices are a few KB.
- The recipients, before any text: the fields that name whom the mail goes to are read strictly, at any depth
  (Outlook puts them a level down, under `message`), in every shape the connectors take: a string (`Name
  <address>`, several split at commas, semicolons or line breaks), a list of strings, `{email}`, `{address}` or
  `{emailAddress: {address}}` objects, and `headers` (To, Cc, Bcc, Resent-To, any `...-To`, as a mapping or as
  name and value pairs). A recipient field is `to`, `cc`, `bcc`, `recipients` and the others the guard knows,
  any field named `to`, `cc` or `bcc` followed by list, emails, addresses or ids, and any name ending in `to`.
  `from`, `sender` and `replyTo` (SENDER_FIELDS) are read as strictly, but they say who sends the mail or where
  answers go, not whom it goes to. Each entry must be exactly one address, the person's; and at least one must
  say whom the mail goes to.
- Every text anywhere in the call, every field name, the subject and the body included, is read for anything
  that looks like an address: an at sign or a look-alike of one (the full-width and small at signs, `%40`,
  `&#64;`, `&commat;`), and a base64 text that decodes to an address. Each must be the person.
- Every text anywhere, and what a base64 text decodes to, is read for what a mail client would fetch by itself
  when the mail is opened, or a link out (`loads_from_elsewhere`): a picture, a frame, an object, a style sheet,
  `url(...)`, a meta refresh, `src=`, `href=`, an `http(s):` address. None of the shipped senders needs one, so
  the routines' mail is plain: no address of a server at all.
A call is also refused when it sends what this hook cannot see (a stored draft, `draftId`; an existing message,
`messageId`; a raw or MIME message, any field whose name holds `raw` or `mime`; a reply to all), or when the
request cannot be read, or is nested deeper than any connector's. It says why on stderr, naming the person and
never the address or the link it refused.
"""
from __future__ import annotations

import base64
import binascii
import faulthandler
import html
import json
import os
import re
import sys
import threading

BLOCK = 2
MAX_DEPTH = 12   # a connector's arguments are a few levels deep; deeper is not read
MAX_INPUT = 64 * 1024   # the largest `tool_input` read, serialized; the routines' mail is a few KB
MAX_RAW = 1024 * 1024   # the most of stdin read at all: a hook event past it is refused unread
MAX_ENTRY = 512   # one recipient entry, `Name <address>` included: an address is at most 254 characters
LIMIT_SECONDS = 10   # the guard's own time; the hook's timeout (routine.GUARD_TIMEOUT) is three times that

# One address: ASCII letters only, so a look-alike letter or at sign is no address.
ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")
# `Name <address>`: the name holds no address of its own and no angle bracket. Greedy: one way to read it.
NAMED = re.compile(r'(?P<name>[^<>@]*)<(?P<address>[^<>]*)>')
SPLIT = re.compile(r"[,;\r\n]")
# An address read around one at sign in a decoded text: at most 64 letters before it, 255 after.
LOCAL_LETTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+'-")
DOMAIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")

# Field names compare without case, `_` or `-`: replyTo, reply_to and Reply-To are one.
RECIPIENT_FIELDS = {"to", "cc", "bcc", "recipient", "recipients", "sendto",
                    "forwardto", "mailto", "toaddress", "toaddresses", "ccaddresses", "bccaddresses",
                    "emails", "addresses", "attendees", "participants", "invitees", "guests"}
# Who sends the mail, and where answers go: the person alone, but none of them says whom the mail goes to (Minor 7).
SENDER_FIELDS = {"from", "sender", "replyto"}
# `toList`, `cc_emails`, `bccAddrs`, `toIds`: read strictly too.
RECIPIENT_NAME = re.compile(r"(?:to|cc|bcc)(?:list|emails?|addresses|address|addrs?|recipients?|ids?|users?)")
# What sends a message whose recipients are not in the call; any name holding `raw` or `mime` too.
UNSEEN_FIELDS = {"draft", "draftid", "messageid", "eml", "rfc822"}
UNSEEN_PARTS = ("raw", "mime")
REPLY_ALL = {"replyall", "replytoall"}
ADDRESS_KEYS = ("email", "address", "emailaddress", "mail", "value")
# The text of the mail: an address there must be the person too, but it names no recipient.
CONTENT_FIELDS = {"subject", "body", "htmlbody", "textbody", "text", "html", "content", "attachments"}

# An at sign, or what a mailbox or a page would read as one.
AT_SIGN = re.compile(r"[@＠﹫]|%40|&#0*64|&#x0*40|&commat", re.IGNORECASE)   # a word ends before a ";"
# Where an address in a text ends: white space, and the marks that surround one in mail and markup.
WORD_BREAK = re.compile(r"[\s<>,;:\"'()\[\]{}|\\/=?!*]+")
# A text that may be a base64 message (a raw message under a name the guard does not know).
BASE64 = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")

# What a mail client fetches by itself when the mail is opened, and a link out (I-7), read in a text after its
# character references and CSS escapes are resolved, once with tabs and line breaks dropped (a browser drops them
# from a link) and once with them read as spaces (they part a tag's name from its attributes): a tag that loads or
# links, an attribute that names a place to load from, CSS that does, any `http-equiv` (a refresh among them; the
# briefing has none, and a pattern that waited for "refresh" read a long run of spaces in time that grew with its
# square, NB-1), and an address of a server. `<meta charset>` and inline `style` colours stay plain.
LOADS = re.compile(
    r"<(?:img|image|picture|source|video|audio|track|iframe|frame|frameset|object|embed|applet|link|base|script"
    r"|form|input|svg|bgsound|layer|ilayer|portal)(?![a-z0-9-])"
    r"|(?:src|srcset|lowsrc|dynsrc|background|poster|href|action|formaction|ping|codebase|longdesc)\s*="
    r"|url\s*\(|image-set\s*\(|@import"
    r"|http-equiv"
    r"|https?:|(?:ftps?|file)\s*:\s*[/\\]|(?<![a-z0-9.-])www\d{0,3}\.[a-z0-9-]",
    re.IGNORECASE)
CSS_ESCAPE = re.compile(r"\\(?:([0-9A-Fa-f]{1,6})[ \t\r\n\f]?|(.))", re.DOTALL)
DROPPED = re.compile(r"[\t\r\n\f\x00]")


class Unreadable(ValueError):
    """A part of the call that cannot be read as recipients: the call is refused."""


def _key(name) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower()) if isinstance(name, str) else ""


def _recipient_field(key: str) -> bool:
    return (key in RECIPIENT_FIELDS or "recipient" in key or "bcc" in key or key.endswith("to")
            or RECIPIENT_NAME.fullmatch(key) is not None)


def _unseen_field(key: str) -> bool:
    return key in UNSEEN_FIELDS or any(part in key for part in UNSEEN_PARTS)


def address_of(text) -> str | None:
    """The one address in `text` (`address` or `Name <address>`), or None when it holds none, more
    than one, or anything else."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) > MAX_ENTRY:
        return None
    named = NAMED.fullmatch(text)
    if named:
        text = named.group("address").strip()
    return text if ADDRESS.fullmatch(text) else None


def why(person) -> str:
    """The line a refusal says: whom the routines' mail goes to, never the address it refused."""
    return f"routines send mail only to {address_of(person) or 'the person whose space this is'}"


def _from_string(text: str) -> list:
    found = []
    for part in SPLIT.split(text):
        if not part.strip():
            continue
        address = address_of(part)
        if address is None:
            raise Unreadable("an entry that is not one address")
        found.append(address)
    return found


def _from_object(obj: dict, depth: int) -> list:
    if depth > MAX_DEPTH:
        raise Unreadable("too deep")
    found, named = [], False
    for key, value in obj.items():
        if _key(key) not in ADDRESS_KEYS:
            continue
        named = True
        if isinstance(value, dict):
            found += _from_object(value, depth + 1)
        elif isinstance(value, str) and address_of(value):
            found.append(address_of(value))
        else:
            raise Unreadable("an address that is not one")
    if not named:
        raise Unreadable("a recipient without an address")
    return found


def _from_value(value, depth: int) -> list:
    """The addresses in one recipient field."""
    if value is None:
        return []
    if isinstance(value, str):
        return _from_string(value)
    if isinstance(value, dict):
        return _from_object(value, depth + 1)
    if isinstance(value, list):
        found = []
        for item in value:
            if isinstance(item, str):
                found += _from_string(item)
            elif isinstance(item, dict):
                found += _from_object(item, depth + 1)
            else:
                raise Unreadable("a recipient that is neither text nor an address object")
        return found
    raise Unreadable("a recipient field that is neither text nor a list")


def _from_headers(value, depth: int, found: list, senders: list) -> None:
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, dict):
                raise Unreadable("a header that is not a name and a value")
            pairs.append((item.get("name", item.get("key")), item.get("value")))
    elif value is None:
        return
    else:
        raise Unreadable("headers that are neither a mapping nor a list")
    for name, text in pairs:
        key = _key(name)
        if key in SENDER_FIELDS or _recipient_field(key):
            if not isinstance(text, str):
                raise Unreadable("a recipient header that is not text")
            (senders if key in SENDER_FIELDS else found).extend(_from_string(text))


def _walk(obj, depth: int, found: list, senders: list) -> None:
    if depth > MAX_DEPTH:
        raise Unreadable("too deep")
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _walk(item, depth + 1, found, senders)
        return
    for key, value in obj.items():
        name = _key(key)
        if _unseen_field(name):
            if value not in (None, "", False, [], {}):
                raise Unreadable("a message whose recipients are not in the call")
        elif name in REPLY_ALL:
            if value not in (None, False):
                raise Unreadable("a reply to all")
        elif name == "headers":
            _from_headers(value, depth + 1, found, senders)
        elif name in SENDER_FIELDS:
            senders += _from_value(value, depth + 1)
        elif _recipient_field(name):
            found += _from_value(value, depth + 1)
        elif isinstance(value, (dict, list)):
            _walk(value, depth + 1, found, senders)


def _people(tool_input) -> tuple:
    """(the addresses the call sends to, the addresses it names as sender or reply-to), each in order."""
    if not isinstance(tool_input, dict):
        raise Unreadable("a request that is not a mapping")
    found: list = []
    senders: list = []
    try:
        _walk(tool_input, 0, found, senders)
    except RecursionError:
        raise Unreadable("too deep") from None
    return found, senders


def recipients(tool_input: dict) -> list:
    """Every address the call names in a field that says whom it goes to, in order. ValueError (`Unreadable`)
    when a part of it cannot be read, or the call sends what it does not show."""
    return _people(tool_input)[0]


def _decoded(text: str) -> str | None:
    """A base64 text (standard or URL-safe, line breaks allowed) as the characters it encodes, or None."""
    packed = re.sub(r"\s+", "", text)
    if not BASE64.fullmatch(packed):
        return None
    packed = packed.replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(packed + "=" * (-len(packed) % 4), validate=True).decode("latin-1")
    except (binascii.Error, ValueError):
        return None


def _addresses(text: str) -> list:
    """Every address in `text`, each read in a window around its at sign (at most 64 letters before it and 255
    after), so a long run of letters is read once, not once for every place an address could start in it."""
    found, at = [], text.find("@")
    while at != -1:
        start = at
        while start > 0 and at - start < 64 and text[start - 1] in LOCAL_LETTERS:
            start -= 1
        domain = DOMAIN.match(text, at + 1, at + 256)
        if start < at and domain:
            found.append(text[start:at] + "@" + domain.group())
        at = text.find("@", at + 1)
    return found


def _mentions(text: str) -> list:
    """Every word of `text` that holds an at sign or a look-alike of one, and every address a base64 text
    decodes to."""
    found = [word for word in WORD_BREAK.split(text) if AT_SIGN.search(word)]
    decoded = _decoded(text)
    if decoded is not None:
        found += _addresses(decoded)
    return found


def _texts(obj, depth: int, content: bool, found: list) -> None:
    """(text, in the text of the mail) for every field name and text in `obj`, at any depth."""
    if depth > MAX_DEPTH:
        raise Unreadable("too deep")
    if isinstance(obj, str):
        found.append((obj, content))
    elif isinstance(obj, list):
        for item in obj:
            _texts(item, depth + 1, content, found)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            _texts(key, depth + 1, content, found)
            _texts(value, depth + 1, content or _key(key) in CONTENT_FIELDS, found)


def _all_texts(tool_input) -> list:
    found: list = []
    try:
        _texts(tool_input, 0, False, found)
    except RecursionError:
        raise Unreadable("too deep") from None
    return found


def mentioned(tool_input) -> list:
    """(word, in the text of the mail) for every address-like word anywhere in the call; `Unreadable` when
    it is nested too deeply."""
    return [(word, content) for text, content in _all_texts(tool_input) for word in _mentions(text)]


def _css_char(match) -> str:
    if match.group(1) is None:
        return match.group(2)
    code = int(match.group(1), 16)
    return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "�"


def loads_from_elsewhere(text: str) -> bool:
    """Whether `text` holds what a mail client would fetch by itself on opening the mail, or a link out (LOADS),
    read as a client reads it: character references resolved (`&#58;`, `&colon;`, twice encoded too), CSS escapes
    (`\\75 rl(`), and tabs and line breaks both dropped and read as spaces (NB-2: `<object`, a line break, then
    `data=` is a tag)."""
    for _ in range(3):
        plain = html.unescape(text)
        if plain == text:
            break
        text = plain
    text = CSS_ESCAPE.sub(_css_char, text)
    return any(LOADS.search(DROPPED.sub(gap, text)) is not None for gap in ("", " "))


def _loads(tool_input) -> bool:
    for text, _ in _all_texts(tool_input):
        decoded = _decoded(text)
        if loads_from_elsewhere(text) or (decoded is not None and loads_from_elsewhere(decoded)):
            return True
    return False


def _is_person(word: str, me: str) -> bool:
    """`word` is the person's address: ASCII, one address, and the same without regard to case. A sentence's
    full stop after it is no part of it."""
    if len(word) > MAX_ENTRY:
        return False
    word = word.rstrip(".")
    return word.isascii() and address_of(word) == word and word.lower() == me.lower()


def _size(tool_input) -> int:
    return len(json.dumps(tool_input, ensure_ascii=False).encode("utf-8"))


def check(tool_input, person) -> str | None:
    """None when the call is plain mail to `person` alone; else the reason it is refused, in plain words. The
    recipients are read before any text, and a call larger than MAX_INPUT is not read at all."""
    me, said = address_of(person), why(person)
    if me is None:
        return said
    try:
        if _size(tool_input) > MAX_INPUT:
            return f"{said}: this call is larger than 64 KB, more than any mail of theirs"
        named, senders = _people(tool_input)
    except (ValueError, TypeError, RecursionError):
        return said
    if not named or not all(a.lower() == me.lower() for a in named + senders):
        return said
    try:
        if not all(_is_person(word, me) for word, _ in mentioned(tool_input)):
            return said
        if _loads(tool_input):
            return f"{said}, as plain mail: no picture, style sheet or link that loads from elsewhere"
    except (ValueError, RecursionError):
        return said
    return None


def allowed(tool_input: dict, person: str) -> bool:
    """True only when the call is plain mail whose every address, anywhere, is `person` (without regard to
    case), with one in a recipient field; False for everything else, a person that is not one address included."""
    return check(tool_input, person) is None


def main(argv: list, raw: bytes) -> int:
    person = argv[0] if argv else None
    try:
        if len(raw) > MAX_RAW:
            reason = f"{why(person)}: this call is larger than 64 KB, more than any mail of theirs"
        else:
            event = json.loads(raw.decode("utf-8"))
            reason = check(event.get("tool_input"), person) if isinstance(event, dict) else why(person)
    except BaseException:   # noqa: BLE001 - whatever went wrong, the mail does not go
        reason = why(person)
    if reason:
        print(reason, file=sys.stderr)
        return BLOCK
    return 0


def arm(said: str) -> threading.Timer:
    """The guard's own time limit (dry run A: a hook Claude Code cuts off lets the call through): after
    LIMIT_SECONDS a daemon timer says why and ends the process with exit 2, whatever it is doing."""
    def expire() -> None:
        try:
            print(f"{said}: the check took too long, so the call is refused", file=sys.stderr, flush=True)
        finally:
            os._exit(BLOCK)
    timer = threading.Timer(LIMIT_SECONDS, expire)
    timer.daemon = True
    timer.start()
    return timer


def bound(seconds: float) -> None:
    """The limit no call outlasts (NB-1): the timer cannot run while one long call holds the interpreter (a
    regular expression, say), so faulthandler's own thread ends the process with exit 1 after `seconds`, and the
    hook command's `|| exit 2` makes that a refusal (`routine.send_guard_command`)."""
    faulthandler.dump_traceback_later(seconds, exit=True, file=sys.stderr)


def run() -> int:
    """The hook: the timer and the hard limit first, then stdin (at most MAX_RAW and one byte), then `main`."""
    argv = sys.argv[1:]
    arm(why(argv[0] if argv else None))
    bound(LIMIT_SECONDS + 2)
    return main(argv, sys.stdin.buffer.read(MAX_RAW + 1))


if __name__ == "__main__":
    sys.exit(run())
