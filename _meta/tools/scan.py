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
    "github_token": re.compile(EDGE + r"gh[pousr]_[A-Za-z0-9]{36,}"),
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


PASSWORD_RE = re.compile(r"(?i)(?:password|passwd|pwd)\s*[:=]\s*(\S+)")
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
