"""scan: the generic secrets scan (rules.md section 1, hard rule 1).

Finds the bright-line shapes that must never enter a repository: private key
blocks, cloud and platform tokens, JSON web tokens, passwords with a real value,
bank account numbers. A finding names the kind, the file and the line, never the
matched text. Binary files and files over 1 MB are skipped. The sync script
unstages every file with a finding and records only the count.

    python scan.py <paths...>                 scan these files
    python scan.py --changed-since <ref>      commits since <ref> plus staged and
                                              working changes ([--root R], default .)

Prints `kind path:line` per finding; exit 1 when anything was found.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 1_000_000
SNIFF_BYTES = 8192
EDGE = r"(?<![A-Za-z0-9])"

PATTERNS = {
    "private_key": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "aws_key": re.compile(EDGE + r"AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),
    "github_token": re.compile(EDGE + r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "gitlab_token": re.compile(EDGE + r"glpat-[A-Za-z0-9_-]{20,}"),
    "slack_token": re.compile(EDGE + r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    "api_key_sk": re.compile(EDGE + r"sk-[A-Za-z0-9]{20,}"),
    "jwt": re.compile(EDGE + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
# Links to shared files, meetings and artifacts carry ids, share keys and passcodes, not secrets:
# inside such a link the loose shapes (a token-like `state`, a meeting `pwd`, an id that passes a
# bank checksum) are no finding. Cloud and platform tokens are found everywhere, links included.
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
QUIET_HOSTS = re.compile(r"(?i)^https?://(?:[a-z0-9-]+\.)*(?:google\.com|googleusercontent\.com|googleapis\.com|"
                         r"claude\.ai|claude\.com|zoom\.us|teams\.microsoft\.com|teams\.live\.com)(?:[/:?#]|$)")
LOOSE_KINDS = ("jwt",)


def _quiet(line: str) -> str:
    """The line without its links to shared files, meetings and artifacts."""
    return URL_RE.sub(lambda m: " " if QUIET_HOSTS.match(m.group(0)) else m.group(0), line)


PASSWORD_WORDS = r"(?:password|passwd|pwd|wachtwoord|paswoord|passwort|mot de passe)"
PASSWORD_RE = re.compile(r"(?i)" + PASSWORD_WORDS + r"\s*[:=]\s*(\S+)")
IBAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Z0-9])")
CODE_RE = re.compile(r"(?i)(?:os\.|env|process\.|config|settings|self\.|get|read|input|[A-Za-z_]\w*\()")
PLACEHOLDER_WORDS = {"", "none", "null", "nil", "redacted", "changeme", "placeholder", "example",
                     "hidden", "omitted", "secret", "password", "true", "false", "required",
                     "optional", "string", "value", "your-password"}


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    line_no: int


def _is_placeholder(raw: str) -> bool:
    """A value that stands for a password rather than being one: a template slot, a
    masked value, a variable reference or a code expression that reads it elsewhere."""
    value = raw.strip().strip("\"'`,;.")
    if value.lower() in PLACEHOLDER_WORDS:
        return True
    if value[:1] in ("<", "[", "$", "%", "{") or value.startswith("{{"):
        return True
    if CODE_RE.match(raw.strip()):
        return True
    if set(value) <= set("*") or re.fullmatch(r"(?i)x{3,}", value):
        return True
    if re.fullmatch(r"[-_.]+", value):
        return True
    return False


def _iban_ok(candidate: str) -> bool:
    compact = candidate.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rotated = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in rotated)
    return int(digits) % 97 == 1


def text_of(path: Path) -> str | None:
    """The file's text, or None when it is missing, binary, non-UTF-8 or over 1 MB."""
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


def scan_text(text: str, label: str) -> list[Finding]:
    found: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        kinds = []
        quiet = _quiet(line)
        for kind, pattern in PATTERNS.items():
            if pattern.search(quiet if kind in LOOSE_KINDS else line):
                kinds.append(kind)
        if any(not _is_placeholder(m.group(1)) for m in PASSWORD_RE.finditer(quiet)):
            kinds.append("password")
        if any(_iban_ok(m.group(0)) for m in IBAN_RE.finditer(quiet)):
            kinds.append("iban")
        found.extend(Finding(kind, label, line_no) for kind in kinds)
    return found


# ----- tokens in links: cut from the text, never an alarm ------------------------------------
# A verification, unsubscribe, invitation, signing or tracking link carries a token that opens one
# page or proves one address. The token is cut (the host, path and ticket number stay, they are
# what the link tells) and nobody is warned about it. Share, meeting and artifact links stay whole.
LINK_TOKEN = "[link-token]"
LINK_RE = re.compile(r"https?://(?:[^\s<>\"')\[\]]|\[(?:redacted|link-token)\])+")
TOKEN_PARAMS = {"token", "jwt", "tk", "access_token", "id_token", "refresh_token", "auth", "authtoken",
                "code", "otp", "key", "sig", "signature", "hash", "secret", "session", "sid",
                "invitation_token", "invite", "mtsec", "pwd", "passcode", "pin"}
PARAM_RE = re.compile(r"(?i)((?:[?&;#]|%3F|%26)([A-Za-z_][\w.-]*)=)"
                      r"(\[redacted\]|\[link-token\]|[^&#\s<>\"')/%\[\]]+)")
JWT_ANY = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
TOKEN_PATH = re.compile(r"(?i)verif|confirm|activat|reset|invit|magic|unsubscribe|login|signin|auth|/sign/")
SEGMENT_RE = re.compile(r"/(\[redacted\]|[A-Za-z0-9_-]{16,})(?=[/?#]|$)")


def _opaque(value: str) -> bool:
    """A value that is a token rather than a word or an id: long, letters and digits mixed."""
    return len(value) >= 32 and bool(re.search(r"[A-Za-z]", value)) and bool(re.search(r"\d", value))


def _cut_link(link: str) -> str:
    if QUIET_HOSTS.match(link):
        return link
    link = JWT_ANY.sub(LINK_TOKEN, link)

    def param(m: re.Match) -> str:
        name, value = m.group(2).lower(), m.group(3)
        if value == LINK_TOKEN or not (name in TOKEN_PARAMS or value == "[redacted]" or _opaque(value)):
            return m.group(0)
        return m.group(1) + LINK_TOKEN
    link = PARAM_RE.sub(param, link)
    path_end = min((i for i in (link.find("?"), link.find("#")) if i >= 0), default=len(link))
    head, tail = link[:path_end], link[path_end:]
    if TOKEN_PATH.search(head) or "[redacted]" in head:
        head = SEGMENT_RE.sub(lambda m: m.group(0) if m.group(1) != "[redacted]" and not (
            re.search(r"[A-Za-z]", m.group(1)) and re.search(r"\d", m.group(1))) else "/" + LINK_TOKEN, head)
    return head + tail


def scrub_link_tokens(text: str) -> str:
    """The text with each link's token cut to `[link-token]`; the rest of every link stays."""
    return LINK_RE.sub(lambda m: _cut_link(m.group(0)), text)


# ----- what warrants a warning to the person ---------------------------------------------------
# Only an obvious password or key: a platform key or private key block, a labelled password, or a
# value the export cut out as `[redacted]`. A token in a link, a meeting passcode or PIN, a one-time
# code, a bare token and a bank number are kept out of the repository but warn nobody.
ALARM_KINDS = ("private_key", "aws_key", "github_token", "gitlab_token", "slack_token", "api_key_sk")
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
        kinds.update(kind for kind in ALARM_KINDS if PATTERNS[kind].search(line))
        bare = LINK_RE.sub(" ", line)   # what rides in a link is a token, not a password
        if any(not _is_placeholder(m.group(1)) for m in PASSWORD_RE.finditer(bare)):
            kinds.add("password")
        for m in re.finditer(r"\[redacted\]", bare):
            before = bare[:m.start()]
            if PASSWORD_BEFORE.search(before):
                kinds.add("password")
            elif not _quiet_marker(lines, at, before):
                kinds.add("redacted")
    return sorted(kinds)


def scan_paths(paths) -> list[Finding]:
    """Findings for every readable text file among `paths`; one per kind per line."""
    found: list[Finding] = []
    for raw in paths:
        path = Path(raw)
        text = text_of(path)
        if text is None:
            continue
        found.extend(scan_text(text, str(path)))
    return found


def _git_lines(root: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", *args, "-z"], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return [p for p in done.stdout.split("\0") if p]


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--changed-since", metavar="REF")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root).resolve()
    paths = [Path(p) for p in args.paths]
    if args.changed_since:
        paths += changed_since(root, args.changed_since)
        if not paths:  # a change that only deletes files has nothing to scan
            print("scan: nothing changed to scan")
            return 0
    if not paths:
        parser.print_usage()
        return 2
    findings = scan_paths(paths)
    for finding in findings:
        print(f"{finding.kind} {finding.path}:{finding.line_no}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
