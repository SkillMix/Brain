import datetime as dt
import json
import os
import types
from pathlib import Path

import pytest
import yaml

import chain
import spaceyaml
from conftest import NOW, make_space

TEMPLATE = Path(__file__).resolve().parents[3]
FIXTURE = TEMPLATE / "_meta" / "tests" / "fixture"
# The fixture company is the template's own test data; an instance keeps whatever copy it
# first received (template sync never carries it), so its shape tests run in the template only.
template_only = pytest.mark.skipif(not (TEMPLATE / ".github" / "template-marker").exists(),
                                   reason="the fixture company is the template's own test data")

FEED_KEYS = {"id", "source", "kind", "at", "from", "to", "subject", "text", "identifiers",
             "attachments", "audience"}
IDENTIFIER_KEYS = {"thread", "channel", "labels", "repo", "tracker", "calendar"}


class Fakes:
    """Fake tool modules whose run functions record (name, root, now, SPACE_NOW)."""

    def __init__(self, failing=()):
        self.calls = []
        self.failing = set(failing)
        self.modules = {
            "sweep": types.SimpleNamespace(run=self._fn("sweep")),
            "distill": types.SimpleNamespace(run=self._fn("distill")),
            "check": types.SimpleNamespace(run=self._fn("check")),
            "status": types.SimpleNamespace(run=self._fn("status")),
            "release": types.SimpleNamespace(ship=self._fn("release")),
            "morning_view": types.SimpleNamespace(run=self._fn("morning_view")),
            "tests_run": types.SimpleNamespace(answers=self._fn("answers"),
                                               leaks=self._fn("leaks")),
            "sign": types.SimpleNamespace(apply=self._fn("sign")),
        }

    def _fn(self, name):
        def run(root, *args, **kwargs):
            self.calls.append((name, Path(root), kwargs.get("now"), os.environ.get("SPACE_NOW"),
                               args, kwargs))
            if name in self.failing:
                raise RuntimeError(f"{name} broke")
            return {"ran": name}
        return run

    def module(self, name):
        return self.modules.get(name)

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def fakes(monkeypatch):
    fake = Fakes()
    monkeypatch.setattr(chain, "_module", fake.module)
    return fake


def test_sweep_chain_runs_sweep_distill_check_in_order(tmp_path, fakes):
    root = make_space(tmp_path / "ada")
    result = chain.sweep_chain(root, now=NOW)
    assert fakes.names() == ["sweep", "distill", "check"]
    assert all(call[2] == NOW for call in fakes.calls)
    assert result["errors"] == []
    # a personal space also reads email replies to its briefing (no fake here: skipped)
    assert set(result["steps"]) == {"journal_records", "sweep", "reply", "distill", "check", "tracker_observe"}
    lines = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    line = json.loads(lines[-1])
    assert line["routine"] == "sweep_chain" and line["at"] == "2026-10-12T09:00:00Z"
    assert {"space", "cost_tokens", "notes"} <= set(line)


def test_a_personal_sweep_chain_writes_journal_records_before_the_sweep(tmp_path, fakes):
    fakes.modules["link"] = types.SimpleNamespace(journal_records=fakes._fn("journal_records"))
    chain.sweep_chain(make_space(tmp_path / "ada"), now=NOW)
    assert fakes.names() == ["journal_records", "sweep", "distill", "check"]
    fakes.calls.clear()
    chain.sweep_chain(make_space(tmp_path / "harbor", profile="project", name="harbor"), now=NOW)
    assert fakes.names() == ["sweep", "distill", "check"]


def test_a_failing_step_is_recorded_and_the_chain_continues(tmp_path, monkeypatch):
    fake = Fakes(failing={"distill"})
    monkeypatch.setattr(chain, "_module", fake.module)
    root = make_space(tmp_path / "ada")
    result = chain.sweep_chain(root, now=NOW)
    assert fake.names() == ["sweep", "distill", "check"]
    assert result["errors"] == [{"step": "distill", "error": "distill broke"}]
    assert result["steps"]["distill"] is None


def test_a_missing_tool_is_skipped_with_a_note(tmp_path, fakes):
    del fakes.modules["check"]
    root = make_space(tmp_path / "ada")
    result = chain.sweep_chain(root, now=NOW)
    assert fakes.names() == ["sweep", "distill"]
    assert result["errors"] == []
    assert any("check" in note and "not present" in note for note in result["skipped"])


def test_daily_order_and_tests_only_when_the_files_exist(tmp_path, fakes):
    root = make_space(tmp_path / "ada")
    result = chain.daily(root, "Ada Okafor", now=NOW, shadow=True,
                         offline_roots={"harbor": tmp_path / "harbor"})
    assert fakes.names() == ["status", "release", "morning_view"]
    release_call = [c for c in fakes.calls if c[0] == "release"][0]
    assert release_call[5]["offline_roots"] == {"harbor": tmp_path / "harbor"}
    morning_call = [c for c in fakes.calls if c[0] == "morning_view"][0]
    assert morning_call[4] == ("Ada Okafor",) and morning_call[5]["shadow"] is True
    assert result["errors"] == []
    fakes.calls.clear()
    (root / "_meta" / "tests").mkdir(parents=True)
    (root / "_meta" / "tests" / "answers.yaml").write_text("questions: []\n", encoding="utf-8")
    (root / "_meta" / "tests" / "leaks.yaml").write_text("leaks: []\n", encoding="utf-8")
    chain.daily(root, "Ada Okafor", now=NOW)
    assert fakes.names() == ["status", "release", "morning_view", "answers", "leaks"]


def test_daily_checks_codebase_links_right_after_access(tmp_path, fakes):
    fakes.modules["access"] = types.SimpleNamespace(run=fakes._fn("access"))
    fakes.modules["link"] = types.SimpleNamespace(check=fakes._fn("link_check"))
    root = make_space(tmp_path / "ada")
    result = chain.daily(root, "Ada Okafor", now=NOW)
    assert fakes.names() == ["status", "access", "link_check", "release", "morning_view"]
    assert [c[2] for c in fakes.calls if c[0] == "link_check"] == [NOW]
    assert result["steps"]["link_check"] == {"ran": "link_check"}


def test_a_step_that_fails_in_the_daily_run_shows_in_the_same_mornings_view(tmp_path, monkeypatch):
    root = make_space(tmp_path / "ada")
    real = chain._module

    def broken(name):
        if name == "release":
            def ship(*a, **k):
                raise RuntimeError("no connector")
            return types.SimpleNamespace(ship=ship)
        return real(name)
    monkeypatch.setattr(chain, "_module", broken)
    result = chain.daily(root, "Ada Okafor", now=NOW)
    assert result["errors"] == [{"step": "release", "error": "no connector"}]
    assert result["steps"]["morning_view"] is not None
    page = (root / "live" / "needs-you.md").read_text(encoding="utf-8")
    assert "stale: release since 2026-10-12" in page
    # the next run clears it as the step ends, before the morning view reads it
    monkeypatch.setattr(chain, "_module", real)
    chain.daily(root, "Ada Okafor", now=NOW + dt.timedelta(hours=1))
    assert "release" not in (yaml.safe_load((root / "_meta/state/stale.yaml").read_text(encoding="utf-8")) or {})
    assert "stale: release" not in (root / "live" / "needs-you.md").read_text(encoding="utf-8")


def test_virtual_counts_steps_and_restores_space_now(tmp_path, fakes, monkeypatch):
    roots = [make_space(tmp_path / "ada", mode="fixture", time_scale=1440),
             make_space(tmp_path / "ben", mode="fixture", time_scale=1440, owner="Ben Liu")]
    sweeps, dailies = [], []
    monkeypatch.setattr(chain, "sweep_chain",
                        lambda root, now=None: sweeps.append((root, os.environ["SPACE_NOW"])) or {})
    monkeypatch.setattr(chain, "daily",
                        lambda root, person, now=None, shadow=False, offline_roots=None:
                        dailies.append((root, person, os.environ["SPACE_NOW"])) or {})
    monkeypatch.delenv("SPACE_NOW", raising=False)
    result = chain.virtual(roots, days=2, steps_per_day=3, persons={"ben": "Ben Liu"})
    assert len(sweeps) == 12 and len(dailies) == 4
    assert result["sweep_chains"] == 12 and result["dailies"] == 4 and result["errors"] == []
    assert "SPACE_NOW" not in os.environ
    # time_scale 1440: a day is 60 seconds, a step 20 seconds.
    stamps = sorted({spaceyaml.parse_iso(s[1]) for s in sweeps})
    assert len(stamps) == 6
    assert (stamps[1] - stamps[0]).total_seconds() == 20
    assert (stamps[3] - stamps[0]).total_seconds() == 60
    persons = {d[1] for d in dailies}
    assert persons == {"Ada Okafor", "Ben Liu"}


def test_virtual_keeps_a_preset_space_now_and_applies_verdicts(tmp_path, fakes, monkeypatch):
    ada = make_space(tmp_path / "ada", mode="fixture", time_scale=1440)
    monkeypatch.setenv("SPACE_NOW", "2026-10-05T00:00:00Z")
    order = []
    monkeypatch.setattr(chain, "sweep_chain", lambda root, now=None: order.append("sweep") or {})
    monkeypatch.setattr(chain, "daily",
                        lambda root, person, now=None, shadow=False, offline_roots=None:
                        order.append("daily") or {})
    verdicts = {ada: {"harbor": ("yes", "")}}
    result = chain.virtual([ada], days=3, steps_per_day=2, verdicts_after_day=2,
                           verdicts=verdicts, by={ada: "Ada Okafor"})
    assert os.environ["SPACE_NOW"] == "2026-10-05T00:00:00Z"
    sign_calls = [c for c in fakes.calls if c[0] == "sign"]
    assert len(sign_calls) == 1
    assert sign_calls[0][4] == ({"harbor": ("yes", "")}, "Ada Okafor")
    assert result["verdicts_applied"] == [{"root": str(ada), "by": "Ada Okafor", "items": 1}]
    # Applied after the second daily: the first sweep after it is day 3's.
    assert order == ["sweep", "sweep", "daily", "sweep", "sweep", "daily", "sweep", "sweep", "daily"]
    assert sign_calls[0][3] == result["steps"][5]["at"]
    assert result["start"] == "2026-10-05T00:00:00Z"


def test_virtual_accepts_verdicts_and_signers_keyed_by_slug(tmp_path, fakes, monkeypatch):
    ada = make_space(tmp_path / "ada", mode="fixture", time_scale=1440)
    ben = make_space(tmp_path / "ben", mode="fixture", time_scale=1440, owner="Ben Liu")
    monkeypatch.setattr(chain, "sweep_chain", lambda root, now=None: {})
    monkeypatch.setattr(chain, "daily",
                        lambda root, person, now=None, shadow=False, offline_roots=None: {})
    # The raw fixture shapes: slugs as keys, a verb string or a YAML boolean as the value.
    result = chain.virtual([ada, ben], days=1, steps_per_day=1, verdicts_after_day=1,
                           verdicts={"ada": {"harbor": "yes"}, "ben": {"beacon": True}, "chloe": {"x": "no"}},
                           by={"ada": "Ada Okafor", "ben": "Ben Liu"})
    sign_calls = [c for c in fakes.calls if c[0] == "sign"]
    assert [(c[1].name, c[4]) for c in sign_calls] == [
        ("ada", ({"harbor": ("yes", "")}, "Ada Okafor")),
        ("ben", ({"beacon": ("yes", "")}, "Ben Liu"))]
    assert [v["items"] for v in result["verdicts_applied"]] == [1, 1]


def test_load_verdicts_maps_slugs_to_roots(tmp_path):
    ada = make_space(tmp_path / "ada")
    chloe = make_space(tmp_path / "chloe", owner="Chloe Martin")
    path = tmp_path / "verdicts.yaml"
    path.write_text("ada: {harbor: \"yes\", beacon: no}\nchloe: {sales: yes}\nben: {}\n",
                    encoding="utf-8")
    loaded = chain.load_verdicts(path, [ada, chloe])
    assert loaded[ada] == {"harbor": ("yes", ""), "beacon": ("no", "")}
    assert loaded[chloe] == {"sales": ("yes", "")}


def test_cli_sweep_and_daily(tmp_path, fakes):
    root = make_space(tmp_path / "ada")
    assert chain.main(["sweep", str(root)]) == 0
    assert chain.main(["daily", str(root), "--person", "Ada Okafor", "--shadow"]) == 0
    assert fakes.names() == ["sweep", "distill", "check", "status", "release", "morning_view"]


# ----- a cloud run: what it leaves to the computer (spec 4.8) ------------------------

def personal_space_with_all_steps(tmp_path, fakes) -> Path:
    """A personal space whose every chain step has a fake: transcripts to scan, an outbox, and a
    template sync that is due."""
    root = make_space(tmp_path / "ada", transcripts={"path": str(tmp_path / "sessions"), "switch": "on"})
    (root / "_meta" / "state" / "onboard.yaml").write_text("made: 2026-10-01\n", encoding="utf-8")
    for module, fn, name in (("transcripts", "scan", "transcripts"), ("reply", "run", "reply"),
                             ("outbox_reconcile", "run", "outbox_reconcile"),
                             ("tracker", "observe", "tracker_observe"), ("access", "run", "access"),
                             ("template_sync", "guarded", "template_sync")):
        fakes.modules[module] = types.SimpleNamespace(**{fn: fakes._fn(name)})
    fakes.modules["link"] = types.SimpleNamespace(journal_records=fakes._fn("journal_records"),
                                                  check=fakes._fn("link_check"))
    fakes.modules["request"] = types.SimpleNamespace(collect=fakes._fn("requests_collect"),
                                                     answer=fakes._fn("requests_answer"))
    return root


def test_a_cloud_sweep_leaves_transcripts_to_the_computer_and_reconciles_the_outbox(tmp_path, fakes):
    root = personal_space_with_all_steps(tmp_path, fakes)
    chain.sweep_chain(root, now=NOW)
    assert fakes.names() == ["transcripts", "journal_records", "sweep", "reply", "distill", "check",
                             "tracker_observe"]   # on the computer, as today
    fakes.calls.clear()
    result = chain.sweep_chain(root, now=NOW, cloud=True)
    assert fakes.names() == ["journal_records", "sweep", "reply", "outbox_reconcile", "distill", "check",
                             "tracker_observe"]
    assert "transcripts" not in result["steps"] and not any("transcripts" in s for s in result["skipped"])


def test_a_cloud_sweep_without_an_outbox_reconciles_nothing(tmp_path, fakes):
    root = make_space(tmp_path / "harbor", profile="project", name="harbor")
    fakes.modules["outbox_reconcile"] = types.SimpleNamespace(run=fakes._fn("outbox_reconcile"))
    chain.sweep_chain(root, now=NOW, cloud=True)
    assert fakes.names() == ["sweep", "distill", "check"]


def test_a_cloud_daily_leaves_release_requests_template_sync_and_outbox_to_the_computer(tmp_path, fakes):
    root = personal_space_with_all_steps(tmp_path, fakes)
    chain.daily(root, "Ada Okafor", now=NOW)
    assert fakes.names() == ["template_sync", "status", "access", "link_check", "requests_collect",
                             "requests_answer", "release", "outbox_reconcile", "morning_view"]   # as today
    fakes.calls.clear()
    result = chain.daily(root, "Ada Okafor", now=NOW, cloud=True)
    assert fakes.names() == ["status", "access", "link_check", "morning_view"]
    left = {"template_sync", "release", "requests_collect", "requests_answer", "outbox_reconcile"}
    assert not left & set(result["steps"]) and result["skipped"] == [] and result["errors"] == []


def test_a_cloud_daily_asks_the_link_check_without_graphql(tmp_path, fakes):
    root = personal_space_with_all_steps(tmp_path, fakes)
    chain.daily(root, "Ada Okafor", now=NOW)
    chain.daily(root, "Ada Okafor", now=NOW, cloud=True)
    computer, cloud = [c[5] for c in fakes.calls if c[0] == "link_check"]
    assert computer.get("graphql", True) is True and cloud["graphql"] is False
    assert computer["now"] == cloud["now"] == NOW


def test_cli_cloud_sweep_and_daily(tmp_path, fakes):
    root = personal_space_with_all_steps(tmp_path, fakes)
    assert chain.main(["sweep", str(root), "--cloud"]) == 0
    assert chain.main(["daily", str(root), "--person", "Ada Okafor", "--cloud"]) == 0
    assert fakes.names() == ["journal_records", "sweep", "reply", "outbox_reconcile", "distill", "check",
                             "tracker_observe", "status", "access", "link_check", "morning_view"]
    lines = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["routine"] for line in lines] == ["sweep_chain", "daily"]


# ----- the gate: the first step of every task the app's own scheduler starts ---------

CLOUD_TASKS = {"sweep": "trig_01AAAAAAAA", "daily": "trig_01BBBBBBBB"}
CLOUD_ROOTS = ["ada/ada-space"]
CLOUD_GRANT = {"read": ["mcp__mail__get_thread"], "write": [], "send": ["mcp__mail__send_message"]}


@pytest.fixture
def desktop(monkeypatch):
    """This computer is `m-desktop`, which its person knows as ADA-DESKTOP."""
    import routine
    monkeypatch.setattr(routine, "this_machine", lambda: "m-desktop")
    monkeypatch.setattr(routine, "machine_name", lambda: "ADA-DESKTOP")
    return routine


def test_the_gate_stops_a_computer_task_when_the_routines_run_in_the_cloud(tmp_path, capsys, desktop):
    ws = make_space(tmp_path / "ada")
    desktop.claim_cloud(ws, CLOUD_TASKS, "Europe/Amsterdam", CLOUD_ROOTS, CLOUD_GRANT, NOW)
    assert chain.gate(ws, NOW) == "your routines run in the cloud"
    assert chain.main(["gate", str(ws)]) == 3
    out = capsys.readouterr().out
    assert "your routines run in the cloud" in out and len(out.strip().splitlines()) == 1
    desktop.set_pause(ws, NOW + dt.timedelta(hours=8))
    assert chain.gate(ws, NOW) == "your routines run in the cloud"   # paused or not, no computer runs them
    assert not (ws / "_meta" / "state" / "runs.jsonl").exists()


def test_the_gate_lets_the_host_computer_run(tmp_path, capsys, desktop):
    ws = make_space(tmp_path / "ada")
    for runner in ("harness", "os"):   # the app's own tasks, or the OS tasks' prompt
        desktop.claim_host(ws, runner, NOW)
        assert chain.gate(ws, NOW) is None
        assert chain.main(["gate", str(ws)]) == 0
        assert capsys.readouterr().out == ""


def test_the_gate_stops_a_computer_that_is_not_the_host(tmp_path, capsys, desktop, monkeypatch):
    ws = make_space(tmp_path / "ada")
    desktop.claim_host(ws, "harness", NOW)
    monkeypatch.setattr(desktop, "this_machine", lambda: "m-laptop")
    assert chain.gate(ws, NOW) == "they run on ADA-DESKTOP"
    assert chain.main(["gate", str(ws)]) == 3
    assert "they run on ADA-DESKTOP" in capsys.readouterr().out


def test_the_gate_stops_a_paused_space(tmp_path, capsys, desktop):
    ws = make_space(tmp_path / "ada")
    desktop.claim_host(ws, "harness", NOW)
    desktop.set_pause(ws, NOW + dt.timedelta(hours=2))
    assert chain.gate(ws, NOW).startswith("paused until ")
    assert chain.gate(ws, NOW + dt.timedelta(hours=3)) is None   # the pause is over
    desktop.set_pause(ws, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2))
    assert chain.main(["gate", str(ws)]) == 3
    assert "paused until" in capsys.readouterr().out


def test_the_gate_stops_while_the_record_is_unclear(tmp_path, capsys, desktop):
    ws = make_space(tmp_path / "ada")
    (ws / desktop.HOST).write_text("machine: [\n", encoding="utf-8")
    assert chain.gate(ws, NOW) == desktop.UNCLEAR
    assert chain.main(["gate", str(ws)]) == 3
    assert desktop.UNCLEAR in capsys.readouterr().out


def test_without_a_record_the_gate_goes_on(tmp_path, capsys):
    ws = make_space(tmp_path / "ada")
    assert chain.gate(ws, NOW) is None
    assert chain.main(["gate", str(ws)]) == 0
    assert capsys.readouterr().out == ""


# ----- the fixture company --------------------------------------------------------

@template_only
def test_fixture_company_yaml_shape():
    company = yaml.safe_load((FIXTURE / "company.yaml").read_text(encoding="utf-8"))
    assert company["company"] == "Lantern Works"
    assert company["top_owner"]["slug"] == "ada"
    assert {p["slug"] for p in company["people"]} == {"ada", "ben", "chloe"}
    assert all(p["parent"] == "brain" for p in company["people"])
    assert all(p["email"].endswith("@lantern.example") for p in company["people"])
    assert {d["id"] for d in company["domains"]} == {"sales", "delivery"}
    projects = {p["id"]: p for p in company["projects"]}
    assert projects["harbor"]["client"] == "tidewater-ferries"
    assert projects["harbor"]["match"] == {"label": "Harbor", "channel": "#harbor",
                                           "repo": "lantern/harbor-site"}
    assert {s["id"] for s in company["subjects"]} >= {"harbor-site", "welcome-pack"}
    assert projects["beacon"]["crew"] == ["ben", "chloe"]
    assert company["standing_choices"] == {"ada": [{"destination": "harbor", "kind": "mail"}]}
    assert company["time_scale"] == 1440 and company["mode"] == "fixture"


@template_only
def test_fixture_feed_lines_parse_and_carry_the_record_shape():
    seen = set()
    by_person = {}
    for person in ("ada", "ben", "chloe"):
        path = FIXTURE / "feed" / f"{person}.jsonl"
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert lines, path
        by_person[person] = [json.loads(line) for line in lines]
        for record in by_person[person]:
            assert FEED_KEYS <= set(record), (person, record.get("id"))
            assert record["source"] in ("mail", "calls", "chat", "tracker")
            assert record["kind"] in spaceyaml.RECORD_KINDS
            assert record["audience"] in spaceyaml.AUDIENCE_WORDS
            assert IDENTIFIER_KEYS <= set(record["identifiers"])
            assert isinstance(record["identifiers"]["labels"], list)
            assert isinstance(record["to"], list) and isinstance(record["attachments"], list)
            stamp = spaceyaml.parse_iso(record["at"])
            assert (stamp.year, stamp.month) == (2026, 10) and stamp.day < 10
            if record["kind"] == "call":
                assert len(record["participants"]) >= 2
            assert record["id"] not in seen
            seen.add(record["id"])
            for address in [record["from"]] + record["to"]:
                assert "@" not in address or address.endswith(".example"), address
    ada = {r["subject"]: r for r in by_person["ada"]}
    assert any("3 November" in r["subject"] for r in by_person["ada"])
    assert any("day rate" in r["text"] for r in by_person["ada"])
    assert any(r["kind"] == "message" and "paid search ads" in r["text"]
               and not any(r["identifiers"][k] for k in IDENTIFIER_KEYS)
               for r in by_person["ada"])
    assert any(r["kind"] == "call" and r["identifiers"]["labels"] == ["Harbor"]
               for r in by_person["ada"])
    assert any(sorted(r["identifiers"]["labels"]) == ["Beacon", "Harbor"] for r in by_person["ada"])
    assert any("Q1 pilot" in r["text"] and r["identifiers"]["labels"] == []
               for r in by_person["chloe"])
    assert any("2026-11-10" in r["text"] and r["identifiers"]["labels"] == ["Beacon"]
               for r in by_person["chloe"])
    assert any(r["source"] == "tracker" and r["kind"] == "document"
               and r["audience"] == "everyone" and "BEACON-12" in r["text"]
               for r in by_person["ben"])
    assert ada  # the subjects index built without error


@template_only
def test_fixture_answers_leaks_and_verdicts_shape():
    answers = yaml.safe_load((FIXTURE / "answers.yaml").read_text(encoding="utf-8"))
    questions = {q["question"]: q for q in answers["questions"]}
    assert questions["When does Harbor go live?"]["expect"] == ["3 November"]
    assert questions["When does Harbor go live?"]["asker"] == "ada"
    assert questions["What did the clinic confirm?"]["subject"] == "beacon"
    assert questions["What does the prospect want?"]["subject"] == "sales"
    assert all(q["via"] in ("walk", "search", "both") for q in answers["questions"])
    leaks = yaml.safe_load((FIXTURE / "leaks.yaml").read_text(encoding="utf-8"))
    by_root = {}
    for leak in leaks["leaks"]:
        assert leak["reason"] and isinstance(leak["must_not_find"], list)
        by_root.setdefault(leak["audience_root"], []).extend(leak["must_not_find"])
    assert set(by_root) == {"harbor", "brain", "chloe", "beacon"}
    assert {"EUR 950", "day rate"} <= set(by_root["harbor"])
    assert "paid search ads" in by_root["chloe"] and "3 November" in by_root["beacon"]
    verdicts = yaml.safe_load((FIXTURE / "verdicts.yaml").read_text(encoding="utf-8"))
    # a record held in doubt is answered by its id: without the router nothing else lets it go
    page = "# Harbor\n\n## Work\n- The go-live moves to 3 November (go-live date: 2026-11-03). [src: m-ada-001]\n"
    assert verdicts == {"ada": {"harbor": ["edit", page], "m-ada-001": "yes"}, "ben": {},
                        "chloe": {"sales": "yes", "m-chloe-002": "yes",
                                  "2026-10-04-pilots-run-for-one-quarter-before-any-rollout": "yes"}}
