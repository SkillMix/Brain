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
    assert imports <= {"__future__", "base64", "binascii", "json", "re", "sys"}


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
