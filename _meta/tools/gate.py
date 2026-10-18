"""gate: which pull requests land on green and which wait for the owner (rules.md
section 8), plus the signature check on changed pages (hard rule 3).

classify(root, changed_files, base_ref=None) -> ("protected" | "auto-mergeable", reasons)
    A change under any `protected:` prefix of space.yaml waits for the owner's one
    click; everything else lands when the checks pass. In the template repository
    itself (`.github/template-marker` present) every pull request is auto-mergeable:
    the template is a code repository, not a space (docs/decisions.md). Given the
    base branch, the rules are read as they stand there, never as the pull request
    has them, and a change to the template's name is protected by itself. A protected
    change still lands when it is a template release its owner approved and every
    protected file it changes is that release's file, byte for byte.
check_signatures(root, changed_files) -> list[str]
    Every changed page under knowledge/, live/, identity/ or _inbound/ with
    `status: stable` carries signed_by, signed_at and a fingerprint equal to its
    body; a stable page under knowledge/sources/ or live/ is an error.

    python gate.py <base_ref> [root]

Prints `GATE: <class>`, the reasons, then any signature errors. Exit 1 on
signature errors only; the class alone never fails the run.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages  # noqa: E402
import spaceyaml  # noqa: E402

PROTECTED = "protected"
AUTO = "auto-mergeable"
PROVENANCE = "_meta/state/template-version.yaml"
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PAGE_ZONES = ("knowledge/", "live/", "identity/", "_inbound/")
NEVER_SIGNED = {"knowledge/sources/": "witness pages are never signed",
                "live/": "status pages are operational state, never signed"}


def _posix(rel: str) -> str:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _under(rel: str, prefix: str) -> bool:
    prefix = _posix(prefix)
    if prefix.endswith("/"):
        return rel.startswith(prefix)
    return rel == prefix or rel.startswith(prefix + "/")


def is_template(root) -> bool:
    return (Path(root) / spaceyaml.TEMPLATE_MARKER).exists()


def protected_prefixes(root) -> tuple[list[str], str | None]:
    """The space's protected prefixes, and a note when the defaults had to stand in."""
    try:
        return list(spaceyaml.load(root).protected), None
    except Exception as exc:  # no or unreadable space.yaml: the defaults still guard
        return list(spaceyaml.DEFAULTS["protected"]), f"space.yaml unreadable ({exc}); defaults used"


def _git(root, *args, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def _show(root, ref: str, rel: str) -> str | None:
    done = _git(root, "show", f"{ref}:{rel}")
    return done.stdout if done.returncode == 0 else None


def _yaml_at(root, ref: str, rel: str) -> dict:
    try:
        data = yaml.safe_load(_show(root, ref, rel) or "")
    except yaml.YAMLError:
        data = None
    return data if isinstance(data, dict) else {}


def _blob(root, ref: str, rel: str) -> str | None:
    done = _git(root, "rev-parse", "--verify", "-q", f"{ref}:{rel}")
    return done.stdout.strip() if done.returncode == 0 else None


def _person(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def rules_at(root, ref: str) -> dict:
    """The rules as they stand at `ref` (the base branch): the protected prefixes, whether this is
    the template, the owner and the template's name. A pull request cannot loosen its own gate."""
    data = _yaml_at(root, ref, "space.yaml")
    protected = data.get("protected")
    if not isinstance(protected, list):
        protected = spaceyaml.DEFAULTS["protected"]
    return {"protected": [str(p) for p in protected],
            "template": _show(root, ref, spaceyaml.TEMPLATE_MARKER.as_posix()) is not None,
            "owner": str(data.get("owner") or ""),
            "repo": _yaml_at(root, ref, PROVENANCE).get("repo")}


def template_url(repo) -> str:
    repo = str(repo)
    return repo if "://" in repo or Path(repo).exists() else f"https://github.com/{repo}"


def _fetch_env(url: str, token: str) -> dict:
    """Git's environment config for the fetch: the checkout's header goes (its token reads only this
    repository), the template token comes in when there is one. Never on a command line."""
    env = {"GIT_TERMINAL_PROMPT": "0"}
    if not url.startswith("https://github.com/"):
        return env
    key = "http.https://github.com/.extraheader"
    pairs = [(key, "")]   # an empty value empties the list of extra headers
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        pairs.append((key, f"AUTHORIZATION: basic {basic}"))
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for n, (k, v) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = k, v
    return env


def fetch_release(root, repo, tag: str) -> str | None:
    """The commit the template's tag points at now, fetched into this repository; None when the
    tag cannot be read (a private template without TEMPLATE_TOKEN, a missing tag)."""
    if not RELEASE_TAG.match(tag):
        return None
    url = template_url(repo)
    depth = ["--depth", "1"] if "://" in url else []
    env = dict(os.environ, **_fetch_env(url, os.environ.get("TEMPLATE_TOKEN", "")))
    if _git(root, "fetch", "--quiet", "--no-tags", *depth, url, f"refs/tags/{tag}", env=env).returncode:
        return None
    done = _git(root, "rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}")
    return done.stdout.strip() or None


def release_match(root, rules: dict, head: str, paths: list) -> tuple[bool, list[str]]:
    """True when the pull request installs a template release its owner approved and every one of
    `paths` (its protected changes) is that release's file, byte for byte."""
    prov = _yaml_at(root, head, PROVENANCE)
    tag, commit = str(prov.get("release") or ""), str(prov.get("commit") or "")
    approved = prov.get("approved") if isinstance(prov.get("approved"), dict) else {}
    if not tag or not commit:
        return False, ["no template release named in the provenance"]
    if str(approved.get("release") or "") != tag or str(approved.get("commit") or "") != commit:
        return False, [f"no approval of release {tag} at {commit[:8]} in the provenance"]
    if not rules["owner"] or _person(approved.get("by")) != _person(rules["owner"]):
        return False, [f"release {tag} was not approved by this space's owner"]
    if not rules["repo"]:
        return False, ["the base branch names no template"]
    fetched = fetch_release(root, rules["repo"], tag)
    if fetched is None:
        return False, [f"release {tag} could not be fetched from the template "
                       "(a private template needs TEMPLATE_READ_TOKEN)"]
    if fetched != commit:
        return False, [f"release {tag} now points at {fetched[:8]}, not the approved {commit[:8]}"]
    differ = []
    for rel in paths:
        mine = _blob(root, head, rel)
        if mine is None or mine != _blob(root, commit, rel):
            differ.append(f"{rel}: not byte-identical to release {tag}")
    if differ:
        return False, differ
    return True, [f"release {tag} approved by {approved.get('by')}: "
                  f"{len(paths)} protected file(s) identical to it"]


def classify(root, changed_files, base_ref: str | None = None, head: str = "HEAD") -> tuple[str, list[str]]:
    root = Path(root)
    rules = rules_at(root, base_ref) if base_ref else None
    if (rules["template"] if rules else is_template(root)):
        return AUTO, ["template repository: pull requests land on green"]
    prefixes, note = (rules["protected"], None) if rules else protected_prefixes(root)
    reasons = [note] if note else []
    guarded = []
    for raw in changed_files:
        rel = _posix(str(raw))
        for prefix in prefixes:
            if _under(rel, prefix):
                reasons.append(f"{rel}: protected by `{prefix}`")
                guarded.append(rel)
                break
    renamed = bool(rules) and PROVENANCE in {_posix(str(p)) for p in changed_files} \
        and _yaml_at(root, head, PROVENANCE).get("repo") != rules["repo"]
    if renamed:
        reasons.append(f"{PROVENANCE}: the template's name changed")
    if guarded and rules and not renamed:
        matched, why = release_match(root, rules, head, guarded)
        if matched:
            return AUTO, reasons + why
        reasons += why
    if guarded or renamed:
        return PROTECTED, reasons
    reasons.append("no protected paths touched" if changed_files else "no changed files")
    return AUTO, reasons


def check_signatures(root, changed_files) -> list[str]:
    root = Path(root)
    errs: list[str] = []
    for raw in changed_files:
        rel = _posix(str(raw))
        path = root / rel
        if not rel.startswith(PAGE_ZONES) or not pages.is_page_path(path) or not path.is_file():
            continue
        try:
            fm, body = pages.read(path)
        except Exception as exc:
            errs.append(f"{rel}: frontmatter does not parse ({exc})")
            continue
        if str(fm.get("status") or "") != "stable":
            continue
        for prefix, why in NEVER_SIGNED.items():
            if rel.startswith(prefix):
                errs.append(f"{rel}: {why}")
        for key in pages.SIGNATURE_FIELDS:
            if not fm.get(key):
                errs.append(f"{rel}: stable page missing signature field `{key}`")
        if fm.get("fingerprint") and fm["fingerprint"] != pages.fingerprint(body):
            errs.append(f"{rel}: fingerprint does not match the body (edited after signing?)")
    return errs


def changed_files(root, base_ref: str) -> list[str]:
    """Paths changed between the merge base with `base_ref` and HEAD."""
    for spec in ([f"{base_ref}...HEAD"], [base_ref, "HEAD"]):
        done = subprocess.run(["git", "diff", "--name-only", "-z", *spec], cwd=str(root),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if done.returncode == 0:
            return [p for p in done.stdout.split("\0") if p]
    raise RuntimeError(f"git diff against {base_ref} failed: {done.stderr.strip()}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    base_ref = argv[0]
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    changed = changed_files(root, base_ref)
    cls, reasons = classify(root, changed, base_ref=base_ref)
    print(f"GATE: {cls}")
    for reason in reasons:
        print(f"  {reason}")
    errs = check_signatures(root, changed)
    for err in errs:
        print(f"error: {err}")
    print(f"{len(changed)} changed files, {len(errs)} signature errors")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
