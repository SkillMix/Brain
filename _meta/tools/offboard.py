"""offboard: close a space when its owner leaves (shape 1).

    python _meta/tools/offboard.py [root] [--online] [--dry-run]

Prints what still waits (queue, held, candidates) and moves nothing; removes the sync task
(online) and the personal instruction block from the user-level file; archives the
repository (online, `gh repo archive`); sets `state: archived` on this space's own subjects
whose home is this space; writes `_meta/state/offboard.yaml`. Deletes no file.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import onboard  # noqa: E402
import pages  # noqa: E402
import registry  # noqa: E402
import spaceyaml  # noqa: E402

ZONES = (("queue", "_outbound/queue"), ("held", "_outbound/held"), ("candidates", "_inbound/candidates"))


def run_cmd(argv, dry_run=False, cwd=None):
    """Offboard's door to the outside: the same one onboard uses, monkeypatched separately."""
    return onboard.run_cmd(argv, dry_run=dry_run, cwd=cwd)


def remove_sync_task(report, root, state, task_name, dry_run) -> None:
    if os.name == "nt":
        present = run_cmd(["schtasks", "/Query", "/TN", task_name], dry_run)
        if not dry_run and present.returncode != 0:
            report.add("sync task removed", True, f"schtasks /Query /TN {task_name}",
                       "none registered (a minted project runs under its person's task)")
            return
        res = run_cmd(["schtasks", "/Delete", "/F", "/TN", task_name], dry_run)
        verify = run_cmd(["schtasks", "/Query", "/TN", task_name], dry_run)
        ok = res.returncode == 0 and (dry_run or verify.returncode != 0)
        report.add("sync task removed", ok, f"schtasks /Query /TN {task_name}", res.stderr.strip()[:200] or task_name)
        return
    current = run_cmd(["crontab", "-l"], dry_run).stdout
    keep = [l for l in current.splitlines() if not ("sync.py" in l and str(root) in l)]
    if len(keep) != len(current.splitlines()):
        with tempfile.NamedTemporaryFile("w", suffix=".cron", delete=False) as tmp:
            tmp.write("\n".join(keep) + ("\n" if keep else ""))
        run_cmd(["crontab", tmp.name], dry_run)
        os.unlink(tmp.name)
    verify = run_cmd(["crontab", "-l"], dry_run).stdout
    ok = dry_run or not any("sync.py" in l and str(root) in l for l in verify.splitlines())
    report.add("sync task removed", ok, "crontab -l", "no sync line left" if ok else "a sync line remains")


def run(root, online, dry_run=False) -> onboard.Report:
    root = Path(root).resolve()
    space = spaceyaml.load(root)
    now = space.now()
    report = onboard.Report(case="offboard")
    counts = {zone: len([p for p in (root / rel).glob("*") if p.is_file() and p.name != ".gitkeep"])
              for zone, rel in ZONES}
    for zone, count in counts.items():
        print(f"{zone}: {count} still here (nothing moved)")
    report.add("still waiting", True, "ls _outbound/queue _outbound/held _inbound/candidates",
               ", ".join(f"{k} {v}" for k, v in counts.items()))
    state = onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    task_name = (state.get("sync_task") or {}).get("name") or f"space-sync-{space.name}"
    if online:
        remove_sync_task(report, root, state, task_name, dry_run)
    else:
        report.add("sync task removed", True, f"schtasks /Query /TN {task_name}",
                   "offline: nothing was registered")
    # Only a space that installed an instruction block (recorded by onboard) has one to remove;
    # a minted project never touches the person's user-level file.
    ui = Path(state["user_instructions"]) if state.get("user_instructions") else None
    if ui is None:
        report.add("personal instruction removed", True, "cat _meta/state/onboard.yaml",
                   "none was installed for this space")
    elif ui.exists() and not onboard.instruction_present(ui, root):
        # the block names another space (the owner's workspace, say): it is theirs, not ours
        report.add("personal instruction removed", True, f"grep -c 'space:personal-instruction' {ui}",
                   "the block in the user-level file belongs to another space; kept")
    elif ui.exists():
        text = ui.read_text(encoding="utf-8")
        new = onboard.remove_block(text)
        if new != text:
            onboard.write_text(ui, new)
        report.add("personal instruction removed", onboard.MARK_OPEN not in new,
                   f"grep -c 'space:personal-instruction' {ui}", str(ui))
    else:
        report.add("personal instruction removed", True, f"test ! -f {ui}", "no user-level file")
    if ui is not None:
        settings = ui.parent / "settings.json"
        onboard.disallow_directory(settings, root)
        report.add("workspace no longer allowed", not onboard.directory_allowed(settings, root),
                   f"grep -c additionalDirectories {settings}", str(settings))
    repo = state.get("repo") or onboard.git_remote(root)
    if online and repo:
        res = run_cmd(["gh", "repo", "archive", repo, "-y"], dry_run)
        verify = run_cmd(["gh", "repo", "view", repo, "--json", "isArchived", "-q", ".isArchived"], dry_run)
        report.add("repository archived", res.returncode == 0 and (dry_run or verify.stdout.strip() == "true"),
                   f"gh repo view {repo} --json isArchived -q .isArchived", res.stderr.strip()[:200] or repo)
    elif online:
        report.add("repository archived", False, "gh repo view <repo> --json isArchived",
                   "no origin remote; archive it by hand", needs_owner=True)
    else:
        report.add("repository archived", True, "gh repo view <repo> --json isArchived", "offline: not attempted")
    reg = registry.Registry(root, space)
    archived = []
    for subject in reg.subjects.values():
        if subject.home == space.name and subject.state != "archived":
            subject.state = "archived"
            archived.append(subject.id)
    if archived:
        reg.save()
    report.add("registry archived", True, "python _meta/tools/registry.py . check",
               ", ".join(archived) or "no subject has its home here")
    onboard.write_yaml(root / "_meta" / "state" / "offboard.yaml",
                       {"at": pages.iso(now), "space": space.name, "counts": counts, "archived": archived,
                        "steps": [s.row() for s in report.steps]})
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    report = run(args.root, args.online, dry_run=args.dry_run)
    onboard.print_steps(report.steps)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
