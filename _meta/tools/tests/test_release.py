"""release.py: ship queue items and signals, retract, pull back."""
import datetime as dt
import json
import subprocess

import yaml

import pages
import release
import sweep
from conftest import NOW, make_space, mount_brain
from test_sweep import BRAIN_SUBJECTS, mount, record, write_feed


def build(tmp_path, ada_mode="live", harbor_mode="live", **ada_extra):
    brain = make_space(tmp_path / "brain", profile="brain", subjects=BRAIN_SUBJECTS)
    harbor_extra = {"mode": harbor_mode}
    if harbor_mode != "live":
        harbor_extra["time_scale"] = 48
    harbor = make_space(tmp_path / "harbor", profile="project", name="harbor", owner="Ben Liu",
                        audience=["Ada Okafor", "Ben Liu"], **harbor_extra)
    ada_extra.setdefault("sources", [{"name": "mail", "adapter": "gmail", "audience": "owner"}])
    ada_extra["mode"] = ada_mode
    if ada_mode != "live":
        ada_extra.setdefault("time_scale", 48)
    ada = make_space(tmp_path / "ada", name="ada", **ada_extra)
    mount_brain(ada, brain)
    mount(ada, "harbor", harbor)
    return brain, harbor, ada


def queue_paths(root):
    return sorted((root / "_outbound" / "queue").glob("*.md"))


def held_items(root):
    return [pages.read(p) for p in sorted((root / "_outbound" / "held").glob("*.md"))]


def landed(harbor):
    return [pages.read(p) for p in sorted((harbor / "_inbound" / "candidates").glob("*.md"))]


def set_listed(path):
    fm, body = pages.read(path)
    fm["listed"] = True
    pages.write(path, fm, body)


def test_window_item_waits_for_listing_then_ships_offline(tmp_path):
    _, harbor, ada = build(tmp_path)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    offline = {"harbor": harbor}
    later = NOW + dt.timedelta(hours=25)
    report = release.ship(ada, now=NOW, offline_roots=offline)
    assert report.shipped == 0 and report.signals == 1, "the signal leaves at once"
    assert (harbor / "_inbound" / "signals").exists()
    signal_path, = sorted((harbor / "_inbound" / "signals").glob("*.yaml"))
    signal = yaml.safe_load(signal_path.read_text(encoding="utf-8"))
    assert signal["subject"] == "harbor" and set(signal) == {"id", "subject", "destination", "at", "note"}
    assert not list((ada / "_outbound" / "signals").glob("*.yaml"))
    report = release.ship(ada, now=later, offline_roots=offline)
    assert report.shipped == 0 and report.skipped_unlisted == 1
    assert landed(harbor) == [] and len(queue_paths(ada)) == 1
    set_listed(queue_paths(ada)[0])
    report = release.ship(ada, now=NOW + dt.timedelta(hours=1), offline_roots=offline)
    assert report.shipped == 0, "listed, but the window has not passed"
    report = release.ship(ada, now=later, offline_roots=offline)
    assert report.shipped == 1 and report.destinations == {"harbor": 1}
    assert queue_paths(ada) == []
    (fm, body), = landed(harbor)
    assert fm["status"] == "draft" and fm["release_class"] == "window"
    assert fm["released_from"] == "ada" and fm["released_by"] == "Ada Okafor"
    assert fm["evidence"] == ["m-0001"] and fm["subject"] == "harbor"
    assert fm["type"] == "source" and fm["kind"] == "mail"
    for key in ("class", "stage_after", "listed", "destination", "from_space"):
        assert key not in fm
    assert "3 November" in body
    log = (ada / "log.md").read_text(encoding="utf-8")
    assert f"released {fm['id']} to harbor (window)" in log
    runs = [json.loads(l) for l in (ada / "_meta" / "state" / "runs.jsonl").read_text().splitlines()]
    assert runs[-1]["routine"] == "release" and runs[-1]["space"] == "ada"


def test_at_once_item_needs_no_listing(tmp_path):
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1 and report.skipped_unlisted == 0
    (fm, _), = landed(harbor)
    assert fm["release_class"] == "at_once"


def test_shadow_source_refuses_a_live_destination(tmp_path):
    _, harbor, ada = build(tmp_path, ada_mode="shadow", harbor_mode="live",
                           standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 0 and report.held == 2 and report.signals == 0
    assert landed(harbor) == [] and queue_paths(ada) == []
    assert not list((ada / "_outbound" / "signals").glob("*.yaml"))
    held = held_items(ada)
    assert sorted(fm["class"] for fm, _ in held) == ["at_once", "signal"]
    for fm, _ in held:
        assert fm["reason"] == "mode" and "shadow" in fm["why"] and "live" in fm["why"]
    assert not (harbor / "_inbound" / "signals").exists() or \
        not list((harbor / "_inbound" / "signals").glob("*.yaml"))


def test_shadow_ships_to_shadow(tmp_path):
    _, harbor, ada = build(tmp_path, ada_mode="shadow", harbor_mode="shadow",
                           standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1 and report.held == 0


def test_scrub_runs_again_at_ship_time(tmp_path):
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    path, = queue_paths(ada)
    fm, body = pages.read(path)
    pages.write(path, fm, body + "\nThe hourly rate stays 120.\n")
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 0 and report.held == 1 and landed(harbor) == []
    (held, _), = held_items(ada)
    assert held["reason"] == "left_out" and "hourly rate" in held["why"]


def test_signed_page_keeps_its_signature(tmp_path):
    _, harbor, ada = build(tmp_path)
    body = "Go-live is 3 November.\n"
    fm = {"id": "p-0000beef", "type": "decision", "title": "Go-live date", "status": "stable",
          "generated": pages.generated("distill", NOW), "signed_by": "Ada Okafor",
          "signed_at": pages.iso(NOW), "signed_in": "ada", "fingerprint": pages.fingerprint(body),
          "class": "at_once", "destination": "harbor", "subject": "harbor",
          "stage_after": pages.iso(NOW), "listed": False, "evidence": ["m-0001"],
          "from_space": "ada", "kind": "mail"}
    pages.write(ada / "_outbound" / "queue" / "p-0000beef.md", fm, body)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1
    (landed_fm, landed_body), = landed(harbor)
    assert landed_fm["status"] == "stable" and landed_fm["signed_by"] == "Ada Okafor"
    assert landed_fm["signed_in"] == "ada" and landed_fm["fingerprint"] == pages.fingerprint(body)
    assert landed_body == body and landed_fm["release_class"] == "at_once"


def test_retract_writes_a_retraction_item_that_ships_at_once(tmp_path):
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    offline = {"harbor": harbor}
    release.ship(ada, now=NOW, offline_roots=offline)
    (shipped_fm, _), = landed(harbor)
    item_id = shipped_fm["id"]
    retraction = release.retract(ada, item_id, now=NOW + dt.timedelta(hours=2))
    fm, body = pages.read(retraction)
    assert fm["class"] == "retraction" and fm["destination"] == "harbor"
    assert fm["retracts"] == item_id and fm["subject"] == "harbor"
    assert body.strip() == f"retracted {item_id}"
    assert pages.parse_iso(fm["stage_after"]) == NOW + dt.timedelta(hours=2)
    assert fm["listed"] is False and fm["status"] == "draft"
    report = release.ship(ada, now=NOW + dt.timedelta(hours=2), offline_roots=offline)
    assert report.shipped == 1 and report.retracted == 1
    by_id = {fm["id"]: fm for fm, _ in landed(harbor)}
    assert by_id[item_id]["retracted"] is True
    assert by_id[fm["id"]]["release_class"] == "retraction"
    assert f"released {fm['id']} to harbor (retraction)" in (ada / "log.md").read_text()


def test_retract_marks_the_original_when_the_destination_is_reachable_offline(tmp_path):
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    (shipped_fm, _), = landed(harbor)
    release.retract(ada, shipped_fm["id"], now=NOW, offline_roots={"harbor": harbor})
    fm, _ = pages.read(harbor / "_inbound" / "candidates" / f"{shipped_fm['id']}.md")
    assert fm["retracted"] is True


def test_pull_back_moves_a_queue_item_to_held(tmp_path):
    _, _, ada = build(tmp_path)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    path, = queue_paths(ada)
    item_id = pages.read(path)[0]["id"]
    held_path = release.pull_back(ada, item_id)
    assert queue_paths(ada) == [] and held_path.exists()
    fm, _ = pages.read(held_path)
    assert fm["reason"] == "pulled" and fm["id"] == item_id and fm["class"] == "window"


def test_unknown_destination_stays_queued(tmp_path):
    _, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={})
    assert report.shipped == 0 and report.held == 0 and report.unreachable == 1
    assert len(queue_paths(ada)) == 1


def test_online_destination_goes_through_publish(tmp_path, monkeypatch):
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    spaces_home = tmp_path / "spaces-home"
    checkout = spaces_home / "harbor"
    checkout.mkdir(parents=True)
    (checkout / ".git").mkdir()
    (checkout / "space.yaml").write_text((harbor / "space.yaml").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))
    calls = []

    def fake_publish(checkout_path, slug, source, write):
        calls.append((checkout_path, slug, source))
        write()
        return True

    monkeypatch.setattr(release, "publish", fake_publish)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW)
    assert report.shipped == 1 and report.signals == 1
    assert calls == [(checkout, "ada", "ada")]
    assert len(list((checkout / "_inbound" / "candidates").glob("*.md"))) == 1
    assert len(list((checkout / "_inbound" / "signals").glob("*.yaml"))) == 1
    assert queue_paths(ada) == []


def test_publish_failure_keeps_the_queue(tmp_path, monkeypatch):
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    spaces_home = tmp_path / "spaces-home"
    checkout = spaces_home / "harbor"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "space.yaml").write_text((harbor / "space.yaml").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))

    def failing_publish(checkout_path, slug, source, write):
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(release, "publish", failing_publish)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW)
    assert report.shipped == 0 and report.failed == 1
    assert len(queue_paths(ada)) == 1


def test_publish_runs_git_as_argument_lists(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    seen = []

    def fake_run(args, **kwargs):
        seen.append(list(args))
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout="[]\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    written = []
    release.publish(checkout, "ada", "ada", lambda: written.append(1))
    assert written == [1]
    flat = [" ".join(a) for a in seen]
    assert all(isinstance(a, list) for a in seen)
    assert flat[0].startswith(f"git -C {checkout} fetch origin --no-recurse-submodules")
    assert f"git -C {checkout} checkout -q -B release/ada origin/main" in flat
    assert any(a[:4] == ["git", "-C", str(checkout), "add"] for a in seen)
    assert f"git -C {checkout} commit -q -m chore: release" in flat
    assert any("push" in a and "--force-with-lease" in a and "release/ada" in a for a in seen)
    assert any(a[:3] == ["gh", "pr", "create"] and "--base" in a and "main" in a for a in seen)
    assert not any("stable" in piece for piece in flat)


def test_main_cli(tmp_path, monkeypatch, capsys):
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    monkeypatch.setenv("SPACE_NOW", pages.iso(NOW))
    sweep.run(ada)
    path, = queue_paths(ada)
    item_id = pages.read(path)[0]["id"]
    assert release.main([str(ada), "pull-back", item_id]) == 0
    assert release.main([str(ada), "ship", "--offline", f"harbor={harbor}"]) == 0
    out = capsys.readouterr().out
    assert "shipped" in out
    assert release.main([str(ada), "retract", "p-00000000"]) != 0
    assert release.main([str(ada)]) == 2


def test_secrets_scan_finding_holds_the_item_without_quoting_it(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("scan")
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    path, = queue_paths(ada)
    fm, body = pages.read(path)
    # Assembled at runtime so the repository's own secrets scan never sees the marker whole.
    marker = "PRIVATE " + "KEY-----"
    leaked = f"-----BEGIN RSA {marker}\nMIIB\n-----END RSA {marker}\n"
    pages.write(path, fm, body + "\n" + leaked)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 0 and report.held == 1 and landed(harbor) == []
    (held, _), = held_items(ada)
    assert held["reason"] == "scan" and "PRIVATE" not in held["why"]


def test_destination_checkout_is_cloned_on_first_release(tmp_path, monkeypatch):
    """A destination with a mount that names its repository needs no hand step: the first
    release clones it under the spaces home and publishes through that clone."""
    _, harbor, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run(git + ["init", "--quiet", "-b", "main"], cwd=harbor, check=True)
    subprocess.run(git + ["add", "-A"], cwd=harbor, check=True)
    subprocess.run(git + ["commit", "--quiet", "-m", "x"], cwd=harbor, check=True)
    bare = tmp_path / "harbor.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(harbor), str(bare)], check=True)
    space_yaml = ada / "space.yaml"
    data = yaml.safe_load(space_yaml.read_text(encoding="utf-8"))
    for mount in data["mounts"]:
        if mount["name"] == "harbor":
            mount["repo"] = str(bare)
    space_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    spaces_home = tmp_path / "spaces-home"
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))
    calls = []

    def fake_publish(checkout_path, slug, source, write):
        calls.append(checkout_path)
        write()
        return True

    monkeypatch.setattr(release, "publish", fake_publish)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW)
    assert report.shipped == 1 and report.unreachable == 0
    assert calls == [spaces_home / "harbor"] and (spaces_home / "harbor" / ".git").exists()
    assert len(list((spaces_home / "harbor" / "_inbound" / "candidates").glob("*.md"))) == 1


def test_item_follows_its_subject_to_a_new_home(tmp_path):
    """Queued for a project space that was offboarded since: the item re-addresses itself to
    the subject's current home instead of staying unreachable forever."""
    brain, _, ada = build(tmp_path, standing_choices=[{"destination": "harbor", "kind": "mail"}])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item,) = queue_paths(ada)
    fm, _ = pages.read(item)
    assert fm["destination"] == "harbor" and fm["subject"] == "harbor"
    reg_path = brain / "_meta" / "subjects.yaml"
    data = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    for subject in data["subjects"]:
        if subject["id"] == "harbor":
            subject["home"] = "brain"
    reg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    report = release.ship(ada, now=NOW, offline_roots={"brain": brain})
    assert report.rerouted == 1 and report.shipped == 1 and report.unreachable == 0
    landed_pages = list((brain / "_inbound" / "candidates").glob("*.md"))
    assert len(landed_pages) == 1
    landed_fm, _ = pages.read(landed_pages[0])
    assert landed_fm["released_from"] == "ada" and landed_fm["subject"] == "harbor"
    assert queue_paths(ada) == []


def test_brain_destination_is_cloned_through_the_brain_mount(tmp_path, monkeypatch):
    """Releases to the brain are addressed to the brain's name while its mount is called
    `brain`: the first release still finds the repository and clones it."""
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=[
        {"id": "pricing", "kind": "topics", "title": "Pricing", "home": "lantern",
         "match": {"label": "Pricing"}}])
    ada = make_space(tmp_path / "ada", name="ada", sources=[{"name": "mail", "adapter": "gmail", "audience": "owner"}],
                     standing_choices=[{"destination": "lantern", "kind": "mail"}])
    mount_brain(ada, brain)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run(git + ["init", "--quiet", "-b", "main"], cwd=brain, check=True)
    subprocess.run(git + ["add", "-A"], cwd=brain, check=True)
    subprocess.run(git + ["commit", "--quiet", "-m", "x"], cwd=brain, check=True)
    bare = tmp_path / "brain.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(brain), str(bare)], check=True)
    space_yaml = ada / "space.yaml"
    data = yaml.safe_load(space_yaml.read_text(encoding="utf-8"))
    for mount in data["mounts"]:
        if mount["name"] == "brain":
            mount["repo"] = str(bare)
    space_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    spaces_home = tmp_path / "spaces-home"
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))
    monkeypatch.setattr(release, "publish", lambda checkout_path, slug, source, write: (write(), True)[1])
    write_feed(ada, [record(subject="Pricing call", identifiers={"thread": "t-2", "labels": ["Pricing"]})])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW)
    assert report.shipped + report.signals == 1 and report.unreachable == 0
    assert (spaces_home / "lantern" / ".git").exists()
    assert list((spaces_home / "lantern" / "_inbound").rglob("*"))
