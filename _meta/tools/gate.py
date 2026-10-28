"""gate: which pull requests land on green and which wait for the owner (rules.md
section 8), plus the signature check on changed pages (hard rule 3).

classify(root, changed_files, base_ref=None) -> ("protected" | "auto-mergeable", reasons)
    A change under any `protected:` prefix of space.yaml, or under the minimum the gate
    always adds (the machinery, the instructions every session loads and git's own
    files), waits for the owner's one click; everything else lands when the checks
    pass. In the template repository itself every pull request is auto-mergeable: the
    template is a code repository, not a space (docs/decisions.md). In the checks
    GitHub's own setting says which repository that is (`spaceyaml.is_template_repo`):
    a copy keeps the marker file until onboarding's first commit lands, never the
    setting. Given the base branch, the rules are read as they stand there, never as
    the pull request has them, and a change to the template's name is protected by
    itself. A protected change still lands when it is a template release its owner
    approved and every protected file it changes is that release's file, byte for
    byte; AGENTS.md, generated in each space, counts when it is what agents_md.py
    renders from the pull request's own space.yaml and _meta/templates (and, for a
    space whose kinds come from the brain, the kinds the base branch's AGENTS.md lists).
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
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents_md  # noqa: E402
import pages  # noqa: E402
import spaceyaml  # noqa: E402

PROTECTED = "protected"
AUTO = "auto-mergeable"
PROVENANCE = "_meta/state/template-version.yaml"
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PAGE_ZONES = ("knowledge/", "live/", "identity/", "_inbound/")
NEVER_SIGNED = {"knowledge/sources/": "witness pages are never signed",
                "live/": "status pages are operational state, never signed"}
# What the gate guards whatever space.yaml says: a list that names fewer never shrinks it. The second line is what
# instructs or runs code on the person's computer: the routines' rendered prompts, the task records `--check` runs, the
# migrations, and the files Claude Code or pytest read at their start. A cloud run has the person's token and a shell,
# so whatever branch it pushes, none of these lands without a click.
MINIMUM_PROTECTED = ("identity/", "rules.md", "space.yaml", ".claude/", "_meta/tools/", ".github/",
                     "CLAUDE.md", "AGENTS.md", ".gitignore", ".gitattributes", ".gitmodules",
                     "_meta/templates/", "_meta/plugins/", ".claude-plugin/",
                     "_meta/state/routines/", "_meta/state/onboard.yaml", "_meta/migrations/", ".mcp.json",
                     "CLAUDE.local.md", "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")
# Read wherever they sit, not only at the root: Claude Code loads a CLAUDE.md, a CLAUDE.local.md and a `.claude/`
# beside the files it opens, and pytest a conftest.py in any folder it collects and its config from any folder above
# the tests. Held at any depth, so a pull request a run pushed cannot put one below the root either.
ANY_DEPTH = ("CLAUDE.md", "CLAUDE.local.md", ".claude", ".mcp.json", "conftest.py", "pytest.ini", "pyproject.toml",
             "setup.cfg", "tox.ini")
# Generated in each space by agents_md.py, so never a release's own file: it rides a release
# when it is what the gate's agents_md.py renders from the pull request's files. The gate's
# agents_md.py is the base branch's, so when a release changes what agents_md.py writes, the
# AGENTS.md regenerated from it lands on its own once that release is on main (the next status run).
GENERATED = "AGENTS.md"
KIND_LINE = re.compile(r"^- `([a-z][a-z0-9-]*)`$")


def _posix(rel: str) -> str:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _under(rel: str, prefix: str) -> bool:
    """`rel` is the prefix's path or inside it, ignoring case: a checkout on a system that ignores
    case puts `.Claude/x` into `.claude/`, and a link may stand where the folder was."""
    rel, prefix = rel.casefold(), _posix(prefix).casefold()
    bare = prefix.rstrip("/")
    return rel == bare or rel.startswith(bare + "/")


def is_template(root) -> bool:
    return spaceyaml.is_template_repo(root)


def with_minimum(prefixes) -> list[str]:
    """The space's own prefixes, then each entry of the minimum they lack, every prefix once (a
    prefix is the same one with or without its slash, in any case, as `_under` matches it)."""
    out, seen = [], set()
    for prefix in [*(str(p) for p in prefixes), *MINIMUM_PROTECTED]:
        key = _posix(prefix).casefold().rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(prefix)
    return out


def protected_prefixes(root) -> tuple[list[str], str | None]:
    """The space's protected prefixes with the minimum, and a note when the defaults had to stand in."""
    try:
        return with_minimum(spaceyaml.load(root).protected), None
    except Exception as exc:  # no or unreadable space.yaml: the defaults still guard
        return with_minimum(spaceyaml.DEFAULTS["protected"]), f"space.yaml unreadable ({exc}); defaults used"


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


def _file(root, ref: str, rel: str) -> str | None:
    """The blob id of `rel` at `ref` when it is a plain file there (not a link, not a submodule)."""
    out = _git(root, "--literal-pathspecs", "ls-tree", "-z", ref, "--", rel).stdout.split("\0")[0]
    meta, _, name = out.partition("\t")
    parts = meta.split()
    if name != rel or len(parts) != 3 or parts[0] not in ("100644", "100755") or parts[1] != "blob":
        return None
    return parts[2]


def _blob_text(root, blob: str) -> str | None:
    """A blob's text, or None when git cannot read it or it is not UTF-8."""
    done = subprocess.run(["git", "cat-file", "blob", blob], cwd=str(root), capture_output=True)
    try:
        return done.stdout.decode("utf-8") if done.returncode == 0 else None
    except UnicodeDecodeError:
        return None


def listed_kinds(text: str | None) -> list[str]:
    """The kinds an AGENTS.md lists after its kinds intro, up to the fixed ones; empty when it lists
    none."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    intros = set(agents_md.KINDS_INTRO.values())
    start = next((n + 1 for n, line in enumerate(lines) if line in intros), len(lines))
    kinds = []
    for line in lines[start:]:
        match = KIND_LINE.match(line)
        if match:
            kinds.append(match.group(1))
        elif line.strip():
            break
    return kinds


def rendered_at(root, ref: str, kinds: list | None = None) -> bool:
    """AGENTS.md at `ref` is a plain file and what this gate's own agents_md.py renders, line endings
    aside, from that commit's space.yaml and _meta/templates, copied into a folder of their own. No
    file of the pull request runs, and no mount is read (the gate's checkout has none): a space
    whose kinds come from the brain and that declares none is rendered with `kinds`, what its
    AGENTS.md on the base branch lists, or else the defaults, never the brain's own list. A
    space.yaml without `name:` never matches: its AGENTS.md is named after a folder the gate does
    not share."""
    blob = _file(root, ref, GENERATED)
    text = _blob_text(root, blob) if blob else None
    blob = _file(root, ref, "space.yaml")
    try:
        data = yaml.safe_load(_blob_text(root, blob) or "") if blob else None
    except yaml.YAMLError:
        data = None
    if text is None or not isinstance(data, dict) or not data.get("name"):
        return False
    if kinds and not data.get("kinds") and data.get("profile") not in ("brain", spaceyaml.TEMPLATE_PROFILE):
        data["kinds"] = list(kinds)
    with tempfile.TemporaryDirectory() as tmp:
        here = Path(tmp)
        (here / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                                         encoding="utf-8", newline="\n")
        rel = f"_meta/templates/{agents_md.TEMPLATE_NAME}"
        blob = _file(root, ref, rel)
        template = _blob_text(root, blob) if blob else None
        if template is not None:
            (here / rel).parent.mkdir(parents=True)
            (here / rel).write_text(template, encoding="utf-8", newline="")
        try:
            return text.replace("\r\n", "\n") == agents_md.render(here)
        except Exception:   # a space.yaml the render cannot read
            return False


def _machinery(rel: str) -> bool:
    """A path template sync installs (its machinery, minus what it never copies)."""
    import template_sync   # imports this module; by now it is loaded

    def under(prefix: str) -> bool:
        return rel == prefix or rel.startswith(prefix + "/")
    return any(under(p) for p in template_sync.MACHINERY) and not any(under(n) for n in template_sync.NEVER)


def _person(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def rules_at(root, ref: str) -> dict:
    """The rules as they stand at `ref` (the base branch): the protected prefixes with the minimum,
    whether this is the template, the owner, the template's name and the kinds AGENTS.md lists. A
    pull request cannot loosen its own gate."""
    data = _yaml_at(root, ref, "space.yaml")
    protected = data.get("protected")
    if not isinstance(protected, list):
        protected = spaceyaml.DEFAULTS["protected"]
    prov = _yaml_at(root, ref, PROVENANCE)
    said = os.environ.get(spaceyaml.TEMPLATE_REPO_ENV, "").strip().lower()   # GitHub's setting, which a copy never has
    return {"protected": with_minimum(protected),
            "template": said == "true" if said in ("true", "false")
            else _show(root, ref, spaceyaml.TEMPLATE_MARKER.as_posix()) is not None,
            "owner": str(data.get("owner") or ""),
            "repo": prov.get("repo"), "release": str(prov.get("release") or ""),
            "kinds": listed_kinds(_show(root, ref, GENERATED))}


def template_url(repo) -> str:
    """Where the template is read: a URL or an absolute local path as given, else `owner/name` on
    GitHub; never a folder relative to the checkout, which the pull request itself could supply."""
    repo = str(repo)
    return repo if "://" in repo or Path(repo).is_absolute() else f"https://github.com/{repo}"


def _version(tag: str) -> tuple | None:
    match = RELEASE_TAG.match(tag)
    return tuple(int(n) for n in match.groups()) if match else None


def _fetch_env(url: str, token: str) -> dict:
    """Git's environment config for the fetch: no global config (the job's url rewrite carries a
    mount token into every address), no checkout header (its token reads only this repository),
    and the template token when there is one. Never on a command line."""
    env = {"GIT_TERMINAL_PROMPT": "0"}
    if not url.startswith("https://github.com/"):
        return env
    env["GIT_CONFIG_GLOBAL"] = os.devnull
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
    # without a token first: a public template needs none, and an expired one would refuse it
    for token in dict.fromkeys(["", os.environ.get("TEMPLATE_TOKEN", "")]):
        env = dict(os.environ, **_fetch_env(url, token))
        if _git(root, "fetch", "--quiet", "--no-tags", *depth, url, f"refs/tags/{tag}", env=env).returncode == 0:
            done = _git(root, "rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}")
            return done.stdout.strip() or None
    return None


def release_match(root, rules: dict, head: str, paths: list) -> tuple[bool, list[str]]:
    """True when the pull request installs a template release its owner approved and every one of
    `paths` (its protected changes) is that release's file, byte for byte, or AGENTS.md as
    agents_md.py renders it from the pull request's own files (`rendered_at`)."""
    generated = GENERATED in paths
    paths = [rel for rel in paths if rel != GENERATED]
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
    if not _version(tag) or (_version(rules["release"]) and _version(tag) < _version(rules["release"])):
        return False, [f"release {tag} is older than {rules['release']}, the base branch's"
                       if _version(tag) else f"{tag} is not a release name"]
    foreign = [f"{rel}: not machinery the template installs" for rel in paths if not _machinery(rel)]
    if foreign:
        return False, foreign
    fetched = fetch_release(root, rules["repo"], tag)
    if fetched is None:
        return False, [f"release {tag} could not be fetched from the template "
                       "(a private template needs TEMPLATE_READ_TOKEN)"]
    if fetched != commit:
        return False, [f"release {tag} now points at {fetched[:8]}, not the approved {commit[:8]}"]
    differ = []
    for rel in paths:
        mine = _file(root, head, rel)
        if mine is None or mine != _file(root, commit, rel):
            differ.append(f"{rel}: not byte-identical to release {tag}")
    if generated and not rendered_at(root, head, rules.get("kinds")):
        differ.append(f"{GENERATED}: not what agents_md.py renders from this space.yaml and _meta/templates")
    if differ:
        return False, differ
    named = [f"{len(paths)} protected file(s) identical to it"] if paths else []
    named += [f"{GENERATED} as agents_md.py renders it"] if generated else []
    return True, [f"release {tag} approved by {approved.get('by')}: " + ", and ".join(named)]


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
        else:
            name = next((n for n in ANY_DEPTH if n.casefold() in rel.casefold().split("/")), None)
            if name:
                reasons.append(f"{rel}: protected by `{name}`, at any depth")
                guarded.append(rel)
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
    """Paths changed between the merge base with `base_ref` and HEAD; a move lists both its paths."""
    for spec in ([f"{base_ref}...HEAD"], [base_ref, "HEAD"]):
        done = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", *spec], cwd=str(root),
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
