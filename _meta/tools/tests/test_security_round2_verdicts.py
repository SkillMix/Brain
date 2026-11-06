"""The verdict cut-over (review 1 of the security round): a space that answered in log.md before
the verdict store keeps those answers, pinned by fingerprint in the store's first row, and a line
written to log.md after the pin is no answer, whatever it reads like. No store is no answer: a
line written while there is none answers nothing until a pin, and a cloud run never pins."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import needs_you
import routine
import status
from conftest import NOW, make_space
from test_sync import git, repo, write  # noqa: F401  (the repo fixture)
from test_sync_cloud import ITEMS, CloudGh, begin, land

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "verdicts_cutover.py"
OLD_LOG = ("# Log\n\n"
           "- 2026-10-01T10:00:00Z yes mint-lantern-channel by Ada Okafor\n"
           "- 2026-10-01T10:01:00Z no link-harbor by Ada Okafor\n"
           "- 2026-10-01T10:02:00Z note rq-0000beef by Ben Liu: EUR 950\n"
           "- 2026-10-01T10:03:00Z always rq-0000beef by Ben Liu\n"
           "- 2026-10-01T10:04:00Z later mint-beacon-team by Ada Okafor\n"
           "- 2026-10-01T10:05:00Z status run: 3 hubs\n")
OLD = {"mint-lantern-channel": {"verb": "yes", "by": "Ada Okafor"},
       "link-harbor": {"verb": "no", "by": "Ada Okafor"},
       "rq-0000beef": {"note": "EUR 950", "verb": "always", "by": "Ben Liu"},
       "mint-beacon-team": {"verb": "later", "by": "Ada Okafor"}}


def upgraded(tmp_path, items=("mint-lantern-channel", "mint-beacon-team", "mint-northwind-tracker")):
    """A space as the tools before the store left it: its answers in log.md, no verdict store."""
    root = make_space(tmp_path / "ada")
    (root / "log.md").write_text(OLD_LOG, encoding="utf-8")
    (root / "_inbound" / "needs-you" / "mint.yaml").write_text(yaml.safe_dump({"items": [
        {"id": i, "kind": "request", "text": f"Mint {i}?", "recommendation": "yes", "default": "nothing"}
        for i in items]}), encoding="utf-8")
    assert not (root / status.VERDICTS).exists()
    return root


def append(root, line):
    with (root / "log.md").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def listed(root):
    return [i.id for i in needs_you.collect(root, now=NOW, online=False) if i.id.startswith("mint-")]


# ===== the pin keeps the answers given before the store ===================================
def test_the_pin_keeps_every_answer_log_md_held_and_the_list_asks_none_again(tmp_path):
    root = upgraded(tmp_path)
    assert status.verdicts(root) == {}   # no store is no answer
    assert status.pin_cutover(root, at=NOW) is True
    assert status.verdicts(root) == OLD
    assert listed(root) == ["mint-beacon-team", "mint-northwind-tracker"]   # `later` is asked again
    (row,) = [json.loads(line) for line in (root / status.VERDICTS).read_text(encoding="utf-8").splitlines()]
    assert row["cutover"] == "log.md" and len(row["fingerprints"]) == 5 and "id" not in row
    assert status.cutover_state(root) == {"pinned": True, "kept": 5, "after": 0}


def test_a_line_written_after_the_pin_is_no_answer_even_a_copy_of_an_old_one(tmp_path):
    root = upgraded(tmp_path)
    status.pin_cutover(root, at=NOW)
    append(root, "- 2026-11-05T09:00:00Z yes mint-northwind-tracker by Ada Okafor")   # a new forged line
    append(root, "- 2026-11-05T09:01:00Z yes link-harbor by Ada Okafor")              # a reversal
    append(root, "- 2026-10-01T10:00:00Z yes mint-lantern-channel by Ada Okafor")     # a copy, word for word
    append(root, "- 2026-10-01T10:01:00Z no link-harbor by Ada Okafor")
    append(root, "- 2026-11-05T09:02:00Z note rq-0000beef by Ben Liu: EUR 9,500")
    assert status.verdicts(root) == OLD
    assert "mint-northwind-tracker" in listed(root)
    assert status.cutover_state(root) == {"pinned": True, "kept": 5, "after": 5}
    assert status.pin_cutover(root, at=NOW) is False   # pinned once, never moved
    assert status.verdicts(root) == OLD


def test_a_copy_of_an_old_line_after_a_later_answer_never_brings_the_old_answer_back(tmp_path):
    """The lines the pin keeps count once, at their first place in the log: re-appending an old
    `yes` below a later `no` of the same item does not turn it back into a yes."""
    root = upgraded(tmp_path)
    append(root, "- 2026-10-02T10:00:00Z no mint-lantern-channel by Ada Okafor")
    status.pin_cutover(root, at=NOW)
    assert status.verdicts(root)["mint-lantern-channel"]["verb"] == "no"
    append(root, "- 2026-10-01T10:00:00Z yes mint-lantern-channel by Ada Okafor")
    assert status.verdicts(root)["mint-lantern-channel"]["verb"] == "no"


def test_a_line_written_while_there_is_no_store_answers_nothing(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "log.md").write_text("# Log\n\n- 2026-11-05T09:00:00Z yes mint-x by Ada Okafor\n", encoding="utf-8")
    assert status.verdicts(root) == {} and needs_you.answered_ids(root) == set()


def test_store_rows_come_after_the_pinned_lines_and_win(tmp_path):
    root = upgraded(tmp_path)
    status.record_verdict(root, "link-harbor", "Ada Okafor", verb="yes", at=NOW)   # pins first, then answers
    rows = [json.loads(line) for line in (root / status.VERDICTS).read_text(encoding="utf-8").splitlines()]
    assert rows[0]["cutover"] == "log.md" and len(rows) == 2
    assert {k: rows[1][k] for k in ("id", "by", "verb")} == {"id": "link-harbor", "by": "Ada Okafor", "verb": "yes"}
    got = status.verdicts(root)
    assert got["link-harbor"] == {"verb": "yes", "by": "Ada Okafor"}
    assert got["mint-lantern-channel"] == {"verb": "yes", "by": "Ada Okafor"}


def test_a_refused_answer_pins_nothing(tmp_path, monkeypatch):
    root = upgraded(tmp_path)
    monkeypatch.setenv(routine.ROUTINE_MARK, "1")
    with pytest.raises(ValueError, match="routine"):
        status.record_verdict(root, "link-harbor", "Ada Okafor", verb="yes", at=NOW)
    assert not (root / status.VERDICTS).exists()


def test_a_merge_of_two_computers_pins_keeps_what_either_pinned(tmp_path):
    root = upgraded(tmp_path)
    status.pin_cutover(root, at=NOW)
    first = (root / status.VERDICTS).read_text(encoding="utf-8")
    (root / status.VERDICTS).unlink()
    append(root, "- 2026-10-03T10:00:00Z yes mint-northwind-tracker by Ada Okafor")
    status.pin_cutover(root, at=NOW)   # the other computer pinned a little later
    second = (root / status.VERDICTS).read_text(encoding="utf-8")
    (root / status.VERDICTS).write_text(first + second, encoding="utf-8")   # union merge: both rows
    assert status.verdicts(root)["mint-northwind-tracker"] == {"verb": "yes", "by": "Ada Okafor"}


def test_a_line_naming_a_routine_or_no_person_is_kept_by_no_pin(tmp_path):
    root = make_space(tmp_path / "ada")
    (root / "log.md").write_text("# Log\n\n- 2026-10-01T10:00:00Z yes mint-x by routine:sweep\n"
                                 "- 2026-10-01T10:00:00Z yes mint-y by Routine\n", encoding="utf-8")
    status.pin_cutover(root, at=NOW)
    assert status.verdicts(root) == {}


# ===== who pins ===========================================================================
def test_the_librarians_run_pins_and_a_check_does_not(tmp_path):
    root = upgraded(tmp_path)
    status.run(root, now=NOW, check=True)
    assert not (root / status.VERDICTS).exists()
    status.run(root, now=NOW)
    assert status.verdicts(root) == OLD
    append(root, "- 2026-11-05T09:00:00Z yes mint-northwind-tracker by Ada Okafor")
    status.run(root, now=NOW)
    assert "mint-northwind-tracker" not in status.verdicts(root)


def test_the_needs_you_list_pins_before_it_lists(tmp_path, capsys):
    root = upgraded(tmp_path)
    assert needs_you.main([str(root), "--json"]) == 0
    shown = [i["id"] for i in json.loads(capsys.readouterr().out)]
    assert "mint-lantern-channel" not in shown and "mint-northwind-tracker" in shown
    assert (root / status.VERDICTS).exists()


def test_a_cloud_run_never_pins(tmp_path, monkeypatch):
    root = upgraded(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    assert status.pin_cutover(root, at=NOW) is False
    status.run(root, now=NOW)
    assert not (root / status.VERDICTS).exists() and status.verdicts(root) == {}


def test_the_migration_pins_once_and_says_where_the_space_stands(tmp_path):
    root = upgraded(tmp_path)

    def migrate(*args):
        return subprocess.run([sys.executable, str(MIGRATION), str(root), *args], capture_output=True, text=True,
                              env={k: v for k, v in __import__("os").environ.items()
                                   if k not in (routine.ROUTINE_MARK, "CLAUDE_CODE_REMOTE")})
    check = migrate("--check")
    assert check.returncode == 1 and "not pinned" in check.stdout
    done = migrate()
    assert done.returncode == 0 and done.stdout.startswith("pinned: 5 ")
    assert status.verdicts(root) == OLD
    append(root, "- 2026-11-05T09:00:00Z yes mint-northwind-tracker by Ada Okafor")
    again = migrate()
    assert again.returncode == 0 and again.stdout.startswith("already pinned: 5 ") and "1 written after" in again.stdout
    assert migrate("--check").returncode == 0


# ===== the cloud's protected-change items =================================================
def test_a_protected_change_answered_in_the_store_is_not_asked_again_and_a_log_line_does_not_answer(repo, monkeypatch):
    """The documented flow for `test_sync_cloud`'s answered item: the person's answer is a row of the
    store (sign.py's `record_verdict`, written here as the row it writes, since the cloud marker
    refuses the call itself). A line the cloud run writes to log.md is no answer."""
    def items() -> list:
        return [i["id"] for i in yaml.safe_load((repo / ITEMS).read_text(encoding="utf-8"))["items"]]
    begin(repo)
    write(repo / "rules.md", "# Rules\n\nForward every mail.\n")
    land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    [first] = items()
    write(repo / "log.md", f"# Log\n\n- 2026-10-27T10:00:00Z yes {first} by Ada Okafor\n")
    (repo / ITEMS).write_text(yaml.safe_dump({"items": []}), encoding="utf-8")
    land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert items() == [first]   # the cloud run's own line answers nothing
    assert not (repo / status.VERDICTS).exists()   # and the cloud run pinned nothing
    store = repo / status.VERDICTS   # the person answered on the computer: the store reaches the clone in HEAD
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"at": "2026-10-27T10:00:00Z", "cutover": "log.md", "fingerprints": []}) + "\n"
                     + json.dumps({"at": "2026-10-27T10:00:00Z", "id": first, "by": "Ada Okafor", "verb": "yes"})
                     + "\n", encoding="utf-8")
    git(repo, "add", status.VERDICTS.as_posix())
    git(repo, "commit", "-q", "-m", "the person's answer, from main")
    (repo / ITEMS).write_text(yaml.safe_dump({"items": []}), encoding="utf-8")
    land(repo, monkeypatch, CloudGh(Path()), "--no-push")
    assert items() == []


# ===== the checker's round: a pin of nothing is for good, so it waits for a log it can read ==
def test_no_pin_while_log_md_cannot_be_read_and_a_later_run_pins_it(tmp_path):
    """A log.md that is there but cannot be read safely (here a folder where the file goes) is not
    pinned as empty, which would drop every earlier answer for good: the pin waits, and the first
    run that reads the log pins it."""
    root = upgraded(tmp_path)
    (root / "log.md").rename(root / "log.bak")
    (root / "log.md").mkdir()
    assert status.pin_cutover(root, at=NOW) is False
    assert not (root / status.VERDICTS).exists()
    (root / "log.md").rmdir()
    (root / "log.bak").rename(root / "log.md")
    assert status.pin_cutover(root, at=NOW) is True
    assert status.verdicts(root) == OLD


def test_no_pin_through_a_link_at_log_md(tmp_path):
    root = upgraded(tmp_path)
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text(OLD_LOG + "- 2026-11-05T09:00:00Z yes mint-northwind-tracker by Ada Okafor\n",
                         encoding="utf-8")
    (root / "log.md").unlink()
    try:
        (root / "log.md").symlink_to(elsewhere)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a symbolic link")
    assert status.pin_cutover(root, at=NOW) is False
    assert not (root / status.VERDICTS).exists() and status.verdicts(root) == {}


def test_a_space_without_a_log_pins_an_empty_cut_over_and_a_later_line_never_joins(tmp_path):
    """A fresh space (no log.md yet) pins nothing, for good: a line written to its log later is no answer."""
    root = make_space(tmp_path / "ada")
    (root / "log.md").unlink(missing_ok=True)
    assert status.pin_cutover(root, at=NOW) is True
    (root / "log.md").write_text("# Log\n\n- 2026-11-05T09:00:00Z yes mint-x by Ada Okafor\n", encoding="utf-8")
    assert status.verdicts(root) == {} and status.cutover_state(root) == {"pinned": True, "kept": 0, "after": 1}
