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
    assert set(result["steps"]) == {"sweep", "reply", "distill", "check", "tracker_observe"}
    lines = (root / "_meta" / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    line = json.loads(lines[-1])
    assert line["routine"] == "sweep_chain" and line["at"] == "2026-10-12T09:00:00Z"
    assert {"space", "cost_tokens", "notes"} <= set(line)


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
    assert verdicts == {"ada": {"harbor": "yes", "m-ada-001": "yes"}, "ben": {},
                        "chloe": {"sales": "yes", "m-chloe-002": "yes",
                                  "2026-10-04-pilots-run-for-one-quarter-before-any-rollout": "yes"}}
