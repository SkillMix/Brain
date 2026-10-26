"""One analysis run per connected app decides read or skip, and the app card says so. No test
starts the real claude: the probe and the readers are fakes, and the journal is the test's own."""
import datetime as dt
import json
import threading

import pytest

import apps
import reader
import routine
import setup_journal
import start

NOW = dt.datetime(2026, 10, 24, 14, 8, tzinfo=dt.timezone(dt.timedelta(hours=2)))
ADA = "ada@lanternworks.example"
THIS_MONTH = dt.date.today().strftime("%Y-%m")   # a count is kept only inside the last twelve months
TOOLS = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread", "mcp__claude_ai_Gmail__send_message",
         "mcp__claude_ai_Gmail__trash_thread", "mcp__claude_ai_Slack__slack_read_channel", "mcp__claude_ai_Slack__slack_send_message",
         "mcp__claude_ai_Microsoft_365__list_mail", "mcp__claude_ai_Microsoft_365__list_events", "Read", "Bash"]


def test_tools_group_into_apps_with_read_and_send_apart():
    by = {g["app"]: g for g in apps.group(TOOLS)}
    assert by["Gmail"]["kinds"] == ["mail"] and "mcp__claude_ai_Gmail__trash_thread" not in by["Gmail"]["read"] + by["Gmail"]["send"]
    assert by["Gmail"]["send"] == ["mcp__claude_ai_Gmail__send_message"]
    assert by["Slack"]["send"] == []                         # chat never sends
    assert set(by["Microsoft 365"]["kinds"]) == {"mail", "calendar"}


def test_a_personal_mailbox_is_skipped_and_said_so(tmp_path):
    v = apps.analyse(apps.group(TOOLS[:3]), {"email": "ada@lanternworks.example"}, tmp_path,
                     reader=lambda *a, **k: {"account": "ada.okafor@outlook.com", "verdict": "personal", "per_month": {}, "why": "no work found"})
    card = apps.card(v, NOW)
    assert card["read"] == [] and card["skip"] == ["Gmail · ada.okafor@outlook.com · personal, no work found"]
    assert card["starts_after"] == (NOW + dt.timedelta(minutes=10)).isoformat(timespec="seconds")


def test_an_expired_connector_becomes_reconnect_not_a_crash(tmp_path):
    def reader_(prompt, tools, *a, **k):
        if "Slack" in tools[0]:
            raise reader.ReaderError("auth", "connector needs re-authentication")
        return {"account": "ada@lanternworks.example", "verdict": "work", "per_month": {"2026-09": 900}, "why": "client mail"}
    v = {x["app"]: x for x in apps.analyse(apps.group(TOOLS), {"email": "ada@lanternworks.example"}, tmp_path, reader=reader_)}
    assert v["Slack"]["verdict"] == "reconnect" and v["Gmail"]["verdict"] == "work"


def test_someone_elses_mailbox_is_counted_never_a_feed(tmp_path):
    v = apps.analyse(apps.group(TOOLS[:3]), {"email": "ada@lanternworks.example"}, tmp_path,
                     reader=lambda *a, **k: {"account": "office@lanternworks.example", "verdict": "someone-else", "per_month": {"2026-09": 40}, "why": "shared mailbox"})
    assert apps.feeds(v) == ""


def test_no_apps_at_all_is_a_connect_need_within_the_signins(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPACES_SETUP_JOURNAL", str(tmp_path / "setup.yaml"))
    monkeypatch.setattr(routine, "probe_tools", lambda *a, **k: {"tools": ["Read", "Bash"], "mcp_servers": []})
    assert start.main(["apps", "--probe", "--json"]) == 0
    assert [n["kind"] for n in json.loads(capsys.readouterr().out)["needs"]] == ["connect-app"]


# ----- grouping -----------------------------------------------------------------------
def test_an_app_is_named_by_the_probes_server_names_else_by_its_tool_prefix():
    tools = ["mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_monday_com__get_board_items"]
    servers = [{"name": "claude.ai Google Calendar", "status": "connected"}, {"name": "claude.ai monday.com", "status": "connected"}]
    assert [g["app"] for g in apps.group(tools, servers)] == ["Google Calendar", "monday.com"]
    assert [g["app"] for g in apps.group(tools[:1])] == ["Google Calendar"]   # no probe names: the underscores read as spaces


def test_only_claude_ai_connectors_of_a_known_kind_with_a_read_tool_are_apps():
    tools = ["mcp__plugin_azure_azure__list_things", "mcp__claude_ai_Weather__get_forecast",
             "mcp__claude_ai_Notetaker__notetaker_get_transcripts", "mcp__claude_ai_Linear__save_issue", "Read"]
    assert [g["app"] for g in apps.group(tools)] == ["Notetaker"]   # a plugin, an unknown kind, nothing to read


def test_one_classifier_decides_what_reads_and_everything_else_is_in_no_list():
    gmail = ("search_threads", "get_thread", "send_message", "reply", "forward", "trash_thread", "create_draft", "label_message")
    [g] = apps.group([f"mcp__claude_ai_Gmail__{t}" for t in gmail])
    assert g["read"] == [f"mcp__claude_ai_Gmail__{t}" for t in ("search_threads", "get_thread")]
    assert g["read"] == [t for t in g["read"] if reader.is_read_tool(t)]
    assert g["send"] == ["mcp__claude_ai_Gmail__send_message"] and g["write"] == []   # reply and forward are in no list


def test_write_tools_exist_for_the_tracker_kind_only():
    linear = ["list_issues", "get_issue", "save_issue", "save_comment", "delete_comment", "create_attachment"]
    by = {g["app"]: g for g in apps.group([f"mcp__claude_ai_Linear__{t}" for t in linear]
                                          + ["mcp__claude_ai_Notion__notion_fetch", "mcp__claude_ai_Notion__notion_create_pages"])}
    assert by["Linear"]["kinds"] == ["tracker"] and by["Linear"]["send"] == []
    assert by["Linear"]["write"] == [f"mcp__claude_ai_Linear__{t}" for t in ("save_issue", "save_comment", "create_attachment")]
    assert by["Notion"]["kinds"] == ["documents"] and by["Notion"]["write"] == []


def test_a_suites_send_tools_are_its_mail_tools_only():
    suite = ["list_mail", "send_mail", "list_chats", "send_chat_message", "list_events", "list_files"]
    [g] = apps.group([f"mcp__claude_ai_Microsoft_365__{t}" for t in suite])
    assert g["kinds"] == ["mail", "calendar", "chat", "documents"]   # in the order of KINDS
    assert g["send"] == ["mcp__claude_ai_Microsoft_365__send_mail"]


ATLASSIAN = ["getJiraIssue", "searchJiraIssuesUsingJql", "createJiraIssue", "search", "fetch", "getConfluencePage"]


def test_a_camel_case_suite_is_an_app_by_the_words_of_its_tool_names():
    servers = [{"name": "claude.ai Atlassian", "status": "connected"}]
    [g] = apps.group([f"mcp__claude_ai_Atlassian__{t}" for t in ATLASSIAN], servers)
    assert g["app"] == "Atlassian" and g["kinds"] == ["documents", "tracker"]
    assert g["read"] == [f"mcp__claude_ai_Atlassian__{t}" for t in ("getJiraIssue", "searchJiraIssuesUsingJql", "search", "fetch", "getConfluencePage")]
    assert g["write"] == ["mcp__claude_ai_Atlassian__createJiraIssue"] and g["send"] == []
    assert [x["app"] for x in apps.group([f"mcp__claude_ai_Atlassian__{t}" for t in ATLASSIAN])] == ["Atlassian"]   # no probe names


def test_an_app_whose_name_is_camel_case_is_known_by_its_whole_word_too():
    tools = ["mcp__claude_ai_ClickUp__get_tasks", "mcp__claude_ai_SharePoint__search", "mcp__claude_ai_OneDrive__list_files"]
    assert {g["app"]: g["kinds"] for g in apps.group(tools)} == {"ClickUp": ["tracker"], "SharePoint": ["documents"],
                                                                 "OneDrive": ["documents"]}


def test_a_name_that_only_holds_a_known_word_in_camel_case_is_not_an_app():
    """ZoomInfo is not Zoom, ChatGPT is not a chat app, MailChimp is not a mailbox: an app is known by
    the whole lower-case words of its name (ClickUp, SharePoint and OneDrive by theirs)."""
    tools = ["mcp__claude_ai_ZoomInfo__search_companies", "mcp__claude_ai_ChatGPT__search",
             "mcp__claude_ai_MailChimp__get_campaign", "mcp__claude_ai_MailChimp__send_campaign"]
    assert apps.group(tools) == []
    servers = [{"name": f"claude.ai {n}", "status": "needs-auth"} for n in ("ZoomInfo", "ChatGPT", "MailChimp")]
    assert apps.not_signed_in(servers) == []


def test_a_connector_that_needs_a_sign_in_again_is_listed_for_the_card_only_when_its_kind_is_known():
    servers = [{"name": "claude.ai Microsoft 365", "status": "needs-auth"}, {"name": "claude.ai Linear", "status": "needs-auth"},
               {"name": "claude.ai Weather", "status": "needs-auth"}, {"name": "claude.ai Gmail", "status": "connected"},
               {"name": "plugin:azure:azure", "status": "needs-auth"}]
    assert apps.not_signed_in(servers) == ["Microsoft 365", "Linear"]
    assert apps.not_signed_in([]) == [] and apps.not_signed_in(None) == []


# ----- the verdict schema ---------------------------------------------------------------
GOOD = {"account": ADA, "verdict": "work", "per_month": {"2026-09": 12}, "why": "client mail"}


def test_the_verdict_schema_is_one_the_readers_validator_enforces():
    assert reader._violation(GOOD, apps.VERDICT_SCHEMA) is None
    assert reader._violation({**GOOD, "verdict": "maybe"}, apps.VERDICT_SCHEMA)
    assert reader._violation({k: v for k, v in GOOD.items() if k != "why"}, apps.VERDICT_SCHEMA)
    assert reader._violation({**GOOD, "extra": 1}, apps.VERDICT_SCHEMA)
    assert reader._violation({**GOOD, "per_month": {"2026-09": "many"}}, apps.VERDICT_SCHEMA)
    assert reader._violation({**GOOD, "per_month": [1]}, apps.VERDICT_SCHEMA)
    assert set(apps.VERDICT_SCHEMA["properties"]["verdict"]["enum"]) == {
        "work", "personal", "mixed", "someone-else", "reconnect", "blocked"}


# ----- the analysis runs -----------------------------------------------------------------
def answer(**more):
    return {**GOOD, **more}


def test_one_reader_per_app_in_parallel(tmp_path):
    both_inside = threading.Barrier(2, timeout=20)   # a reader waits for the other: serial runs break it

    def reader_(prompt, tools, *a, **k):
        both_inside.wait()
        return answer()
    v = apps.analyse(apps.group(TOOLS[:3] + TOOLS[4:6]), {"email": ADA}, tmp_path, reader=reader_, workers=2)
    assert [x["app"] for x in v] == ["Gmail", "Slack"] and {x["verdict"] for x in v} == {"work"}


def test_each_reader_gets_its_own_apps_read_tools_the_schema_and_the_scratch_folder(tmp_path):
    seen = []

    def reader_(prompt, tools, schema, cwd, **k):
        seen.append({"prompt": prompt, "tools": tools, "schema": schema, "cwd": cwd, **k})
        return answer()
    apps.analyse(apps.group(TOOLS), {"email": ADA}, tmp_path, reader=reader_, known_tools=TOOLS, claude="/bin/claude")
    by = {s["tools"][0].split("__")[1]: s for s in seen}
    assert by["claude_ai_Gmail"]["tools"] == ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread"]
    assert by["claude_ai_Slack"]["tools"] == ["mcp__claude_ai_Slack__slack_read_channel"]
    assert all(s["schema"] is apps.VERDICT_SCHEMA and s["cwd"] == tmp_path and s["known_tools"] == TOOLS
               and s["claude"] == "/bin/claude" for s in seen)


def test_a_verdict_is_the_group_plus_the_schemas_fields(tmp_path):
    [group] = apps.group(TOOLS[:3])
    [v] = apps.analyse([group], {"email": ADA}, tmp_path, reader=lambda *a, **k: answer(), today=dt.date(2026, 10, 24))
    assert {k: v[k] for k in group} == group
    assert (v["account"], v["verdict"], v["per_month"], v["why"]) == (ADA, "work", {"2026-09": 12}, "client mail")


def test_every_reader_error_maps_to_a_verdict_and_the_others_go_on(tmp_path):
    errors = {"Gmail": reader.ReaderError("auth", "Gmail needs the person to sign in again"),
              "Slack": reader.ReaderError("cli", "stopped after 15 minutes"),
              "Microsoft 365": reader.ReaderError("format", "the answer was not a JSON object")}

    def reader_(prompt, tools, *a, **k):
        raise errors[next(app for app in errors if app.replace(" ", "_") in tools[0])]
    got = {v["app"]: v for v in apps.analyse(apps.group(TOOLS), {"email": ADA}, tmp_path, reader=reader_)}
    assert got["Gmail"]["verdict"] == "reconnect" and got["Gmail"]["why"] == "Gmail needs the person to sign in again"
    assert got["Slack"]["verdict"] == "blocked" and got["Slack"]["why"] == "stopped after 15 minutes"
    assert got["Microsoft 365"]["verdict"] == "blocked" and got["Microsoft 365"]["why"] == "the answer was not a JSON object"
    assert all(v["per_month"] == {} and v["account"] == "" for v in got.values())


def test_a_limit_stops_new_readers_and_keeps_what_finished(tmp_path):
    called = []

    def reader_(prompt, tools, *a, **k):
        called.append(tools[0])
        if "Slack" in tools[0]:
            raise reader.ReaderError("limit", "the plan's limit is reached")
        return answer()
    with pytest.raises(apps.LimitReached) as stop:
        apps.analyse(apps.group(TOOLS), {"email": ADA}, tmp_path, reader=reader_, workers=1)
    assert [v["app"] for v in stop.value.verdicts] == ["Gmail"] and len(called) == 2   # Microsoft 365 never started
    assert stop.value.text == "the plan's limit is reached"


def test_an_app_skipped_before_its_reader_starts_is_never_read_and_has_no_verdict(tmp_path):
    """The person's skips are asked again right before each reader starts (R43, N2-3): one said while the first
    readers run keeps an app still queued behind them from being read at all."""
    called, skips = [], set()

    def reader_(prompt, tools, *a, **k):
        called.append(tools[0])
        skips.add("Slack")                                                    # said while Gmail is read
        return answer()
    v = apps.analyse(apps.group(TOOLS), {"email": ADA}, tmp_path, reader=reader_, workers=1, skipped=lambda: skips)
    assert [x["app"] for x in v] == ["Gmail", "Microsoft 365"] and not any("Slack" in t for t in called)


def test_someone_elses_mailbox_never_sends_and_the_group_is_left_as_it_was(tmp_path):
    [group] = apps.group(TOOLS[:3])
    [v] = apps.analyse([group], {"email": ADA}, tmp_path, reader=lambda *a, **k: answer(verdict="someone-else", account="office@lanternworks.example"))
    assert v["verdict"] == "someone-else" and v["send"] == [] and v["write"] == []
    assert group["send"] == ["mcp__claude_ai_Gmail__send_message"]


def test_a_personal_app_keeps_no_tool_that_sends_or_writes(tmp_path):
    linear = apps.group(["mcp__claude_ai_Linear__list_issues", "mcp__claude_ai_Linear__save_issue"])
    for [group] in (apps.group(TOOLS[:3]), linear):
        [v] = apps.analyse([group], {"email": ADA}, tmp_path, reader=lambda *a, **k: answer(verdict="personal", why="a club"))
        assert v["verdict"] == "personal" and v["send"] == [] and v["write"] == [] and v["read"] == group["read"]
    [work] = apps.analyse(apps.group(TOOLS[:3]), {"email": ADA}, tmp_path, reader=lambda *a, **k: answer())
    assert work["send"] == ["mcp__claude_ai_Gmail__send_message"]   # an app that is read keeps its grant


def test_a_fault_that_is_not_a_reader_error_is_that_apps_blocked_verdict_and_the_others_go_on(tmp_path):
    def reader_(prompt, tools, *a, **k):
        if "Slack" in tools[0]:
            raise OSError(13, "mail text that must not be repeated")
        return answer()
    got = {v["app"]: v for v in apps.analyse(apps.group(TOOLS), {"email": ADA}, tmp_path, reader=reader_)}
    assert got["Gmail"]["verdict"] == "work" and got["Microsoft 365"]["verdict"] == "work"
    assert got["Slack"]["verdict"] == "blocked" and got["Slack"]["per_month"] == {} and got["Slack"]["account"] == ""
    assert "mail text" not in got["Slack"]["why"] and "OSError" not in got["Slack"]["why"] and got["Slack"]["why"]


def test_the_answer_is_cleaned_before_it_is_kept(tmp_path):
    why = "client mail\n" + "x" * 400
    [v] = apps.analyse(apps.group(TOOLS[:3]), {"email": ADA}, tmp_path,
                       reader=lambda *a, **k: answer(why=why, account="  " + ADA + " \n"))
    assert v["account"] == ADA and "\n" not in v["why"] and len(v["why"]) <= 200


def test_only_the_twelve_months_asked_for_are_kept_and_only_real_counts(tmp_path):
    inside = {f"2025-{m:02d}": m for m in range(11, 13)} | {f"2026-{m:02d}": m for m in range(1, 11)}   # 2025-11 .. 2026-10
    outside = {"2025-10": 1, "2026-11": 2, "2027-01": 3, "1999-05": 4}                                   # older, future
    broken = {"last year": 5, "2026-13": 6, "2026-03": -1, "2026-04": True, "2026-05": 1.5, "2026-06": "7"}
    [v] = apps.analyse(apps.group(TOOLS[:3]), {"email": ADA}, tmp_path, today=dt.date(2026, 10, 24),
                       reader=lambda *a, **k: answer(per_month={**inside, **outside, **broken}))
    assert sorted(v["per_month"]) == sorted(set(inside) - {"2026-03", "2026-04", "2026-05", "2026-06"})   # those four were overridden by broken ones
    assert v["per_month"]["2025-11"] == 11 and v["per_month"]["2026-10"] == 10 and len(v["per_month"]) == 8
    assert not (set(outside) | {"last year", "2026-13"}) & set(v["per_month"])


def test_the_reader_prompt_is_plain_names_no_other_app_and_sets_the_limits():
    gmail, slack = apps.group(TOOLS)[:2]
    person = {"name": "Ada Okafor", "email": ADA, "company": "Lantern Works", "domains": ["lanternworks.example"]}
    text = apps.reader_prompt(gmail, person, dt.date(2026, 10, 24))
    assert "Gmail" in text and "Slack" not in text and "Microsoft" not in text
    assert "30" in text and "5" in text and "12 months" in text and "2026-10-24" in text
    assert "Lantern Works" in text and "lanternworks.example" in text and ADA in text
    assert "never quote" in text or "never quoted" in text
    assert "Gmail" not in apps.reader_prompt(slack, person, dt.date(2026, 10, 24))


def test_without_a_company_the_prompt_judges_work_by_the_persons_own_mail_domain():
    [gmail] = apps.group(TOOLS[:3])
    text = apps.reader_prompt(gmail, {"name": "Ada Okafor", "email": ADA}, dt.date(2026, 10, 24))
    assert "lanternworks.example" in text and "Lantern Works" not in text


def test_the_company_is_the_researched_card_else_the_given_name_with_the_mail_domain():
    journal = {"research": {"state": "done", "company": {"name": "Lantern Works", "domains": ["lanternworks.example"]}}}
    assert apps.company_of(journal, None, [ADA]) == {"company": "Lantern Works", "domains": ["lanternworks.example"]}
    assert apps.company_of({"research": {"company": "Lantern Works"}}, None, [ADA])["domains"] == ["lanternworks.example"]
    assert apps.company_of({}, "Trial Works", [ADA]) == {"company": "Trial Works", "domains": ["lanternworks.example"]}
    assert apps.company_of({}, None, [ADA]) == {"company": None, "domains": ["lanternworks.example"]}
    assert apps.company_of({}, None, ["ada@gmail.com", ""]) == {"company": None, "domains": []}   # a free mail domain is nobody's company


# ----- the card and the feeds --------------------------------------------------------------
def verdict(app, kinds, kind, account=ADA, why="a reason", **more):
    return {"app": app, "kinds": kinds, "read": [], "write": [], "send": [], "account": account, "verdict": kind,
            "per_month": {}, "why": why, **more}


MIXED = [verdict("Gmail", ["mail"], "work"), verdict("Google Calendar", ["calendar"], "mixed"),
         verdict("Slack", ["chat"], "someone-else", account="office@lanternworks.example", why="shared"),
         verdict("Notetaker", ["calls"], "personal", why="family calls"),
         verdict("Notion", ["documents"], "reconnect", account="", why="sign in again"),
         verdict("Linear", ["tracker"], "blocked", account="", why="stopped after 15 minutes")]


def test_the_card_reads_work_and_mixed_skips_the_rest_and_leaves_failures_to_the_needs():
    card = apps.card(MIXED, NOW)
    assert card["read"] == [f"Gmail · {ADA} · work", f"Google Calendar · {ADA} · mixed"]
    assert card["skip"] == ["Slack · office@lanternworks.example · someone-else, shared",
                            f"Notetaker · {ADA} · personal, family calls"]
    assert card["not_signed_in"] == []   # nothing was given


def test_the_card_lists_connectors_that_need_a_sign_in_as_plain_information():
    assert apps.card([], NOW, ["Microsoft 365"])["not_signed_in"] == ["Microsoft 365 · not signed in"]
    assert apps.card([], NOW)["starts_after"] == "2026-10-24T14:18:00+02:00"


def test_the_feeds_are_the_kinds_of_apps_that_are_read_in_the_order_of_kinds():
    assert apps.feeds(MIXED) == "mail: owner, calendar: owner"
    every = [verdict(a, [k], "work") for a, k in (("Linear", "tracker"), ("Notion", "documents"), ("Notetaker", "calls"),
                                                  ("Slack", "chat"), ("Google Calendar", "calendar"), ("Gmail", "mail"))]
    assert apps.feeds(every) == "mail: owner, calendar: owner, chat: owner, calls: owner, documents: owner, tracker: owner"
    assert apps.feeds([]) == ""


# ----- the phase ----------------------------------------------------------------------------
APP_TOOLS = TOOLS[:6] + ["Read", "Bash"]
SERVERS = [{"name": "claude.ai Gmail", "status": "connected"}, {"name": "claude.ai Slack", "status": "connected"},
           {"name": "claude.ai Microsoft 365", "status": "needs-auth"}, {"name": "claude.ai Weather", "status": "needs-auth"}]


class ReaderStarted(BaseException):
    """Not an Exception: a fault in one reader becomes that app's verdict, but a reader nobody
    stood in for must fail the test."""


@pytest.fixture
def phase(monkeypatch):
    """The probe, the person and `claude`'s place are fakes; any reader the test does not stand in
    for fails the test, so none can start the real command line."""
    def nothing(*a, **k):
        raise ReaderStarted("a reader started")
    monkeypatch.setattr(routine, "probe_tools", lambda *a, **k: {"tools": APP_TOOLS, "mcp_servers": SERVERS})
    monkeypatch.setattr(routine, "find_claude", lambda: "/bin/claude")
    monkeypatch.setattr(start, "identity", lambda name=None, email=None, offline=False:
                        {"name": "Ada Okafor", "email": ADA, "login": ""})
    monkeypatch.setattr(reader, "read", nothing)
    return monkeypatch


def readers(monkeypatch, by_app, seen=None):
    def read_(prompt, tools, schema, cwd, **k):
        app = "Gmail" if "Gmail" in tools[0] else "Slack"
        if seen is not None:
            seen.append({"app": app, "prompt": prompt, "tools": tools, "cwd": cwd, **k})
        got = by_app[app]
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(reader, "read", read_)


def run(capsys, *more):
    code = start.main(["apps", "--json", *more])
    return code, json.loads(capsys.readouterr().out)


def journal_apps():
    return setup_journal.load()["apps"]


def test_the_probe_alone_writes_the_groups_and_starts_no_reader(phase, capsys):
    code, out = run(capsys, "--probe")
    assert code == 0 and out["state"] == "pending" and out["needs"] == []
    record = journal_apps()
    assert record["state"] == "pending" and [g["app"] for g in record["groups"]] == ["Gmail", "Slack"]
    assert record["not_signed_in"] == ["Microsoft 365"]   # Weather is of no known kind
    assert "verdicts" not in record


def test_connecting_an_app_closes_the_connect_need(phase, capsys):
    phase.setattr(routine, "probe_tools", lambda *a, **k: {"tools": ["Read"], "mcp_servers": []})
    code, out = run(capsys, "--probe")
    [need] = out["needs"]
    assert out["state"] == "waiting" and need["kind"] == "connect-app" and need["link"] == "https://claude.ai/settings/connectors"
    assert "mcp__" not in need["text"] and "_meta" not in need["text"] and "python" not in need["text"]
    phase.setattr(routine, "probe_tools", lambda *a, **k: {"tools": APP_TOOLS, "mcp_servers": SERVERS})   # the person connected one
    code, out = run(capsys, "--probe")
    assert out["needs"] == [] and out["state"] == "pending"


def test_a_probe_that_fails_is_a_failed_phase_never_no_apps(phase, capsys):
    def broken(*a, **k):
        raise routine.ProbeError("claude printed no init event (exit 1): boom")
    phase.setattr(routine, "probe_tools", broken)
    code, out = run(capsys)
    assert code == 1 and out["state"] == "failed" and out["needs"] == []   # not a connect-app need
    assert "boom" not in out["why"] and "mcp" not in out["why"].lower()


def test_a_full_run_records_verdicts_card_and_feeds(phase, capsys):
    seen = []
    readers(phase, {"Gmail": answer(per_month={THIS_MONTH: 900}), "Slack": answer(verdict="personal", why="a club", account="ada@club.example")}, seen)
    before = dt.datetime.now().astimezone()
    code, out = run(capsys)
    assert code == 0 and out["state"] == "done" and out["needs"] == []
    record = journal_apps()
    assert record["state"] == "done" and record["feeds"] == "mail: owner"
    assert [v["verdict"] for v in record["verdicts"]] == ["work", "personal"]
    assert record["verdicts"][0]["per_month"] == {THIS_MONTH: 900} and record["verdicts"][0]["read"]
    assert record["card"]["read"] == [f"Gmail · {ADA} · work"]
    assert record["card"]["skip"] == ["Slack · ada@club.example · personal, a club"]
    assert record["card"]["not_signed_in"] == ["Microsoft 365 · not signed in"]   # information, no need
    starts = dt.datetime.fromisoformat(record["card"]["starts_after"])
    assert dt.timedelta(minutes=9) < starts - before < dt.timedelta(minutes=11)   # at least ten minutes before any copy
    assert out["card"] == record["card"] and out["feeds"] == "mail: owner" and len(seen) == 2


def test_readers_run_in_a_scratch_folder_denied_every_other_tool_and_told_the_company(phase, capsys, tmp_path):
    setup_journal.set_phase("research", "done", company={"name": "Lantern Works", "domains": ["lanternworks.example"]})
    seen = []
    readers(phase, {"Gmail": answer(), "Slack": answer()}, seen)
    run(capsys)
    scratch = tmp_path / "_home" / ".spaces" / "readers"
    for s in seen:
        assert s["cwd"].is_dir() and scratch in s["cwd"].parents   # never the person's home or a repository
        assert s["known_tools"] == APP_TOOLS and s["claude"] == "/bin/claude"
        assert "Lantern Works" in s["prompt"] and "lanternworks.example" in s["prompt"]
    assert sorted(s["app"] for s in seen) == ["Gmail", "Slack"]


def test_with_no_research_yet_the_persons_mail_domain_is_the_company(phase, capsys):
    seen = []
    readers(phase, {"Gmail": answer(), "Slack": answer()}, seen)
    run(capsys)
    assert all("lanternworks.example" in s["prompt"] for s in seen)


def test_an_expired_connector_is_a_reconnect_need_and_the_other_apps_go_on(phase, capsys):
    readers(phase, {"Gmail": answer(), "Slack": reader.ReaderError("auth", "Slack needs the person to sign in again")})
    code, out = run(capsys)
    [need] = out["needs"]
    assert code == 0 and out["state"] == "done"
    assert need["kind"] == "reconnect" and "Slack" in need["text"] and need["link"] == "https://claude.ai/settings/connectors"
    assert "mcp__" not in need["text"] and "python" not in need["text"] and "/" not in need["text"]
    record = journal_apps()
    assert {v["app"]: v["verdict"] for v in record["verdicts"]} == {"Gmail": "work", "Slack": "reconnect"}
    assert record["card"]["read"] == [f"Gmail · {ADA} · work"] and record["card"]["skip"] == []   # a need, not a card line
    assert record["feeds"] == "mail: owner"


def test_an_app_that_cannot_be_read_is_an_it_admin_need_with_the_errors_text(phase, capsys):
    readers(phase, {"Gmail": answer(), "Slack": reader.ReaderError("cli", "claude ended with an error (error_during_execution)")})
    code, out = run(capsys)
    [need] = out["needs"]
    assert out["state"] == "done" and need["kind"] == "it-admin" and "Slack" in need["text"]
    assert "mcp__" not in need["text"] and "python" not in need["text"]
    [slack] = [v for v in journal_apps()["verdicts"] if v["app"] == "Slack"]
    assert slack["verdict"] == "blocked" and slack["why"] == "claude ended with an error (error_during_execution)"


def test_a_run_again_replaces_the_needs_of_the_run_before(phase, capsys):
    readers(phase, {"Gmail": reader.ReaderError("auth", "Gmail needs the person to sign in again"),
                    "Slack": reader.ReaderError("cli", "stopped after 15 minutes")})
    assert sorted(n["kind"] for n in run(capsys)[1]["needs"]) == ["it-admin", "reconnect"]
    readers(phase, {"Gmail": answer(), "Slack": answer()})   # the person reconnected, the admin allowed it
    assert run(capsys)[1]["needs"] == []


def test_a_limit_records_what_finished_and_waits_in_plain_words(phase, capsys):
    phase.setattr(routine, "probe_tools", lambda *a, **k: {"tools": APP_TOOLS, "mcp_servers": SERVERS})
    phase.setattr(apps, "analyse", lambda groups, *a, **k: (_ for _ in ()).throw(
        apps.LimitReached([{**verdict("Gmail", ["mail"], "work"), "read": ["x"]}], "the plan's limit is reached")))
    code, out = run(capsys)
    assert code == 0 and out["state"] == "waiting" and "1 of 2 apps" in out["why"]
    assert "the plan's limit is reached" in out["why"] and "mcp__" not in out["why"]
    record = journal_apps()
    assert record["state"] == "waiting" and record["card"]["read"] == [f"Gmail · {ADA} · work"]
    assert record["feeds"] == "mail: owner" and record["unfinished"] == ["Slack"]


def test_a_complete_run_leaves_nothing_unfinished(phase, capsys):
    readers(phase, {"Gmail": answer(), "Slack": answer()})
    run(capsys)
    assert journal_apps()["unfinished"] == []


def test_a_full_run_with_no_apps_asks_for_one_and_reads_nothing(phase, capsys):
    phase.setattr(routine, "probe_tools", lambda *a, **k: {"tools": ["Read"], "mcp_servers": [
        {"name": "claude.ai Weather", "status": "connected"}]})
    code, out = run(capsys)
    assert out["state"] == "waiting" and [n["kind"] for n in out["needs"]] == ["connect-app"]
    assert "verdicts" not in journal_apps()


def test_offline_there_is_nothing_to_read(phase, capsys):
    phase.setattr(routine, "probe_tools", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    code, out = run(capsys, "--offline")
    assert code == 0 and out["state"] == "done" and out["needs"] == []


def test_a_dry_run_asks_the_probe_and_changes_nothing(phase, capsys):
    code, out = run(capsys, "--dry-run")
    assert code == 0 and out["state"] == "pending" and out["apps"] == ["Gmail", "Slack"]
    assert setup_journal.load() == {}


def test_without_the_json_flag_the_phase_says_its_state_in_words(phase, capsys):
    readers(phase, {"Gmail": answer(), "Slack": reader.ReaderError("auth", "Slack needs the person to sign in again")})
    assert start.main(["apps"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("apps: done") and "yours: reconnect:" in out


# ===== Fix wave F2 (Ruling R45): a slow reader is tried again, and what finished is kept ==========================
def test_a_slow_reader_leaves_its_app_without_a_verdict_and_the_others_go_on(tmp_path):
    """A reader that ran out of turns or time is `slow`: no verdict (so the app stays unfinished and is read again
    next time), never `blocked` (R45, I-3)."""
    def reader_(prompt, tools, *a, **k):
        if "Slack" in tools[0]:
            raise reader.ReaderError("slow", "it used up its 25 turns")
        return answer()
    got = apps.analyse(apps.group(TOOLS), {"email": ADA}, tmp_path, reader=reader_)
    assert [v["app"] for v in got] == ["Gmail", "Microsoft 365"]


def test_an_answer_that_came_with_extra_usage_is_kept_and_no_reader_starts_after(tmp_path):
    """Extra usage starting stops the analysis, but the verdict that came with it stays (R45, I-2)."""
    called = []

    def reader_(prompt, tools, *a, **k):
        called.append(tools[0])
        raise reader.ReaderError("limit", "extra usage has started", answer=answer(verdict="mixed"))
    with pytest.raises(apps.LimitReached) as stop:
        apps.analyse(apps.group(TOOLS), {"email": ADA}, tmp_path, reader=reader_, workers=1)
    assert [(v["app"], v["verdict"]) for v in stop.value.verdicts] == [("Gmail", "mixed")] and len(called) == 1
    assert stop.value.text == "extra usage has started"


def test_the_reader_prompt_keeps_within_the_readers_turns():
    [group] = apps.group(TOOLS[:3])
    prompt = apps.reader_prompt(group, {"email": ADA}, dt.date(2026, 10, 24))
    assert f"{reader.MAX_TURNS} turns" in prompt and "several tool calls in one turn" in prompt


def test_a_slow_app_is_unfinished_and_read_again_never_an_it_admin_need(phase, capsys):
    seen = []
    readers(phase, {"Gmail": answer(), "Slack": reader.ReaderError("slow", "stopped after 15 minutes")}, seen)
    code, out = run(capsys)
    assert code == 0 and out["state"] == "waiting" and out["needs"] == [] and out["unfinished"] == ["Slack"]
    assert "Slack" in out["why"] and "again" in out["why"] and "15 minutes" not in out["why"] and "skip" in out["why"]
    record = journal_apps()
    assert [v["app"] for v in record["verdicts"]] == ["Gmail"] and record["unfinished"] == ["Slack"]
    seen.clear()
    readers(phase, {"Gmail": answer(), "Slack": answer()}, seen)
    code, out = run(capsys)
    assert out["state"] == "done" and [s["app"] for s in seen] == ["Slack"] and out["unfinished"] == []
    assert [v["app"] for v in journal_apps()["verdicts"]] == ["Gmail", "Slack"]


def test_after_a_limit_what_finished_is_kept_and_only_the_rest_and_new_apps_are_read(phase, capsys):
    """The skill says "What finished is kept" (R45, I-2): the run after a limit keeps the verdicts the journal holds
    and reads only the apps without one, and apps connected since; an app to reconnect is read again."""
    seen = []
    readers(phase, {"Gmail": answer(), "Slack": reader.ReaderError("limit", "the plan's limit is reached")}, seen)
    phase.setattr(apps, "analyse", lambda groups, *a, **k: real_analyse(groups, *a, **{**k, "workers": 1}))
    code, out = run(capsys)
    assert out["state"] == "waiting" and out["unfinished"] == ["Slack"] and "1 of 2 apps" in out["why"]
    seen.clear()
    readers(phase, {"Gmail": AssertionError("Gmail was read again"), "Slack": answer(verdict="mixed")}, seen)
    code, out = run(capsys)
    assert out["state"] == "done" and [s["app"] for s in seen] == ["Slack"] and out["unfinished"] == []
    record = journal_apps()
    assert [(v["app"], v["verdict"]) for v in record["verdicts"]] == [("Gmail", "work"), ("Slack", "mixed")]
    assert record["feeds"] == "mail: owner, chat: owner" and record["card"]["read"][0] == f"Gmail · {ADA} · work"


real_analyse = apps.analyse
