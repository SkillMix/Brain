"""release.py: ship queue items and signals, retract, pull back."""
import datetime as dt
import json
import subprocess

import yaml

import gate
import pages
import release
import spaceyaml
import sweep
from conftest import NOW, make_space, mount_brain
from test_sweep import BRAIN_SUBJECTS, CREW_MAIL, mount, record, write_feed


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


def as_window_item(root):
    """The sweep holds what nobody judged; a window item is what the router queues. One made
    from the held page, as the router would have queued it."""
    (fm, body), = held_items(root)
    item = {k: v for k, v in fm.items() if k not in ("reason", "why")}
    item.update({"class": "window", "stage_after": pages.iso(NOW + dt.timedelta(hours=24)), "listed": False})
    pages.write(root / "_outbound" / "queue" / f"{fm['id']}.md", item, body)
    (root / "_outbound" / "held" / f"{fm['id']}.md").unlink()


def set_listed(path):
    fm, body = pages.read(path)
    fm["listed"] = True
    pages.write(path, fm, body)


def test_window_item_waits_for_listing_then_ships_offline(tmp_path):
    _, harbor, ada = build(tmp_path)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    as_window_item(ada)
    offline = {"harbor": harbor}
    later = NOW + dt.timedelta(hours=25)
    report = release.ship(ada, now=NOW, offline_roots=offline)
    assert report.shipped == 0 and report.signals == 1, "the signal leaves at once"
    assert (harbor / "_inbound" / "signals").exists()
    signal_path, = sorted((harbor / "_inbound" / "signals").glob("*.yaml"))
    signal = yaml.safe_load(signal_path.read_text(encoding="utf-8"))
    assert signal["subject"] == "harbor" and set(signal) == {"id", "subject", "destination", "at", "note", "from"}
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
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1 and report.skipped_unlisted == 0
    (fm, _), = landed(harbor)
    assert fm["release_class"] == "at_once"


def test_shadow_source_refuses_a_live_destination(tmp_path):
    _, harbor, ada = build(tmp_path, ada_mode="shadow", harbor_mode="live",
                           sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 0 and report.held == 1 and report.signals == 0
    assert landed(harbor) == [] and queue_paths(ada) == []
    assert not list((ada / "_outbound" / "signals").glob("*.yaml"))
    held = held_items(ada)
    assert sorted(fm["class"] for fm, _ in held) == ["at_once"]
    for fm, _ in held:
        assert fm["reason"] == "mode" and "shadow" in fm["why"] and "live" in fm["why"]
    assert not (harbor / "_inbound" / "signals").exists() or \
        not list((harbor / "_inbound" / "signals").glob("*.yaml"))


def test_shadow_ships_to_shadow(tmp_path):
    _, harbor, ada = build(tmp_path, ada_mode="shadow", harbor_mode="shadow",
                           sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1 and report.held == 0


def test_scrub_at_ship_time_checks_facts_not_words(tmp_path):
    """What an item says was judged before it was queued; at ship time a word holds nothing,
    a secret still does."""
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    path, = queue_paths(ada)
    fm, body = pages.read(path)
    pages.write(path, fm, body + "\nThe hourly rate stays 120 and the settlement report is due.\n")
    pages.write(path.with_name("p-0000c0de.md"), dict(fm, id="p-0000c0de"),
                body + "".join(["-----BEGIN ", "PRIVATE KEY-----\nabc\n-----END ", "PRIVATE KEY-----\n"]))
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1 and report.held == 1
    (held, _), = held_items(ada)
    assert held["id"] == "p-0000c0de" and held["reason"] == "scan"


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
    # sign.py's line in this space's own history: without it a stable item is held, never shipped
    with (ada / "log.md").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"- {pages.iso(NOW)} fingerprint p-0000beef {pages.fingerprint(body)} by Ada Okafor\n")
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1
    (landed_fm, landed_body), = landed(harbor)
    assert landed_fm["status"] == "stable" and landed_fm["signed_by"] == "Ada Okafor"
    assert landed_fm["signed_in"] == "ada" and landed_fm["fingerprint"] == pages.fingerprint(body)
    assert landed_body == body and landed_fm["release_class"] == "at_once"


def test_retract_writes_a_retraction_item_that_ships_at_once(tmp_path):
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
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
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
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
    as_window_item(ada)
    path, = queue_paths(ada)
    item_id = pages.read(path)[0]["id"]
    held_path = release.pull_back(ada, item_id)
    assert queue_paths(ada) == [] and held_path.exists()
    fm, _ = pages.read(held_path)
    assert fm["reason"] == "pulled" and fm["id"] == item_id and fm["class"] == "window"


def test_unknown_destination_stays_queued(tmp_path):
    _, _, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={})
    assert report.shipped == 0 and report.held == 0 and report.unreachable == 1
    assert len(queue_paths(ada)) == 1


def test_online_destination_goes_through_publish(tmp_path, monkeypatch):
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
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
    assert report.shipped == 1 and report.signals == 0   # at once: the item, no signal
    # the branch is `release/<slug>-<8 hex>` (release.branch_slug), the shape the gate reads back
    slug = release.branch_slug(spaceyaml.load(ada))
    assert calls == [(checkout, slug, "ada")] and gate.RELEASE_BRANCH.match(f"release/{slug}").group(1) == "ada"
    assert len(list((checkout / "_inbound" / "candidates").glob("*.md"))) == 1
    assert not list((checkout / "_inbound" / "signals").glob("*.yaml"))
    assert queue_paths(ada) == []


def test_settings_come_from_the_mount_before_a_release_clone(tmp_path, monkeypatch):
    """A release clone is refreshed only when something ships; a mount moves with every sync
    run. Without a given root, a destination's settings come from its mount."""
    _, harbor, ada = build(tmp_path)
    checkout = tmp_path / "spaces-home" / "harbor"
    (checkout / ".git").mkdir(parents=True)
    stale = yaml.safe_load((harbor / "space.yaml").read_text(encoding="utf-8"))
    stale["left_out"] = ["transcript"]
    (checkout / "space.yaml").write_text(yaml.safe_dump(stale), encoding="utf-8")
    monkeypatch.setenv("SPACES_HOME", str(tmp_path / "spaces-home"))
    space = spaceyaml.load(ada)
    assert "transcript" not in release.destination_space(space, "harbor").left_out
    assert "transcript" in release.destination_space(space, "harbor", checkout).left_out


def test_publish_failure_keeps_the_queue(tmp_path, monkeypatch):
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
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
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
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
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
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
    monkeypatch.setenv("SPACES_LOCAL_CLONES", "1")   # the bare repositories are local paths
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
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


def test_a_release_clone_of_another_repository_is_set_aside(tmp_path, monkeypatch):
    """The spaces home keys release clones by space name. A clone left by an older space of the
    same name (a company set up again, a deleted repository) is moved aside and the destination
    cloned fresh, instead of publishing into the wrong repository or failing without a word."""
    monkeypatch.setenv("SPACES_LOCAL_CLONES", "1")   # the bare repositories are local paths
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run(git + ["init", "--quiet", "-b", "main"], cwd=harbor, check=True)
    subprocess.run(git + ["add", "-A"], cwd=harbor, check=True)
    subprocess.run(git + ["commit", "--quiet", "-m", "x"], cwd=harbor, check=True)
    bare, older = tmp_path / "harbor.git", tmp_path / "older-harbor.git"
    for target in (bare, older):
        subprocess.run(["git", "clone", "--quiet", "--bare", str(harbor), str(target)], check=True)
    spaces_home = tmp_path / "spaces-home"
    subprocess.run(["git", "clone", "--quiet", str(older), str(spaces_home / "harbor")], check=True)
    space_yaml = ada / "space.yaml"
    data = yaml.safe_load(space_yaml.read_text(encoding="utf-8"))
    for mount in data["mounts"]:
        if mount["name"] == "harbor":
            mount["repo"] = str(bare)
    space_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))
    monkeypatch.setattr(release, "publish", lambda checkout_path, slug, source, write: (write(), True)[1])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW)
    assert report.shipped == 1 and report.unreachable == 0
    origin = subprocess.run(["git", "-C", str(spaces_home / "harbor"), "remote", "get-url", "origin"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert origin == str(bare)
    (aside,) = (spaces_home / ".stale").iterdir()
    assert subprocess.run(["git", "-C", str(aside), "remote", "get-url", "origin"],
                          capture_output=True, text=True, check=True).stdout.strip() == str(older)


def test_item_follows_its_subject_to_a_new_home(tmp_path):
    """Queued for a project space that was offboarded since: the item re-addresses itself to
    the subject's current home instead of staying unreachable forever."""
    brain, _, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item,) = queue_paths(ada)
    fm, _ = pages.read(item)
    assert fm["destination"] == "harbor" and fm["subject"] == "harbor"
    reg_path = brain / "_meta" / "subjects.yaml"
    data = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    for subject in data["subjects"]:
        if subject["id"] == "harbor":   # the home moves, and its classification with it
            subject["home"] = "brain"
            subject["classification"] = dict(subject["classification"], home="brain", **{"class": "company"})
    reg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    released = ada / "_meta" / "state" / "released.jsonl"   # released once since: no first-release wait
    released.write_text(json.dumps({"id": "p-1", "at": pages.iso(NOW), "subject": "harbor"}) + "\n",
                        encoding="utf-8")
    report = release.ship(ada, now=NOW, offline_roots={"brain": brain})
    # the item follows the subject (queued at once, it raised no signal)
    assert report.rerouted == 1 and report.shipped == 1 and report.unreachable == 0
    landed_pages = list((brain / "_inbound" / "candidates").glob("*.md"))
    assert len(landed_pages) == 1
    landed_fm, _ = pages.read(landed_pages[0])
    assert landed_fm["released_from"] == "ada" and landed_fm["subject"] == "harbor"
    assert queue_paths(ada) == []


def test_brain_destination_is_cloned_through_the_brain_mount(tmp_path, monkeypatch):
    """Releases to the brain are addressed to the brain's name while its mount is called
    `brain`: the first release still finds the repository and clones it."""
    monkeypatch.setenv("SPACES_LOCAL_CLONES", "1")   # the bare repositories are local paths
    brain = make_space(tmp_path / "brain", profile="brain", name="lantern", subjects=[
        {"id": "pricing", "kind": "topics", "title": "Pricing", "home": "lantern",
         "match": {"label": "Pricing"}}])
    ada = make_space(tmp_path / "ada", name="ada", sources=[{"name": "mail", "adapter": "gmail", "audience": "everyone"}])
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
    assert report.shipped == 1 and report.unreachable == 0
    assert (spaces_home / "lantern" / ".git").exists()
    assert list((spaces_home / "lantern" / "_inbound").rglob("*"))


def test_live_source_waits_while_the_destination_looks_shadow(tmp_path):
    """A live space never holds an item only because a destination still runs shadow: the
    destination may be a stale view, so the item waits in the queue and ships once it is live."""
    _, harbor, ada = build(tmp_path, ada_mode="live", harbor_mode="shadow",
                           sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.held == 0 and report.shipped == 0 and report.waiting >= 1
    assert len(queue_paths(ada)) == 1 and held_items(ada) == []
    data = yaml.safe_load((harbor / "space.yaml").read_text(encoding="utf-8"))
    data["mode"] = "live"
    data.pop("time_scale", None)
    (harbor / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.shipped == 1 and queue_paths(ada) == []


def test_items_held_for_a_shadow_destination_return_to_the_queue(tmp_path):
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item,) = queue_paths(ada)
    fm, body = pages.read(item)
    release.hold(ada, fm, body, "mode", "this space runs live, the destination harbor runs shadow")
    item.unlink()
    report = release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert report.requeued == 1 and report.shipped == 1 and held_items(ada) == []


def test_a_doubt_page_held_before_recommendations_gets_one_on_the_next_release(tmp_path):
    """A page held in doubt before holds carried a recommendation gets the hold's recommendation
    and default written on it; a page with its own recommendation keeps it."""
    _, harbor, ada = build(tmp_path)
    held = ada / "_outbound" / "held"
    pages.write(held / "old.md", {"id": "old", "reason": "doubt", "why": "privacy doubt"}, "old body\n")
    pages.write(held / "own.md", {"id": "own", "reason": "doubt", "why": "privacy doubt",
                                  "recommendation": "yes: send it", "default": "goes"}, "own body\n")
    release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    old, _ = pages.read(held / "old.md")
    own, _ = pages.read(held / "own.md")
    assert (old["recommendation"], old["default"]) == ("no: keep it home", "stays home")
    assert (own["recommendation"], own["default"]) == ("yes: send it", "goes")


def test_refresh_checkout_reads_the_destination_as_it_is_now(tmp_path):
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]
    origin = make_space(tmp_path / "harbor", profile="project", name="harbor", mode="shadow", time_scale=48)
    subprocess.run(git + ["init", "--quiet", "-b", "main"], cwd=origin, check=True)
    subprocess.run(git + ["add", "-A"], cwd=origin, check=True)
    subprocess.run(git + ["commit", "--quiet", "-m", "x"], cwd=origin, check=True)
    bare = tmp_path / "harbor.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(origin), str(bare)], check=True)
    clone = tmp_path / "home" / "harbor"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(clone)], check=True)
    data = yaml.safe_load((origin / "space.yaml").read_text(encoding="utf-8"))
    data["mode"] = "live"
    data.pop("time_scale", None)
    (origin / "space.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    subprocess.run(git + ["commit", "--quiet", "-am", "live"], cwd=origin, check=True)
    subprocess.run(["git", "push", "--quiet", str(bare), "main"], cwd=origin, check=True)
    assert release.refresh_checkout(clone) is True
    assert yaml.safe_load((clone / "space.yaml").read_text(encoding="utf-8"))["mode"] == "live"


def test_signal_follows_its_subject_to_a_new_home(tmp_path):
    """A signal addressed to a space that was offboarded since goes to the subject's current
    home, like a queue item, instead of waiting forever."""
    brain, _, ada = build(tmp_path)
    signals = ada / "_outbound" / "signals"
    signals.mkdir(parents=True, exist_ok=True)
    (signals / "p-0000c001.yaml").write_text(yaml.safe_dump(
        {"id": "p-0000c001", "destination": "harbor", "subject": "harbor", "at": pages.iso(NOW)}),
        encoding="utf-8")
    reg_path = brain / "_meta" / "subjects.yaml"
    data = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    for subject in data["subjects"]:
        if subject["id"] == "harbor":   # the home moves, and its classification with it
            subject["home"] = "brain"
            subject["classification"] = dict(subject["classification"], home="brain", **{"class": "company"})
    reg_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    report = release.ship(ada, now=NOW, offline_roots={"brain": brain})
    assert report.rerouted == 1 and report.signals == 1
    assert list((brain / "_inbound" / "signals").glob("*.yaml"))


def test_the_ship_scrub_holds_a_file_too_large_to_scan(tmp_path, monkeypatch):
    import scan
    space_root = make_space(tmp_path / "ada", name="ada")
    path = space_root / "_outbound" / "queue" / "p-0000ff01.md"
    fm = {"id": "p-0000ff01", "title": "Big", "destination": "harbor"}
    pages.write(path, fm, "word " * 200)
    monkeypatch.setattr(scan, "MAX_BYTES", 100)
    import spaceyaml
    reason = release.scrub_reason(spaceyaml.load(space_root), None, fm, "word " * 200, path)
    assert reason and reason[0] == "scan"


# ----- a retraction takes effect where the release landed ---------------------------------
def _evidence_ids(root):
    import distill
    return {ev.fm.get("id") for ev in distill.gather_evidence(root, distill.Reader(), "harbor")}


def _shipped_and_landed(tmp_path):
    import landing
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    (shipped_fm, _), = landed(harbor)
    landing.land(harbor, now=NOW)
    return harbor, ada, shipped_fm["id"]


def test_a_retraction_withdraws_a_record_that_already_landed(tmp_path):
    import landing
    harbor, ada, item_id = _shipped_and_landed(tmp_path)
    filed, = (harbor / "knowledge" / "sources").rglob(f"{item_id}.md")
    assert item_id in _evidence_ids(harbor)
    later = NOW + dt.timedelta(hours=2)
    release.retract(ada, item_id, now=later)
    release.ship(ada, now=later, offline_roots={"harbor": harbor})
    landing.land(harbor, now=later)
    fm, _ = pages.read(filed)
    assert fm["retracted"] is True and fm["status"] == "deprecated", "the landed record is withdrawn, kept"
    assert item_id not in _evidence_ids(harbor), "a withdrawn record is no longer evidence"
    kinds = [pages.read(p)[0].get("release_class") for p in (harbor / "knowledge" / "sources").rglob("*.md")]
    assert "retraction" not in kinds, "the retraction itself is never filed as a record"
    assert landed(harbor) == []
    assert f"withdrew {item_id}" in (harbor / "log.md").read_text(encoding="utf-8")


def test_a_retraction_before_landing_keeps_the_record_out(tmp_path):
    import landing
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    (shipped_fm, _), = landed(harbor)
    release.retract(ada, shipped_fm["id"], now=NOW, offline_roots={"harbor": harbor})
    release.ship(ada, now=NOW, offline_roots={"harbor": harbor})
    assert shipped_fm["id"] not in _evidence_ids(harbor), "marked retracted: not evidence while it waits"
    landing.land(harbor, now=NOW)
    assert landed(harbor) == []
    assert not list((harbor / "knowledge" / "sources").rglob("*.md")), "neither the record nor its retraction lands"
    assert f"dropped {shipped_fm['id']}" in (harbor / "log.md").read_text(encoding="utf-8")


def test_a_record_withdrawn_at_home_is_no_longer_evidence(tmp_path):
    import landing
    harbor, _, item_id = _shipped_and_landed(tmp_path)
    landing.retract(harbor, item_id, by="Ben Liu", now=NOW + dt.timedelta(hours=1))
    assert item_id not in _evidence_ids(harbor)


def _online_harbor(tmp_path, monkeypatch, states):
    """ada with a release clone of harbor; publish opens pull request #41, and its state is read
    from `states` (one answer per question)."""
    _, harbor, ada = build(tmp_path, sources=CREW_MAIL)
    spaces_home = tmp_path / "spaces-home"
    checkout = spaces_home / "harbor"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "space.yaml").write_text((harbor / "space.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("SPACES_HOME", str(spaces_home))
    monkeypatch.setattr(release, "publish", lambda checkout_path, slug, source, write: (write(), 41)[1])
    asked = []
    monkeypatch.setattr(release, "pr_state", lambda checkout_path, number: asked.append(number) or states.pop(0))
    return ada, asked


def _released_lines(root):
    path = root / "_meta" / "state" / "released.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_a_release_counts_as_released_only_once_its_pull_request_merged(tmp_path, monkeypatch):
    # Seen live: a release was logged as released once its pull request existed; the pull request
    # failed its checks and never carried the pages, and the owner's space believed they shipped.
    ada, asked = _online_harbor(tmp_path, monkeypatch, ["OPEN", "MERGED"])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item_id,) = [pages.read(p)[0]["id"] for p in queue_paths(ada)]
    release.ship(ada, now=NOW)
    assert queue_paths(ada) == [] and _released_lines(ada) == []
    log = (ada / "log.md").read_text(encoding="utf-8")
    assert f"sent {item_id} to harbor in pull request #41" in log and f"released {item_id}" not in log
    assert (ada / "_outbound" / "sent" / f"{item_id}.md").exists()
    release.ship(ada, now=NOW + dt.timedelta(minutes=15))   # still open: it waits
    assert _released_lines(ada) == [] and asked == [41]
    release.ship(ada, now=NOW + dt.timedelta(minutes=30))   # merged: now it is released
    (line,) = _released_lines(ada)
    assert json.loads(line)["id"] == item_id and json.loads(line)["destination"] == "harbor"
    assert f"released {item_id} to harbor" in (ada / "log.md").read_text(encoding="utf-8")
    assert not (ada / "_outbound" / "sent" / f"{item_id}.md").exists()


def test_a_release_whose_pull_request_closed_unmerged_comes_back_to_its_owner(tmp_path, monkeypatch):
    ada, _ = _online_harbor(tmp_path, monkeypatch, ["CLOSED"])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item_id,) = [pages.read(p)[0]["id"] for p in queue_paths(ada)]
    release.ship(ada, now=NOW)
    release.ship(ada, now=NOW + dt.timedelta(minutes=15))
    assert _released_lines(ada) == [] and not list((ada / "_outbound" / "sent").glob("*.md"))
    (fm, _), = held_items(ada)
    assert fm["id"] == item_id and fm["reason"] == "not landed" and "#41" in fm["why"]
    assert not any(k in fm for k in ("sent_at", "sent_to", "pull_request"))
    rows = release.held_health(ada)
    assert any(r["id"] == item_id and "did not land" in r["text"] for r in rows), rows
    assert release.requeue(ada, item_id) == ada / "_outbound" / "queue" / f"{item_id}.md"


def test_a_release_still_unmerged_after_a_day_is_on_the_health_line(tmp_path, monkeypatch):
    ada, _ = _online_harbor(tmp_path, monkeypatch, ["OPEN"])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item_id,) = [pages.read(p)[0]["id"] for p in queue_paths(ada)]
    release.ship(ada, now=NOW)
    assert release.held_health(ada, now=NOW + dt.timedelta(hours=2)) == []
    rows = release.held_health(ada, now=NOW + dt.timedelta(days=2))
    assert any(r["id"] == item_id and "#41" in r["text"] and "not merged" in r["text"] for r in rows), rows


def test_an_item_still_in_its_pull_request_can_be_retracted(tmp_path, monkeypatch):
    ada, _ = _online_harbor(tmp_path, monkeypatch, ["OPEN", "OPEN"])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item_id,) = [pages.read(p)[0]["id"] for p in queue_paths(ada)]
    release.ship(ada, now=NOW)
    assert release.released_record(ada, item_id)["destination"] == "harbor"
    release.retract(ada, item_id, now=NOW)
    (fm, _), = [pages.read(p) for p in queue_paths(ada)]
    assert fm["class"] == "retraction" and fm["retracts"] == item_id and fm["destination"] == "harbor"


def test_an_item_sent_without_a_pull_request_number_comes_back_to_its_owner(tmp_path, monkeypatch):
    ada, asked = _online_harbor(tmp_path, monkeypatch, [])
    monkeypatch.setattr(release, "publish", lambda checkout_path, slug, source, write: (write(), None)[1])
    write_feed(ada, [record()])
    sweep.run(ada, now=NOW)
    (item_id,) = [pages.read(p)[0]["id"] for p in queue_paths(ada)]
    release.ship(ada, now=NOW)
    release.ship(ada, now=NOW + dt.timedelta(minutes=15))
    assert asked == [] and not list((ada / "_outbound" / "sent").glob("*.md"))
    (fm, _), = held_items(ada)
    assert fm["id"] == item_id and fm["reason"] == "not landed" and "number" in fm["why"]
