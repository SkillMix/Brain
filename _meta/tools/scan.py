"""scan: the generic secrets scan (rules.md section 1, hard rule 1).

Finds the bright-line shapes that must never enter a repository: private key
blocks, cloud and platform tokens and keys (Anthropic, OpenAI, xAI, Groq, AWS, GitHub,
GitLab, Slack, Stripe, Google, Azure, npm, PyPI, Hugging Face, Linear, Notion, SendGrid,
Discord, Shopify, Atlassian and others), JSON web tokens, a password inside a URL, an
Authorization header, a labelled key or password with a real value (`API_KEY=`, an
environment name ending in `_KEY`, `_TOKEN`, `_SECRET` or `_PASS`, a JSON
`"password":`), bank account numbers. A token wrapped over two lines (a flowed mail,
a quoted-printable soft break, a page extracted from a PDF) is found on both lines.
A finding names the kind, the file and the line, never the matched text.

Every file is read, whatever its encoding (`readings`): UTF-8 as it is; anything
else (UTF-16, UTF-32, latin-1, cp1252, a binary file) as bytes with its NUL bytes
dropped, so the ASCII shape of a token is still seen, and after a UTF-16 or UTF-32
byte order mark also in that encoding. Bytes that are no text (a picture, a compressed
stream: more than TEXT_CONTROLS percent control bytes, `_textlike`) are searched only for
the anchored shapes, the platform tokens and a private key: the labelled checks (a password,
a labelled key, an IBAN, a password in a URL, an Authorization header) run on text, on the
members an archive unpacks to that are text, and on a PDF's extracted text, never on random
bytes, which spell `pwd=` now and then. A zip archive (an Office or OpenDocument file
too, and one with data before it), a PDF's compressed streams, and a gzip, bzip2, xz or
zlib file (every member of it) are also read unpacked, member by member, and an XML member
also without its tags; a file that is two of these at once is read as each. A PDF page's
text is also read with its pieces joined; text in a font that numbers its own glyphs (most
word processors' PDFs), or behind another PDF filter, is not read. A file of any size is
read: up to SCAN_MAX_BYTES whole, a larger one in chunks of CHUNK_BYTES that each repeat
the end of the one before (CHUNK_OVERLAP, from a line's start), so a token across a border
is still found, and a member past SCAN_MAX_BYTES goes through a temporary file: memory
stays bounded. A file that exists but cannot be read, an archive that unpacks to more than
UNPACK_RATIO times its size (and SCAN_MAX_BYTES, whatever its size) or nests deeper than
UNPACK_DEPTH, and a scan that runs past SCAN_SECONDS, is no clean file: it is the finding
`unscanned` (line 0). A file whose name is a credential file's (`.env`, `*.pem`, `id_rsa`,
`client_secret*.json`, CREDENTIAL_NAMES, or one under `.ssh/`, `.gnupg/` and the like,
CREDENTIAL_DIRS) is the finding `credential_file` (line 0), whatever it holds, but for a
Keynote deck: a `.key` file that is a zip archive with no PEM header (`keynote`).
The sync script unstages every file with a finding and records only the count; it
scans the blob it is about to commit (`scan_bytes`), not the working tree, and for a
Git LFS pointer the content LFS would upload. A finding in a commit's history names
the file as `path@commit`.

    python scan.py <paths...>                 scan these files
    python scan.py --changed-since <ref>      commits since <ref> plus staged and
                                              working changes ([--root R], default .);
                                              every file each of those commits added
                                              or changed is read as that commit holds
                                              it, so a secret added in one commit and
                                              removed in the next still fails

Prints `kind path:line` per finding; exit 1 when anything was found.
"""
from __future__ import annotations

import argparse
import base64
import bisect
import bz2
import codecs
import contextlib
import fnmatch
import io
import lzma
import mmap
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

MAX_BYTES = 1_000_000   # `text_of`: the largest file the tools read and rewrite as text
SCAN_MAX_BYTES = 25_000_000   # the most the scan reads into memory at once: a larger file or member is read in chunks
CHUNK_BYTES = 4_000_000   # one chunk of a file read in chunks (`_scan_chunks`)
CHUNK_OVERLAP = 16_384   # what each chunk reads again of the one before: more than any token, from a line's start
UNPACK_RATIO = 50   # an archive may unpack to this many times its size (and to SCAN_MAX_BYTES whatever its size)
UNPACK_CAP = 2_000_000_000   # and never to more than this: past either it is an archive bomb, `unscanned`
SCAN_SECONDS = 600   # one file's scan, its members included; a file not read in this time is `unscanned`
SNIFF_BYTES = 8192
NUL = chr(0)
EDGE = r"(?<![A-Za-z0-9])"
END = r"(?![A-Za-z0-9])"

PATTERNS = {
    "private_key": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|PuTTY-User-Key-File-\d"),
    "aws_key": re.compile(EDGE + r"(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}" + END),
    "aws_secret": re.compile(r"(?i:aws_?secret_?(?:access_?)?key)\W{0,5}[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
    "github_token": re.compile(EDGE + r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "gitlab_token": re.compile(EDGE + r"gl(?:pat|dt|rt|ptt|cbt|soat|ft|imt)-[A-Za-z0-9_-]{20,}"),
    "slack_token": re.compile(EDGE + r"(?:xox[abposre]-[A-Za-z0-9-]{10,}|xapp-\d-[A-Za-z0-9-]{10,})"),
    "slack_webhook": re.compile(r"hooks\.slack\.com/(?:services|workflows|triggers)/T[A-Z0-9]+/[A-Z0-9]+/[A-Za-z0-9]+"),
    "api_key_sk": re.compile(EDGE + r"sk-[A-Za-z0-9]{20,}"),
    "anthropic_key": re.compile(EDGE + r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(EDGE + r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"),
    "stripe_key": re.compile(EDGE + r"(?:(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|whsec_[A-Za-z0-9]{24,})"),
    "google_key": re.compile(EDGE + r"(?:AIza[0-9A-Za-z_-]{35}|GOCSPX-[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{20,}"
                                    r"|1//0[A-Za-z0-9_-]{30,})"),
    "azure_key": re.compile(r"(?i:AccountKey|SharedAccessKey)=[A-Za-z0-9+/]{40,}={0,2}|SharedAccessSignature=(?:sv)="),
    "npm_token": re.compile(EDGE + r"npm_[A-Za-z0-9]{36}" + END),
    "pypi_token": re.compile(EDGE + r"pypi-AgE[A-Za-z0-9_-]{50,}"),
    "huggingface_token": re.compile(EDGE + r"hf_[A-Za-z0-9]{30,}"),
    "linear_key": re.compile(EDGE + r"lin_(?:api|oauth)_[A-Za-z0-9]{30,}"),
    "notion_token": re.compile(EDGE + r"ntn_[A-Za-z0-9]{40,}"),
    "sendgrid_key": re.compile(EDGE + r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    "discord_webhook": re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{30,}"),
    # Other model providers' keys, by their own prefixes: xAI, Groq, Replicate, Perplexity.
    "ai_key": re.compile(EDGE + r"(?:xai-[A-Za-z0-9]{60,}|gsk_[A-Za-z0-9]{48,}|r8_[A-Za-z0-9]{30,}"
                                r"|pplx-[A-Za-z0-9]{40,})" + END),
    # SaaS tokens with a prefix of their own: Shopify, DigitalOcean, Atlassian, Supabase, Postman,
    # Grafana, Databricks, Square, a Telegram bot, a Twilio API key.
    "platform_token": re.compile(EDGE + r"(?:shp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}|do[por]_v1_[a-f0-9]{64}"
                                        r"|ATATT3[A-Za-z0-9_=-]{100,}|sbp_[a-f0-9]{40}|PMAK-[a-f0-9]{24}-[a-f0-9]{34}"
                                        r"|glsa_[A-Za-z0-9]{32}_[A-Fa-f0-9]{8}|glc_[A-Za-z0-9+/]{32,}={0,2}"
                                        r"|dapi[a-f0-9]{32}|sq0(?:atp|csp)-[A-Za-z0-9_-]{22,}"
                                        r"|\d{8,10}:AA[A-Za-z0-9_-]{33}|SK[0-9a-f]{32})" + END),
    "jwt": re.compile(EDGE + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
# A token wrapped over two lines is searched across the line break for these kinds: every platform
# shape, but not a private key's BEGIN line (no token) or the loose jwt (a link's state, often).
WRAP_KINDS = tuple(kind for kind in PATTERNS if kind not in ("private_key", "jwt"))
TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-./+=")
WRAP_REACH = 160   # how far either side of a line break a wrapped token is looked for
# Links to shared files, meetings and artifacts carry ids, share keys and passcodes, not secrets:
# inside such a link the loose shapes (a token-like `state`, a meeting `pwd`, an id that passes a
# bank checksum) are no finding. Cloud and platform tokens are found everywhere, links included.
URL_RE = re.compile(r"(?i)https?://[^\s<>\"')\]]+")
QUIET_HOSTS = re.compile(r"(?i)^https?://(?:[a-z0-9-]+\.)*(?:google\.com|googleusercontent\.com|googleapis\.com|"
                         r"claude\.ai|claude\.com|zoom\.us|teams\.microsoft\.com|teams\.live\.com)(?:[/:?#]|$)")
LOOSE_KINDS = ("jwt",)


def _quiet(line: str) -> str:
    """The line without its links to shared files, meetings and artifacts."""
    return URL_RE.sub(lambda m: " " if QUIET_HOSTS.match(m.group(0)) else m.group(0), line)


PASSWORD_WORDS = r"(?:password|passwd|pwd|wachtwoord|paswoord|passwort|mot de passe)"
PASSWORD_RE = re.compile(r"(?i)" + PASSWORD_WORDS + r"\s*[:=]\s*(\S+)")
# A quoted label with a quoted value, as JSON has it (`"password": "<value>"`); a quoted label before a
# name (`{"password": pw}` in code) is no password.
JSON_PASSWORD_RE = re.compile(r"(?i)[\"']" + PASSWORD_WORDS + r"[\"']\s*[:=]\s*[\"'](?=[^\"'\s])(\S+)")
# An environment name that holds a password, as in `DB_PASS=<value>` or `SMTP_PASS: <value>`. Upper
# case only, so a variable in code (`first_pass = 1`) is no label.
ENV_PASS_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Z][A-Z0-9]*_)+PASS[\"']?\s*[:=]\s*(\S+)")
# A key or token under its own label, as in a .env file, a config or a JSON document: `API_KEY=...`,
# `"client_secret": "..."`. Counted only for a value of 20 characters or more that mixes letters and
# digits and stands for nothing (`_is_placeholder`).
LABELLED_RE = re.compile(
    r"(?i)(?:api[_-]?key|apikey|api[_-]?token|secret[_-]?key|client[_-]?secret|access[_-]?key|auth[_-]?token|"
    r"access[_-]?token|refresh[_-]?token|private[_-]?token|bearer[_-]?token|webhook[_-]?secret|"
    r"signing[_-]?secret|app[_-]?secret|consumer[_-]?secret)[A-Za-z0-9_-]{0,30}"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-/+=.]{20,})")
# The same in the shapes a .env file and a JSON or YAML document give it without those words: an
# environment name ending in KEY, TOKEN or SECRET (`GITHUB_TOKEN=`, `CALLS_KEY=`, `SECRET=`;
# upper case only, so `cache_key = ...` in code is no label), and a field named just `token` or
# `secret`. The value is held to more there (`_generic`): a path or a dotted name is no key.
ENV_LABEL_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Z][A-Z0-9]*_)*(?:KEY|TOKEN|SECRET|APIKEY)[\"']?\s*[:=]\s*"
                          r"[\"']?([A-Za-z0-9_\-/+=.]{20,})")
FIELD_LABEL_RE = re.compile(r"(?i)(?<![A-Za-z0-9_.-])[\"']?(?:token|secret)[\"']?\s*[:=]\s*[\"']?"
                            r"([A-Za-z0-9_\-/+=.]{20,})")
WORDS_RE = re.compile(r"(?:[A-Za-z]+\d{0,2}|\d+)(?:[-_.](?:[A-Za-z]+\d{0,2}|\d+))+")
EXAMPLE_RE = re.compile(r"(?i)0123456789|1234567890|abcdef|example|dummy|sample|placeholder|your|x{4}")
AUTH_HEADER_RE = re.compile(r"(?i)authorization[\"']?\s*:\s*[\"']?(?:bearer|basic|token)\s+([A-Za-z0-9._~+/=-]{16,})")
# A password in a URL's user info: `postgres://user:<password>@host`. The user part holds no `/`, `:` or `@`.
URL_CREDENTIALS_RE = re.compile(r"(?i)(?<![a-z0-9+.-])[a-z][a-z0-9+.-]{0,30}://[^/\s:@\"'<>]+:([^/\s@\"'<>]{3,})@")
IBAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Z0-9])")
# What reads a password from somewhere else rather than being one: an environment or settings
# lookup, a dotted access, a dotted call or a named reader. Case counts: `Getaway2024` is no code.
CODE_RE = re.compile(r"(?:os\.environ|os\.getenv|process\.env|ENV\[|env\[|config\.|settings\.|self\.|secrets\.|"
                     r"vault\.|getpass\.|[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\(|(?:getenv|getpass|input|env)\()")
# A template slot that stands for the value, and only when it is the whole value: `${DB_PASS}`,
# `$DB_PASS`, `%DB_PASS%`, `{{ vault.x }}`, `${{ secrets.X }}`, `{pw}`, `<your password>`, `[redacted]`.
# An environment name is upper case; brackets hold words only, no digit or symbol. The slot must
# end the value (`SLOT_END` or the line's end follows it): `{{}}Hunter2` is a password.
SLOT_RE = re.compile(r"\$\{\{.*?\}\}|\{\{.*?\}\}|\$\{[A-Za-z_]\w*\}|\$[A-Z_][A-Z0-9_]*|%[A-Z_][A-Z0-9_]*%|"
                     r"\{[A-Za-z_]\w*\}|<[A-Za-z][A-Za-z _-]*>|\[[A-Za-z][A-Za-z _-]*\]")
SLOT_END = frozenset(" \t\"'`,;.)]}")
# What no code expression holds: a lookup with one of these in it is a password that starts like one.
NOT_CODE = re.compile(r"[!@#$%^&*~|\\<>?]")
CUT_MARKERS = ("[redacted]", "[link-token]")   # a value the export or the link scrub already cut
PLACEHOLDER_WORDS = {"", "none", "null", "nil", "redacted", "changeme", "placeholder", "example",
                     "hidden", "omitted", "secret", "password", "true", "false", "required",
                     "optional", "string", "value", "your-password"}

# File names that hold credentials whatever their content: never staged, never landed. Matched on
# the lower-cased file name; CREDENTIAL_OK names a template of such a file, which holds none.
CREDENTIAL_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
                    "*.ppk", "id_rsa", "id_rsa.*", "id_dsa", "id_dsa.*", "id_ecdsa", "id_ecdsa.*", "id_ed25519",
                    "id_ed25519.*", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd",
                    "credentials", "client_secret*.json", "*service-account*.json", "*service_account*.json",
                    "*credentials*.json", "token.json", "*.tfvars")
CREDENTIAL_NAMES += ("*.env", ".envrc", ".pgpass", "*.p8", "id_rsa_*", "id_dsa_*", "id_ecdsa_*", "id_ed25519_*",
                     "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml", ".secrets", "kubeconfig",
                     "*.kubeconfig")
CREDENTIAL_OK = (".env.example", ".env.sample", ".env.template", "*.pub")
# Folders that hold keys and sign-ins, whatever the file in them is called: `.ssh/config`, `.aws/config`.
CREDENTIAL_DIRS = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker"})


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    line_no: int


def credential_file(rel, head: bytes | None = None) -> bool:
    """Whether the file's name is a credential file's (CREDENTIAL_NAMES, less CREDENTIAL_OK), or it
    lies in a folder of keys and sign-ins (CREDENTIAL_DIRS), whatever its name. Case does not count.
    `head`, the file's first bytes when the caller has them, tells a Keynote deck from a key: a
    `.key` that is a zip archive and holds no PEM header (`keynote`) is no credential file by its
    name; the scan still reads it, members and all, like any other file."""
    parts = [part.lower() for part in Path(str(rel).replace("\\", "/")).parts]
    if not parts:
        return False
    if any(part in CREDENTIAL_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    if any(fnmatch.fnmatchcase(name, ok) for ok in CREDENTIAL_OK):
        return False
    hits = [pattern for pattern in CREDENTIAL_NAMES if fnmatch.fnmatchcase(name, pattern)]
    if hits == ["*.key"] and head is not None and keynote(head):
        return False
    return bool(hits)


HEAD_BYTES = 4096   # what `credential_file` is given of a file to tell a Keynote deck from a key


def keynote(head: bytes) -> bool:
    """A file that starts as a zip archive (what Keynote writes) with no PEM header in its first
    bytes: a key is PEM text, or DER, which starts with 0x30, never with a zip's `PK`."""
    head = bytes(head[:HEAD_BYTES])
    return head.startswith(b"PK\x03\x04") and b"-----BEGIN" not in head


def file_head(path: Path) -> bytes | None:
    """A regular file's first HEAD_BYTES (`credential_file`), or None for a link, a folder or a file that
    cannot be read."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with open(path, "rb") as fh:
            return fh.read(HEAD_BYTES)
    except OSError:
        return None


def _is_placeholder(raw: str) -> bool:
    """A value that stands for a password rather than being one: a template slot, a masked
    value, a variable reference or a code expression that reads it elsewhere. `raw` is the
    rest of the line from the value on, so a slot with spaces in it (`<your password>`) is
    seen whole; the value itself is its first word."""
    rest = raw.strip().strip("\"'`")
    token = (raw.split() or [""])[0]
    value = token.strip().strip("\"'`,;.")
    if value.lower() in PLACEHOLDER_WORDS or rest.startswith("[a line held back:"):
        return True   # a value the sync's held line already cut
    if any(rest.startswith(marker) and not rest[len(marker):len(marker) + 1].isalnum() for marker in CUT_MARKERS):
        return True   # a value the export or the link scrub already cut, and nothing written onto it
    slot = SLOT_RE.match(rest)
    if slot and _ends(rest, slot.end()):
        return True
    code = token.strip().strip("\"'`")
    if code.startswith("process.env.") and code.endswith("!"):
        code = code[:-1]   # TypeScript's non-null `process.env.X!`
    if CODE_RE.match(code) and not NOT_CODE.search(code):
        return True
    if set(value) <= set("*") or re.fullmatch(r"(?i)x{3,}", value):
        return True
    if re.fullmatch(r"[-_.]+", value):
        return True
    return False


def _ends(rest: str, at: int) -> bool:
    """Whether the value ends at `at` in `rest`: the line ends there or a SLOT_END follows."""
    return at >= len(rest) or rest[at] in SLOT_END


def _mixed(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value)) and bool(re.search(r"\d", value))


def _iban_ok(candidate: str) -> bool:
    compact = candidate.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rotated = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rotated)
    return int(digits) % 97 == 1


def text_of(path: Path) -> str | None:
    """The file's text, or None when it is missing, binary, non-UTF-8 or over MAX_BYTES: what the
    tools may read and rewrite as text. The scan itself reads every file (`scan_file`)."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:SNIFF_BYTES]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


BOMS = ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"))
# The same encodings without their mark, for a chunk after the first of a file read in chunks (`_scan_chunks`).
BOM_CODECS = ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
              (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))
# Bytes no text holds but random and compressed bytes do, about a quarter of them: the C0 controls but
# tab, line feed, vertical tab, form feed and carriage return, DEL, and the C1 controls. NUL is left out:
# UTF-16 text without a mark is half NULs.
CONTROL_BYTES = bytes([*range(1, 9), *range(14, 32), 127, *range(128, 160)])
TEXT_CONTROLS = 10   # percent: bytes with more control bytes than this, NULs aside, are no text


def readings(data: bytes) -> list[str]:
    """The ways the scan reads a file's bytes, never none. UTF-8 without a NUL byte is read once.
    Anything else is read as bytes with its NULs dropped (UTF-16 or UTF-32 without a mark, a
    binary file, latin-1 or cp1252 text: an ASCII token reads whole), and, after a UTF-16 or
    UTF-32 byte order mark, also as that encoding. Both are read, so a mark in front of ASCII
    text cannot hide what follows it."""
    return [text for text, _ in _readings(data)]


def _readings(data: bytes, codec: str | None = None) -> list[tuple[str, bool]]:
    """`readings`, each with whether it is text (`loose`: every check runs on it) or bytes that are
    no text (only the anchored shapes run, `scan_text`). UTF-8 and a reading in the encoding a mark
    names are text; the bytes with their NULs dropped are text when they look like it
    (`_textlike`): latin-1 and cp1252 do, a picture or a compressed stream does not. `codec` is the
    encoding of a chunk after the first of a file whose mark named it (`_scan_chunks`)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and NUL not in text:
        return [(text, True)]
    found = [codec] if codec else [codec for bom, codec in BOMS if data.startswith(bom)][:1]
    bare = data.replace(NUL.encode(), b"").decode("latin-1")
    return [(data.decode(c, errors="replace"), True) for c in found] + [(bare, _textlike(data))]


CONTROL_CHARS = dict.fromkeys(map(ord, CONTROL_BYTES.decode("latin-1")))   # for str.translate: drop them
WINDOW = 64   # in bytes that are no text, the labelled checks run where this much either side of a hint reads as text
RUN = 28   # or where the hint lies in this many characters with no control character among them (`_run`)
FIELD = 12   # or in this many printable ASCII characters: a short value behind its length byte (`_run`)


def _textlike(data: bytes) -> bool:
    """Whether bytes that are not UTF-8 read as text rather than as binary data: at most
    TEXT_CONTROLS percent of them, NULs aside, are control bytes (`CONTROL_BYTES`). Random or
    compressed bytes hold about a quarter, so `PWd=...` in them is chance, not a password."""
    return _plain(bytes(data).replace(NUL.encode(), b"").decode("latin-1"))


def _plain(text: str) -> bool:
    """Whether `text` (bytes read as latin-1) reads as text: at most TEXT_CONTROLS percent control
    characters, as latin-1, or as UTF-8 with what does not decode dropped. The second reading is for
    UTF-8 text with one stray byte, whose Cyrillic or CJK letters hold bytes 0x80-0x9F."""
    for reading in (text, text.encode("latin-1").decode("utf-8", "ignore")):
        if reading and (len(reading) - len(reading.translate(CONTROL_CHARS))) * 100 <= len(reading) * TEXT_CONTROLS:
            return True
    return False


def _text_lines(text: str) -> set:
    """In bytes that are no text (`_textlike`), the lines where text sits among them: a hint of a labelled
    check (`HINTS`) or an IBAN's shape with WINDOW characters either side that read as text (`_plain`).
    A config file in a tar, a row of a SQLite file or a mail body beside its attachment is such text;
    the labelled checks run on these lines (`scan_text`), never on the random bytes around them."""
    lower, hits = text.lower(), [m.start() for m in IBAN_RE.finditer(text)]
    for hint in {hint for kind in CHECKS for hint in HINTS.get(kind, ())}:
        at = lower.find(hint)
        while at >= 0:
            hits.append(at)
            at = lower.find(hint, at + 1)
    hits = [at for at in hits if _plain(text[max(0, at - WINDOW):at + WINDOW]) or _run(text, at)
            or _run(text, at, FIELD, _ascii)]
    if not hits:
        return set()
    starts, at = [], 0
    for line in text.splitlines(True):
        starts.append(at)
        at += len(line)
    return {bisect.bisect_right(starts, hit) - 1 for hit in hits}


def _run(text: str, at: int, size: int = RUN, fits=lambda c: ord(c) not in CONTROL_CHARS) -> bool:
    """Whether the hint at `at` lies in `size` characters in a row that each `fits`: by default RUN without a
    control character among them, a short row of text between binary fields (a SQLite row beside its blob),
    which random bytes hold about once in a hundred hints; or FIELD printable ASCII ones (`_ascii`), a short
    string behind the length byte that begins it (a cell of an old .xls, a SQLite value), where random bytes
    are printable ASCII about one in three."""
    lo = hi = at
    while lo > 0 and hi - lo < size and fits(text[lo - 1]):
        lo -= 1
    while hi < len(text) and hi - lo < size and fits(text[hi]):
        hi += 1
    return hi - lo >= size


def _ascii(char: str) -> bool:
    return " " <= char <= "~"


def _bom_codec(head: bytes) -> str | None:
    """The encoding a byte order mark at the start of a file names, without its mark; None without one."""
    return next((codec for bom, codec in BOM_CODECS if bytes(head).startswith(bom)), None)


CHECKS = {"password": PASSWORD_RE, "url_credentials": URL_CREDENTIALS_RE, "auth_header": AUTH_HEADER_RE,
          "labelled_key": LABELLED_RE, "iban": IBAN_RE}
# Text that every match of a kind holds, lower-cased: a line without any of it cannot match, so a
# large log is searched only where a kind could be. A kind without hints is searched everywhere.
HINTS = {
    "private_key": ("-----begin ", "putty-user-key-file-"),
    "aws_key": ("akia", "asia", "agpa", "aida", "aroa", "anpa", "anva", "aipa"),
    "aws_secret": ("secret",),
    "github_token": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
    "gitlab_token": ("glpat-", "gldt-", "glrt-", "glptt-", "glcbt-", "glsoat-", "glft-", "glimt-"),
    "slack_token": ("xox", "xapp-"),
    "slack_webhook": ("hooks.slack.com/",),
    "api_key_sk": ("sk-",), "anthropic_key": ("sk-ant-",), "openai_key": ("sk-",),
    "stripe_key": ("_live_", "_test_", "whsec_"),
    "google_key": ("aiza", "gocspx-", "ya29.", "1//0"),
    "azure_key": ("accountkey=", "sharedaccesskey=", "sharedaccesssignature="),
    "npm_token": ("npm_",), "pypi_token": ("pypi-age",), "huggingface_token": ("hf_",),
    "linear_key": ("lin_api_", "lin_oauth_"), "notion_token": ("ntn_",), "sendgrid_key": ("sg.",),
    "discord_webhook": ("discord",), "jwt": ("eyj",),
    "ai_key": ("xai-", "gsk_", "r8_", "pplx-"),
    "platform_token": ("shp", "_v1_", "atatt3", "sbp_", "pmak-", "glsa_", "glc_", "dapi", "sq0", ":aa", "sk"),
    "password": ("password", "passwd", "pwd", "wachtwoord", "paswoord", "passwort", "mot de passe", "_pass"),
    "url_credentials": ("://",), "auth_header": ("authorization",), "labelled_key": ("key", "token", "secret"),
}


def _live(text: str, loose: bool = True) -> set:
    """The kinds that could occur in `text`: only these are looked for line by line. Without
    `loose`, only the anchored shapes (PATTERNS): bytes that are no text are never searched for a
    labelled password or key (`CHECKS`), which random bytes spell now and then."""
    lower = text.lower()
    live = set()
    for kind, pattern in (*PATTERNS.items(), *(CHECKS.items() if loose else ())):
        hints = HINTS.get(kind)
        if any(hint in lower for hint in hints) if hints else pattern.search(text):
            live.add(kind)
    return live


def _line_kinds(line: str, live=None, loose: bool = True) -> list[str]:
    kinds = []
    quiet, lower = _quiet(line), line.lower()

    def wanted(kind: str) -> bool:
        return (live is None or kind in live) and (kind not in HINTS or any(h in lower for h in HINTS[kind]))

    for kind, pattern in PATTERNS.items():
        if wanted(kind) and pattern.search(quiet if kind in LOOSE_KINDS else line):
            kinds.append(kind)
    tests = {
        "password": lambda: _password(quiet),
        "url_credentials": lambda: any(_url_password(m.group(1)) for m in URL_CREDENTIALS_RE.finditer(line)),
        "auth_header": lambda: any(not _is_placeholder(m.group(1)) for m in AUTH_HEADER_RE.finditer(line)),
        "labelled_key": lambda: _labelled_key(quiet),
        "iban": lambda: any(_iban_ok(m.group(0)) for m in IBAN_RE.finditer(quiet)),
    }
    if loose:
        kinds.extend(kind for kind, test in tests.items() if wanted(kind) and test())
    return kinds


def _url_password(value: str) -> bool:
    """A real password in a URL's user info: no placeholder, and no token a platform shape names
    already (that one is its own finding)."""
    value = unquote(value)
    return not _is_placeholder(value) and not any(p.search(value) for p in PATTERNS.values())


def _labelled(value: str) -> bool:
    return _mixed(value) and not _is_placeholder(value) and not any(p.search(value) for p in PATTERNS.values())


def _generic(value: str) -> bool:
    """A value under a label without a key word of its own (`ENV_LABEL_RE`, `FIELD_LABEL_RE`) that is a
    key: `_labelled`; no name made of words and numbers (`app-state-2026-10-22`), no example value
    (`abcd1234...`, `1234567890`), and no path, dotted name or file name unless it mixes upper and
    lower case and digits, as a base64 secret does."""
    if not _labelled(value) or WORDS_RE.fullmatch(value) or EXAMPLE_RE.search(value):
        return False
    if re.search(r"[/.]", value):
        cases = all(re.search(pattern, value) for pattern in (r"[A-Z]", r"[a-z]", r"\d"))
        return cases and not re.search(r"\.[A-Za-z]{2,4}$", value)   # a file name ends in an extension
    return True


def _password(text: str) -> bool:
    """A labelled password with a real value in `text`: `password: ...`, `"password": "..."`, `DB_PASS=...`."""
    return any(not _is_placeholder(text[m.start(1):]) for regex in (PASSWORD_RE, JSON_PASSWORD_RE, ENV_PASS_RE)
               for m in regex.finditer(text))


def _labelled_key(text: str) -> bool:
    """A labelled key or token with a real value in `text` (`LABELLED_RE`, `ENV_LABEL_RE`, `FIELD_LABEL_RE`)."""
    if any(_labelled(m.group(1)) for m in LABELLED_RE.finditer(text)):
        return True
    bare = URL_RE.sub(" ", text)   # a `?token=` in a link is the link scrub's to cut, not a key
    return any(_generic(m.group(1)) for regex in (ENV_LABEL_RE, FIELD_LABEL_RE) for m in regex.finditer(bare))


def _wrapped(line: str, nxt: str) -> list[str]:
    """The platform kinds of a token that starts on `line` and goes on at the start of `nxt`: found
    only across the break, not already whole on `line`."""
    tail, head = line.rstrip(), nxt.lstrip()
    if not tail or not head or tail[-1] not in TOKEN_CHARS or head[0] not in TOKEN_CHARS:
        return []
    kinds = []
    # A quoted-printable soft break ends the line with `=`, which is no part of the token.
    for end in ([tail, tail[:-1]] if tail.endswith("=") and len(tail) > 1 else [tail]):
        a, b = end[-WRAP_REACH:], head[:WRAP_REACH]
        joined, cut = a + b, len(a)
        for kind in WRAP_KINDS:
            if kind in kinds:
                continue
            for m in PATTERNS[kind].finditer(joined):
                if m.start() < cut < m.end() and not PATTERNS[kind].search(joined[m.start():cut]):
                    kinds.append(kind)
                    break
    return kinds


def scan_text(text: str, label: str, *, loose: bool = True, windows: bool = True) -> list[Finding]:
    """Findings in `text`, one per kind per line. A token wrapped over two lines is a finding on both.
    `loose` False is for bytes that are no text (`_textlike`): the anchored shapes are looked for
    everywhere, the platform tokens and a private key, and the labelled checks only on a line where
    text sits among the bytes (`_text_lines`), unless `windows` is False (a PDF's own bytes, whose
    encoded streams are all printable: `_scan_source` reads its text with them blanked)."""
    found: list[Finding] = []
    texty = _text_lines(text) if windows and not loose else set()
    lines, live = text.splitlines(), _live(text, loose or bool(texty))
    carried: dict[int, list[str]] = {}   # line index: the kinds of a token wrapped onto it
    for at, line in enumerate(lines):
        kinds = _line_kinds(line, live, loose or at in texty) if live else []
        kinds += [kind for kind in carried.pop(at, []) if kind not in kinds]
        if at + 1 < len(lines):
            for kind in _wrapped(line, lines[at + 1]):
                if kind not in kinds:
                    kinds.append(kind)
                carried.setdefault(at + 1, []).append(kind)
        found.extend(Finding(kind, label, at + 1) for kind in kinds)
    return found


UNPACK_DEPTH = 3   # an archive inside an archive is unpacked this deep; deeper is `unscanned`
TAG_RE = re.compile(rb"<[^<>]{0,2000}>")
XML_NAMES = (".xml", ".rels", ".xhtml", ".html", ".htm", ".svg")


class _TooBig(Exception):
    """An archive that unpacks past its bound (`_Bounds`), nests deeper than UNPACK_DEPTH or cannot
    be read whole, or a scan that ran out of time."""


class _Bounds:
    """What one file's scan may still spend. Unpacked bytes: SCAN_MAX_BYTES whatever the file's
    size, UNPACK_RATIO times its size for a larger one, never more than UNPACK_CAP; past that it
    is an archive bomb. Time: SCAN_SECONDS, its archive members included. Memory is bounded
    apart from these: the scan holds at most SCAN_MAX_BYTES of one member and a chunk at a time."""

    def __init__(self, size: int) -> None:
        self.left = min(UNPACK_CAP, max(SCAN_MAX_BYTES, UNPACK_RATIO * size))
        self.deadline = time.monotonic() + SCAN_SECONDS

    def spend(self, count: int, label: str) -> None:
        self.left -= count
        if self.left < 0:
            raise _TooBig(label)
        self.tick(label)

    def tick(self, label: str) -> None:
        if time.monotonic() > self.deadline:
            raise _TooBig(f"{label}: out of time")


class _Sink:
    """What an unpacked member holds: in memory up to SCAN_MAX_BYTES, in a temporary file past it,
    so a large member never sits in memory whole."""

    def __init__(self) -> None:
        self.parts: list = []
        self.size = 0
        self.file = None

    def write(self, data: bytes) -> None:
        if not data:
            return
        self.size += len(data)
        if self.file is None and self.size > SCAN_MAX_BYTES:
            self.file = tempfile.TemporaryFile()
            for part in self.parts:
                self.file.write(part)
            self.parts = []
        if self.file is not None:
            self.file.write(data)
        else:
            self.parts.append(bytes(data))

    def value(self):
        """The bytes, or the temporary file at its start (the caller closes it)."""
        if self.file is None:
            return b"".join(self.parts)
        self.file.flush()
        self.file.seek(0)
        return self.file

    def drop(self) -> None:
        if self.file is not None:
            self.file.close()


def _close(body) -> None:
    if not isinstance(body, (bytes, bytearray)):
        body.close()


def _feed(inflate, piece: bytes, sink: _Sink, bounds: _Bounds, label: str) -> None:
    """One piece of input through a zlib, bz2 or lzma decompressor, its output into `sink` CHUNK_BYTES
    at a time, so a small input that inflates to a lot is counted as it grows (`bounds`)."""
    zlib_like = hasattr(inflate, "unconsumed_tail")
    buf = piece
    while True:
        out = inflate.decompress(buf, CHUNK_BYTES)
        if out:
            bounds.spend(len(out), label)
            sink.write(out)
        if inflate.eof:
            return
        if zlib_like:   # zlib keeps the input it did not reach; a full output may have more behind it
            buf = inflate.unconsumed_tail
            if not buf and len(out) < CHUNK_BYTES:
                return
        else:   # bz2, lzma keep it themselves: more output waits while they need no input
            if inflate.needs_input:
                return
            buf = b""


def _inflate(make, data, start: int, stop: int, bounds: _Bounds, label: str, again: bytes = b""):
    """`data[start:stop]` decompressed by a fresh `make()` (zlib, bz2 or lzma), read CHUNK_BYTES at a
    time; None when it is not that format (it is then read as the bytes it is). A gzip or bzip2 file
    may hold several members one after another, which their tools give out as one: while what is left
    starts with `again` (the format's mark), it is read as the next member. A file cut off is what it
    inflated to. The bytes, or a temporary file past SCAN_MAX_BYTES (`_Sink`); raises `_TooBig` past
    `bounds`."""
    sink, pos, first = _Sink(), start, True
    inflate, pending = make(), b""
    try:
        while True:
            if not pending:
                if pos >= stop:
                    break   # the input ended: what was inflated is the member
                pending = data[pos:min(stop, pos + CHUNK_BYTES)]
                pos += len(pending)
            try:
                _feed(inflate, pending, sink, bounds, label)
            except (zlib.error, OSError, EOFError, ValueError, lzma.LZMAError):
                if first:
                    sink.drop()
                    return None
                break   # what follows the last whole member is read as the file's own bytes
            pending = b""
            if not inflate.eof:
                continue
            first = False
            rest = inflate.unused_data
            head = (rest + data[pos:min(stop, pos + len(again))])[:len(again)] if again else b""
            if not again or head != again:
                break
            inflate, pending = make(), rest
    except BaseException:
        sink.drop()
        raise
    return sink.value()


# A PDF's streams: each body between `stream` and `endstream`, most of them Flate (zlib) compressed.
# The end of line after `stream` is CRLF or LF; CR alone is taken too, as lenient readers do.
PDF_STREAM_RE = re.compile(rb"(?<!end)stream[ \t]*(?:\r\n|\r|\n)")
PDF_STREAMS_MAX = 20_000   # a PDF with more streams than this is `unscanned`, not read in part
# The text a page shows: the strings of a `[...] TJ` array, which a PDF writer splits for spacing.
PDF_TJ_RE = re.compile(rb"\[((?:[^\[\]\\]|\\.){0,20000})\]\s*TJ", re.S)
PDF_STRING_RE = re.compile(rb"\(((?:[^()\\]|\\.){0,5000})\)|<([0-9A-Fa-f\s]{0,10000})>", re.S)
PDF_TJ_REACH = 60_000   # more than one TJ array: a window of a large stream repeats this much of the one before


def _pdf_text(body: bytes) -> bytes:
    """The strings of each TJ array in a page's content joined, one array a line, so a token the
    writer split for kerning (`[(ghp_ab) -20 (cd)] TJ`) is whole again; b"" when there is none.
    Text in a font that numbers its own glyphs (most word processors' PDFs) is not read."""
    lines = []
    for array in PDF_TJ_RE.finditer(body):
        parts = []
        for match in PDF_STRING_RE.finditer(array.group(1)):
            if match.group(1) is not None:
                parts.append(re.sub(rb"\\(.)", rb"\1", match.group(1)))
            else:
                digits = re.sub(rb"\s", b"", match.group(2))
                try:
                    parts.append(bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode("ascii")))
                except ValueError:
                    pass
        lines.append(b"".join(parts))
    return b"\n".join(lines)


def _windows(read, size: int, reach: int):
    """`read(start, stop)` over [0, size) in windows of CHUNK_BYTES, each repeating `reach` bytes of the one
    before it, so what is shorter than `reach` and lies across a border is whole in one window."""
    start = 0
    while start < size:
        stop = min(size, start + max(CHUNK_BYTES, reach + 1))
        yield read(start, stop)
        if stop >= size:
            return
        start = stop - reach


def _file_reader(fh):
    def read(start: int, stop: int) -> bytes:
        fh.seek(start)
        return fh.read(stop - start)
    return read


def _pdf_streams(data):
    """(dictionary, begin, end) of each stream of a PDF (`data`, bytes or a mapped file): the bytes of the
    object before its `stream` keyword, and where its body lies."""
    at, count = 0, 0
    while True:
        match = PDF_STREAM_RE.search(data, at)
        if not match:
            return
        end = data.find(b"endstream", match.end())
        if end < 0:
            end = len(data)
        count += 1
        if count > PDF_STREAMS_MAX:
            raise _TooBig("pdf streams")
        head = bytes(data[max(at, match.start() - 2048):match.start()])
        cut = head.rfind(b"obj")
        yield (head[cut + 3:] if cut >= 0 else head), match.end(), end
        at = end + len(b"endstream")   # past it: its own `stream` is no new stream


def _a85(body: bytes) -> bytes | None:
    """An ASCII85 stream's bytes (`<~ ... ~>`, whitespace aside); None when they do not decode."""
    cut = body.find(b"~>")
    body = (body[:cut] if cut >= 0 else body).strip()
    try:
        return base64.a85decode(body[2:] if body.startswith(b"<~") else body, adobe=False)
    except ValueError:
        return None


LINE_BREAKS = b"\n\r\x0b\x0c\x1c\x1d\x1e\x85"   # what `str.splitlines` breaks a latin-1 reading at
BLANK = bytes(b if b in LINE_BREAKS else 32 for b in range(256))


def _pdf_blanked(data: bytes) -> bytes | None:
    """A PDF's own bytes with the body of each encoded stream (its dictionary names a /Filter) blanked,
    its line breaks kept, so the labelled checks never read encoded noise: an ASCII85 stream is all
    printable and spells `PWd=...` now and then. What such a stream holds is read decoded, as a member
    (`_pdf_members`). None for a file that is no PDF or has no encoded stream."""
    if b"%PDF-" not in data[:1024]:
        return None
    out, last = [], 0
    for head, begin, end in _pdf_streams(data):
        if b"/Filter" in head:
            out += [data[last:begin], data[begin:end].translate(BLANK)]
            last = end
    return b"".join(out + [data[last:]]) if out else None


def _pdf_members(data, bounds: _Bounds):
    """Each stream of a PDF (`data`, bytes or a mapped file) decoded, when it is ASCII85, and inflated,
    when it is zlib compressed (FlateDecode, also an object stream), and the text its TJ arrays show
    joined (`_pdf_text`); a stream that does not inflate is already read as the PDF's own bytes, and its
    TJ text is added too. A stream or its text past SCAN_MAX_BYTES is read in windows (`_windows`)."""
    for head, begin, end in _pdf_streams(data):
        bounds.tick("pdf stream")
        # shortcut: an ASCII85 stream past SCAN_MAX_BYTES is read as its own bytes, not decoded
        a85 = (b"/ASCII85Decode" in head or b"/A85" in head) and end - begin <= SCAN_MAX_BYTES
        decoded = _a85(bytes(data[begin:end])) if a85 else None
        if decoded is not None:
            bounds.spend(len(decoded), "pdf stream")
            body = _inflate(zlib.decompressobj, decoded, 0, len(decoded), bounds, "pdf stream") or decoded
        else:
            body = _inflate(zlib.decompressobj, data, begin, end, bounds, "pdf stream")
        # its text is read from what it inflated to, else from the stream as it is; a large one window by window
        if isinstance(body, (bytes, bytearray)) and body:
            pieces = [_pdf_text(body)]   # a `_Sink` gives bytes only up to SCAN_MAX_BYTES
        elif body:
            pieces = [_pdf_text(window) for window in _windows(_file_reader(body), body.seek(0, 2), PDF_TJ_REACH)]
            body.seek(0)
        elif end - begin <= SCAN_MAX_BYTES:
            pieces = [_pdf_text(bytes(data[begin:end]))]
        else:
            pieces = [_pdf_text(window) for window in
                      _windows(lambda a, b: data[begin + a:begin + b], end - begin, PDF_TJ_REACH)]
        if body:
            yield "pdf-stream", body
        for text in pieces:
            if text:
                bounds.spend(len(text), "pdf text")
                yield "pdf-text", text


def _is_zip(data, fh=None) -> bool:
    """A zip archive: one that starts like one, or one with data before it (a script or an
    installer in front), which zip tools read from its end (the central directory)."""
    if bytes(data[:4]) == b"PK\x03\x04":
        return True
    try:
        return zipfile.is_zipfile(fh if fh is not None else io.BytesIO(data))
    except (OSError, ValueError):
        return False


def _zip_members(fh, bounds: _Bounds):
    """Each member of a zip archive (`fh`, a file object) with its name, read CHUNK_BYTES at a time;
    nothing for a file that only looks like a zip (it is read as the bytes it is). An encrypted
    member is passed over: what lands is ciphertext. A member past SCAN_MAX_BYTES comes as a
    temporary file (`_Sink`)."""
    try:
        archive = zipfile.ZipFile(fh)
        infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return
    for info in infos:
        if info.is_dir() or info.flag_bits & 0x1:
            continue
        sink = _Sink()
        try:
            with archive.open(info) as member:
                while True:
                    piece = member.read(CHUNK_BYTES)
                    if not piece:
                        break
                    bounds.spend(len(piece), info.filename)
                    sink.write(piece)
        except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError, EOFError,
                zlib.error, lzma.LZMAError) as exc:
            sink.drop()
            raise _TooBig(info.filename) from exc
        except BaseException:
            sink.drop()
            raise
        yield info.filename, sink.value()


COMPRESSED = ((b"\x1f\x8b", lambda: zlib.decompressobj(wbits=31), "gzip"),
              (b"BZh", bz2.BZ2Decompressor, "bzip2"),
              (b"\xfd7zXZ\x00", lzma.LZMADecompressor, "xz"))


def _members(data, fh, bounds: _Bounds):
    """What a compressed file (`data`: bytes or a mapped file; `fh`: a file object over the same
    bytes, or None) holds, member by member as (name, bytes or a temporary file): each member of a
    zip archive (an Office or OpenDocument file too, and a zip with data before it), each
    zlib-compressed stream of a PDF and the text its TJ arrays show (`_pdf_members`), and the
    content of a gzip, bzip2, xz or zlib file, every member of it; each that applies (a file can be
    a PDF and a zip at once). Each reading on its own, not one or the other: a file can be two at once
    (a PDF or a gzip file with a zip after it), and each reader reads its own part."""
    if _is_zip(data, fh):
        yield from _zip_members(fh if fh is not None else io.BytesIO(data), bounds)
    if b"%PDF-" in bytes(data[:1024]):
        yield from _pdf_members(data, bounds)
    head = bytes(data[:6])
    kind = next((k for k in COMPRESSED if head.startswith(k[0])), None)
    if kind is None and len(head) > 2 and head[0] & 0x0F == 8 and head[0] >> 4 <= 7 \
            and (head[0] << 8 | head[1]) % 31 == 0:
        kind = (b"", zlib.decompressobj, "zlib")   # a zlib header: deflate, and a check that holds
    if kind is not None:
        body = _inflate(kind[1], data, 0, len(data), bounds, kind[2], again=kind[0])
        if body is not None:
            yield "", body


def unpacked(data: bytes, budget: list | None = None, depth: int = 0) -> list[bytes]:
    """What a compressed file holds, as bytes, an archive member unpacked in turn, an XML member also
    without its tags, since a word processor splits a line's text over several runs (`_members`); []
    for any other file. Raises `_TooBig` when the members come to more than their bound (`_Bounds`),
    nest deeper than UNPACK_DEPTH, or cannot be read: such a file is `unscanned`, never clean. The
    scan itself reads member by member (`_scan_source`); this list is for a caller that wants them all
    and holds them in memory."""
    bounds = budget[0] if budget and isinstance(budget[0], _Bounds) else _Bounds(len(data))
    out: list[bytes] = []
    for name, body in _members(data, None, bounds):
        if depth >= UNPACK_DEPTH:
            _close(body)
            raise _TooBig("nested")
        if not isinstance(body, (bytes, bytearray)):
            with body:
                body = body.read()
        out.append(body)
        if name.lower().endswith(XML_NAMES):
            out.append(TAG_RE.sub(b"", body))
        out.extend(unpacked(body, [bounds], depth + 1))
    return out


def _keep(found: dict, findings, offset: int = 0) -> None:
    for finding in findings:
        line = finding.line_no + offset if finding.line_no else 0
        found.setdefault((finding.kind, line), Finding(finding.kind, finding.path, line))


def _cut(data, start: int, limit: int, unit: int) -> int:
    """Where a chunk that may run to `limit` ends: after the last line break in its last CHUNK_OVERLAP
    bytes, else at `limit`, never inside a UTF-8 character, nor inside a UTF-16 or UTF-32 one (`unit`)."""
    at = data.rfind(b"\n", max(start + 1, limit - CHUNK_OVERLAP), limit)
    if at >= 0:
        cut = _past(at, unit)
    else:
        cut = limit
        for _ in range(3):
            if cut - 1 > start and data[cut] & 0xC0 == 0x80:
                cut -= 1
        cut -= cut % unit
    return max(cut, start + 1)


def _past(at: int, unit: int) -> int:
    """Just past the line break whose byte 0x0A is at `at`, its whole character in UTF-16 or UTF-32 (`unit`
    bytes): rounded up to the character's end."""
    after = at + 1
    return after + (-after) % unit


def _resume(data, start: int, stop: int, unit: int) -> int:
    """Where the chunk after [start, stop) starts: at the start of the line it ended with, so that
    line is read again with the next one (a token wrapped over the border), when that is within
    CHUNK_OVERLAP; else CHUNK_OVERLAP before its end, so a token across the border is whole."""
    low = max(start + 1, stop - CHUNK_OVERLAP)
    at = data.rfind(b"\n", low, stop - unit)
    nxt = _past(at, unit) if at >= 0 else low - low % unit
    return max(nxt, start + 1)


def _scan_chunks(data, label: str, bounds: _Bounds, found: dict, transform=None) -> None:
    """A file too large to read whole (`data`: bytes or a mapped file), read CHUNK_BYTES at a time,
    each chunk repeating the end of the one before (`_resume`), so a token or a wrapped line across
    a border is still found. Each finding keeps its line in the whole file. `transform` is applied
    to each chunk first (an XML member's tags taken out)."""
    size = len(data)
    codec = _bom_codec(data[:4])
    unit = 4 if codec and "32" in codec else 2 if codec else 1
    start, lines, counted = 0, 0, 0
    while True:
        bounds.tick(label)
        stop = size if size - start <= CHUNK_BYTES else _cut(data, start, start + CHUNK_BYTES, unit)
        chunk = data[start:stop]
        lines += data[counted:start].count(b"\n")
        counted = start
        if transform is not None:
            chunk = transform(chunk)
        for text, loose in _readings(chunk, codec if start else None):
            _keep(found, scan_text(text, label, loose=loose), lines)
        if stop >= size:
            return
        start = _resume(data, start, stop, unit)


@contextlib.contextmanager
def _mapped(fh):
    """A file object's bytes as a read-only map (searched like bytes, never read into memory); a
    file too small to map is read."""
    if hasattr(fh, "flush"):
        fh.flush()
    size = fh.seek(0, 2)
    fh.seek(0)
    if size == 0:
        yield b""
        return
    view = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        yield view
    finally:
        view.close()


def _scan_source(data, fh, label: str, bounds: _Bounds, depth: int, found: dict) -> None:
    """Every reading of `data` (bytes, or a mapped file with `fh` its file object) and of each member
    it holds, unpacked in turn, member by member. Up to SCAN_MAX_BYTES a file is read whole, a larger
    one in chunks (`_scan_chunks`); a member past SCAN_MAX_BYTES comes as a temporary file and is read
    the same way."""
    if len(data) <= SCAN_MAX_BYTES:
        whole = bytes(data)
        # A PDF's own bytes: the anchored shapes everywhere, the labelled checks with its encoded streams
        # blanked. shortcut: a PDF past SCAN_MAX_BYTES is read in chunks, every check on every byte.
        blanked = _pdf_blanked(whole)
        for text, loose in _readings(whole):
            _keep(found, scan_text(text, label, loose=loose and blanked is None, windows=blanked is None))
        for text, loose in _readings(blanked) if blanked is not None else ():
            _keep(found, scan_text(text, label, loose=loose))
    else:
        _scan_chunks(data, label, bounds, found)
    for name, body in _members(data, fh, bounds):
        try:
            if depth >= UNPACK_DEPTH:
                raise _TooBig("nested")
            xml = name.lower().endswith(XML_NAMES)
            if isinstance(body, (bytes, bytearray)):
                if xml:
                    for text, loose in _readings(TAG_RE.sub(b"", body)):
                        _keep(found, scan_text(text, label, loose=loose))
                _scan_source(body, None, label, bounds, depth + 1, found)
            else:
                with _mapped(body) as view:
                    if xml:
                        _scan_chunks(view, label, bounds, found, transform=lambda chunk: TAG_RE.sub(b"", chunk))
                    _scan_source(view, body, label, bounds, depth + 1, found)
        finally:
            _close(body)


def scan_bytes(data: bytes, label: str) -> list[Finding]:
    """Findings in a file's bytes, whatever their encoding, and in what it holds when it is an
    archive (`_scan_source`): a file of any size is read, a large one in chunks. The one finding
    `unscanned` for an archive that unpacks past its bound or nests too deep, one that cannot be
    read whole, or a scan past SCAN_SECONDS, since not all of it was read."""
    found: dict = {}
    try:
        _scan_source(data, None, label, _Bounds(len(data)), 0, found)
    except _TooBig:
        return [Finding("unscanned", label, 0)]
    return list(found.values())


def _scan_handle(fh, label: str) -> list[Finding]:
    """`scan_bytes` for a file object (a file on disk or a temporary file), mapped rather than read
    when it is larger than SCAN_MAX_BYTES, so memory stays bounded whatever its size."""
    size = fh.seek(0, 2)
    fh.seek(0)
    if size <= SCAN_MAX_BYTES:
        return scan_bytes(fh.read(), label)
    found: dict = {}
    try:
        with _mapped(fh) as view:
            _scan_source(view, fh, label, _Bounds(size), 0, found)
    except (_TooBig, OSError, ValueError):
        return [Finding("unscanned", label, 0)]
    return list(found.values())


def scan_file(path: Path, label: str | None = None) -> list[Finding]:
    """Findings in one file on disk, of any size. A missing path or a directory has none; a file
    that exists but cannot be read, or an archive past its bound, is the finding `unscanned`."""
    label = str(path) if label is None else label
    try:
        if not path.is_file():
            return []
        with open(path, "rb") as fh:
            return _scan_handle(fh, label)
    except OSError:
        return [Finding("unscanned", label, 0)] if os.path.lexists(path) else []


def scan_stream(stream, label: str) -> list[Finding]:
    """Findings in what a stream gives out (a `git cat-file blob` pipe), copied CHUNK_BYTES at a time
    into a temporary file first, so a blob of any size is scanned in bounded memory."""
    with tempfile.TemporaryFile() as fh:
        try:
            while True:
                piece = stream.read(CHUNK_BYTES)
                if not piece:
                    break
                fh.write(piece)
        except OSError:
            return [Finding("unscanned", label, 0)]
        fh.flush()
        return _scan_handle(fh, label)


def scan_blob(root: Path, oid: str, label: str) -> list[Finding]:
    """Findings in one git blob, streamed out of `git cat-file` (`scan_stream`); `unscanned` when git
    cannot hand it over."""
    try:
        proc = subprocess.Popen(["git", "cat-file", "blob", oid], cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return [Finding("unscanned", label, 0)]
    with proc:
        found = scan_stream(proc.stdout, label)
        proc.stdout.close()
        code = proc.wait()
    return found if code == 0 else [Finding("unscanned", label, 0)]


def blob_head(root: Path, oid: str, count: int = 4096) -> bytes:
    """The first `count` bytes of a git blob (b"" when git cannot give them): enough to tell a Keynote
    deck from a key (`credential_file`)."""
    try:
        proc = subprocess.Popen(["git", "cat-file", "blob", oid], cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return b""
    with proc:
        head = proc.stdout.read(count)
        proc.stdout.close()
        proc.kill()
        proc.wait()
    return head


# ----- tokens in links: cut from the text, never an alarm ------------------------------------
# A verification, unsubscribe, invitation, signing or tracking link carries a token that opens one
# page or proves one address. The token is cut (the host, path and ticket number stay, they are
# what the link tells) and nobody is warned about it. Share, meeting and artifact links stay whole.
# A sign-in link and a signed storage link on those same hosts are no share link: their token is cut,
# and so is a sign-in parameter (SHARE_HOST_CUT) on any other link to them.
LINK_TOKEN = "[link-token]"
LINK_RE = re.compile(r"(?i)https?://(?:[^\s<>\"')\[\]]|\[(?:redacted|link-token)\])+")
TOKEN_PARAMS = {"token", "jwt", "tk", "access_token", "id_token", "refresh_token", "auth", "authtoken",
                "code", "otp", "key", "sig", "signature", "hash", "secret", "session", "sid",
                "invitation_token", "invite", "mtsec", "pwd", "passcode", "pin",
                "apikey", "api_key", "api-key", "client_secret", "access_key", "password", "magic", "login_token",
                "x-goog-signature", "x-goog-credential", "x-amz-signature", "x-amz-credential",
                "x-amz-security-token", "awsaccesskeyid"}
# On a share, meeting or artifact host a link stays whole but for these: they hold a sign-in, not a
# share. A plain `token` stays: a shared document's link carries one.
SHARE_HOST_CUT = {"jwt", "access_token", "id_token", "refresh_token", "authtoken", "client_secret",
                  "apikey", "api_key", "api-key", "password", "login_token", "magic", "invitation_token",
                  "x-goog-signature", "x-goog-credential", "x-amz-signature", "x-amz-credential",
                  "x-amz-security-token"}
SHORT_PARAMS = {"t", "k"}   # cut only when the value looks like a token: `?t=30s` is a time, not a token
# A parameter after a literal `?`, `&`, `;` or `#`: its value runs to the next separator, percent
# escapes and `/` included, so an encoded or base64 token is cut whole. After an encoded `%3F` or
# `%26` (a link inside a link), the name may end in an encoded `%3D` and the value stops at `/` or
# `%`, where the inner link goes on.
PARAM_RE = re.compile(r"(?i)((?:[?&;#])([A-Za-z_][\w.-]*)=)"
                      r"(\[redacted\]|\[link-token\]|(?:%[0-9A-Fa-f]{2}|[^&#\s<>\"')%\[\]])+)")
ENCODED_PARAM_RE = re.compile(r"(?i)((?:%3F|%26)([A-Za-z_][\w.-]*)(?:=|%3D))"
                              r"(\[redacted\]|\[link-token\]|[^&#\s<>\"')/%\[\]]+)")
JWT_ANY = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
TOKEN_PATH = re.compile(r"(?i)verif|confirm|activat|reset|invit|magic|unsubscribe|login|signin|auth|/sign/")
SEGMENT_RE = re.compile(r"/(\[redacted\]|[A-Za-z0-9_-]{16,})(?=[/?#]|$)")
FRAGMENT_PARAMS = re.compile(r"[A-Za-z_][\w.-]*=")   # a fragment of `name=value` pairs, cut pair by pair
SHORT_PATH_RE = re.compile(r"(?i)^(https?://[^/?#]+)/(m|l|t)/([A-Za-z0-9_-]{20,})(?=[/?#]|$)")
SIGNIN_RE = re.compile(r"(?i)^https?://(?:(?:[a-z0-9-]+\.)*(?:claude\.ai|claude\.com)/(?:[^?#\s]*/)?"
                       r"(?:magic|login|auth|verify|sign-?in|oauth)\d?(?=[-_/?#]|$)|"
                       r"accounts\.google\.com/|(?:[a-z0-9-]+\.)*storage\.googleapis\.com/|"
                       r"storage\.cloud\.google\.com/)")


def _opaque(value: str) -> bool:
    """A value that is a token rather than a word, an id or a link: long, letters and digits mixed."""
    return len(value) >= 32 and _mixed(value) and "://" not in unquote(value)


def _params(link: str, cut) -> str:
    def param(m: re.Match) -> str:
        name, value = m.group(2).lower(), m.group(3)
        if value == LINK_TOKEN or not cut(name, value):
            return m.group(0)
        return m.group(1) + LINK_TOKEN
    return ENCODED_PARAM_RE.sub(param, PARAM_RE.sub(param, link))


def _cut_signin(link: str) -> str:
    """A sign-in or signed storage link on a share host: its fragment, its token parameters and its
    mixed path segments are cut; an OAuth `state` and the rest of the link stay."""
    base, hashed, fragment = link.partition("#")
    if hashed and fragment and not FRAGMENT_PARAMS.match(fragment):
        fragment = LINK_TOKEN
    elif hashed and fragment:
        fragment = _params("#" + fragment, lambda name, value: name in TOKEN_PARAMS)[1:]
    base = _params(base, lambda name, value: name in TOKEN_PARAMS)
    path_end = min((i for i in (base.find("?"),) if i >= 0), default=len(base))
    head = SEGMENT_RE.sub(lambda m: "/" + LINK_TOKEN if _mixed(m.group(1)) else m.group(0), base[:path_end])
    return head + base[path_end:] + (hashed + fragment if hashed else "")


def _cut_link(link: str) -> str:
    if SIGNIN_RE.match(link):
        return _cut_signin(link)
    if QUIET_HOSTS.match(link):
        return _params(link, lambda name, value: name in SHARE_HOST_CUT)
    link = JWT_ANY.sub(LINK_TOKEN, link)
    link = _params(link, lambda name, value: name in TOKEN_PARAMS or value == "[redacted]" or _opaque(value)
                   or (name in SHORT_PARAMS and len(value) >= 16 and _mixed(value)))
    link = SHORT_PATH_RE.sub(lambda m: f"{m.group(1)}/{m.group(2)}/" + (
        LINK_TOKEN if _mixed(m.group(3)) else m.group(3)), link)
    path_end = min((i for i in (link.find("?"), link.find("#")) if i >= 0), default=len(link))
    head, tail = link[:path_end], link[path_end:]
    if TOKEN_PATH.search(head) or "[redacted]" in head:
        head = SEGMENT_RE.sub(lambda m: m.group(0) if m.group(1) != "[redacted]" and not _mixed(m.group(1))
                              else "/" + LINK_TOKEN, head)
        fragment = tail[tail.find("#") + 1:] if "#" in tail else ""
        if fragment and not FRAGMENT_PARAMS.match(fragment) and len(fragment) >= 16 and _mixed(fragment):
            tail = tail[:tail.find("#") + 1] + LINK_TOKEN
    return head + tail


def scrub_link_tokens(text: str) -> str:
    """The text with each link's token cut to `[link-token]`; the rest of every link stays."""
    return LINK_RE.sub(lambda m: _cut_link(m.group(0)), text)


# ----- what warrants a warning to the person ---------------------------------------------------
# Only an obvious password or key: a platform key or private key block, a labelled password or key,
# a password in a URL, an Authorization header, or a value the export cut out as `[redacted]`. A
# token in a link, a meeting passcode or PIN, a one-time code, a bare token and a bank number are
# kept out of the repository but warn nobody.
ALARM_KINDS = ("private_key", "aws_key", "aws_secret", "github_token", "gitlab_token", "slack_token",
               "slack_webhook", "api_key_sk", "anthropic_key", "openai_key", "stripe_key", "google_key",
               "azure_key", "npm_token", "pypi_token", "huggingface_token", "linear_key", "notion_token",
               "sendgrid_key", "discord_webhook", "ai_key", "platform_token", "url_credentials", "auth_header",
               "labelled_key")
PASSWORD_BEFORE = re.compile(r"(?i)" + PASSWORD_WORDS + r"\s*[:=]?\s*$")
QUIET_BEFORE = re.compile(r"(?i)(?:passcode|wachtwoordcode|pincode|pin|code|verificatiecode|iban|"
                          r"rekeningnummer|account number)\s*[:=]?\s*$")
CODE_WORD = re.compile(r"(?i)\bcode\b|passcode|pincode|verificatiecode")


def _near(lines: list[str], at: int, step: int) -> str:
    """The nearest non-empty line from `at` in direction `step`, or ''."""
    i = at + step
    while 0 <= i < len(lines) and not lines[i].strip():
        i += step
    return lines[i] if 0 <= i < len(lines) else ""


def _quiet_marker(lines: list[str], at: int, before: str) -> bool:
    """A cut value that is a meeting code, PIN, one-time code or bank number, or a cut inside a
    word: no password. A value alone on its line takes its meaning from the lines around it."""
    if before[-1:].isalnum() or QUIET_BEFORE.search(before):
        return True
    if before.strip() or lines[at].strip() != "[redacted]":
        return False
    around = _near(lines, at, -1) + " " + _near(lines, at, 1)
    return bool(CODE_WORD.search(around)) and not re.search(r"(?i)" + PASSWORD_WORDS, around)


def alarms(text: str) -> list[str]:
    """The kinds of obvious password or key in `text`, sorted; never the value."""
    kinds: set[str] = set()
    lines = text.splitlines()
    for at, line in enumerate(lines):
        kinds.update(kind for kind in ALARM_KINDS if kind in PATTERNS and PATTERNS[kind].search(line))
        if at + 1 < len(lines):
            kinds.update(_wrapped(line, lines[at + 1]))
        if any(_url_password(m.group(1)) for m in URL_CREDENTIALS_RE.finditer(line)):
            kinds.add("url_credentials")
        bare = LINK_RE.sub(" ", line)   # what rides in a link is a token, not a password
        if any(not _is_placeholder(m.group(1)) for m in AUTH_HEADER_RE.finditer(bare)):
            kinds.add("auth_header")
        if _labelled_key(bare):
            kinds.add("labelled_key")
        if _password(bare):
            kinds.add("password")
        for m in re.finditer(r"\[redacted\]", bare):
            before = bare[:m.start()]
            if PASSWORD_BEFORE.search(before):
                kinds.add("password")
            elif not _quiet_marker(lines, at, before):
                kinds.add("redacted")
    return sorted(kinds)


def scan_paths(paths, root: Path | None = None) -> list[Finding]:
    """Findings for every file among `paths`, one per kind per line: `credential_file` for a
    credential file's name, `unscanned` for a file the scan could not read whole. A missing
    path or a directory has none. Under `root`, a credential folder is looked for in the path
    from `root` on only, so a checkout that itself lies in one is no finding."""
    found: list[Finding] = []
    for raw in paths:
        path = Path(raw)
        try:
            rel = path.relative_to(root) if root is not None else path
        except ValueError:
            rel = path
        if path.is_file() and credential_file(rel, file_head(path)):
            found.append(Finding("credential_file", str(path), 0))
        found.extend(scan_file(path))
    return found


def _git_lines(root: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", *args, "-z"], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return [p for p in done.stdout.split("\0") if p]


def _git_out(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()}")
    return done.stdout


def changed_since(root: Path, ref: str) -> list[Path]:
    """Files changed since `ref`: commits (ref...HEAD), the index and the working tree."""
    rels: list[str] = []
    try:
        rels += _git_lines(root, "diff", "--name-only", "--diff-filter=d", f"{ref}...HEAD")
    except RuntimeError:
        rels += _git_lines(root, "diff", "--name-only", "--diff-filter=d", ref, "HEAD")
    rels += _git_lines(root, "diff", "--name-only", "--diff-filter=d", "--cached")
    rels += _git_lines(root, "diff", "--name-only", "--diff-filter=d")
    seen: dict[str, None] = {}
    for rel in rels:
        seen.setdefault(rel, None)
    return [root / rel for rel in seen]


def committed_findings(root: Path, ref: str) -> list[Finding]:
    """Findings in every file a commit in `ref..HEAD` added or changed, as that commit holds it, so
    a secret one commit adds and a later one removes still counts: the history lands too. A merge
    counts for what it holds that none of its parents did (`git diff-tree -c`, a change made while
    merging). `credential_file` for a credential file's name; `unscanned` for a blob over
    SCAN_MAX_BYTES or one git cannot hand over. A finding names the file as `path@commit`. A root
    that is no git checkout has none here (`changed_since` refuses it)."""
    probe = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=str(root), capture_output=True)
    if probe.returncode != 0:
        return []
    commits = _git_out(root, "rev-list", "--parents", f"{ref}..HEAD").splitlines()
    entries: list[tuple[str, str, str]] = []   # (label, blob id, path)
    found: list[Finding] = []
    for line in commits:
        ids = line.split()
        if not ids:
            continue
        commit, parents = ids[0], ids[1:]
        how = ["-c"] if len(parents) > 1 else ([] if parents else ["--root"])
        out = _git_out(root, "diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-abbrev", "--no-commit-id",
                       *how, commit)
        fields = out.split("\0")
        for meta, rel in zip(fields[0::2], fields[1::2]):
            parts = meta.lstrip(":").split()
            if not rel or len(parts) < 5:
                continue
            n = (len(parts) - 1) // 2 - 1   # parents: n + 1 modes, n + 1 ids, then the status
            mode, oid = parts[n], parts[2 * n + 1]
            if set(oid) == {"0"} or mode == "160000":   # a deletion, or a gitlink
                continue
            entries.append((f"{rel}@{commit[:12]}", oid, rel))
    found.extend(_scan_blobs(root, entries))
    return found


def _scan_blobs(root: Path, entries: list[tuple[str, str, str]]) -> list[Finding]:
    """Findings in each (label, blob id, path), read with `git cat-file`, `credential_file` for a
    credential file's name among them; a blob is read once however many commits hold it, and one
    over SCAN_MAX_BYTES (`--batch-check` says its size) is streamed (`scan_blob`), not read whole."""
    unique = list(dict.fromkeys(oid for _, oid, _ in entries))
    sizes = {oid: size for oid, (kind, size, _) in zip(unique, _cat_file(root, "--batch-check", unique))
             if kind == "blob"}
    wanted = [oid for oid in unique if oid in sizes and sizes[oid] <= SCAN_MAX_BYTES]
    blobs = {oid: body for oid, (kind, _, body) in zip(wanted, _cat_file(root, "--batch", wanted))
             if kind == "blob"}
    found: list[Finding] = []
    for label, oid, rel in entries:
        body = blobs.get(oid)
        large = body is None and oid in sizes
        head = body[:HEAD_BYTES] if body is not None else blob_head(root, oid) if large else None
        if credential_file(rel, head):
            found.append(Finding("credential_file", label, 0))
        if large:
            found.extend(scan_blob(root, oid, label))
        else:
            found.extend([Finding("unscanned", label, 0)] if body is None else scan_bytes(body, label))
    return found


def _cat_file(root: Path, mode: str, oids: list[str]) -> list[tuple[str, int, bytes]]:
    """(type, size, bytes) for each object id, in order, from one `git cat-file --batch` (bytes) or
    `--batch-check` (no bytes); ("missing", 0, b"") for one git does not have or could not give."""
    if not oids:
        return []
    done = subprocess.run(["git", "cat-file", mode], cwd=str(root), capture_output=True,
                          input="".join(oid + "\n" for oid in oids).encode("ascii"))
    data, at, out = done.stdout, 0, []
    for _ in oids:
        end = data.find(b"\n", at)
        header = data[at:end].split() if done.returncode == 0 and end >= 0 else []
        if len(header) != 3:
            out.append(("missing", 0, b""))
            at = end + 1 if end >= 0 else len(data)
            continue
        size, at = int(header[2]), end + 1
        if mode == "--batch":
            out.append((header[1].decode("ascii"), size, data[at:at + size]))
            at += size + 1   # the content, then a newline
        else:
            out.append((header[1].decode("ascii"), size, b""))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--changed-since", metavar="REF")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root).resolve()
    paths = [Path(p) for p in args.paths]
    findings: list[Finding] = []
    if args.changed_since:
        changed = changed_since(root, args.changed_since)
        findings += scan_paths(changed, root)
        findings += committed_findings(root, args.changed_since)
        if not paths and not changed and not findings:  # a change that only deletes files has nothing to scan
            print("scan: nothing changed to scan")
            return 0
    elif not paths:
        parser.print_usage()
        return 2
    findings = scan_paths(paths) + findings
    for finding in findings:
        print(f"{finding.kind} {finding.path}:{finding.line_no}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
