"""send_guard: the routines' send guard. A PreToolUse hook on each send tool a routine's profile allows
(`routine.routine_settings`): it lets a call through only when every address it holds, anywhere, is the person
whose space it is, and refuses (exit 2) everything else. A routine sends one thing, the person's own morning
briefing, from their own mailbox to themselves; a record it read can ask for anything.

    python -I send_guard.py <person>        # the hook's JSON on stdin; exit 0 allows, exit 2 blocks

It runs as `python -I`, so it can import nothing but the standard library, and it fails closed (Ruling R30):
what it does not know is refused, never let through. Two readings of the call, both must pass:
- The fields that name recipients are read strictly, at any depth (Outlook puts them a level down, under
  `message`), in every shape the connectors take: a string (`Name <address>`, several split at commas,
  semicolons or line breaks), a list of strings, `{email}`, `{address}` or `{emailAddress: {address}}`
  objects, and `headers` (To, Cc, Bcc, Reply-To, Resent-To, any `...-To`, as a mapping or as name and value
  pairs). A recipient field is one the guard knows (`to`, `cc`, `bcc`, `recipients`, `replyTo`, ...), any
  field named `to`, `cc` or `bcc` followed by list, emails, addresses or ids, and any name ending in `to`.
  Each entry must be exactly one address.
- Every text anywhere in the call, every field name, the subject and the body included, is read for
  anything that looks like an address: an at sign or a look-alike of one (the full-width and small at signs,
  `%40`, `&#64;`, `&commat;`), and a base64 text that decodes to an address. Each must be the person.
A call is refused when:
- no address outside the subject and the body names whom it goes to (a call that holds none at all included);
- any address, anywhere, is not the person (a blind copy, a reply-to, a field the guard does not know, an
  address in the text: a look-alike letter or at sign, a `+` tag or a zero-width character makes another one);
- a recipient field holds anything it cannot read as exactly one address each (a name alone, an id, a number);
- it sends what this hook cannot see: a stored draft (`draftId`), an existing message (`messageId`), a raw
  or MIME message (any field whose name holds `raw` or `mime`), or a reply to all;
- the request itself cannot be read, or is nested deeper than any connector's.
It says why on stderr, naming the person and never the address it refused.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import sys

BLOCK = 2
MAX_DEPTH = 12   # a connector's arguments are a few levels deep; deeper is not read

# One address: ASCII letters only, so a look-alike letter or at sign is no address.
ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")
# `Name <address>`: the name holds no address of its own and no angle bracket.
NAMED = re.compile(r'(?P<name>[^<>@]*?)\s*<(?P<address>[^<>]*)>')
SPLIT = re.compile(r"[,;\r\n]")

# Field names compare without case, `_` or `-`: replyTo, reply_to and Reply-To are one.
RECIPIENT_FIELDS = {"to", "cc", "bcc", "recipient", "recipients", "replyto", "from", "sender", "sendto",
                    "forwardto", "mailto", "toaddress", "toaddresses", "ccaddresses", "bccaddresses",
                    "emails", "addresses", "attendees", "participants", "invitees", "guests"}
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
AT_SIGN = re.compile(r"[@\uff20\ufe6b]|%40|&#0*64|&#x0*40|&commat", re.IGNORECASE)   # a word ends before a ";"
# Where an address in a text ends: white space, and the marks that surround one in mail and markup.
WORD_BREAK = re.compile(r"[\s<>,;:\"'()\[\]{}|\\/=?!*]+")
# A text that may be a base64 message (a raw message under a name the guard does not know).
BASE64 = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")


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
    named = NAMED.fullmatch(text)
    if named:
        text = named.group("address").strip()
    return text if ADDRESS.fullmatch(text) else None


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


def _from_headers(value, depth: int) -> list:
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, dict):
                raise Unreadable("a header that is not a name and a value")
            pairs.append((item.get("name", item.get("key")), item.get("value")))
    elif value is None:
        return []
    else:
        raise Unreadable("headers that are neither a mapping nor a list")
    found = []
    for name, text in pairs:
        if _recipient_field(_key(name)):
            if not isinstance(text, str):
                raise Unreadable("a recipient header that is not text")
            found += _from_string(text)
    return found


def _walk(obj, depth: int, found: list) -> None:
    if depth > MAX_DEPTH:
        raise Unreadable("too deep")
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _walk(item, depth + 1, found)
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
            found += _from_headers(value, depth + 1)
        elif _recipient_field(name):
            found += _from_value(value, depth + 1)
        elif isinstance(value, (dict, list)):
            _walk(value, depth + 1, found)


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


def _mentions(text: str) -> list:
    """Every word of `text` that holds an at sign or a look-alike of one, and every address a base64 text
    decodes to."""
    found = [word for word in WORD_BREAK.split(text) if AT_SIGN.search(word)]
    decoded = _decoded(text)
    if decoded is not None:
        found += ADDRESS.findall(decoded)
    return found


def _scan(obj, depth: int, content: bool, found: list) -> None:
    """(word, in the text of the mail) for every address-like word in `obj`: its field names and its texts,
    at any depth."""
    if depth > MAX_DEPTH:
        raise Unreadable("too deep")
    if isinstance(obj, str):
        found += [(word, content) for word in _mentions(obj)]
    elif isinstance(obj, list):
        for item in obj:
            _scan(item, depth + 1, content, found)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            _scan(key, depth + 1, content, found)
            _scan(value, depth + 1, content or _key(key) in CONTENT_FIELDS, found)


def mentioned(tool_input) -> list:
    """(word, in the text of the mail) for every address-like word anywhere in the call; `Unreadable` when
    it is nested too deeply."""
    found: list = []
    try:
        _scan(tool_input, 0, False, found)
    except RecursionError:
        raise Unreadable("too deep") from None
    return found


def _is_person(word: str, me: str) -> bool:
    """`word` is the person's address: ASCII, one address, and the same without regard to case. A sentence's
    full stop after it is no part of it."""
    word = word.rstrip(".")
    return word.isascii() and address_of(word) == word and word.lower() == me.lower()


def recipients(tool_input: dict) -> list:
    """Every address the call names in a recipient field, in order. ValueError (`Unreadable`) when a
    part of it cannot be read, or the call sends what it does not show."""
    if not isinstance(tool_input, dict):
        raise Unreadable("a request that is not a mapping")
    found: list = []
    try:
        _walk(tool_input, 0, found)
    except RecursionError:
        raise Unreadable("too deep") from None
    return found


def allowed(tool_input: dict, person: str) -> bool:
    """True only when every address the call holds, anywhere, is `person` (without regard to case), and one
    outside the subject and the body says whom it goes to; False for everything else, a person that is not
    one address included."""
    me = address_of(person)
    if me is None:
        return False
    try:
        named = recipients(tool_input)
        words = mentioned(tool_input)
    except ValueError:
        return False
    if not all(a.lower() == me.lower() for a in named) or not all(_is_person(w, me) for w, _ in words):
        return False
    return bool(named) or any(not content for _, content in words)


def main(argv: list, raw: bytes) -> int:
    person = argv[0] if argv else None
    try:
        event = json.loads(raw.decode("utf-8"))
        ok = isinstance(event, dict) and allowed(event.get("tool_input"), person)
    except BaseException:   # noqa: BLE001 - whatever went wrong, the mail does not go
        ok = False
    if not ok:
        print(f"routines send mail only to {address_of(person) or 'the person whose space this is'}", file=sys.stderr)
        return BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin.buffer.read()))
