"""offboard: close a space when its owner leaves (shape 1).

    python _meta/tools/offboard.py [root] [--online] [--dry-run]

Prints what still waits (queue, held, candidates) and moves nothing; removes the sync task
(online: the Windows task, the macOS launch agent and its file, the systemd user timer and its two
unit files, or the cron line by its `# <task>` marker) and the personal instruction block from the
user-level file; archives
the repository (online, `gh repo archive`); sets `state: archived` on the subjects whose home
is this space: in its own registry at once, and in the registry that holds one (the brain) by
a registry note that ships at once, alone (online, or to `offline_roots`); writes
`_meta/state/offboard.yaml`. Deletes no file of the space.
"""
from __future__ import annotations

import argparse
import re
import sys
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


def remove_probe_task(report, root, state=None, dry_run=False) -> None:
    """The routines' probe task a build recorded (`probe_task` in onboard.yaml, written before the task was
    registered) and did not remove, because it was stopped first: removed through `remove_sync_task`, and
    forgotten once it is gone. The record is read from a file a merge can change, so it only says whether there
    is one: what is removed is this space's own probe task by the name and launch agent label the code derives
    (`<prefix>probe-<name>`, the prefix `onboard.task_prefix` gives), never by a name or label the record holds."""
    root = Path(root)
    state = state if state is not None else onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    record = state.get("probe_task")
    try:
        name = f"{onboard.task_prefix()}probe-{spaceyaml.load(root).name}"
    except Exception:
        return
    if not (isinstance(record, dict) and record.get("name") == name):
        return
    kind = {"kind": record["kind"]} if record.get("kind") in ("schtasks", "launchd", "systemd", "cron") else {}
    remove_sync_task(report, root, {"routine_tasks": [{"name": name, **kind}]}, name, dry_run,
                     label=f"routine probe task {name} removed")
    if not dry_run and report.steps and report.steps[-1].done:
        onboard.forget_probe_task(root)


def remove_sync_task(report, root, state, task_name, dry_run, label="sync task removed") -> None:
    """Remove one OS task onboard registered: the sync task, or a routine's (`label` names it)."""
    if not dry_run and label == "sync task removed":
        onboard.forget_space(root)   # the chains stop running over it on this machine
    if onboard.SYSTEM == "Windows":
        present = run_cmd(["schtasks", "/Query", "/TN", task_name], dry_run)
        if not dry_run and present.returncode != 0:
            report.add(label, True, f"schtasks /Query /TN {task_name}",
                       "none registered (a minted project runs under its person's task)")
            return
        res = run_cmd(["schtasks", "/Delete", "/F", "/TN", task_name], dry_run)
        verify = run_cmd(["schtasks", "/Query", "/TN", task_name], dry_run)
        ok = res.returncode == 0 and (dry_run or verify.returncode != 0)
        report.add(label, ok, f"schtasks /Query /TN {task_name}", res.stderr.strip()[:200] or task_name)
        return
    recorded = next((r for r in [state.get("sync_task") or {}, *(state.get("routine_tasks") or [])]
                     if isinstance(r, dict) and r.get("name") == task_name), {})
    ident = str(recorded.get("label") or "")
    if not re.fullmatch(r"com\.spaces\.[\w.-]+", ident):   # the record is read from a file a merge can change
        ident = onboard.LAUNCHD_PREFIX + task_name
    agent = Path.home() / "Library" / "LaunchAgents" / f"{ident}.plist"
    if agent.exists() or recorded.get("kind") == "launchd":   # a Mac: the agent is let go of, then its file removed
        loaded = f"gui/{onboard.user_id()}/{ident}"
        run_cmd(["launchctl", "bootout", loaded], dry_run)
        verify = run_cmd(["launchctl", "print", loaded], dry_run)
        ok = dry_run or verify.returncode != 0   # whatever bootout said: the file goes only once it is not loaded
        if ok and not dry_run:
            agent.unlink(missing_ok=True)
        report.add(label, ok, f"launchctl print {loaded}",
                   "launch agent let go, its file removed" if ok else "the launch agent is still loaded")
        return
    timer = Path.home() / ".config" / "systemd" / "user" / f"{task_name}.timer"
    if timer.exists():   # a Linux with a systemd user session got a timer
        run_cmd(["systemctl", "--user", "disable", "--now", timer.name], dry_run)
        verify = run_cmd(["systemctl", "--user", "is-enabled", timer.name], dry_run)
        ok = dry_run or verify.returncode != 0   # the files stay while systemd still has the timer
        if ok and not dry_run:
            timer.unlink()
            timer.with_suffix(".service").unlink(missing_ok=True)
            run_cmd(["systemctl", "--user", "daemon-reload"])
        report.add(label, ok, f"systemctl --user is-enabled {timer.name}",
                   "timer disabled, its two unit files removed" if ok else "the timer is still enabled")
        return
    lines, state, why = onboard.read_crontab(dry_run, run=run_cmd)
    if state == "missing":
        report.add(label, True, "crontab -l", "no cron on this machine: nothing was registered")
        return
    if state == "failed":   # not an empty crontab: nothing was written, and "no line left" would be a guess
        report.add(label, False, "crontab -l", f"this computer's scheduler could not be read ({why}), so nothing was "
                   "removed: try again once it can be read")
        return
    keep = [l for l in lines if not onboard.is_task_line(l, task_name, root)]
    if len(keep) != len(lines):
        onboard.write_crontab(keep, dry_run, run=run_cmd)
    verify = run_cmd(["crontab", "-l"], dry_run).stdout
    ok = dry_run or not any(onboard.is_task_line(l, task_name, root) for l in verify.splitlines())
    report.add(label, ok, "crontab -l", "no line left" if ok else "a line remains")


def raw_records(root: Path) -> list:
    """A personal space's raw records: the feed files and the witness pages of its own sweep."""
    root = Path(root)
    out = sorted((root / "_meta" / "state" / "feed").glob("*.jsonl")) if (root / "_meta/state/feed").is_dir() else []
    base = root / "knowledge" / "sources"
    out += sorted(base.rglob("*.md")) if base.is_dir() else []
    return out


def run(root, online, dry_run=False, forget_records: bool = False,
        offline_roots: dict | None = None) -> onboard.Report:
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
    if space.profile == "personal":
        raw = raw_records(root)
        if forget_records and not dry_run:
            # the person's own command: raw personal records go at offboard; git keeps history
            for path in raw:
                path.unlink(missing_ok=True)
            report.add("raw records removed", True, "ls _meta/state/feed knowledge/sources",
                       f"{len(raw)} files removed on the person's own command; history keeps them")
        else:
            report.add("raw records listed", True, "python _meta/tools/offboard.py . --forget-records",
                       f"{len(raw)} raw records stay; the person removes them with --forget-records")
    state = onboard.read_yaml(root / "_meta" / "state" / "onboard.yaml", {}) or {}
    try:   # unrecorded, the name onboard gave it under the prefix in force (F-7): a test run never names the person's
        task_name = (state.get("sync_task") or {}).get("name") or f"{onboard.task_prefix()}sync-{space.name}"
    except ValueError as exc:   # a prefix that is none of ours names no task, so none is touched
        task_name = None
        report.add("sync task removed", False, f"echo ${onboard.TASK_PREFIX_ENV}", str(exc)[:200])
    if online:
        if task_name:
            remove_sync_task(report, root, state, task_name, dry_run)
        for routine in state.get("routine_tasks") or []:   # routines the OS ran, without a harness scheduler
            remove_sync_task(report, root, state, routine["name"], dry_run, label=f"routine task {routine['name']} removed")
        remove_probe_task(report, root, state, dry_run)   # a build stopped before it removed its probe task
    elif task_name:
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
        if new != text and not dry_run:
            onboard.write_text(ui, new)
        report.add("personal instruction removed", dry_run or onboard.MARK_OPEN not in new,
                   f"grep -c 'space:personal-instruction' {ui}", str(ui))
    else:
        report.add("personal instruction removed", True, f"test ! -f {ui}", "no user-level file")
    if ui is not None:
        settings = ui.parent / "settings.json"
        if not dry_run:
            onboard.disallow_directory(settings, root)
        report.add("workspace no longer allowed", dry_run or not onboard.directory_allowed(settings, root),
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
    if archived and not dry_run:
        reg.save()
    # a subject homed here that another registry holds: that registry hears it from its home
    held = sorted(sid for sid, s in reg.ancestors.items()
                  if sid not in reg.subjects and s.state in ("active", "dormant") and reg.home_of(sid) == space.name)
    notes = 0
    if held and not dry_run:
        import classify
        import release
        for sid in held:
            notes += classify.send_state(root, sid, "archived", now) is not None
        if online or offline_roots:
            release.ship(root, now, offline_roots=offline_roots, notes_only=True)
    archived += held
    waiting = len(list((root / "_outbound" / "signals").glob("*.yaml"))) if notes else 0
    report.add("registry archived", True, "python _meta/tools/registry.py . check",
               (", ".join(archived) or "no subject has its home here")
               + (f"; {waiting} signals wait for the next ship" if waiting else ""))
    if not dry_run:
        onboard.write_yaml(root / "_meta" / "state" / "offboard.yaml",
                           {"at": pages.iso(now), "space": space.name, "counts": counts, "archived": archived,
                            "steps": [s.row() for s in report.steps]})
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--forget-records", action="store_true",
                    help="the person's own command: remove the raw feed files and witness pages")
    args = ap.parse_args(argv)
    report = run(args.root, args.online, dry_run=args.dry_run, forget_records=args.forget_records)
    onboard.print_steps(report.steps)
    return 1 if report.failed() else 0


if __name__ == "__main__":
    sys.exit(main())
