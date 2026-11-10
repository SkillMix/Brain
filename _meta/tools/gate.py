"""gate: which pull requests land on green and which wait for the owner (rules.md
section 8), plus the signature check on changed pages (hard rule 3).

classify(root, changed_files, base_ref=None) -> ("protected" | "auto-mergeable", reasons)
    A change under any `protected:` prefix of space.yaml, or under the minimum the gate
    always adds (the machinery, the instructions every session loads, git's own files
    and the state that builds or steers other spaces), waits for the owner's one click;
    so does a name read at any depth (`ANY_DEPTH`), a name another system reads as a
    different path (a trailing dot or space, a colon, a backslash, a device name, an
    8.3 short name such as `CLAUDE~1`, the provenance in another case), a compiled
    module anywhere, and a module at the root (a source, a `.pyc` or an extension) that
    `python -m` would import before the installed one. Everything else lands when the
    checks pass (`protected_reason` is the one test, which the sync shares). Given the
    base branch, a changed path that a checkout ignoring case reads as another path of
    the tree (`case_twins`) waits too. In the template repository every pull request lands
    on green, its machinery included, unless it carries a link or one of those names
    (`shape_reason`): the one human gate on what reaches the spaces is the release tag,
    which only a maintainer creates. In the checks GitHub's own setting
    says which repository that is (`spaceyaml.is_template_repo`): a copy keeps the
    marker file until onboarding's first commit lands, never the setting. Given the base
    branch, the rules are read as they stand there, never as the pull request has them;
    a change to the template's name is protected by itself, and so is a change to the
    release or commit the provenance records (in a space following the template's main, a
    dev-only mode, a move forwards along it lands: `follows_main`). A protected change
    still lands when it is a template release its owner approved, on the template's
    default branch, and every
    protected file it changes is that release's file, byte for byte; AGENTS.md,
    generated in each space, counts when it is what agents_md.py renders from the pull
    request's own space.yaml and _meta/templates (and, for a space whose kinds come from
    the brain, the kinds the base branch's AGENTS.md lists).
    Given the base branch it also reads what the paths alone do not show, and none of it
    rides a template release: a path the pull request makes a symbolic link or an unlisted
    submodule (`link_changes`); a subject whose entry points trust somewhere new: a
    `match.repo`, a home naming a space the base branch does not know, a workspace or login
    that is not its person's own first fill, a decided classification that changes, a space
    that gains a signer (`registry_reasons`); a stable page under
    knowledge/ not signed in this space by its people, unless it files the candidate that
    carried it, a log.md that loses or changes a line, a released review batch and a new
    security item on Needs you (`page_reasons`); and a release (`_inbound/candidates/` with
    `released_from`, `_inbound/signals/`) whose branch, files, sender or author do not vouch
    for it, plus, in the brain, a registry edit by a login that is not its owner's, unless it is
    a registered colleague's upkeep (`release_reasons`).
check_signatures(root, changed_files) -> list[str]
    Every changed page under knowledge/, live/, identity/ or _inbound/ (folder and
    `.md` matched ignoring case) with `status: stable` carries signed_by, signed_at and
    a fingerprint equal to its body, and its signed fields still match `signed_fields`
    (`sign.fields_problem`); a stable page under knowledge/sources/ or live/ is an error,
    and so is one whose folder or suffix is spelled in another case, and a stable `.md`
    anywhere outside knowledge/, identity/, _inbound/ and _outbound/ (the desk, the journal
    and the outbox aside, where validate.py warns: a copied page can carry a stale stamp).

    python gate.py <base_ref> [root] [--author LOGIN] [--head-ref BRANCH]

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
import unicodedata
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
# Where a stable page may stand: signed pages, identity, and the corridors that carry a signed
# release (`_inbound/candidates/`, `_outbound/queue/` and the held items beside them).
SIGNED_ZONES = ("knowledge/", "identity/", "_inbound/", "_outbound/", "live/")
# The desk, the journal and the outbox may carry a stale stamp (a page copied there; mint drops it):
# validate.py warns about one and grounding counts it as working, so it never fails the checks.
DESK_ZONES = ("work/", "journal/", "outbox/")
NEVER_SIGNED = {"knowledge/sources/": "witness pages are never signed",
                "live/": "status pages are operational state, never signed"}
# What the gate guards whatever space.yaml says: a list that names fewer never shrinks it. The second line is what
# instructs or runs code on the person's computer: the routines' rendered prompts, the task records `--check` runs, the
# migrations, and the files Claude Code or pytest read at their start. A cloud run has the person's token and a shell,
# so whatever branch it pushes, none of these lands without a click.
# The third line is what a later run reads as its own instruction or state: the behaviour scenarios (command lines
# behaviour.py runs), every name pytest 9 reads its config from, the page AGENTS.md tells every session to read, an
# invitation (its mounts and address build a colleague's workspace), the sources' audiences, the template's origin
# and the pinned packages the checks and contributors install.
MINIMUM_PROTECTED = ("identity/", "rules.md", "space.yaml", ".claude/", "_meta/tools/", ".github/",
                     "CLAUDE.md", "AGENTS.md", ".gitignore", ".gitattributes", ".gitmodules",
                     "_meta/templates/", "_meta/plugins/", ".claude-plugin/",
                     "_meta/state/routines/", "_meta/state/onboard.yaml", "_meta/migrations/", ".mcp.json",
                     "CLAUDE.local.md", "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
                     ".pytest.ini", "pytest.toml", ".pytest.toml", "_meta/tests/behaviour/", "docs/shape-1.md",
                     "_meta/state/invitations/", "_meta/access/resources.yaml", "_meta/state/template-origin.yaml",
                     "requirements.txt", "requirements-dev.txt")
# Read wherever they sit, not only at the root: Claude Code loads a CLAUDE.md, a CLAUDE.local.md and a `.claude/`
# beside the files it opens, other harnesses an AGENTS.md, git a .gitattributes, and pytest a conftest.py in any
# folder it collects and its config from any folder above the tests. Held at any depth, so a pull request a run
# pushed cannot put one below the root either.
ANY_DEPTH = ("CLAUDE.md", "CLAUDE.local.md", ".claude", ".mcp.json", "conftest.py", "pytest.ini", "pyproject.toml",
             "setup.cfg", "tox.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "AGENTS.md", ".gitattributes")
# An NTFS 8.3 short name: on Windows `CLAUDE~1` is `.claude` and `GITHUB~1` is `.github`, and git writes through it.
SHORT_NAME = re.compile(r"^[^~/]{1,6}~[0-9]{1,6}(\.[^./]{0,3})?$")
# What Python imports from a folder on its path, not only a source: a sourceless `.pyc` beside it loads as the module,
# and so does an extension (`.pyd` on Windows, `.so` elsewhere, `yaml.cpython-312-x86_64-linux-gnu.so` included).
MODULE_SUFFIXES = (".py", ".pyw", ".pyc", ".pyo", ".pyd", ".so")
# Compiled modules anywhere: Python loads an unchecked hash-based `.pyc` from `__pycache__` in place of its source
# without comparing the two, so a committed one runs code no reviewer sees. No routine writes one (git ignores them).
COMPILED = (".pyc", ".pyo")
# Generated in each space by agents_md.py, so never a release's own file: it rides a release
# when it is what the gate's agents_md.py renders from the pull request's files. The gate's
# agents_md.py is the base branch's, so when a release changes what agents_md.py writes, the
# AGENTS.md regenerated from it lands on its own once that release is on main (the next status run).
GENERATED = "AGENTS.md"
KIND_LINE = re.compile(r"^- `([a-z][a-z0-9-]*)`$")
# What the sync never stages (sync.PRIVATE_ZONES, the same tuple: a test keeps the two equal). git writes an ignored
# file a merge brings, so a pull request that adds one overwrites what the routines read as the owner's own (a
# subject's brief, the Needs-you shown mark, an app grant): it waits for the click in every profile.
PRIVATE_ZONES = ("personal/", "academic/", "_scratch/", "_meta/state/private/", "_meta/state/feed/")


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


def alias_reason(raw) -> str | None:
    """Why a path's name may be another path on some system, or None. Windows drops a trailing dot
    or space (`rules.md.` is `rules.md`), reads `:` as a stream and a device name as a device, and
    resolves an 8.3 short name (`CLAUDE~1/` is `.claude/`); a backslash is a separator there; and a
    name git could not give as UTF-8 is not the name shown. No prefix test can see through these,
    so such a path waits for the click whatever it names."""
    raw = str(raw)
    if "\\" in raw:
        return "a backslash, which Windows reads as a separator"
    if "\ufffd" in raw:   # git's bytes were not UTF-8: the name shown is not the name another system writes
        return "a name that is not UTF-8"
    for part in _posix(raw).split("/"):
        problem = pages.name_problem(part)
        if problem:
            return f"a name that {problem}"
        if SHORT_NAME.match(part):
            return "an 8.3 short name, which Windows resolves to another folder (`CLAUDE~1` is `.claude`)"
    return None


def shape_reason(raw) -> str | None:
    """Why a path waits whatever repository it is in, the template included, or None: a name
    another system reads as another path (`alias_reason`), the provenance spelled in another
    case, a `.git` folder, a compiled module anywhere, or a module at the root that `python -m`
    imports before the installed one. No legitimate change carries one, so holding them costs no
    agent anything. `protected_reason` asks the same, between its other tests."""
    rel = _posix(str(raw))
    why = alias_reason(raw)
    if why:
        return f"{rel}: protected, {why}"
    if rel.casefold() == PROVENANCE.casefold() and rel != PROVENANCE:
        return f"{rel}: protected, the provenance spelled in another case (a checkout that ignores case reads it)"
    parts = rel.casefold().split("/")
    if ".git" in parts:
        return f"{rel}: protected, it holds `.git`, git's own folder"
    if "__pycache__" in parts or parts[-1].endswith(COMPILED):
        return f"{rel}: protected, a compiled module Python may load in place of its source"
    if (len(parts) == 1 and parts[0].endswith(MODULE_SUFFIXES)) \
            or (len(parts) == 2 and parts[1].split(".", 1)[0] == "__init__" and parts[1].endswith(MODULE_SUFFIXES)):
        return f"{rel}: protected, a module at the root shadows an import under `python -m`"
    return None


def protected_reason(raw, prefixes) -> str | None:
    """Why the gate holds this changed path, or None: a name another system reads differently
    (`alias_reason`), the provenance spelled in another case, a protected prefix, a name read at
    any depth, a compiled module anywhere (`COMPILED`, or a `__pycache__` folder), or a module that
    `python -m` imports from the working folder before the installed one (a root module of any
    `MODULE_SUFFIXES`, or a first-level package's `__init__`). One test for the gate and the sync,
    so the two never drift apart."""
    rel = _posix(str(raw))
    why = alias_reason(raw)
    if why:
        return f"{rel}: protected, {why}"
    if rel.casefold() == PROVENANCE.casefold() and rel != PROVENANCE:
        return f"{rel}: protected, the provenance spelled in another case (a checkout that ignores case reads it)"
    for prefix in prefixes:
        if _under(rel, prefix):
            return f"{rel}: protected by `{prefix}`"
    zone = next((z for z in PRIVATE_ZONES if _under(rel, z)), None)
    if zone:
        return f"{rel}: protected, `{zone}` is private: the sync never stages it, so only another writer puts it there"
    parts = rel.casefold().split("/")
    name = next((n for n in ANY_DEPTH if n.casefold() in parts), None)
    if name:
        return f"{rel}: protected by `{name}`, at any depth"
    guarded = pages.guarded_reason(rel)
    if guarded:
        return f"{rel}: protected, it {guarded}"
    if "__pycache__" in parts or parts[-1].endswith(COMPILED):
        return f"{rel}: protected, a compiled module Python may load in place of its source"
    if (len(parts) == 1 and parts[0].endswith(MODULE_SUFFIXES)) \
            or (len(parts) == 2 and parts[1].split(".", 1)[0] == "__init__" and parts[1].endswith(MODULE_SUFFIXES)):
        return f"{rel}: protected, a module at the root shadows an import under `python -m`"
    return None


def _fold(text: str) -> str:
    """A name as a system that ignores case and Unicode normalisation (Windows, macOS) compares it."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def case_twins(root, ref: str, changed) -> dict:
    """Each changed path present at `ref` that a checkout ignoring case reads as another path of
    that tree (`Notes.md` beside `notes.md`, or `Knowledge/x.md` beside `knowledge/`), with the
    other spelling. Such a checkout writes both to one file, the last one written winning, so a
    file the gate lets land can overwrite one it would hold or a page someone signed. Empty when
    git cannot list the tree."""
    done = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", ref], cwd=str(root), capture_output=True)
    if done.returncode != 0:
        return {}
    tree = [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]
    spellings: dict = {}
    for path in tree:
        parts = path.split("/")
        for n in range(1, len(parts) + 1):
            prefix = "/".join(parts[:n])
            spellings.setdefault(_fold(prefix), set()).add(prefix)
    present, twins = set(tree), {}
    for raw in changed:
        rel = _posix(str(raw))
        if rel not in present:
            continue      # deleted here: a case-only rename leaves one spelling
        parts = rel.split("/")
        for n in range(1, len(parts) + 1):
            prefix = "/".join(parts[:n])
            others = spellings.get(_fold(prefix), set()) - {prefix}
            if others:
                twins[rel] = sorted(others)[0]
                break
    return twins


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
    return any(under(p) for p in template_sync.MACHINERY) and not any(under(n) for n in template_sync.NEVER) \
        and template_sync.copyable(rel)


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
            "commit": str(prov.get("commit") or ""),
            "updates": str(data.get("template_updates") or "releases"),
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


RELEASE_REF, TEMPLATE_HEAD = "refs/spaces-gate/release", "refs/spaces-gate/template-head"


def fetch_release(root, repo, tag: str) -> str | None:
    """The commit the template's tag points at now, fetched into this repository with the
    template's default branch (`TEMPLATE_HEAD`, for `on_template_main`); None when the tag cannot
    be read (a private template without TEMPLATE_TOKEN, a missing tag) or the address could be read
    as an option. The whole history comes (no `--depth`): `on_template_main` walks it, and a
    filter needs a configured remote, which this fetch never adds."""
    if not RELEASE_TAG.match(tag):
        return None
    url = template_url(repo)
    if url.startswith("-"):
        return None
    refspecs = [f"+refs/tags/{tag}:{RELEASE_REF}", f"+HEAD:{TEMPLATE_HEAD}"]
    # without a token first: a public template needs none, and an expired one would refuse it
    for token in dict.fromkeys(["", os.environ.get("TEMPLATE_TOKEN", "")]):
        env = dict(os.environ, **_fetch_env(url, token))
        # `--` before the address: a value that starts with `-` is never read as an option
        if _git(root, "fetch", "--quiet", "--no-tags", "--", url, *refspecs, env=env).returncode == 0:
            done = _git(root, "rev-parse", "--verify", "-q", f"{RELEASE_REF}^{{commit}}")
            return done.stdout.strip() or None
    return None


def on_template_main(root, commit: str) -> bool:
    """`commit` is on the template's default branch as `fetch_release` fetched it: a tag on a
    branch nobody merged is no release, whoever pushed it."""
    return _git(root, "merge-base", "--is-ancestor", commit, TEMPLATE_HEAD).returncode == 0


def release_match(root, rules: dict, head: str, paths: list) -> tuple[bool, list[str]]:
    """True when the pull request installs a template release its owner approved and every one of
    `paths` (its protected changes) is that release's file, byte for byte, or AGENTS.md as
    agents_md.py renders it from the pull request's own files (`rendered_at`)."""
    generated = GENERATED in paths
    # the provenance is the space's own record of the release, never a file of it: what it names is checked below
    paths = [rel for rel in paths if rel not in (GENERATED, PROVENANCE)]
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
    if not on_template_main(root, commit):
        return False, [f"release {tag} points at {commit[:8]}, which is not on the template's default branch"]
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
    named = named or ["the provenance records it"]
    return True, [f"release {tag} approved by {approved.get('by')}: " + ", and ".join(named)]


# ----- what a diff carries besides its paths ------------------------------------------------
LINK_MODE, GITLINK_MODE = "120000", "160000"
# The branch a release is pushed on (`release.branch_slug`): the sender's slug and eight hex digits.
RELEASE_BRANCH = re.compile(r"^release/([a-z0-9][a-z0-9-]*)-[0-9a-f]{8}$")
# The Needs-you files a security item may come from (needs_you.SECURITY_WRITERS), all under this folder.
NEEDS_YOU = "_inbound/needs-you/"
CANDIDATES, SIGNALS_IN = "_inbound/candidates/", "_inbound/signals/"
REGISTRY = "_meta/subjects.yaml"
# what the receiving space writes on a release where it waits: landing.py's `_mark`, and the `deadline` the
# person's `later` sets (sign._candidate); landing.STRIP drops every one before a page is filed. The one change
# the receiving space's own agent makes to a release (`marks_only`, which the gate and sync.Run.forged_release apply)
RELEASE_MARKS = frozenset({"landing", "health", "held_at", "for", "disagrees_with", "deadline"})


def marks_only(was: str | None, now: str | None) -> bool:
    """Whether candidate text `now` is the release `was` with only `RELEASE_MARKS` changed: the same
    `released_from`, the same body (`pages.normalize`), and every other frontmatter key as it was."""
    try:
        old, old_body = pages.split(was)
        new, body = pages.split(now)
    except Exception:   # none there, or one that does not read: no release it marks
        return False
    # landing marks a release pending again or deletes it, never un-marks one (page_reasons trusts that)
    if str(old.get("landing") or "").startswith("pending") and not str(new.get("landing") or "").startswith("pending"):
        return False
    return bool(new.get("released_from")) and old.get("released_from") == new.get("released_from") \
        and pages.normalize(old_body) == pages.normalize(body) \
        and all(old.get(k) == new.get(k) for k in set(old) | set(new) if k not in RELEASE_MARKS)


def raw_changes(root, base_ref: str, head: str = "HEAD") -> list[tuple[str, str, str, str]]:
    """(old mode, new mode, status, path) for every path changed between the merge base with
    `base_ref` and `head`, renames split into a delete and an add; empty when git cannot say."""
    for spec in ([f"{base_ref}...{head}"], [base_ref, head]):
        done = subprocess.run(["git", "diff", "--raw", "-z", "--no-renames", "--no-abbrev", *spec],
                              cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if done.returncode == 0:
            break
    else:
        return []
    out, fields = [], done.stdout.split("\0")
    i = 0
    while i + 1 < len(fields):
        meta, path = fields[i], fields[i + 1]
        i += 2
        parts = meta.lstrip(":").split()
        if len(parts) >= 5:
            out.append((parts[0], parts[1], parts[4][:1], _posix(path)))
    return out


def _gitmodules_paths(root, ref: str) -> set:
    """The submodule paths `.gitmodules` names at `ref` (the base branch: a pull request cannot add one)."""
    text = _show(root, ref, ".gitmodules") or ""
    return {m.group(1).strip().strip('"') for m in re.finditer(r"(?m)^\s*path\s*=\s*(.+?)\s*$", text)}


def link_changes(root, base_ref: str, head: str = "HEAD") -> list[str]:
    """A reason for each path the pull request makes a symbolic link (mode 120000), or a
    submodule (160000) at a path the base branch's `.gitmodules` does not name: a link in the tree
    points a later write or read anywhere, so it waits for the owner's click whatever it names."""
    listed = _gitmodules_paths(root, base_ref)
    reasons = []
    for _, new, status, rel in raw_changes(root, base_ref, head):
        if status == "D":
            continue
        if new == LINK_MODE or (new == GITLINK_MODE and rel not in listed):
            reasons.append(f"{rel}: a symbolic link (or unlisted submodule)")
    return reasons


def _frontmatter_at(root, ref: str, rel: str) -> dict:
    """A page's frontmatter at `ref`, or {} when it is missing or does not parse."""
    text = _show(root, ref, rel)
    if text is None:
        return {}
    try:
        fm, _ = pages.split(text)
    except Exception:
        return {}
    return fm if isinstance(fm, dict) else {}


def _subjects_at(root, ref: str | None) -> dict:
    """id -> subject mapping of `_meta/subjects.yaml` at `ref` (the working tree when None)."""
    if ref is None:
        try:
            data = yaml.safe_load((Path(root) / REGISTRY).read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            data = None
    else:
        data = _yaml_at(root, ref, REGISTRY)
    rows = data.get("subjects") if isinstance(data, dict) else None
    return {str(r.get("id")): r for r in rows or [] if isinstance(r, dict) and r.get("id")}


def _trust_fields(row: dict) -> tuple:
    """What in one subject points a trust root or a release somewhere: its home, its
    classification's home, its `match.repo`, a person's workspace and workspace repository."""
    match = row.get("match") if isinstance(row.get("match"), dict) else {}
    cls = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    return (str(row.get("home") or ""), str(cls.get("home") or ""), repr(match.get("repo")),
            str(row.get("workspace") or ""), repr(row.get("workspace_repo")), repr(match.get("github")))


def _match(row: dict) -> dict:
    return row.get("match") if isinstance(row.get("match"), dict) else {}


def _classification(row: dict) -> dict:
    return row.get("classification") if isinstance(row.get("classification"), dict) else {}


def _logins(value) -> set:
    """A `match.github` value (one login or a list) as a set of lower-case logins."""
    return {str(v).strip().casefold() for v in (value if isinstance(value, list) else [value])
            if v not in (None, "") and str(v).strip()}


PERSON_FIELDS = ("workspace", "workspace_repo", "match.github")


def _person_fields(row: dict) -> dict:
    """A person's own trust fields: their workspace, its repository and their GitHub logins."""
    return {"workspace": str(row.get("workspace") or ""), "workspace_repo": str(row.get("workspace_repo") or ""),
            "match.github": frozenset(_logins(_match(row).get("github")))}


def known_homes(root, base_ref: str, base: dict | None = None) -> set:
    """The spaces a home may name without a click, as the base branch knows them: this space (its
    name), each mount its space.yaml lists, and every space the base registry already names as a
    home. A mount is named `brain` while the brain's space has a name of its own (a company
    classification writes that one), so the homes already in use count too: each reached the base
    branch through this gate."""
    here = _yaml_at(root, base_ref, "space.yaml")
    names = {str(here.get("name") or "")}
    names |= {str(m.get("name") or "") for m in here.get("mounts") or [] if isinstance(m, dict)}
    for row in (base if base is not None else _subjects_at(root, base_ref)).values():
        names |= {str(row.get("home") or ""), str(_classification(row).get("home") or "")}
    names.discard("")
    return names


INVITATIONS = "_meta/state/invitations/"


def _repo_owner(value) -> str:
    """The account a GitHub repository reference sits under, lower case, or "" for anything else."""
    import registry
    url = registry.clone_url(value) if isinstance(value, str) else None
    return url.split("/")[3].casefold() if url else ""


def _own_fill(sid: str, old: dict | None, new: dict, author: str | None, base: dict, head_rows: dict,
              owner: str, invitation=None, taken: set | frozenset = frozenset(), host_checked: bool = False) -> bool:
    """The pull request fills, for the first time, a person's own workspace, workspace repository
    or GitHub login, and opened it as that person: its author is the login the base registry
    already gives that person, or, when it gives none, the one login the pull request sets on the
    row, for a person the owner invited with that login (`invitation(sid)`: the base branch's
    invitation file, a protected one, which must name the author as `login`; one that names no
    login is the owner's click, or `onboard.py invite --login` again). The workspace is a free
    name bound to the row: the row's id or the invitation's slug, never a name the base branch
    already gives a space or a subject (`taken`), so a fill cannot take over a project's or the
    brain's release corridor and signers. A workspace repository lands this way only under the
    person's own login (as `landing.owned_by_login` trusts one); one under an organisation is
    classify's, which asks the host first (`classify.verify_workspace_repo`). Never another
    person's row, never a value that was there (changing or removing one is the click's), never
    the space owner's row (their login is the owner's trust root), never a row whose name
    another person row shares, and never a workspace, repository or login another row has."""
    if not author or old is None or old.get("kind") != "people" or new.get("kind") != "people":
        return False
    if str(old.get("state") or "active") == "archived" or str(new.get("state") or "active") == "archived":
        return False
    title = _person(old.get("title"))
    if not title or title != _person(new.get("title")) or title == _person(owner):
        return False
    if any(_person(r.get("title")) == title and r.get("kind") == "people"
           for other, r in [*base.items(), *head_rows.items()] if other != sid):
        return False
    me = author.strip().casefold()
    was, now = _person_fields(old), _person_fields(new)
    for key in PERSON_FIELDS:
        if was[key] and was[key] != now[key]:
            return False      # a value that was there changes or goes: never a fill
    invited = invitation(sid) if invitation else None
    if was["match.github"]:
        if me not in was["match.github"]:
            return False
    elif now["match.github"] != {me}:
        return False
    elif not invited or me not in _logins(invited.get("login")):
        return False   # nobody invited this person with this login: the first login to push never claims a row
    others = [r for other, r in [*base.items(), *head_rows.items()] if other != sid]
    if now["match.github"] and any(now["match.github"] & _logins(_match(r).get("github")) for r in others):
        return False
    if now["workspace"] and not was["workspace"]:
        bound = {sid} | ({str(invited.get("slug"))} if invited and pages.is_slug(invited.get("slug")) else set())
        if now["workspace"] not in bound or now["workspace"] in taken \
                or any(str(r.get("workspace") or "") == now["workspace"] for r in others):
            return False   # a workspace is the person's own slug, never a space or a subject the base names
    if now["workspace_repo"] and not was["workspace_repo"]:
        import registry
        if (not host_checked and _repo_owner(now["workspace_repo"]) not in now["match.github"]) \
                or any(registry.same_repo(now["workspace_repo"], r.get("workspace_repo")) for r in others):
            return False   # under an organisation: classify asks the host first
    return was != now


NOTES_LOG = "_meta/state/registry-notes.jsonl"   # registry.NOTES_LOG


def _noted(root, base_ref: str) -> list[dict]:
    """Each file the base branch's own line of commits (first parents: what main held, never a commit
    only a merged branch held) added or changed under `_inbound/signals/`, as it was there: a note a
    space's release put on main, its `from` bound to the sender's login (`release_reasons`), as
    `onboard.merged_note` trusts one. shortcut: the newest 500, enough for one company's notes."""
    done = _git(root, "log", "--first-parent", "--diff-merges=first-parent", "--format=%x01%H", "--name-only",
                "--diff-filter=AM", "-n", "500", base_ref, "--", SIGNALS_IN)
    out = []
    for block in done.stdout.split("\x01") if done.returncode == 0 else []:
        commit, *names = [line.strip() for line in block.splitlines() if line.strip()] or [""]
        for rel in names:
            if rel.endswith((".yaml", ".yml")):
                try:
                    data = yaml.safe_load(_show(root, commit, rel) or "")
                except yaml.YAMLError:
                    continue
                out += [data] if isinstance(data, dict) else []
    return out


def _noted_fill(root, base_ref: str, head: str, sid: str, old: dict | None, new: dict, author: str | None,
                owners: set, base: dict, head_rows: dict, owner: str, invitation, taken) -> bool:
    """The brain's agent fills, from a login of the owner's, a person's workspace and workspace
    repository as that person's own note asked (classify.apply_note): a `workspace` note the base
    branch held (`_noted`), from the slug the fill names and by that person; a repository only when
    such a note named it and the notes log at the head records it checked with the host (`filled`,
    classify._workspace_repo). Then `_own_fill`'s rules as if that person had pushed it (the slug
    bound to the row, nothing another row has), except that the repository may sit under an
    organisation. A note never sets a login."""
    me = (author or "").strip().casefold()
    if not me or me not in owners - {""} or old is None:
        return False
    logins = _logins(_match(old).get("github"))
    if not logins or _logins(_match(new).get("github")) != logins:
        return False
    ws, repo = str(new.get("workspace") or ""), new.get("workspace_repo")
    notes = [n for n in _noted(root, base_ref) if n.get("kind") == "registry" and n.get("note") == "workspace"
             and str(n.get("from") or "") == ws and _person(n.get("by")) == _person(old.get("title"))]
    if not ws or not notes:
        return False
    checked = False
    if repo and repo != old.get("workspace_repo"):
        import json
        import registry
        named = any(registry.same_repo((n.get("data") if isinstance(n.get("data"), dict) else {}).get("repo"), repo)
                    for n in notes)
        rows = []
        for line in (_show(root, head, NOTES_LOG) or "").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        checked = named and any(isinstance(r, dict) and isinstance(r.get("filled"), dict)
                                and str(r["filled"].get("person") or "") == sid and r["filled"].get("checked")
                                and registry.same_repo(r["filled"].get("workspace_repo"), repo) for r in rows)
        if not checked:
            return False
    return _own_fill(sid, old, new, sorted(logins)[0], base, head_rows, owner, invitation, taken, host_checked=checked)


def registry_reasons(root, base_ref: str, head: str = "HEAD", *, author: str | None = None) -> list[str]:
    """A reason for each subject whose registry entry points trust somewhere new: landing pins a
    signer's repository and people from these fields (`release.trust_repo`,
    `landing.signing_space`) and the release clones from them. Held: any change of `match.repo`; a
    home or classification home that names a space the base branch does not know (`known_homes`)
    or is removed; a person's workspace, workspace repository or login that is set, changed or
    removed, unless it is that person's own first fill (`_own_fill`, with `author` the pull
    request's author) or the brain's agent's fill from that person's note (`_noted_fill`); a person row whose name changes while it carries any of them, or a row
    that becomes or stops being a person while it does; a classification that was decided once
    (`_decision`) whose class, home or crew changes, or whose subject's home moves (a walled
    subject made company is the person's reclassification), the same for the decision that
    governs a subject from its umbrella, a decided subject removed or re-added under a new id
    (`_governed_reasons`); and a space that gains a signer through the registry (`_signer_reasons`). Lands on green: a plain subject, a first home set to
    this space, a mount or a space the registry already names (a first classification, as the
    routines settle one), a re-opening and a re-settling to the same decision, every other field."""
    base, head_rows = _subjects_at(root, base_ref), _subjects_at(root, head)
    homes = known_homes(root, base_ref, base)
    owner = str(_yaml_at(root, base_ref, "space.yaml").get("owner") or "")

    def invitation(sid: str) -> dict | None:
        rel = f"{INVITATIONS}{sid}.yaml"
        return _yaml_at(root, base_ref, rel) if _file(root, base_ref, rel) else None

    reasons = []
    for sid, row in head_rows.items():
        old = base.get(sid)
        was = old or {}
        why = []
        for label, before, after in (
                ("home", str(was.get("home") or ""), str(row.get("home") or "")),
                # as last decided: an ask keeps the decided home in `previous`, which removes nothing
                ("classification home", _decided_home(_classification(was)), _decided_home(_classification(row)))):
            if before != after and (not after or after not in homes):
                why.append(f"its {label} names {after or 'nothing'}, a space the base branch does not know"
                           if after else f"its {label} is removed")
        decided = _decision(_classification(was))
        if decided[0] and (_decision(_classification(row)) != decided
                           or str(was.get("home") or "") != str(row.get("home") or "")):
            why.append(f"its {decided[0]} classification, once decided, changes its class, home or crew")
        if repr(_match(was).get("repo")) != repr(_match(row).get("repo")):
            why.append("its `match.repo` changes")
        fields_now, fields_was = _person_fields(row), _person_fields(was)
        taken = homes | (set(base) - {sid})
        if fields_now != fields_was and not _own_fill(sid, old, row, author, base, head_rows, owner, invitation,
                                                      taken=taken) \
                and not _noted_fill(root, base_ref, head, sid, old, row, author,
                                    _owner_logins(root, base_ref, base, owner), base, head_rows, owner,
                                    invitation, taken):
            why.append("a workspace, a workspace repository or a login is set, changed or removed")
        carries = any(fields_now.values()) or any(fields_was.values())
        if old is not None and carries and (_person(was.get("title")) != _person(row.get("title"))
                                            or (was.get("kind") == "people") != (row.get("kind") == "people")):
            why.append("a person with a workspace or a login changes name or kind")
        if why:
            reasons.append(f"{REGISTRY}: subject `{sid}` changes where trust points: " + "; ".join(why))
    reasons += _governed_reasons(base, head_rows)
    reasons += _signer_reasons(root, base_ref, base, head_rows, owner)
    return reasons


def _match_keys(row: dict) -> set:
    """A subject's `match` identifiers as (key, value) pairs, values in lower case."""
    return {(str(k), str(one).strip().casefold()) for k, v in _match(row).items()
            for one in (v if isinstance(v, list) else [v])
            if isinstance(one, (str, int)) and not isinstance(one, bool) and str(one).strip()}


def _governed_reasons(base: dict, head_rows: dict) -> list[str]:
    """A reason for each subject a settled walled or owner decision governs at the base
    (`registry.Registry.classification`: its own or an umbrella's) that the head governs by
    another class, home or crew, or homes elsewhere: re-parenting a child out from under a walled
    umbrella moves its records as surely as reclassifying the umbrella. Also the removal of a
    subject whose own classification was decided walled or owner, and a new subject that takes the
    match identifiers of a subject so governed under another decision (a drop and re-add under a
    new id). Read as last decided (`_as_decided`): a re-opened or asking classification still
    governs by its decision, so neither re-opening an umbrella nor detaching a child first and
    re-homing it in a later pull request moves it on green. A first settle, a re-opening and a
    re-settling to the same decision land."""
    before, after = _stub_registry(_as_decided(base)), _stub_registry(_as_decided(head_rows))

    def ruling(reg, sid):
        try:
            got = reg.classification(sid)
            return (_decision(got) if got else None), reg.home_of(sid)
        except Exception:
            return None, None

    reasons, governed = [], {}
    for sid, was in base.items():
        own = _decision(_classification(was))[0]
        gov, home = ruling(before, sid)
        # its own decision or its umbrella's: a governed child dropped is re-added under a company umbrella next
        ruled = own if own in ("walled", "owner") else gov[0] if gov else ""
        if sid not in head_rows and ruled in ("walled", "owner"):
            reasons.append(f"{REGISTRY}: subject `{sid}` is removed, governed by a decided {ruled} classification")
        if not gov or gov[0] not in ("walled", "owner"):
            continue
        governed[sid] = (gov, _match_keys(was))
        if sid in head_rows:
            gov2, home2 = ruling(after, sid)
            if home2 != home or gov2 != gov:
                reasons.append(f"{REGISTRY}: subject `{sid}`, governed by a decided {gov[0]} classification, "
                               "changes its governing class, home or crew")
    for sid in sorted(head_rows.keys() - base.keys()):
        keys, gov2 = _match_keys(head_rows[sid]), ruling(after, sid)[0]
        for other, (gov, theirs) in sorted(governed.items()):
            if keys & theirs and gov2 != gov:
                reasons.append(f"{REGISTRY}: subject `{sid}` takes the match identifiers of `{other}` "
                               f"under another decision than its {gov[0]} one")
                break
    return reasons


def _decision(c: dict) -> tuple:
    """The class, home and crew a classification last decided: its own once it has a class, else
    the `previous` an ask or a re-opening keeps; ("", "", ()) when it was never decided."""
    got = c if c.get("class") else (c.get("previous") if isinstance(c.get("previous"), dict) else {})
    crew = got.get("crew")
    crew = crew if isinstance(crew, list) else [crew] if crew else []
    return (str(got.get("class") or ""), str(got.get("home") or ""), tuple(sorted(str(p) for p in crew)))


def _as_decided(rows: dict) -> dict:
    """The rows with each classification as it was last decided (`_decision`), settled; one never
    decided is left out, so it hides no umbrella's decision while it asks."""
    out = {}
    for sid, row in rows.items():
        cls, home, crew = _decision(_classification(row))
        row = {k: v for k, v in row.items() if k != "classification"}
        if cls:
            row["classification"] = {"class": cls, "state": "settled", "home": home, "crew": list(crew)}
        out[sid] = row
    return out


def _decided_home(c: dict) -> str:
    return _decision(c)[1] or str(c.get("home") or "")


def _stub_registry(rows: dict):
    """A `registry.Registry` over these rows alone, read from no disk, with no person's workspace:
    a workspace makes its person a signer through `_own_fill`'s own rule."""
    import registry
    reg = registry.Registry.__new__(registry.Registry)
    reg.root, reg.space, reg.path = Path("."), None, Path(REGISTRY)
    reg.subjects, reg.ancestors, reg.ancestor_ids, reg.ancestor_homes = {}, {}, set(), {}
    reg._brain_names = {}   # what `Registry.__init__` sets beside them; trusted_view reads it
    for sid, row in rows.items():
        try:
            reg.subjects[sid] = registry.Subject.from_dict(
                {k: v for k, v in row.items() if k not in ("workspace", "workspace_repo")})
        except (TypeError, ValueError):
            continue
    return reg


def _signer_reasons(root, base_ref: str, base: dict, head_rows: dict, owner: str) -> list[str]:
    """A reason for each space that gains a signer through the registry: landing counts the owner
    and the crew of every subject homed in a space among the people who sign there
    (`landing.registry_people`), so a subject added, homed, re-parented or given an owner or crew
    can name a new signer for a project space or the brain. Worked out the way landing does, at
    the base and at the head, for every home either names, per subject (its umbrellas' people
    count) and for the union; and a subject homed outside the brain put under an umbrella these
    rows do not hold (a mount's, whose people the gate cannot see). A gain lands only when it is this
    space's owner, or, for the brain (the mount called `brain`, this space when it is the brain, a
    home a company classification names), a registered person the base registry holds who is not
    archived (a domain's owner, a client's lead). Anything else waits: an unregistered name, or
    a colleague who becomes a signer of a project space."""
    import landing
    here = _yaml_at(root, base_ref, "space.yaml")
    name = str(here.get("name") or "")
    brain = {"brain"} | ({name} if str(here.get("profile") or "") == "brain" else set())
    brain |= {str(_classification(r).get("home") or "") for r in base.values()
              if _classification(r).get("class") == "company"}
    registered = {_person(r.get("title")) for r in base.values()
                  if r.get("kind") == "people" and str(r.get("state") or "active") != "archived"}
    before, after = _stub_registry(base), _stub_registry(head_rows)
    spaces = {name}
    for reg in (before, after):
        for subject in reg.subjects.values():
            try:
                spaces.add(reg.home_of(subject.id) or "")
            except Exception:
                pass
            c = subject.extra.get("classification")
            spaces.add(str(c.get("home") or "") if isinstance(c, dict) else "")
    spaces.discard("")
    reasons = []
    # per subject too, as landing signs (its umbrellas' owners and crew count); None is the union
    ids = [None, *sorted(set(before.subjects) | set(after.subjects))]
    for space in sorted(spaces):
        allowed = {_person(owner)} | (registered if space in brain else set())
        gained = set()
        for sid in ids:
            was = {_person(p) for p in landing.registry_people(before, space, here=space == name, subject=sid)}
            now = {_person(p) for p in landing.registry_people(after, space, here=space == name, subject=sid)}
            gained |= {p for p in now - was - allowed if p}
        if gained:
            reasons.append(f"{REGISTRY}: {space} gains a signer through the registry: {', '.join(sorted(gained))}")
    # an umbrella only a mount's registry holds: landing reads its owner and crew, these rows cannot
    if str(here.get("profile") or "") != "brain":
        for sid, row in sorted(head_rows.items()):
            parent = str(row.get("part_of") or "")
            where = {str(row.get("home") or ""), str(_classification(row).get("home") or "")} - {""}
            if parent and parent not in head_rows and parent != str((base.get(sid) or {}).get("part_of") or "") \
                    and where - brain:
                reasons.append(f"{REGISTRY}: subject `{sid}`, homed in {', '.join(sorted(where - brain))}, "
                               f"is put under `{parent}`, a subject this registry does not hold")
    return reasons


def _login_of(rows: dict, name: str) -> set:
    """The GitHub logins the base registry gives the person called `name`."""
    out = set()
    for row in rows.values():
        if row.get("kind") == "people" and _person(row.get("title")) == _person(name):
            out |= _logins(_match(row).get("github"))
    return out


CODEOWNERS = ".github/CODEOWNERS"


def _code_owner_logins(root, ref: str) -> set:
    """The plain `@login`s (never an `@org/team`) the base branch's CODEOWNERS names, lower case:
    onboard writes the owner's login there, under .github/, a protected path. A project space's
    registry holds no people rows, so this is the one place its owner's login is written."""
    out = set()
    for line in (_show(root, ref, CODEOWNERS) or "").splitlines():
        out |= {m.casefold() for m in re.findall(r"(?:^|\s)@([A-Za-z0-9][A-Za-z0-9-]*)(?=\s|$)", line.split("#", 1)[0])}
    out.discard("owner-login")   # the template's placeholder
    return out


def _owner_logins(root, ref: str, rows: dict, owner: str) -> set:
    """The logins that are the space owner's: the base registry's for the owner, and CODEOWNERS'."""
    return _login_of(rows, owner) | _code_owner_logins(root, ref)


def _registered_logins(rows: dict) -> set:
    """Every GitHub login the base registry gives a registered person (not archived)."""
    out = set()
    for row in rows.values():
        if row.get("kind") == "people" and str(row.get("state") or "active") != "archived":
            out |= _logins(_match(row).get("github"))
    return out


# What a registered colleague may change in the brain without its owner: upkeep, the fields no
# tool reads to decide where material goes, who signs or receives it, or what records attach to a
# subject. A list of what lands, not of what waits: a field added later waits until it is named
# here. Everything else (who a subject is and who owns it, its classification, crew and place in
# the tree, every match key, its linked `repos`, its `aliases` and `email`, which map an address
# to a person on a call, its `obligations`, a workspace) is the owner's (`landing.signing_space`,
# `registry.Registry.match`, `sweep`). A subject's `state` is upkeep, except a person's and a
# move to or from `archived` (an archived subject drops out of matching and of its home's signers).
BRAIN_UPKEEP_FIELDS = ("summary", "notes", "work", "period", "important", "stateful", "added", "added_by", "state")
# Of a person's own row, also their own names, while no other row is known by them.
OWN_NAME_FIELDS = ("aliases", "email")


def _known_by(row: dict) -> set:
    """Every name and address a registry row is known by, lower case."""
    import registry
    names = {str(row.get("id") or ""), str(row.get("title") or ""), registry.address_of(row.get("email"))}
    aliases = row.get("aliases")
    names |= {registry.address_of(a) for a in (aliases if isinstance(aliases, list) else [aliases]) if a}
    return {n.strip().casefold() for n in names if n and n.strip()}


def _upkeep(old: dict | None, row: dict) -> set:
    """The fields of a colleague's change to one subject (`old` None: added) that are not upkeep."""
    if old is None:
        extra = set(row) - {"id", "kind", "title", "home", *BRAIN_UPKEEP_FIELDS}
        if "match" in extra and set(_match(row)) <= set(_no_attach_keys()):
            extra.discard("match")   # a key that attaches nothing (`repo_owner`) only steers linking
        return extra
    changed = {k for k in set(old) | set(row) if old.get(k) != row.get(k)}
    held = changed - set(BRAIN_UPKEEP_FIELDS)
    if "state" in changed and (old.get("kind") == "people"
                               or "archived" in (str(old.get("state") or "active"), str(row.get("state") or "active"))):
        held.add("state")
    return held


def _no_attach_keys() -> tuple:
    import registry
    return tuple(getattr(registry, "NO_ATTACH_KEYS", ()))


def _colleague_reasons(base: dict, head_rows: dict, me: str, registered: bool, brain_name: str) -> list[str]:
    """What a registered colleague's edit of the brain registry may not carry without the owner's
    click: a subject removed, a person added, a person's state changed, a subject archived or
    brought back, a new subject homed anywhere but the brain or carrying anything beyond its
    name, kind and upkeep, or any change of an existing subject that is not upkeep
    (`BRAIN_UPKEEP_FIELDS`), except their own first fill of their workspace, workspace repository
    and login on their own row (`_own_fill` decides it in `registry_reasons`) and their own
    aliases and address on the row the base registry gives their login, while no other row is
    known by them. A login the base registry gives no person may land only that fill."""
    out = []
    for sid in base.keys() - head_rows.keys():
        out.append(f"subject `{sid}` removed")
    for sid, row in head_rows.items():
        old = base.get(sid)
        if old is None:
            if row.get("kind") == "people":
                out.append(f"person `{sid}` added")
            elif str(row.get("home") or "") not in ("", brain_name)                     or str(_classification(row).get("home") or "") not in ("", brain_name):
                out.append(f"subject `{sid}` added with a home outside the brain")
            elif not registered:
                out.append(f"subject `{sid}` added")
            elif _upkeep(None, row):
                out.append(f"subject `{sid}` added with {', '.join(sorted(_upkeep(None, row)))}")
            continue
        raw = {k for k in set(old) | set(row) if old.get(k) != row.get(k)}
        changed = _upkeep(old, row) if registered else raw
        if row.get("kind") == "people" and old.get("kind") == "people"                 and me in _logins(_match(row).get("github")):
            # their own first fill: only the fields a fill sets, and of `match` only the login
            fill = {"workspace", "workspace_repo"}
            if _match(old).get("github") != _match(row).get("github")                     and {k: v for k, v in _match(old).items() if k != "github"}                     == {k: v for k, v in _match(row).items() if k != "github"}:
                fill.add("match")
            if me in _logins(_match(old).get("github")):
                # their own names, on the row that was already theirs, while nobody else is known by one
                others = set().union(*(_known_by(r) for other, r in [*base.items(), *head_rows.items()]
                                       if other != sid))
                if not (_known_by(row) - _known_by(old)) & others:
                    fill |= set(OWN_NAME_FIELDS)
            changed -= fill
        if not registered:
            if changed:
                out.append(f"subject `{sid}` changed")
            continue
        if old.get("kind") == "people" and "state" in changed:
            out.append(f"person `{sid}` changes state")
            changed.discard("state")
        if changed:
            out.append(f"subject `{sid}` changes {', '.join(sorted(changed))}")
    return out


# A release branch as senders on an older template push it, `release/<slug>` without the hex.
# REMOVE after the release that follows v0.3.0: every sender's space is on the branch naming by then.
LEGACY_RELEASE_BRANCH = re.compile(r"^release/([a-z0-9][a-z0-9-]*)$")


def release_slug(head_ref: str | None) -> str | None:
    """The sender's slug a release branch names: `release/<slug>-<8 hex>`, or for one release cycle
    the older `release/<slug>` (`LEGACY_RELEASE_BRANCH`); every other condition of a release is
    the same for both."""
    m = RELEASE_BRANCH.match(head_ref or "") or LEGACY_RELEASE_BRANCH.match(head_ref or "")
    return m.group(1) if m else None


def _sender_logins(rows: dict, slug: str, homes: set) -> set:
    """The GitHub logins that vouch for a release from `slug`: those of the person whose registered
    workspace it is. While no row names it as a workspace, those of the active person whose
    registry id it is and who has no workspace, when no space or subject is homed under that name:
    a colleague registered before workspaces were recorded (their own first fill records it). The
    author must still be a login the base registry gives that exact person, so a sender can only
    name their own row. REMOVE the fallback with `LEGACY_RELEASE_BRANCH`."""
    people = {sid: r for sid, r in rows.items() if r.get("kind") == "people"}
    named = [r for r in people.values() if str(r.get("workspace") or "") == slug]
    if not named:
        row = people.get(slug)
        if row is not None and not row.get("workspace") and slug not in homes \
                and str(row.get("state") or "active") != "archived":
            named = [row]
    return {v for row in named for v in _logins(_match(row).get("github"))}


def _space_sender_logins(root, base_ref: str, rows: dict, owner: str, slug: str) -> set:
    """The logins that vouch for a release from `slug` when no registered person's workspace is
    `slug`. In the brain, from a project space it knows as a home: the brain owner's
    (`_owner_logins`), and those the base registry gives the people of the subjects homed there
    (`landing.registry_people`: the project's owner and crew). In a project space, whose registry
    holds no people rows: those of the audience member whose name is `slug`
    (`release.slug_of`, as a workspace is named): their `_space_logins`, and for the owner also
    CODEOWNERS'. Any other login still waits for the click."""
    import landing
    import release
    here = _yaml_at(root, base_ref, "space.yaml")
    profile = str(here.get("profile") or "")
    if profile == "brain" and slug in known_homes(root, base_ref, rows) and slug != str(here.get("name") or ""):
        try:
            people = landing.registry_people(_stub_registry(rows), slug)
        except Exception:   # an unreadable registry gives no one: the release waits, as before
            people = set()
        return _owner_logins(root, base_ref, rows, owner) | {v for name in people for v in _login_of(rows, name)}
    if profile == "project" and isinstance(here.get("audience"), list):
        out = set()
        for name in here["audience"]:
            if release.slug_of(name) == slug:
                out |= _space_logins(here, name)
                if _person(name) == _person(owner):
                    out |= _code_owner_logins(root, base_ref)
        return out
    return set()


def release_reasons(root, base_ref: str, head: str, changes: list, owner: str,
                    author: str | None, head_ref: str | None) -> list[str]:
    """A reason when the pull request adds or changes a release (`_inbound/candidates/` with
    `released_from`, or `_inbound/signals/`) that its branch and author do not vouch for: it must
    come on `release/<slug>-<8 hex>` (or, for one release cycle, `release/<slug>`), touch nothing
    outside `_inbound/`, name that slug as its sender in every file, and be opened by a GitHub
    login the base registry gives the person whose workspace that slug is. Without that,
    `released_from` and a signal's `from` are only what the sender wrote. In the brain, a direct
    registry edit lands from its owner's login, and from any other login the base registry gives a
    registered person when it is upkeep (`_colleague_reasons`); a login it gives no one may land
    only their own first fill."""
    rows = _subjects_at(root, base_ref)
    reasons = []
    released = []
    mine = None
    for _, _, status, rel in changes:
        if status == "D":
            continue
        folded = rel.casefold()
        if folded.startswith(SIGNALS_IN):
            data = _yaml_at(root, head, rel)
            released.append((rel, str(data.get("from") or "")))
        elif folded.startswith(CANDIDATES):
            fm = _frontmatter_at(root, head, rel)
            if fm.get("released_from") and author:
                if mine is None:
                    mine = author.strip().casefold() in _owner_logins(root, base_ref, rows, owner)
                # the receiving space's own agent marking a release that waits (landing.py `_mark`), on its sync branch
                if mine and marks_only(_show(root, base_ref, rel), _show(root, head, rel)):
                    continue
            if fm.get("released_from"):
                released.append((rel, str(fm.get("released_from"))))
    if released:
        slug = release_slug(head_ref)
        logins = _sender_logins(rows, slug, known_homes(root, base_ref, rows)) if slug else set()
        if slug and not logins:
            logins = _space_sender_logins(root, base_ref, rows, owner, slug)
        if slug is None:
            reasons.append("a release that does not come on its sender's branch (release/<slug>-<8 hex>)")
        elif any(not rel.casefold().startswith("_inbound/") for _, _, _, rel in changes):
            reasons.append(f"release/{slug}: it changes files outside _inbound/")
        elif any(sender != slug for _, sender in released):
            reasons.append(f"release/{slug}: a file names another sender")
        elif not author or author.casefold() not in logins:
            reasons.append(f"release/{slug}: opened by a login the registry does not give {slug}'s person")
    touched = {rel for _, _, status, rel in changes}
    if REGISTRY in touched and rows and any(r.get("kind") for r in rows.values()):
        here = _yaml_at(root, base_ref, "space.yaml")
        if str(here.get("profile") or "") == "brain":
            me = (author or "").strip().casefold()
            if not me:
                reasons.append(f"{REGISTRY}: edited in the brain by a login nobody named")
            elif me not in _owner_logins(root, base_ref, rows, owner):
                why = _colleague_reasons(rows, _subjects_at(root, head), me, me in _registered_logins(rows),
                                         str(here.get("name") or ""))
                if why:
                    reasons.append(f"{REGISTRY}: edited in the brain by {me}, not its owner: " + "; ".join(why))
    return reasons


def _signed_here(fm: dict, here: dict, more=()) -> bool:
    """Signed in this space (`signed_in` its base name) by its owner, one of its audience, or one of
    `more`: the people the base registry gives the page's own subject here (`_subject_signers`)."""
    people = {_person(here.get("owner"))} | {_person(p) for p in more}
    if isinstance(here.get("audience"), list):
        people |= {_person(a) for a in here["audience"]}
    return bool(here.get("name")) and str(fm.get("signed_in") or "") == str(here.get("name")) \
        and _person(fm.get("signed_by")) in people - {""}


SIGNATURE = ("id", "status", "signed_by", "signed_in", "signed_at", "fingerprint", "signed_fields")
# The keys of a signed page beyond its signature that readers act on: which decision is in force, what kind
# of page it is and about which subject, whether it is withdrawn, and which space and person released it
# (whose retraction withdraws it: `landing._apply_retraction`). Only the owner's login changes them under a
# signature it did not push (`page_reasons`); `sign.fields_fingerprint` covers only some of them.
READ_KEYS = ("supersedes", "type", "subject", "title", "kind", "high_stakes", "retracted", "retracted_at",
             "retracted_by", "landed_from", "released_from", "released_by")


def _subject_signers(root, base_ref: str, here: dict, subject) -> set:
    """The people the base branch's registry gives a page about `subject` in this space, as landing
    and release count them (`landing.registry_people` per subject: the subject's own owner and
    crew, its umbrellas', its walled classification's): a domain owner who signs in the brain, a
    crew member in a project space. The crew of one subject never signs another's page, and a
    page about no subject adds no one."""
    import landing
    try:
        reg = _stub_registry(_subjects_at(root, base_ref))
        return landing.registry_people(reg, str(here.get("name") or ""), here=True, subject=str(subject or ""))
    except Exception:   # an unreadable registry gives no one: the page waits, as before
        return set()


def appended_only(old: str, new: str) -> bool:
    """No line of `old` is removed or changed in `new`: its lines are an ordered subsequence of
    `new`'s, line endings aside. Not a prefix test: log.md merges by union (.gitattributes), and a
    union merge writes this side's new lines before the other side's, so a branch that took the
    default branch's later lines still holds every one of them, in their order."""
    want = old.replace("\r\n", "\n").split("\n")
    open_end = want[-1] != ""          # no newline after the last line: an append may continue it
    if not open_end:
        want.pop()
    have = iter(new.replace("\r\n", "\n").split("\n"))
    for n, line in enumerate(want):
        last = open_end and n == len(want) - 1
        if not any((got.startswith(line) if last else got == line) for got in have):
            return False
    return True


def _space_logins(here: dict, name) -> set:
    """The GitHub logins the base space.yaml gives `name`, a person of its listed audience:
    `logins: {<name>: <login or list>}`, which mint and a crew change write beside the audience.
    space.yaml is protected, so a login lands there only with the owner's click, as in CODEOWNERS."""
    audience, table = here.get("audience"), here.get("logins")
    if not isinstance(audience, list) or not isinstance(table, dict) \
            or _person(name) not in {_person(a) for a in audience} - {""}:
        return set()
    return {v for k, val in table.items() if _person(k) == _person(name) for v in _logins(val)}


def _pushed_by_signer(fm: dict, here: dict, rows: dict, author: str, owners=frozenset()) -> bool:
    """The pull request's author is a login the base registry gives the page's `signed_by`
    person, or, for the space owner's signature, one of `owners` (`_code_owner_logins`): a
    signature is a hash anyone computes, so only the signer's own push vouches for it. In a
    personal space, whose only writer is its owner, the owner's signature also lands from any
    login while the base registry gives the owner none. A project space's registry holds no people
    rows: its audience's logins are those its base space.yaml lists (`_space_logins`)."""
    logins = _login_of(rows, str(fm.get("signed_by") or "")) | _space_logins(here, fm.get("signed_by"))
    is_owner = _person(fm.get("signed_by")) == _person(here.get("owner"))
    if (author or "").strip().casefold() in logins | (set(owners) if is_owner else set()):
        return True
    return not logins and str(here.get("profile") or "") == "personal" and is_owner


# the public halves of the keys a computer's sync signs its commits with (`sync.py --signing-key`); protected
ALLOWED_SIGNERS = ".github/allowed_signers"
# the log.md lines landing reads as a signature (`landing._has_line`): `- <ISO> fingerprint|fields <id> ...`
SIGNATURE_LINE = re.compile(r"- \S+ (?:fingerprint|fields) ")


def _key_signed(root, base_ref: str, head: str, rel: str) -> bool:
    """Whether every commit between `base_ref` and `head` that changed `rel` verifies (`git verify-commit`)
    against a key the base branch's ALLOWED_SIGNERS lists. A cloud run pushes with its person's token, as its
    person, to any branch: the login vouches for no signature it pushes. The key stays on the person's
    computer, so only the computer's own commits verify. The tie is opt-in: True while the base branch has no
    such file (the cloud-identity limit SECURITY.md names then applies), and the file is protected, so taking
    it away again waits for the owner's click."""
    keys = _show(root, base_ref, ALLOWED_SIGNERS)
    if keys is None or not keys.strip():
        return True
    # CI judges GitHub's merge of the pull request into its base (`checks.yml` passes the pull request's head as
    # PR_HEAD_SHA). That merge is never signed, and what it holds beyond the base comes from the head, whose
    # commits are checked; a merge inside the pull request is one of those commits, so it is checked too
    pr = os.environ.get("PR_HEAD_SHA", "").strip()
    if pr and _git(root, "rev-parse", f"{head}^2").stdout.strip() == pr \
            and _git(root, "merge-base", "--is-ancestor", f"{head}^1", base_ref).returncode == 0:
        head = pr
    log = subprocess.run(["git", "log", "--format=%H", f"{base_ref}..{head}", "--", rel], cwd=str(root),
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    shas = log.stdout.split()
    if log.returncode or not shas:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        allowed = Path(tmp) / "allowed_signers"
        # only `<principal> ssh-ed25519 <key>` lines: ssh-keygen ignores what follows the key, so a key smuggled in
        # after another key's look-alike end never verifies (the click row compares the key's end); and only
        # ed25519, the one type the sync makes, whose end no one can choose (an ssh-rsa modulus's low bytes can be)
        allowed.write_text("".join(f"{line}\n" for line in keys.splitlines()
                                   if len(f := line.split()) == 3 and f[1] == "ssh-ed25519"), encoding="utf-8")
        return all(subprocess.run(["git", "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={allowed.as_posix()}",
                                   "verify-commit", sha], cwd=str(root), capture_output=True).returncode == 0
                   for sha in shas)


def _own_subject_signers(root, base_ref: str, here: dict, rel: str, fm: dict) -> set:
    """The people of a page's own subject who may sign it here (`_subject_signers`), only for a
    page filed under knowledge/<kind>/<subject>/ that names that subject, never a decision (under
    knowledge/decisions/, or `type: decision` or a `supersedes` anywhere: decisions.scan reads such a
    page as a decision and applies what it supersedes); when the base branch holds the page, at this
    path or under its id at another (a move), under another subject, only those who sign that subject
    too, and no one when it was a decision there. So a subject's people never re-sign another
    subject's page, or a decision, by re-pointing `subject` or by moving it into their own folder."""
    parts, subject = _posix(rel).split("/"), str(fm.get("subject") or "")
    if len(parts) < 4 or parts[1].casefold() == "decisions" or parts[2] != subject \
            or str(fm.get("type") or "") == "decision" or fm.get("supersedes"):
        return set()
    people = _subject_signers(root, base_ref, here, subject)
    for path in sorted({rel, *_paths_of_id(root, base_ref, fm.get("id"))}):
        was = _frontmatter_at(root, base_ref, path)
        if not was:
            continue
        if path.split("/")[1].casefold() == "decisions" or str(was.get("type") or "") == "decision":
            return set()
        if str(was.get("subject") or "") != subject:
            people &= _subject_signers(root, base_ref, here, was.get("subject"))
    return people


def _paths_of_id(root, base_ref: str, page_id) -> set:
    """The pages under knowledge/ (not sources/) that carry `page_id` in their frontmatter at `base_ref`."""
    if not isinstance(page_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", page_id):
        return set()   # any id a page carries, in a shape no regex reads as more than itself
    done = _git(root, "grep", "-l", "-E", "-e", rf"^id: *['\"]?{page_id}['\"]? *$", base_ref, "--", "knowledge/")
    found = {line.split(":", 1)[1] for line in done.stdout.splitlines() if ":" in line} if done.returncode == 0 else set()
    return {p for p in found if not p.casefold().startswith("knowledge/sources/")
            and str(_frontmatter_at(root, base_ref, p).get("id") or "") == page_id}


# The ledgers the tools act on as a person's answer or as proof that request.py asked or answered
# (status.verdicts, release.ship_requests): outside a personal space only the owner's own push lands
# a change, and a verdict row also from the login of the person it names (`ledger_reason`).
VERDICTS = "_meta/state/verdicts.jsonl"
OWNER_LEDGERS = (VERDICTS, "_meta/state/requests-asked.jsonl", "_meta/state/requests.jsonl",
                 "_meta/state/queued.jsonl")


def ledger_reason(root, base_ref: str, head: str, status: str, rel: str, rows: dict, author: str,
                  owners: set) -> str | None:
    """Why a change to one of `OWNER_LEDGERS` waits, or None: it lands from a login of the space
    owner's (`owners`); the verdict store also when it is appended to only and every new row names,
    as `by`, a person the base registry gives the author's login (a row naming no one, the
    cut-over's, is the owner's), or one the base space.yaml's `logins` give it (`_space_logins`)."""
    me = (author or "").strip().casefold()
    if me and me in owners:
        return None
    if rel == VERDICTS and status != "D":
        import collections
        import json
        old, new = _show(root, base_ref, rel) or "", _show(root, head, rel) or ""

        def by(line):
            try:
                row = json.loads(line)
            except ValueError:
                return ""
            return str(row.get("by") or "") if isinstance(row, dict) else ""
        # counted, not a set: a copy of an earlier row appended again is a new answer (a later one wins)
        added = [line for line in (collections.Counter(new.splitlines()) - collections.Counter(old.splitlines())).elements()
                 if line.strip()]
        here = _yaml_at(root, base_ref, "space.yaml")
        if me and appended_only(old, new) and all(by(line) and me in _login_of(rows, by(line)) | _space_logins(here, by(line))
                                                  for line in added):
            return None
    return f"{rel}: a ledger the tools act on as an answer, changed by {me or 'a login nobody named'}, " \
           "not by the person a row names or the owner"


def _files_unasked(root, base_ref: str, rel: str, was: dict) -> bool:
    """Whether the release `was` (base's candidate) is one landing files with no question, judged without
    its lookup of the signer's history (`landing.signer_problem`), which no gate run can make: its
    sender's own signature (`signed_in` is `released_from`: no relay) by a person the base registry
    gives that sender (`landing.registry_people`), not high stakes, from no space the base trust.yaml
    pauses, and over no page here under its id that another person or space signed. A release lands
    unmarked in main hours before landing runs, and a signature is a hash anyone computes: anything
    else waits for the click. Filed at `rel`, where landing files it (`sign.target_of`), never as a
    decision (under knowledge/decisions/, `type: decision` or a `supersedes`: decisions.scan applies what it
    supersedes, and no subject's people sign a decision, `_own_subject_signers`)."""
    import datetime as dt
    import landing
    import sign   # lazily, as fields_problem's caller does
    sender = str(was.get("released_from") or "")
    if not sender or str(was.get("signed_in") or "") != sender or landing.stakes(was):
        return False
    if was.get("supersedes") or str(was.get("type") or "") == "decision":
        return False
    try:
        target = sign.target_of(was, Path(rel), dt.datetime.now(dt.timezone.utc))
    except pages.UnsafePath:
        return False
    if _posix(rel) != target or target.split("/")[1].casefold() == "decisions":
        return False
    paused = _yaml_at(root, base_ref, str(landing.TRUST.as_posix())).get("pending") or []
    if not isinstance(paused, list) or sender in {str(x) for x in paused}:
        return False
    rows = _subjects_at(root, base_ref)
    # the person whose workspace it is (the stub registry carries no workspace), then the subject's people
    people = {str(r.get("title") or "") for r in rows.values() if r.get("kind") == "people"
              and str(r.get("workspace") or "") == sender and str(r.get("state") or "active") != "archived"}
    try:
        people |= landing.registry_people(_stub_registry(rows), sender, subject=str(was.get("subject") or ""))
    except Exception:   # an unreadable registry gives no one more: the release waits
        return False
    if _person(was.get("signed_by")) not in {_person(p) for p in people} - {""}:
        return False
    for path in {rel, *_paths_of_id(root, base_ref, was.get("id"))}:
        old = _frontmatter_at(root, base_ref, path)
        if old and any(str(old.get(k) or "") != str(was.get(k) or "") for k in ("signed_by", "signed_in")):
            return False
    return True


def page_reasons(root, base_ref: str, head: str, changes: list, owner: str,
                 author: str | None = None) -> list[str]:
    """A reason for each changed page under knowledge/ (not knowledge/sources/) that is stable
    at `head` and was not signed here (`_signed_here`, the people of the page's subject counted:
    `_subject_signers`) by the pull request's `author` (`_pushed_by_signer`; None, the sync's own
    preflight, skips that tie, as the gate in CI always names the author), unless its signature is
    the base branch's unchanged or the pull request
    files the release that carried it: it deletes `_inbound/candidates/<id>.md`, which the base
    branch holds with the same signature and no
    `landing: pending` mark, and which landing files unasked (`_files_unasked`). A page another
    space signed reaches knowledge/ only through
    landing, which checks it against the signer's own history. A subject's people count only on
    a page filed under that subject (`_own_subject_signers`). Also a log.md that is not
    appended to (a line changed or removed), a released review batch, a new or rewritten security
    item on Needs you (`security_items`), a verdict row in a commit no computer's key signed
    (`_key_signed`), and outside a personal space a change to a ledger the
    tools act on as an answer that its person's or the owner's login did not push (`ledger_reason`)."""
    here = _yaml_at(root, base_ref, "space.yaml")
    rows = _subjects_at(root, base_ref) if author is not None else {}
    owners = _owner_logins(root, base_ref, rows, owner) if author is not None else set()
    personal = str(here.get("profile") or "") == "personal"
    deleted = {rel.casefold() for _, _, status, rel in changes if status == "D"}
    reasons = []
    for _, _, status, rel in changes:
        folded = rel.casefold()
        # a row is a person's answer and only sign.py on the computer writes one (record_verdict refuses in a
        # routine); a cloud run pushes as its person, so in every profile only the computer's key vouches
        # a deletion too: with no store, the computer's next status run pins every log.md line (status.pin_cutover)
        if author is not None and folded == VERDICTS and not _key_signed(root, base_ref, head, rel):
            reasons.append(f"{rel}: a person's answers in a commit no computer's key signed (.github/allowed_signers)")
        if author is not None and not personal and folded in OWNER_LEDGERS:
            why = ledger_reason(root, base_ref, head, status, rel, rows, author, owners)
            reasons += [why] if why else []
            continue
        if status == "D" and folded != "log.md":   # deleting log.md is judged below: re-adding it would reset the history
            continue
        if folded.startswith("knowledge/") and not folded.startswith("knowledge/sources/") \
                and folded.endswith(".md"):
            fm = _frontmatter_at(root, head, rel)
            stable = str(fm.get("status") or "") == "stable"
            signed = stable and (_signed_here(fm, here)
                                 or _signed_here(fm, here, _own_subject_signers(root, base_ref, here, rel, fm)))
            unkeyed = False   # the signer's own login pushed it, but no commit the computer's key signed carries it
            if signed and author is not None:
                signed = _pushed_by_signer(fm, here, rows, author, owners)
                unkeyed = signed and not _key_signed(root, base_ref, head, rel)
                signed = signed and not unkeyed
            if stable and not signed:
                old = _frontmatter_at(root, base_ref, rel)
                # past the owner's own logins in a commit the computer's key signed, what readers act on
                # stays the base copy's too (decisions.scan, check): the login alone vouches for nothing
                owner_keyed = author is None or (author.strip().casefold() in owners
                                                 and _key_signed(root, base_ref, head, rel))
                keys = SIGNATURE if owner_keyed else SIGNATURE + READ_KEYS
                same = all(str(old.get(k) or "") == str(fm.get(k) or "") for k in keys)
                cand = f"{CANDIDATES}{fm.get('id')}.md"
                if not same and pages.is_page_id(fm.get("id")) and cand.casefold() in deleted:
                    was = _frontmatter_at(root, base_ref, cand)
                    # filed as landing files it: the candidate's keys, `landed_from` its sender (landing.STRIP aside)
                    same = all(str(was.get(k) or "") == str(fm.get(k) or "")
                               for k in SIGNATURE + READ_KEYS if k != "landed_from") \
                        and str(fm.get("landed_from") or "") == str(was.get("released_from") or "") \
                        and not str(was.get("landing") or "").startswith("pending") \
                        and _files_unasked(root, base_ref, rel, was)
                if not same and unkeyed:
                    reasons.append(f"{rel}: stable page signed by {_person(fm.get('signed_by')) or 'nobody'}, "
                                   f"but no commit the computer's key signed ({ALLOWED_SIGNERS}): it waits for the click")
                elif not same:
                    reasons.append(f"{rel}: stable page signed by {_person(fm.get('signed_by')) or 'nobody'}, "
                                   "not in this space by its people: lands through _inbound/candidates")
        elif folded == "log.md":
            old, new = _show(root, base_ref, rel) or "", _show(root, head, rel) or ""
            if not appended_only(old, new):
                reasons.append("log.md: a line changed or removed; the history is appended to only")
            # an open last line is continued by the next append, which changes a pinned answer's fingerprint
            if old.endswith("\n") and not new.endswith("\n"):
                reasons.append("log.md: its last line loses its line break; the history is appended to only")
            # a copy of a base line is never new history (a union merge repeats none, every tool line is stamped),
            # and inserted above the original it moves a pinned answer's first place (status.verdicts)
            import collections
            was = collections.Counter(line.strip() for line in old.splitlines())
            now = collections.Counter(line.strip() for line in new.splitlines())
            if any(line and now[line] > n for line, n in was.items()):
                reasons.append("log.md: repeats an earlier line; the history is appended to only")
            # a fingerprint or fields line is a signature other spaces' landing trusts (landing.signer_problem):
            # only sign.py on the computer writes one, so it is tied to the computer's key as a verdict row is
            added = set(new.splitlines()) - set(old.splitlines())
            if author is not None and any(SIGNATURE_LINE.match(line.strip()) for line in added) \
                    and not _key_signed(root, base_ref, head, rel):
                reasons.append(f"log.md: a signature line in a commit no computer's key signed ({ALLOWED_SIGNERS})")
        elif folded.startswith(CANDIDATES) and folded.endswith(".md"):
            fm = _frontmatter_at(root, head, rel)
            if fm.get("released_from") and (fm.get("review_batch") or fm.get("lines")):
                reasons.append(f"{rel}: a review batch is never released")
        elif folded.startswith(NEEDS_YOU) and folded.endswith((".yaml", ".yml")):
            if security_items(root, head, rel) - security_items(root, base_ref, rel):
                reasons.append(f"{rel}: adds a security item; only the sync and the morning view write one")
    return reasons


SIGNALS_OUT = "_outbound/signals/"


def _withdrawn(fm: dict) -> bool:
    """Withdrawn by any key a reader reads: `pages.withdrawn` (`retracted`, `retracted_at`) or `retracted_by`."""
    return pages.withdrawn(fm) or bool(fm.get("retracted_by"))


def withdrawal_reasons(root, base_ref: str, head: str, changes: list, owner: str, author: str,
                       head_ref: str | None) -> list[str]:
    """A reason for each signed page the pull request withdraws, and each new or changed
    `_outbound/signals/` file of kind `sample_failed` whose `item` the base branch does not hold
    withdrawn already (not stable and `pages.withdrawn`, as `landing.retract` leaves it), whoever
    pushed it and on whatever branch. Withdrawn: a stable page under knowledge/ (not sources/)
    deleted, made not stable, or newly withdrawn (`_withdrawn`). A cloud shell runs any program and
    pushes as its person, to any branch name, so neither the author nor the branch tells the gate
    a withdrawal is the person's word (`landing.retract` refuses in a routine): it waits for the
    owner's click, and the sync puts it on its protected branch. Not a withdrawal: a move (the
    base page's id stable and not withdrawn at another path at `head`, with the base copy's
    signature or that of the candidate it files, which `page_reasons` judges there), and the
    sender's retraction the pull request files (an `_inbound/candidates/` file the base holds with
    `release_class: retraction`, `retracts` that page, `released_from` the space the page was
    signed in and `released_by` the person who released it, which landing deletes when it applies
    it; a page relayed from a space other than its signer's waits for the click). Kept apart from `page_reasons`, which the sync bisects with every deletion attached."""
    retracted, cands = {}, {}
    for _, _, status, rel in changes:
        if status == "D" and rel.casefold().startswith(CANDIDATES):
            was = _frontmatter_at(root, base_ref, rel)
            cands[str(was.get("id") or "")] = was
            if pages.is_page_id(was.get("retracts")) and was.get("released_from") \
                    and str(was.get("release_class") or "") == "retraction":   # as landing applies one
                retracted[str(was["retracts"])] = was

    def sig(fm):
        return tuple(str(fm.get(k) or "") for k in SIGNATURE)

    def moved(rel, was):
        pid = str(was.get("id") or "")
        for path in _paths_of_id(root, head, pid) - {rel}:
            now = _frontmatter_at(root, head, path)
            if str(now.get("status") or "") == "stable" and not _withdrawn(now) \
                    and sig(now) in [sig(was)] + ([sig(cands[pid])] if pid in cands else []):
                return True
        return False

    reasons = []
    for _, _, status, rel in changes:
        folded = rel.casefold()
        if folded.startswith(SIGNALS_OUT) and folded.endswith((".yaml", ".yml")) and status != "D":
            data = _yaml_at(root, head, rel)
            if str(data.get("kind") or "") == "sample_failed" and not any(
                    str(fm.get("status") or "") != "stable" and pages.withdrawn(fm)
                    for fm in (_frontmatter_at(root, base_ref, p) for p in _paths_of_id(root, base_ref, data.get("item")))):
                reasons.append(f"{rel}: a sample_failed signal for a page the base branch does not hold "
                               "withdrawn: it pauses the releaser's path, the person's word")
            continue
        if not (folded.startswith("knowledge/") and not folded.startswith("knowledge/sources/")
                and folded.endswith(".md")):
            continue
        was = _frontmatter_at(root, base_ref, rel)
        if str(was.get("status") or "") != "stable" or _withdrawn(was):
            continue
        now = {} if status == "D" else _frontmatter_at(root, head, rel)
        if status != "D" and str(now.get("status") or "") == "stable" and not _withdrawn(now):
            continue
        if status == "D" and moved(rel, was):
            continue
        # only a page signed in the retracting space itself (`signed_in`, which the signature binds; never
        # `landed_from`, which a push can write), and as the person who released it, as landing checks
        rx = retracted.get(str(was.get("id") or ""))
        if status != "D" and rx and str(was.get("signed_in") or "") == str(rx["released_from"]) \
                and str(was.get("released_by") or rx.get("released_by") or "") == str(rx.get("released_by") or ""):
            continue
        reasons.append(f"{rel}: a stable page signed by {_person(was.get('signed_by')) or 'nobody'} is "
                       f"{'deleted' if status == 'D' else 'withdrawn'} by {(author or '').strip() or 'a login nobody named'}; "
                       "a withdrawal is the person's word, given by the owner's click")
    return reasons


def security_items(root, ref: str, rel: str) -> set:
    """The security items a Needs-you file holds at `ref`, each whole (its id, text, recommendation,
    default and the rest) as one canonical string: an item that keeps an id but says something else is
    a new item, never the one the base branch held."""
    import json
    try:
        data = yaml.safe_load(_show(root, ref, rel) or "")
    except yaml.YAMLError:
        data = None
    # read as needs_you.routine_items reads it: an `items` list, or the file itself a list
    items = data.get("items") if isinstance(data, dict) else data
    return {json.dumps(i, sort_keys=True, default=str) for i in (items if isinstance(items, list) else [])
            if isinstance(i, dict) and i.get("kind") == "security"}


def _template_verdict(root, changed_files, base_ref: str | None, head: str, twins: dict) -> tuple[str, list[str]]:
    """The template repository's verdict. Its pull requests land on green when the checks pass,
    its machinery included, as before this gate grew: agents build and fix the template on their
    own. The one human gate on what reaches the spaces is the release: only a maintainer creates a
    `v*` tag (a tag ruleset, SECURITY.md), a tag counts only on the default branch (release.yml,
    `on_template_main`), and a space takes a release only on its owner's yes and only byte for
    byte (`release_match`). A space that follows main instead is a dev-only mode. What no
    legitimate change carries still waits for a maintainer's merge: a link or an unlisted
    submodule, a name another system reads as another path, a compiled or root module, a path
    that is another where case is ignored (`shape_reason`, `case_twins`)."""
    odd = []
    for raw in changed_files:
        rel = _posix(str(raw))
        why = shape_reason(raw)
        if not why and rel in twins:
            why = f"{rel}: protected, the same path as `{twins[rel]}` where case is ignored (Windows, macOS)"
        if why:
            odd.append(why)
    held = link_changes(root, base_ref, head) if base_ref else []
    if odd or held:
        return PROTECTED, ["template repository: a link or a name no change needs waits for a maintainer's merge"] \
            + odd + held
    return AUTO, ["template repository: lands on green; the release tag is the human gate"]


def fetch_template_main(root, repo) -> bool:
    """The template's default branch fetched as `TEMPLATE_HEAD` (for `follows_main`); False when it
    cannot be read or the address could be read as an option. Same token rules as `fetch_release`."""
    url = template_url(repo)
    if url.startswith("-"):
        return False
    for token in dict.fromkeys(["", os.environ.get("TEMPLATE_TOKEN", "")]):
        env = dict(os.environ, **_fetch_env(url, token))
        if _git(root, "fetch", "--quiet", "--no-tags", "--", url, f"+HEAD:{TEMPLATE_HEAD}", env=env).returncode == 0:
            return True
    return False


FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def follows_main(root, rules: dict, head_prov: dict, changed) -> str | None:
    """Why a provenance change lands in a space that follows the template's main
    (`template_updates: main` on the base branch, a dev-only mode), or None: it keeps the release
    it records, moves the commit forwards along the template's default branch (the old commit is
    an ancestor of the new one, and the new one is on that branch), and the pull request changes
    no file template sync installs. A provenance-only sync then lands each week with no click, and
    no pull request can move the space back or onto a branch nobody merged."""
    old, new = rules.get("commit") or "", str(head_prov.get("commit") or "")
    if rules.get("updates") != "main" or not rules.get("repo") \
            or str(head_prov.get("release") or "") != rules.get("release", ""):
        return None
    if not FULL_COMMIT.match(old) or not FULL_COMMIT.match(new):
        return None
    if any(_machinery(_posix(str(rel))) for rel in changed):
        return None
    if not fetch_template_main(root, rules["repo"]):
        return None
    if _git(root, "merge-base", "--is-ancestor", old, new).returncode != 0 \
            or _git(root, "merge-base", "--is-ancestor", new, TEMPLATE_HEAD).returncode != 0:
        return None
    return f"{PROVENANCE}: follows the template's main from {old[:8]} to {new[:8]}, with no machinery changed"


def classify(root, changed_files, base_ref: str | None = None, head: str = "HEAD",
             author: str | None = None, head_ref: str | None = None) -> tuple[str, list[str]]:
    root = Path(root)
    rules = rules_at(root, base_ref) if base_ref else None
    template = rules["template"] if rules else is_template(root)
    twins = case_twins(root, head, changed_files) if rules else {}
    if template:
        return _template_verdict(root, changed_files, base_ref, head, twins)
    prefixes, note = (rules["protected"], None) if rules else protected_prefixes(root)
    reasons = [note] if note else []
    guarded = []
    for raw in changed_files:
        why = protected_reason(raw, prefixes)
        if not why and _posix(str(raw)) in twins:
            why = (f"{_posix(str(raw))}: protected, the same path as `{twins[_posix(str(raw))]}` "
                   "where case is ignored (Windows, macOS)")
        if why:
            reasons.append(why)
            guarded.append(_posix(str(raw)))
    # what the base branch can see beyond the paths: a link or a submodule is held in every
    # repository, the template included, and no release match lets one through
    held = link_changes(root, base_ref, head) if base_ref else []
    touched = {_posix(str(p)).casefold() for p in changed_files}
    head_prov = _yaml_at(root, head, PROVENANCE) if rules and PROVENANCE.casefold() in touched else {}
    renamed = bool(rules) and PROVENANCE.casefold() in touched and head_prov.get("repo") != rules["repo"]
    if renamed:
        reasons.append(f"{PROVENANCE}: the template's name changed")
    # A change of the release or commit the provenance records is held like machinery: it lands only as part of
    # the approved release it names, so no pull request can freeze a space on a release that does not exist or
    # move it back to an older one. A space that follows the template's main (a dev-only mode) records only a
    # commit: a move forward along that main, with no machinery in the pull request, lands (`follows_main`).
    if rules and not renamed and PROVENANCE.casefold() in touched and PROVENANCE not in guarded \
            and (str(head_prov.get("release") or ""), str(head_prov.get("commit") or "")) \
            != (rules["release"], rules["commit"]):
        along = follows_main(root, rules, head_prov, changed_files)
        if along:
            reasons.append(along)
        else:
            reasons.append(f"{PROVENANCE}: the template release it records changed")
            guarded.append(PROVENANCE)
    if rules:
        changes = raw_changes(root, base_ref, head)
        held += registry_reasons(root, base_ref, head, author=author) \
            if REGISTRY in {c[3] for c in changes} else []
        held += page_reasons(root, base_ref, head, changes, rules["owner"], author or "")
        held += withdrawal_reasons(root, base_ref, head, changes, rules["owner"], author or "", head_ref)
        held += release_reasons(root, base_ref, head, changes, rules["owner"], author, head_ref)
    if held:   # never part of a template release: no release match lets one through
        return PROTECTED, reasons + held
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
        folded = rel.casefold()
        # Zones and the suffix are matched ignoring case: a checkout that ignores case puts `Knowledge/x.MD`
        # into knowledge/, where it reads as a page, so it is checked as one.
        zone = next((z for z in PAGE_ZONES if folded.startswith(z)), None)
        page = path.suffix.casefold() == ".md" and path.name.casefold() not in ("index.md", "log.md", "readme.md")
        if path.suffix.casefold() != ".md" or not path.is_file() or pages.is_link(path):
            continue
        if zone is None or not page:
            # Outside the page zones only a stable page is an error: the tools never read one there
            # as signed, so a signature there can only mislead (a hub, a log or a readme included).
            try:
                outside, _ = pages.read(path)
            except Exception:
                continue
            if isinstance(outside, dict) and str(outside.get("status") or "") == "stable" \
                    and not folded.startswith(SIGNED_ZONES + DESK_ZONES):
                errs.append(f"{rel}: a stable page outside knowledge/ and identity/ is never signed")
            continue
        try:
            fm, body = pages.read(path)
        except Exception as exc:
            errs.append(f"{rel}: frontmatter does not parse ({exc})")
            continue
        if str(fm.get("status") or "") != "stable":
            continue
        if not rel.startswith(zone) or path.suffix != ".md":
            errs.append(f"{rel}: a stable page whose folder or suffix is spelled unlike `{zone}` and `.md`; "
                        "the tools would not read it as signed, a checkout that ignores case would")
        for prefix, why in NEVER_SIGNED.items():
            if folded.startswith(prefix):
                errs.append(f"{rel}: {why}")
        for key in pages.SIGNATURE_FIELDS:
            if not fm.get(key):
                errs.append(f"{rel}: stable page missing signature field `{key}`")
        if fm.get("fingerprint") and fm["fingerprint"] != pages.fingerprint(body):
            errs.append(f"{rel}: fingerprint does not match the body (edited after signing?)")
        import sign   # lazily: sign imports pages, and pages imports this module only when asked
        problem = sign.fields_problem(fm)
        if problem:
            errs.append(f"{rel}: {problem}")
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
    opts = {}
    rest = []
    it = iter(argv)
    for arg in it:
        if arg in ("--author", "--head-ref"):
            opts[arg] = next(it, "")
        else:
            rest.append(arg)
    if not rest:
        print(__doc__)
        return 2
    base_ref = rest[0]
    root = Path(rest[1] if len(rest) > 1 else ".").resolve()
    changed = changed_files(root, base_ref)
    cls, reasons = classify(root, changed, base_ref=base_ref, author=opts.get("--author") or None,
                            head_ref=opts.get("--head-ref") or None)
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
