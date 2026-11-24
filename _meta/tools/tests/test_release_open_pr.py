"""release.publish on an open release pull request (fixed in 0.3.3)."""
import subprocess

import release


def test_publish_builds_on_an_open_release_pull_request_and_never_resets_it(tmp_path, monkeypatch):
    # Seen live: a run reset the branch of an open, failed release pull request to main and
    # force-pushed, so the pages it carried were lost when it later merged.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    seen = []

    def fake_run(args, **kwargs):
        seen.append(list(args))
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout='[{"number": 7}]\n', stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    release.publish(checkout, "ada", "ada", lambda: None)
    flat = [" ".join(a) for a in seen]
    assert f"git -C {checkout} checkout -q -B release/ada origin/release/ada" in flat
    assert not any("origin/main" in line for line in flat)
    assert not any(a[:3] == ["gh", "pr", "create"] for a in seen)
