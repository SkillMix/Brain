"""routine: run one of the person's routines headless through Claude Code's command line, so the
routines outlive the session that set them up. The OS scheduler starts it at the routine's times
(`onboard.py <workspace> --os-routines`).

The prompt is the rendered routine file without its frontmatter. Claude runs in the workspace
with its permission profile; nobody is there to answer a permission prompt, so anything that
would ask is refused and the routine goes on without it. Output and the exit code go to
`~/.spaces/logs/<routine>-<space>.log`.

    python routine.py <workspace>/_meta/state/routines/<routine>.md
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor  # noqa: E402
import spaceyaml  # noqa: E402

CLAUDE = ["-p", "--permission-mode", "acceptEdits", "--permission-prompts", "none"]


def find_claude() -> str | None:
    """Claude Code's command: on PATH, else where its own installer puts it."""
    found = shutil.which("claude")
    if found:
        return found
    for folder in (Path.home() / ".local" / "bin", Path.home() / ".claude" / "local"):
        for name in ("claude", "claude.exe"):   # a scheduled task may start without the user's PATH
            if (folder / name).exists():
                return str(folder / name)
    return None


def main(argv=None, run=subprocess.run) -> int:
    argv = sys.argv[1:] if argv is None else argv
    prompt = Path(argv[0]).resolve()
    workspace = prompt.parents[3]   # <workspace>/_meta/state/routines/<routine>.md
    text = prompt.read_text(encoding="utf-8")
    body = text.split("---", 2)[2].lstrip() if text.startswith("---") else text
    log = Path.home() / ".spaces" / "logs" / f"{prompt.stem}-{spaceyaml.load(workspace).name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    doctor.use_tools()
    claude = find_claude()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n== {dt.datetime.now().isoformat(timespec='seconds')} {prompt.stem}\n")
        if claude is None:
            fh.write("claude is not installed or not on PATH: install Claude Code, then run onboard "
                     "--os-routines again\nexit 127\n")
            return 127
        try:
            res = run([claude, *CLAUDE], input=body, cwd=str(workspace), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=3600)
        except subprocess.TimeoutExpired:
            fh.write("stopped after an hour\nexit 124\n")
            return 124
        fh.write((res.stdout or "") + (res.stderr or "") + f"\nexit {res.returncode}\n")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
