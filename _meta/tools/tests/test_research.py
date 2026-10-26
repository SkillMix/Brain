"""Company research (phase 3 of start): the company's own sources, its documents, files and a few
notes become a cited profile. No test starts the real claude or looks at the real home folder: the
readers are stand-ins, and the home is a temporary one (`conftest._machine_home`)."""
import builtins
import datetime as dt
import json
import os
import re
import types
from pathlib import Path

import pytest
import yaml

import doctor
import onboard
import pages
import reader
import research
import routine
import setup_journal
import start
import validate
from conftest import NOW, make_space

ADA = "ada@lanternworks.example"
TERMS = ["lantern works", "lanternworks"]


# ----- the local search --------------------------------------------------------------------------
def test_local_search_finds_company_documents_and_leaves_photos_alone(tmp_path):
    (tmp_path / "Documents" / "Lantern Works").mkdir(parents=True)
    (tmp_path / "Documents" / "Lantern Works" / "Brand book v3.pdf").write_bytes(b"%PDF")
    (tmp_path / "Pictures").mkdir(); (tmp_path / "Pictures" / "lantern works party.pdf").write_bytes(b"%PDF")
    (tmp_path / "Documents" / "tax 2025.pdf").write_bytes(b"%PDF")
    found = [p.name for p in research.company_files(tmp_path, ["lantern works", "lanternworks"])]
    assert found == ["Brand book v3.pdf"]


def test_claude_notes_only_lines_about_this_company(tmp_path):
    mem = tmp_path / ".claude" / "projects" / "x" / "memory"; mem.mkdir(parents=True)
    (mem / "a.md").write_text("Lantern Works brand colour is amber\nBeacon Labs invoice due\n", encoding="utf-8")
    assert [n["line"] for n in research.claude_notes(tmp_path, ["lantern works"])] == ["Lantern Works brand colour is amber"]


def test_profile_claims_become_lines_not_questions(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    rec = {"id": "note-1", "title": "Claude note", "at": "2026-10-20T00:00:00Z", "about": "notes",
           "text": "brand colour amber", "claims": [{"think": "Brand colour is amber"}], "weak": True}
    path = tmp_path / "r.jsonl"; path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    research.ingest(brain, path, as_profile=True)
    assert not list((brain / "_inbound" / "questions").glob("research-*"))
    page = next((brain / "knowledge" / "sources").rglob("notes-*.md")).read_text(encoding="utf-8")
    assert "weak: true" in page


def test_a_record_holding_a_key_never_reaches_the_brain():
    key = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    kept, kinds = research.clean([{"id": "n1", "title": "note", "text": f"deploy token {key}", "claims": []},
                                  {"id": "n2", "title": "note", "text": "brand colour amber", "claims": []}])
    assert [r["id"] for r in kept] == ["n2"] and len(kinds) == 1 and key not in " ".join(kinds)


def touch(path: Path, when: float | None = None, data: bytes = b"%PDF") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if when is not None:
        os.utime(path, (when, when))
    return path


def names(found) -> list:
    return [p.name for p in found]


def test_the_work_folders_the_words_and_the_extensions_are_the_ones_the_plan_names():
    assert research.WORK_FOLDERS == ("Documents", "Desktop", "Downloads", "OneDrive*", "Google Drive*", "Dropbox", "iCloud Drive")
    assert research.DOC_EXTS == (".pdf", ".docx", ".pptx", ".md", ".txt")
    assert research.DOC_WORDS == ("brand", "brandbook", "style guide", "huisstijl", "pitch", "deck", "company profile",
                                  "business plan", "vision", "strategy", "proposal", "offer", "org chart", "handbook",
                                  "onboarding")
    assert research.PROFILE_SECTIONS == ("company", "offering", "vision", "locations", "people", "clients", "brand",
                                         "tools", "signs_for")
    assert research.ABOUT == ("identity", "people", "registry", "code", "documents", "files", "notes", "profile")


def test_a_file_is_found_by_the_company_in_its_name_or_by_a_document_word_in_the_companys_folder(tmp_path):
    touch(tmp_path / "Documents" / "Lanternworks - pitch.pdf")                   # the company in the name
    touch(tmp_path / "Desktop" / "Lantern Works" / "Deep" / "Handbook.docx")     # a document word, in the company's folder
    touch(tmp_path / "Downloads" / "handbook.docx")                               # a document word alone: not enough
    touch(tmp_path / "Documents" / "Holiday" / "strategy.pdf")
    touch(tmp_path / "Documents" / "Lantern Works" / "photo.png")                  # not a document
    touch(tmp_path / "Documents" / "Lantern Works" / "run.exe")
    touch(tmp_path / "Documents" / "Lantern Works" / "Notes on vision.md")
    touch(tmp_path / "Documents" / "Lantern Works" / "Other things.txt")           # in the folder, but no document word
    touch(tmp_path / "Documents" / "Lantern Works" / "Television.txt")             # `vision` is a word, not letters in a word
    assert sorted(names(research.company_files(tmp_path, TERMS))) == ["Handbook.docx", "Lanternworks - pitch.pdf", "Notes on vision.md"]


def test_every_work_folder_is_searched_with_its_wildcards(tmp_path):
    for folder in ("Documents", "Desktop", "Downloads", "OneDrive - Lantern Works", "Google Drive (ada)", "Dropbox", "iCloud Drive"):
        touch(tmp_path / folder / "Lantern Works brand.pdf")
    touch(tmp_path / "Elsewhere" / "Lantern Works brand.pdf")                      # not a work folder
    touch(tmp_path / "Code" / "Documents" / "Lantern Works brand.pdf")             # nor is a folder with the name deeper down
    assert len(research.company_files(tmp_path, TERMS)) == 7


def test_the_newest_come_first_and_the_list_is_cut_at_the_limit(tmp_path):
    for n, when in enumerate((1_700_000_000, 1_800_000_000, 1_750_000_000)):
        touch(tmp_path / "Documents" / f"Lantern Works {n}.pdf", when)
    assert names(research.company_files(tmp_path, TERMS)) == ["Lantern Works 1.pdf", "Lantern Works 2.pdf", "Lantern Works 0.pdf"]
    assert names(research.company_files(tmp_path, TERMS, limit=2)) == ["Lantern Works 1.pdf", "Lantern Works 2.pdf"]


def test_the_search_never_goes_into_photos_music_app_data_library_or_hidden_folders(tmp_path):
    base = tmp_path / "Documents" / "Lantern Works"
    for folder in ("Pictures", "Photos", "Music", "Videos", "AppData", "Library", ".git", ".cache"):
        touch(base / folder / "brand.pdf")
    touch(base / "Brand.pdf")
    touch(base / ".hidden brand.pdf")
    touch(tmp_path / "Documents" / ".private" / "Lantern Works" / "brand.pdf")
    assert names(research.company_files(tmp_path, TERMS)) == ["Brand.pdf"]


def test_the_search_goes_no_deeper_than_the_limit(tmp_path):
    touch(tmp_path / "Documents" / "Lantern Works" / "a" / "b" / "c" / "Brand.pdf")        # four folders down
    touch(tmp_path / "Documents" / "Lantern Works" / "a" / "b" / "c" / "d" / "Brand 5.pdf")
    assert names(research.company_files(tmp_path, TERMS)) == ["Brand.pdf"]
    assert names(research.company_files(tmp_path, TERMS, max_depth=3)) == []
    assert sorted(names(research.company_files(tmp_path, TERMS, max_depth=5))) == ["Brand 5.pdf", "Brand.pdf"]


def test_a_folder_named_after_another_company_in_the_journal_is_not_entered(tmp_path):
    touch(tmp_path / "Documents" / "Beacon Labs" / "Lantern Works pitch.pdf")          # made for a client
    touch(tmp_path / "Documents" / "Lantern Works x Beacon Labs" / "Brand.pdf")        # ours: it holds our name
    touch(tmp_path / "Documents" / "Lantern Works" / "Beacon-Labs" / "Brand.pdf")
    assert len(research.company_files(tmp_path, TERMS)) == 3                           # the journal names no other company
    setup_journal.set_phase("research", "running", others=["Beacon Labs"])
    assert [p.parent.name for p in research.company_files(tmp_path, TERMS)] == ["Lantern Works x Beacon Labs"]
    assert len(research.company_files(tmp_path, TERMS, others=[])) == 3   # and the caller can say there is none


def test_a_link_is_not_followed(tmp_path):
    real = touch(tmp_path / "Elsewhere" / "Lantern Works brand.pdf")
    (tmp_path / "Documents").mkdir()
    try:
        (tmp_path / "Documents" / "Lantern Works brand.pdf").symlink_to(real)
        (tmp_path / "Documents" / "Lantern Works").symlink_to(real.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system gives no right to make links")
    assert research.company_files(tmp_path, TERMS) == []


class Entry:
    """A directory entry as os.scandir gives one: a symbolic link or not, the reparse tag of its own
    stat (Windows; None where there is none), and `is_junction` where Python 3.12 has it."""

    def __init__(self, link=False, tag=None, junction=None):
        self.link, self.tag = link, tag
        if junction is not None:
            self.is_junction = lambda: junction

    def is_symlink(self):
        return self.link

    def stat(self, follow_symlinks=True):
        assert follow_symlinks is False   # a link's own stat, never its target's
        return types.SimpleNamespace(**({} if self.tag is None else {"st_reparse_tag": self.tag}))


MOUNT_POINT, SYMLINK_TAG, CLOUD_FILE = 0xA0000003, 0xA000000C, 0x9000601A   # a junction, a symbolic link, a OneDrive placeholder


def test_a_symlink_or_a_junction_is_a_link_and_a_link_is_skipped_where_none_can_be_made(tmp_path, monkeypatch):
    assert research._is_link(Entry(link=True)) and research._is_link(Entry(junction=True))
    assert research._is_link(Entry(tag=MOUNT_POINT)) and research._is_link(Entry(tag=SYMLINK_TAG))   # Python 3.11: a junction is only its tag
    assert not research._is_link(Entry()) and not research._is_link(Entry(junction=False)) and not research._is_link(Entry(tag=0))
    assert not research._is_link(Entry(tag=CLOUD_FILE))   # a reparse point is not a link: OneDrive's files on demand carry one
    touch(tmp_path / "Documents" / "Lantern Works" / "Brand.pdf")
    touch(tmp_path / "Documents" / "Lantern Works brand.pdf")
    monkeypatch.setattr(research, "_is_link", lambda entry: entry.name in ("Lantern Works", "Lantern Works brand.pdf"))
    assert research.company_files(tmp_path, TERMS) == []


def test_the_search_reads_no_file_content_and_has_nothing_to_find_in_an_empty_home(tmp_path):
    touch(tmp_path / "Documents" / "Lantern Works" / "Brand.pdf")

    def opened(*a, **k):
        raise AssertionError("a file was opened")
    with pytest.MonkeyPatch.context() as m:
        m.setattr(builtins, "open", opened)
        m.setattr(Path, "open", opened)
        m.setattr(Path, "read_bytes", opened)
        m.setattr(Path, "read_text", opened)
        assert names(research.company_files(tmp_path, TERMS, others=[])) == ["Brand.pdf"]
    assert research.company_files(tmp_path / "nobody", TERMS) == [] and research.company_files(tmp_path, []) == []


# ----- Claude's notes --------------------------------------------------------------------------------
def test_notes_come_from_the_memory_of_every_project_and_the_global_instruction_only(tmp_path):
    claude = tmp_path / ".claude"
    for project in ("one", "two"):
        (claude / "projects" / project / "memory").mkdir(parents=True)
    (claude / "projects" / "one" / "memory" / "brand.md").write_text("- LANTERN WORKS signs in amber\n", encoding="utf-8")
    (claude / "projects" / "two" / "memory" / "MEMORY.md").write_text("see the lanternworks site\nnothing here\n", encoding="utf-8")
    (claude / "projects" / "two" / "notes.md").write_text("Lantern Works is not in a memory folder\n", encoding="utf-8")
    (claude / "projects" / "one" / "memory" / "data.json").write_text('{"x": "Lantern Works"}', encoding="utf-8")
    (claude / "settings.json").write_text('{"note": "Lantern Works"}', encoding="utf-8")
    (claude / "CLAUDE.md").write_text("Work for Lantern Works\n\n  indented line about lanternworks  \n", encoding="utf-8")
    got = research.claude_notes(tmp_path, TERMS)
    assert sorted(n["line"] for n in got) == ["- LANTERN WORKS signs in amber", "Work for Lantern Works",
                                             "indented line about lanternworks", "see the lanternworks site"]
    assert all(isinstance(n["path"], str) and Path(n["path"]).is_file() for n in got)
    assert {Path(n["path"]).name for n in got} == {"brand.md", "MEMORY.md", "CLAUDE.md"}
    assert research.claude_notes(tmp_path / "nobody", TERMS) == [] and research.claude_notes(tmp_path, []) == []


def test_a_note_file_that_is_too_big_or_not_text_is_left_alone_and_the_lines_are_capped(tmp_path):
    mem = tmp_path / ".claude" / "projects" / "x" / "memory"
    mem.mkdir(parents=True)
    (mem / "big.md").write_text("Lantern Works\n" * 10, encoding="utf-8")
    (mem / "binary.md").write_bytes(b"\xff\xfe Lantern Works")
    (mem / "wide.md").write_text("Lantern Works " + "x" * 2000, encoding="utf-8")
    original = research.MAX_NOTE_BYTES
    research.MAX_NOTE_BYTES = 40
    try:
        assert [Path(n["path"]).name for n in research.claude_notes(tmp_path, TERMS)] == []
    finally:
        research.MAX_NOTE_BYTES = original
    got = research.claude_notes(tmp_path, TERMS)
    assert len(got) == 11 and max(len(n["line"]) for n in got) <= research.MAX_LINE
    assert len(research.claude_notes(tmp_path, TERMS, limit=3)) == 3


# ----- ingest ----------------------------------------------------------------------------------------
def write_records(tmp_path, *records) -> Path:
    path = tmp_path / "records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


WEB = {"id": "site-about", "url": "https://lanternworks.example/about", "title": "About us", "at": "2026-10-20T08:00:00Z",
       "about": "identity", "text": "We make signs.", "claims": [{"think": "Lantern Works makes signs.", "candidate": "identity/spine.md"}]}


def test_without_the_profile_flag_a_claim_is_still_a_question(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    out = research.ingest(brain, write_records(tmp_path, WEB))
    assert (brain / "_inbound" / "questions" / "research-site-about-1.md").exists() and len(out["written"]) == 2


def test_as_profile_a_web_claim_asks_nothing_but_the_page_is_still_written(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    out = research.ingest(brain, write_records(tmp_path, WEB), as_profile=True)
    assert len(out["written"]) == 1 and not list((brain / "_inbound" / "questions").glob("research-*"))
    fm, body = pages.read(brain / out["written"][0])
    assert fm["source"] == "web" and fm["audience"] == "everyone" and fm["url"] == WEB["url"]
    assert research.ingest(brain, write_records(tmp_path, WEB), as_profile=True)["written"] == []   # run again: nothing new


def test_as_profile_the_claims_stay_on_the_source_page_as_weak_lines_and_not_as_questions(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    rec = {**WEB, "claims": [{"think": "Lantern Works makes signs."}, {"think": "  "}, {"think": "It has two offices."}]}
    out = research.ingest(brain, write_records(tmp_path, rec), as_profile=True)
    assert len(out["written"]) == 1 and not list((brain / "_inbound" / "questions").glob("research-*"))
    body = pages.read(brain / out["written"][0])[1]
    assert "Claims, not confirmed" in body and "- Lantern Works makes signs.\n- It has two offices.\n" in body
    assert body.index("Source:") < body.index("Claims, not confirmed")
    bare = research.ingest(make_space(tmp_path / "other", profile="brain", name="other"),
                           write_records(tmp_path, {**WEB, "claims": []}), as_profile=True)
    assert "Claims" not in pages.read(tmp_path / "other" / bare["written"][0])[1]   # no claim, no heading


def test_documents_files_and_notes_are_accepted_and_none_of_them_is_public(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    doc = {"id": "documents-brand-book", "url": "https://docs.google.com/document/d/abc/edit", "title": "Brand book",
           "at": "2026-09-01T00:00:00Z", "about": "documents", "text": "Amber and slate."}
    file = {"id": "files-pitch", "title": "Pitch deck.pdf", "at": "2026-08-01T00:00:00Z", "about": "files", "weak": True,
            "where": "a file on this computer: Pitch deck.pdf", "text": "Signage for ports."}
    note = {"id": "notes-1", "title": "Claude notes, brand", "at": "2026-10-20T00:00:00Z", "about": "notes", "weak": True,
            "text": "brand colour amber"}
    reg = {"id": "register-entry", "url": "https://register.example/entry", "title": "Register entry", "about": "registry",
           "text": "Registered 2019."}
    out = research.ingest(brain, write_records(tmp_path, doc, file, note, reg), as_profile=True)
    assert out["skipped"] == [] and len(out["written"]) == 4
    audience = {Path(p).stem: pages.read(brain / p)[0]["audience"] for p in out["written"]}
    assert audience == {"doc-documents-brand-book": "owner", "notes-files-pitch": "owner", "notes-notes-1": "owner",
                        "web-register-entry": "everyone"}
    body = pages.read(brain / next(p for p in out["written"] if "files-pitch" in p))[1]
    assert "a file on this computer: Pitch deck.pdf" in body and "agent's own notes" not in body
    assert [e for e in validate.validate(brain) if not e.startswith("warning:")] == []


def test_a_record_that_is_not_an_object_or_has_no_url_and_is_not_weak_is_skipped_not_fatal(tmp_path):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    path = tmp_path / "r.jsonl"
    path.write_text('[1, 2]\n"text"\nnot json\n' + json.dumps({"id": "x", "about": "documents", "title": "No link"}) + "\n"
                    + json.dumps({"id": "y", "about": "gossip", "url": "https://x.example"}) + "\n", encoding="utf-8")
    out = research.ingest(brain, path, as_profile=True)
    assert out["written"] == [] and len(out["skipped"]) == 1 and out["skipped"][0].startswith("y:")


def test_the_command_line_takes_the_profile_flag(tmp_path, capsys):
    brain = make_space(tmp_path / "brain", profile="brain", name="brain")
    assert research.main([str(brain), "ingest", str(write_records(tmp_path, WEB)), "--as-profile"]) == 0
    assert "1 written" in capsys.readouterr().out and not list((brain / "_inbound" / "questions").glob("research-*"))


# ----- clean -----------------------------------------------------------------------------------------
KEY = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def test_a_key_anywhere_in_a_record_drops_the_whole_record_and_only_the_kind_is_returned():
    records = [{"id": "in-text", "title": "t", "text": f"key {KEY}", "claims": []},
               {"id": "in-title", "title": f"key {KEY}", "text": "fine", "claims": []},
               {"id": "in-claim", "title": "t", "text": "fine", "claims": [{"think": f"it is {KEY}"}]},
               {"id": "password", "title": "t", "text": "the login\n" + "pass" + "word: hunter2-real", "claims": []},   # built here
               {"id": "clean", "title": "t", "text": "brand colour amber", "claims": [{"think": "amber"}]},
               {"id": "none", "title": None, "text": None}]
    kept, kinds = research.clean(records)
    assert [r["id"] for r in kept] == ["clean", "none"] and kinds == ["github_token", "password"]
    assert KEY not in json.dumps(kinds) and "hunter2" not in json.dumps(kinds)
    assert research.clean([]) == ([], [])


# ----- the profile lines -----------------------------------------------------------------------------
SITE_RECORD = {"id": "site-about-us", "title": "About us", "about": "identity", "at": "2026-10-24T08:00:00Z",
               "url": "https://lanternworks.example/about", "text": "We make signs."}


def test_a_profile_line_cites_a_real_record_in_a_known_section_and_nothing_else_enters():
    answer = {"profile": {
        "offering": [{"text": "Signage for ports", "source": "site-about-us"}, {"text": "Signage for ports", "source": "site-about-us"},
                     {"text": "Made up", "source": "no-such-record"}, {"text": "   ", "source": "site-about-us"},
                     {"text": f"A key {KEY}", "source": "site-about-us"}],
        "secrets": [{"text": "Not a section", "source": "site-about-us"}],
        "people": [{"text": "Ada Okafor, founder", "source": "files-1"}]}}
    files = {"id": "files-1", "title": "Pitch deck.pdf", "about": "files", "at": "2026-08-01T10:00:00Z", "weak": True, "text": "x"}
    assert research.profile_lines(answer, [SITE_RECORD, files]) == {
        "offering": [{"text": "Signage for ports", "source": "site, About us", "source_id": "site-about-us", "at": "2026-10-24"}],
        "people": [{"text": "Ada Okafor, founder", "source": "file, Pitch deck.pdf", "source_id": "files-1", "at": "2026-08-01"}]}
    assert research.profile_lines({"profile": {}}, [SITE_RECORD]) == {} and research.profile_lines({}, [SITE_RECORD]) == {}


def test_every_origin_reads_in_plain_words():
    cases = {"identity": "site, T", "people": "site, T", "registry": "register, T", "code": "code organisation, T",
             "documents": "documents, T", "files": "file, T", "notes": "T"}
    for about, label in cases.items():
        assert research.label({"about": about, "title": "T"}) == label


def test_the_register_is_known_by_the_country_of_the_domain():
    assert research.register_host("lanternworks.nl") and research.register_host("lanternworks.co.uk") == research.register_host("x.uk")
    assert research.register_host("lanternworks.example") is None and research.register_host("lanternworks") is None
    assert all(re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]+", h) for h in research.REGISTERS.values())
    assert research.web_rules("lanternworks.example") == ["WebFetch(domain:lanternworks.example)", "WebFetch(domain:www.lanternworks.example)"]
    assert research.web_rules("www.lanternworks.example") == ["WebFetch(domain:www.lanternworks.example)", "WebFetch(domain:lanternworks.example)"]
    assert research.web_rules("not a host") == [] and research.web_rules("") == []


def test_each_source_is_asked_for_its_own_records_and_the_prompts_hold_no_acting_words_for_the_file_reader():
    card = {"name": "Lantern Works", "domain": "lanternworks.example", "domains": ["lanternworks.example"]}
    assert "lanternworks.example" in research.site_prompt(card) and "Lantern Works" in research.site_prompt(card)
    assert "register.example" in research.register_prompt(card, "register.example")
    assert "lantern" in research.code_prompt(card, "lantern")
    assert "Google Drive" in research.documents_prompt(card, "Google Drive")
    assert research.files_prompt(card).count(reader.FILES_TOKEN) == 1
    merge = research.merge_prompt(card, [SITE_RECORD])
    assert "[site-about-us]" in merge and "We make signs." in merge and all(s in merge for s in research.PROFILE_SECTIONS)


# ----- the phase -------------------------------------------------------------------------------------
DRIVE = ["mcp__claude_ai_Google_Drive__search_files", "mcp__claude_ai_Google_Drive__read_file_content"]
PROBE = {"tools": [*DRIVE, "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__send_message", "Read", "Bash"],
         "mcp_servers": []}
SITE = {"name": "Lantern Works", "records": [
    {"title": "About us", "url": "https://lanternworks.example/about", "about": "identity",
     "text": "We make wayfinding and signage for ports and transit."},
    {"title": "Team", "url": "https://www.lanternworks.example/team", "about": "people", "text": "Ada Okafor, founder."}]}
DOCS = {"records": [{"title": "Brand book", "url": "https://docs.google.com/document/d/abc/edit", "at": "2026-09-01",
                     "text": "Brand colour is amber."}]}
FILES = {"records": [{"file": "01-brand-book.pdf", "text": "The pitch deck: signage for ports."}]}


def merge_answer(prompt):
    ids = re.findall(r"^\[([^\]]+)\] ", prompt, re.M)
    return {"profile": {"offering": [{"text": "Wayfinding and signage for ports and transit", "source": ids[0]}],
                        "company": [{"text": "Invented by the reader", "source": "no-such-record"}]}}


class ReaderStarted(BaseException):
    """Not an Exception: a fault in one source is noted and the others go on, but a reader nobody
    stood in for must fail the test."""


class Readers:
    """Stand-ins for `reader.read` and `reader.read_files`: each records what it was asked and
    answers by what it was given (or raises, when the answer is an exception)."""

    def __init__(self, monkeypatch, answers=None):
        self.answers = {"site": SITE, "register": {"records": []}, "code": {"records": []}, "documents": DOCS,
                        "merge": merge_answer, "files": FILES, **(answers or {})}
        self.seen = []
        monkeypatch.setattr(reader, "read", self.read)
        monkeypatch.setattr(reader, "read_files", self.read_files)

    def name(self, tools) -> str:
        if not tools:
            return "merge"
        if all(t.startswith("WebFetch(domain:") for t in tools):
            return ("code" if "github.com" in tools[0] else "register" if any(h in tools[0] for h in research.REGISTERS.values())
                    else "site")
        return "documents"

    def answer(self, name, prompt):
        got = self.answers[name]
        if isinstance(got, Exception):
            raise got
        return got(prompt) if callable(got) else got

    def read(self, prompt, tools, schema, cwd, **kw):
        name = self.name(list(tools))
        self.seen.append({"name": name, "prompt": prompt, "tools": list(tools), "schema": schema, "cwd": cwd, **kw})
        return self.answer(name, prompt)

    def read_files(self, prompt, files, schema, **kw):
        named = {f"{n:02d}-{Path(p).stem.lower().replace(' ', '-')}{Path(p).suffix}": Path(p) for n, p in enumerate(files, 1)}
        self.seen.append({"name": "files", "prompt": prompt, "files": [Path(p) for p in files], "schema": schema, **kw})
        got = self.answer("files", prompt)
        return got, named

    def asked(self, name) -> list:
        return [s for s in self.seen if s["name"] == name]


@pytest.fixture
def phase(monkeypatch):
    """The probe, the person and `claude`'s place are fakes; any reader the test does not stand in
    for fails the test, so none can start the real command line."""
    def nothing(*a, **k):
        raise ReaderStarted("a reader started")
    monkeypatch.setattr(routine, "probe_tools", lambda *a, **k: PROBE)
    monkeypatch.setattr(routine, "find_claude", lambda: "/bin/claude")
    monkeypatch.setattr(start, "identity", lambda name=None, email=None, offline=False:
                        {"name": "Ada Okafor", "email": ADA, "login": "ada-okafor"})
    monkeypatch.setattr(reader, "read", nothing)
    monkeypatch.setattr(reader, "read_files", nothing)
    return monkeypatch


def the_apps(starts_after=None, drive="work"):
    """What the apps phase left in the journal: a mailbox and a documents app, the card's start."""
    verdicts = [{"app": "Gmail", "kinds": ["mail"], "read": ["mcp__claude_ai_Gmail__search_threads"], "write": [],
                 "send": ["mcp__claude_ai_Gmail__send_message"], "account": ADA, "verdict": "work", "per_month": {}, "why": "w"},
                {"app": "Google Drive", "kinds": ["documents"], "read": DRIVE, "write": [], "send": [], "account": ADA,
                 "verdict": drive, "per_month": {}, "why": "w"}]
    when = starts_after or (dt.datetime.now().astimezone() - dt.timedelta(minutes=1))
    setup_journal.set_phase("apps", "done", verdicts=verdicts, card={"read": [], "skip": [], "not_signed_in": [],
                                                                      "starts_after": when.isoformat(timespec="seconds")})


def the_home(tmp_path):
    """A brand book in the company's folder and a note about it in Claude's memory, in the fake home."""
    touch(tmp_path / "_home" / "Documents" / "Lantern Works" / "Brand book.pdf", 1_790_000_000)
    memory = tmp_path / "_home" / ".claude" / "projects" / "x" / "memory"
    memory.mkdir(parents=True)
    (memory / "brand.md").write_text("Lantern Works brand colour is amber\nBeacon Labs invoice due\n", encoding="utf-8")


def run(capsys, *more):
    code = start.main(["research", "--json", "--company", "Lantern Works", "--email", ADA, *more])
    return code, json.loads(capsys.readouterr().out)


def journal() -> dict:
    return setup_journal.load()["research"]


def records_on_disk() -> list:
    return [json.loads(line) for line in research.records_path().read_text(encoding="utf-8").splitlines()]


def test_a_colleague_joining_a_brain_skips_the_research(phase, capsys):
    for flag in (["--invitation", "acme/brain:ada"], ["--brain", "acme/brain"]):
        code, out = run(capsys, *flag)
        assert code == 0 and out["state"] == "done" and out["skipped"] == "colleague" and out["needs"] == []
        assert journal()["state"] == "done" and journal()["skipped"] == "colleague"   # the brain has the profile already
    assert not research.records_path().exists()


def test_offline_there_is_nothing_to_research(phase, capsys):
    phase.setattr(routine, "probe_tools", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    code, out = run(capsys, "--offline")
    assert code == 0 and out["state"] == "done" and "offline" in out["why"] and not research.records_path().exists()


def test_a_dry_run_changes_nothing_and_starts_no_reader(phase, capsys):
    code, out = run(capsys, "--dry-run")
    assert code == 0 and out["state"] == "pending" and out["company"]["domain"] == "lanternworks.example"
    assert setup_journal.load() == {} and not research.research_home().exists()


def test_without_a_company_name_or_a_company_mail_address_nothing_is_researched(phase, capsys):
    phase.setattr(start, "identity", lambda name=None, email=None, offline=False: {"name": "Ada Okafor", "email": "ada@gmail.com", "login": ""})
    code = start.main(["research", "--json", "--email", "ada@gmail.com"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["state"] == "done" and out["skipped"] == "no company" and "company" in out["why"]
    assert journal()["skipped"] == "no company" and not research.records_path().exists()


def test_a_full_run_reads_each_source_with_its_own_tools_and_writes_the_records_and_the_profile(phase, capsys, tmp_path):
    the_home(tmp_path)
    the_apps()
    readers = Readers(phase)
    code, out = run(capsys)
    assert code == 0 and out["state"] == "done" and out["needs"] == []
    # the web readers get page rules for the company's own hosts and nothing else; no search, no Read
    [site] = readers.asked("site")
    assert site["tools"] == ["WebFetch(domain:lanternworks.example)", "WebFetch(domain:www.lanternworks.example)"]
    # the documents app's reader gets that app's read tools only, not the mailbox's, never a send tool
    [docs] = readers.asked("documents")
    assert docs["tools"] == DRIVE and "Google Drive" in docs["prompt"]
    # the final reader has no tool at all and reads the records, as data
    [merge] = readers.asked("merge")
    assert merge["tools"] == [] and "We make wayfinding" in merge["prompt"] and "Brand colour is amber" in merge["prompt"]
    assert all("Read" not in s["tools"] and "WebSearch" not in " ".join(s["tools"]) for s in readers.seen if "tools" in s)
    scratch = tmp_path / "_home" / ".spaces" / "readers" / "research"
    for s in readers.seen:
        if "tools" in s:
            assert s["cwd"].is_dir() and scratch in [s["cwd"], *s["cwd"].parents] and s["known_tools"] == PROBE["tools"]
            assert s["claude"] == "/bin/claude"
    # the company card the apps phase reads
    assert journal()["company"] == {"name": "Lantern Works", "domain": "lanternworks.example", "domains": ["lanternworks.example"],
                                    "domain_from": "address"}
    assert journal()["state"] == "done" and journal()["files"] == [str(tmp_path / "_home" / "Documents" / "Lantern Works" / "Brand book.pdf")]
    assert "register" in journal()["unread"] and "code" in journal()["unread"]   # no register for .example; no organisation
    # the records: web ones public, the document, the file and the note kept as what they are
    got = {r["id"]: r for r in records_on_disk()}
    assert {r["about"] for r in got.values()} == {"identity", "people", "documents", "files", "notes"}
    assert got["files-brand-book"]["weak"] is True and "url" not in got["files-brand-book"]
    assert got["site-about-us"]["url"] == "https://lanternworks.example/about" and not got["site-about-us"].get("weak")
    assert got["documents-brand-book"]["url"].startswith("https://docs.google.com/") and got["documents-brand-book"]["at"].startswith("2026-09-01")
    [note] = [r for r in got.values() if r["about"] == "notes"]
    assert note["weak"] is True and note["text"] == "Lantern Works brand colour is amber" and "Beacon" not in note["text"]
    # the profile: the line that cites a real record, with its title and date; the invented one is gone
    profile = yaml.safe_load(research.profile_path().read_text(encoding="utf-8"))
    assert list(profile) == ["offering"]
    [line] = profile["offering"]
    assert line["text"] == "Wayfinding and signage for ports and transit" and line["source"].startswith("site, ")
    assert line["source_id"] in got and re.fullmatch(r"\d{4}-\d{2}-\d{2}", line["at"])
    assert journal()["profile_lines"] == 1 and journal()["records"] == len(got)


def test_what_the_files_reader_may_use_are_the_files_the_search_found_and_it_is_told_the_company(phase, capsys, tmp_path):
    the_home(tmp_path)
    touch(tmp_path / "_home" / "Documents" / "tax 2025.pdf")
    readers = Readers(phase)
    run(capsys)
    [files] = readers.asked("files")
    assert files["files"] == [tmp_path / "_home" / "Documents" / "Lantern Works" / "Brand book.pdf"]
    assert "Lantern Works" in files["prompt"] and files["prompt"].count(reader.FILES_TOKEN) == 1
    assert files["claude"] == "/bin/claude" and files["known_tools"] == PROBE["tools"]


def test_the_register_is_read_only_where_a_register_is_known_and_the_code_organisation_only_when_it_is_one(phase, capsys, tmp_path):
    setup_journal.set_phase("signins", "done", github="ada-okafor", namespace="lantern")
    readers = Readers(phase)
    code = start.main(["research", "--json", "--company", "Lantern Works", "--email", "ada@lanternworks.nl"])
    capsys.readouterr()
    assert code == 0
    [register] = readers.asked("register")
    assert register["tools"] == [f"WebFetch(domain:{research.REGISTERS['nl']})"]
    [organisation] = readers.asked("code")
    assert organisation["tools"] == ["WebFetch(domain:github.com)"] and "github.com/lantern" in organisation["prompt"]
    assert "register" not in journal().get("unread", {}) and "code" not in journal().get("unread", {})
    # the account the person signed in with is their own, not an organisation
    setup_journal.set_phase("signins", "done", namespace="ada-okafor")
    readers = Readers(phase)
    start.main(["research", "--json", "--company", "Lantern Works", "--email", ADA, "--org", "ada-okafor"])
    capsys.readouterr()
    assert readers.asked("code") == [] and "own account" in journal()["unread"]["code"]


def test_an_app_that_is_not_work_is_not_read_for_documents(phase, capsys):
    the_apps(drive="personal")
    readers = Readers(phase)
    run(capsys)
    assert readers.asked("documents") == []


def test_a_source_that_fails_is_noted_and_the_others_go_on(phase, capsys, tmp_path):
    the_home(tmp_path)
    the_apps()
    readers = Readers(phase, {"site": reader.ReaderError("cli", "stopped after 15 minutes"),
                              "documents": reader.ReaderError("auth", "Google Drive needs the person to sign in again")})
    code, out = run(capsys)
    assert code == 0 and out["state"] == "done"
    assert journal()["unread"]["site"] == "stopped after 15 minutes"
    assert "sign in again" in journal()["unread"]["documents:Google Drive"]
    assert not {"site", "documents:Google Drive"} & set(journal()["sources_done"])   # said again, they are tried again
    assert {r["about"] for r in records_on_disk()} == {"files", "notes"}
    assert readers.asked("merge") and research.profile_path().exists()   # the profile is made from what there is


def test_a_fault_nobody_expected_in_one_source_is_a_fixed_plain_sentence_and_the_journal_keeps_its_kind(phase, capsys):
    readers = Readers(phase, {"site": RuntimeError("the page said: ghp_secret")})
    code, out = run(capsys)
    assert code == 0 and "ghp_" not in json.dumps(journal()) and journal()["unread"]["site"] == research.UNEXPECTED
    assert readers.asked("site")
    assert not re.search(r"Claude|administrator|\bIT\b", research.UNEXPECTED)   # a fault of the setup's own blames nobody
    assert journal()["faults"] == {"site": "RuntimeError"} and "faults" not in out   # the class of the fault, in the journal only
    # the same for the reader that merges
    Readers(phase)
    phase.setattr(research, "profile_lines", lambda *a, **k: 1 / 0)
    run(capsys)
    assert journal()["unread"]["profile"] == research.UNEXPECTED and journal()["faults"]["profile"] == "ZeroDivisionError"


def test_the_plans_limit_keeps_what_finished_and_a_second_run_reads_only_the_rest(phase, capsys, tmp_path):
    the_home(tmp_path)
    the_apps()
    readers = Readers(phase, {"documents": reader.ReaderError("limit", "the plan's limit is reached")})
    code, out = run(capsys)
    assert code == 0 and out["state"] == "waiting" and "limit" in out["why"] and "mcp__" not in out["why"]
    assert "documents:Google Drive" not in journal()["sources_done"] and {"site", "files", "notes"} <= set(journal()["sources_done"])
    assert not research.profile_path().exists()   # nothing is merged on half the sources
    first = len(records_on_disk())
    readers = Readers(phase)   # the limit has reset
    code, out = run(capsys)
    assert out["state"] == "done"
    assert [s["name"] for s in readers.seen] == ["documents", "merge"]   # not the site again, not the files
    assert len(records_on_disk()) == first + 1 and research.profile_path().exists()


def test_a_run_again_with_everything_read_asks_nobody(phase, capsys, tmp_path):
    the_home(tmp_path)
    the_apps()
    Readers(phase)
    run(capsys)
    before = research.records_path().read_text(encoding="utf-8")
    readers = Readers(phase)
    code, out = run(capsys)
    assert code == 0 and out["state"] == "done" and readers.seen == []
    assert research.records_path().read_text(encoding="utf-8") == before


def test_another_company_than_the_one_researched_starts_over(phase, capsys):
    readers = Readers(phase)
    run(capsys)
    first = {r["id"] for r in records_on_disk()}
    readers = Readers(phase, {"site": {"name": "Beacon Labs", "records": [
        {"title": "Home", "url": "https://beaconlabs.example/", "about": "identity", "text": "Beacon Labs builds beacons."}]}})
    code = start.main(["research", "--json", "--company", "Beacon Labs", "--email", "ada@beaconlabs.example"])
    capsys.readouterr()
    assert code == 0 and readers.asked("site") and journal()["company"]["domain"] == "beaconlabs.example"
    assert {r["id"] for r in records_on_disk()}.isdisjoint(first)


def test_records_holding_a_key_never_reach_the_records_file_and_only_the_kind_is_in_the_journal(phase, capsys, tmp_path):
    the_home(tmp_path)
    memory = tmp_path / "_home" / ".claude" / "projects" / "x" / "memory"
    (memory / "keys.md").write_text(f"Lantern Works deploy token {KEY}\n", encoding="utf-8")
    Readers(phase)
    code, out = run(capsys)
    assert code == 0 and out["state"] == "done"
    on_disk = research.records_path().read_text(encoding="utf-8")
    assert KEY not in on_disk and KEY not in json.dumps(journal()) and KEY not in research.profile_path().read_text(encoding="utf-8")
    assert journal()["dropped"] == {"records": 1, "kinds": ["github_token"]}
    assert "amber" in on_disk   # the clean note beside it is kept


def test_a_secret_in_what_a_reader_answered_is_dropped_too(phase, capsys):
    site = {"name": "Lantern Works", "records": [
        {"title": "About us", "url": "https://lanternworks.example/about", "text": f"Deploy with {KEY}"},
        {"title": "Team", "url": "https://lanternworks.example/team", "text": "Ada Okafor, founder."}]}
    Readers(phase, {"site": site})
    run(capsys)
    assert KEY not in research.records_path().read_text(encoding="utf-8")
    assert [r["title"] for r in records_on_disk()] == ["Team"] and journal()["dropped"]["records"] == 1


def test_a_record_with_a_url_off_the_hosts_the_reader_was_given_is_not_kept(phase, capsys):
    site = {"records": [{"title": "Elsewhere", "url": "https://other.example/x", "text": "Not ours."},
                        {"title": "No link", "text": "No url."},
                        {"title": "Ours", "url": "https://www.lanternworks.example/a?token=" + "a1" * 20, "text": "Ours."}]}
    Readers(phase, {"site": site})
    run(capsys)
    [only] = records_on_disk()
    assert only["title"] == "Ours" and "a1a1a1a1" not in only["url"] and "[link-token]" in only["url"]


def test_the_documents_wait_until_the_card_has_been_shown_for_ten_minutes(phase, capsys):
    soon = dt.datetime.now().astimezone() + dt.timedelta(minutes=7)
    the_apps(starts_after=soon)
    readers = Readers(phase)
    code, out = run(capsys)
    assert code == 0 and out["state"] == "waiting" and readers.asked("documents") == []
    assert "documents" in out["why"].lower() and soon.strftime("%H:%M") in out["why"] and "mcp__" not in out["why"]
    assert readers.asked("site") and "documents:Google Drive" not in journal()["sources_done"]
    assert journal()["documents_after"] == soon.isoformat(timespec="seconds")
    # said again once the time has come: only the documents are read, and the profile is made again
    the_apps(starts_after=dt.datetime.now().astimezone() - dt.timedelta(seconds=1))
    readers = Readers(phase)
    code, out = run(capsys)
    assert out["state"] == "done" and [s["name"] for s in readers.seen] == ["documents", "merge"]
    assert "documents_after" not in journal() or journal()["documents_after"] is None


def test_the_documents_wait_for_the_apps_verdicts_when_the_probe_found_a_documents_app(phase, capsys):
    """Research runs beside the apps phase. While the probe has seen a documents app that has no verdict yet, the
    documents step waits for it (Ruling R38, I-11); the rest is read, and the phase run again reads the documents."""
    drive = {"app": "Google Drive", "kinds": ["documents"], "read": DRIVE, "write": [], "send": []}
    setup_journal.set_phase("apps", "running", groups=[drive], not_signed_in=[])
    readers = Readers(phase)
    code, out = run(capsys)
    assert code == 0 and out["state"] == "waiting" and readers.asked("documents") == []
    assert "apps" in out["why"] and "mcp__" not in out["why"] and readers.asked("site")
    assert "documents:Google Drive" not in journal()["sources_done"]
    the_apps()                                                                # the apps phase is through
    readers = Readers(phase)
    code, out = run(capsys)
    assert out["state"] == "done" and [s["name"] for s in readers.seen] == ["documents", "merge"]


def test_a_probe_without_a_documents_app_does_not_hold_the_research(phase, capsys):
    mail = {"app": "Gmail", "kinds": ["mail"], "read": ["mcp__claude_ai_Gmail__search_threads"], "write": [], "send": []}
    setup_journal.set_phase("apps", "pending", groups=[mail], not_signed_in=[])
    Readers(phase)
    code, out = run(capsys)
    assert code == 0 and out["state"] == "done"



# ----- Task 13 fix round 2 (Ruling R40): a skip is never outrun, and an unfinished documents app is waited for -----
NOTION = ["mcp__claude_ai_Notion__search", "mcp__claude_ai_Notion__fetch"]


def say_skip(app: str) -> None:
    got = start.skip_apps(types.SimpleNamespace(skip=[app], workspace=None))
    assert got["skipped"] == [app], got


def test_a_skip_said_while_research_waits_for_the_card_is_kept_and_the_app_is_never_read(phase, capsys):
    """The person says "skip Google Drive" in the ten minutes `--wait` stays for: the documents step looks at the
    verdicts again after its wait, so the app is not read and nothing of it reaches the brain (I-5 a, probe P3)."""
    soon = dt.datetime.now().astimezone() + dt.timedelta(minutes=7)
    the_apps(starts_after=soon)
    clock = {"now": dt.datetime.now().astimezone(), "said": False}

    def sleep(seconds):
        if not clock["said"]:
            say_skip("Google Drive")
            clock["said"] = True
        clock["now"] += dt.timedelta(seconds=seconds)
    phase.setattr(start, "_sleep", sleep)
    phase.setattr(start, "_now", lambda: clock["now"])
    readers = Readers(phase)
    code, out = run(capsys, "--wait")
    assert code == 0 and out["state"] == "done" and clock["said"]
    assert readers.asked("documents") == [] and "documents:Google Drive" not in journal()["sources_done"]
    assert not [r for r in records_on_disk() if r.get("about") == "documents"]


def test_each_documents_app_is_looked_up_again_right_before_it_is_read(phase, capsys):
    """Two documents apps: a skip said while the first is read keeps the second from being read (I-5 a)."""
    the_apps()
    record = setup_journal.load()["apps"]
    notion = {**record["verdicts"][1], "app": "Notion", "read": NOTION}
    setup_journal.set_phase("apps", "done", verdicts=[*record["verdicts"], notion])
    said = []

    def documents(prompt):
        if not said:
            say_skip("Notion")
            said.append(prompt)
        return DOCS
    readers = Readers(phase, answers={"documents": documents})
    code, out = run(capsys)
    asked = [s["prompt"] for s in readers.asked("documents")]
    assert code == 0 and len(asked) == 1 and "Google Drive" in asked[0]
    assert "documents:Notion" not in journal()["sources_done"] and "documents:Google Drive" in journal()["sources_done"]
    assert [r for r in records_on_disk() if r.get("about") == "documents"]   # what the checks above look for is there


def test_a_skip_said_while_that_documents_app_is_read_keeps_nothing_of_it(phase, capsys):
    """Probe Q2b: "skip Google Drive" lands while Drive's own documents reader runs. What it read is dropped and the
    app is not marked read, so nothing of it reaches the records or the profile (R43, N2-2)."""
    the_apps()

    def documents(prompt):
        say_skip("Google Drive")
        return DOCS
    readers = Readers(phase, answers={"documents": documents})
    code, out = run(capsys)
    assert code == 0 and out["state"] == "done" and len(readers.asked("documents")) == 1
    assert "documents:Google Drive" not in journal()["sources_done"]
    assert not [r for r in records_on_disk() if r.get("about") == "documents"]
    assert "Brand book" not in readers.asked("merge")[0]["prompt"]


def test_an_apps_phase_stopped_with_the_documents_app_unread_holds_the_documents_step(phase, capsys):
    """The apps phase stopped at a limit before it judged the documents app (`unfinished`): its verdicts exist, but
    not the one the documents step needs, so research waits for it as before any verdict (N-4, probe P5). Skipped
    by the person, it no longer holds anything."""
    drive = {"app": "Google Drive", "kinds": ["documents"], "read": DRIVE, "write": [], "send": []}
    gmail = {"app": "Gmail", "kinds": ["mail"], "read": ["mcp__claude_ai_Gmail__search_threads"], "write": [], "send": []}
    setup_journal.set_phase("apps", "waiting", groups=[gmail, drive], not_signed_in=[], unfinished=["Google Drive"],
                            verdicts=[{**gmail, "account": ADA, "verdict": "work", "per_month": {}, "why": "w"}],
                            card={"read": [], "skip": [], "not_signed_in": [],
                                  "starts_after": (dt.datetime.now().astimezone() - dt.timedelta(minutes=1)).isoformat()})
    readers = Readers(phase)
    code, out = run(capsys)
    assert out["state"] == "waiting" and out["why"] == start.APPS_FIRST_WHY and readers.asked("documents") == []
    say_skip("Google Drive")
    record = setup_journal.load()["apps"]
    assert record["unfinished"] == [] and record["state"] == "done"
    assert next(v for v in record["verdicts"] if v["app"] == "Google Drive")["verdict"] == "skip"
    readers = Readers(phase)
    code, out = run(capsys)
    assert out["state"] == "done" and readers.asked("documents") == []

def test_with_wait_the_phase_stays_until_the_card_has_been_shown_for_ten_minutes(phase, capsys):
    soon = dt.datetime.now().astimezone() + dt.timedelta(minutes=7)
    the_apps(starts_after=soon)
    clock = {"now": dt.datetime.now().astimezone(), "slept": []}

    def sleep(seconds):
        clock["slept"].append(seconds)
        clock["now"] += dt.timedelta(seconds=seconds)
    phase.setattr(start, "_sleep", sleep)
    phase.setattr(start, "_now", lambda: clock["now"])
    readers = Readers(phase)
    code, out = run(capsys, "--wait")
    assert code == 0 and out["state"] == "done" and readers.asked("documents")
    assert 0 < sum(clock["slept"]) <= 7 * 60 + 60 and max(clock["slept"]) <= 60


# ----- the build lands it ----------------------------------------------------------------------------
def a_researched_brain(tmp_path, phase, capsys):
    """Research done on this machine; a brain the build would make, with its draft identity."""
    the_home(tmp_path)
    Readers(phase)
    run(capsys)
    brain = make_space(tmp_path / "home" / "brain", profile="brain", name="lantern-works")
    onboard.write_identity(brain, "Lantern Works", "Ada Okafor", [], [], NOW)
    return brain


def build(tmp_path, brain, *more):
    return start.main(["build", "--company", "Lantern Works", "--template", "acme/Brain", "--home", str(tmp_path / "home"),
                       "--json", "--user-instructions", str(tmp_path / "CLAUDE.md"), *more])


def test_the_build_puts_the_records_into_the_new_brain_and_drafts_the_profile(phase, capsys, tmp_path):
    brain = a_researched_brain(tmp_path, phase, capsys)
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": brain}))
    assert build(tmp_path, brain) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["state"] == "done" and built["research"]["profile"] == "identity/profile.md" and built["research"]["written"] >= 4
    sources = {p.name for p in (brain / "knowledge" / "sources").rglob("*.md")}
    assert any(n.startswith("web-site-about-us") for n in sources) and any(n.startswith("notes-notes-") for n in sources)
    text = (brain / "identity" / "profile.md").read_text(encoding="utf-8")
    assert re.search(r"- Wayfinding and signage for ports and transit \(source: site, .+, \d{4}-\d{2}-\d{2}\)", text)
    assert "Wayfinding and signage for ports and transit (source:" in (brain / "identity" / "spine.md").read_text(encoding="utf-8")
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "onboard-profile.yaml").read_text(encoding="utf-8"))["items"]
    assert [i["id"] for i in items] == ["onboard-profile"] and "Correct any line" in items[0]["text"]
    assert not list((brain / "_inbound" / "questions").glob("research-*"))   # lines, not questions
    assert [e for e in validate.validate(brain) if not e.startswith("warning:")] == []
    # said again: nothing is written twice and the one Needs-you item stays one
    assert build(tmp_path, brain) == 0
    json.loads(capsys.readouterr().out)
    items = yaml.safe_load((brain / "_inbound" / "needs-you" / "onboard-profile.yaml").read_text(encoding="utf-8"))["items"]
    assert len(items) == 1


def test_a_colleagues_build_and_a_dry_run_land_nothing(phase, capsys, tmp_path):
    brain = a_researched_brain(tmp_path, phase, capsys)
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": brain}))
    assert start.main(["build", "--invitation", "acme/brain:ada", "--json", "--home", str(tmp_path / "home")]) == 0
    capsys.readouterr()
    assert build(tmp_path, brain, "--dry-run") == 0
    assert "research" not in json.loads(capsys.readouterr().out)
    assert not (brain / "identity" / "profile.md").exists() and not list((brain / "knowledge" / "sources").rglob("*.md"))


def test_the_build_without_research_or_without_a_brain_lands_nothing(phase, capsys, tmp_path):
    brain = make_space(tmp_path / "home" / "brain", profile="brain", name="lantern-works")
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": brain}))
    assert build(tmp_path, brain) == 0       # no records on this machine
    assert "research" not in json.loads(capsys.readouterr().out) and not (brain / "identity" / "profile.md").exists()
    research.research_home().mkdir(parents=True)
    research.records_path().write_text(json.dumps(WEB) + "\n", encoding="utf-8")
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start"))
    assert build(tmp_path, brain) == 0       # the build made no brain
    assert "research" not in json.loads(capsys.readouterr().out)


def test_the_domain_flag_names_the_site_when_the_mail_address_is_a_free_one(phase, capsys):
    phase.setattr(start, "identity", lambda name=None, email=None, offline=False: {"name": "Ada Okafor", "email": "ada@gmail.com", "login": ""})
    readers = Readers(phase)
    code = start.main(["research", "--json", "--company", "Lantern Works", "--email", "ada@gmail.com", "--domain", "LanternWorks.example"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["company"]["domain"] == "lanternworks.example"
    assert readers.asked("site")[0]["tools"][0] == "WebFetch(domain:lanternworks.example)"


def test_the_company_card_is_what_the_apps_phase_judges_work_against(phase, capsys):
    import apps
    Readers(phase)
    run(capsys)
    got = apps.company_of(setup_journal.load(), None, [])
    assert got == {"company": "Lantern Works", "domains": ["lanternworks.example"]}


def test_the_site_names_the_company_when_the_person_gave_only_an_address(phase, capsys):
    Readers(phase)
    code = start.main(["research", "--json", "--email", ADA])
    capsys.readouterr()
    assert code == 0 and journal()["company"]["name"] == "Lantern Works"   # the name as the site writes it, not "Lanternworks"


def test_a_build_that_cannot_put_the_research_in_the_brain_says_so_and_is_still_done(phase, capsys, tmp_path):
    brain = a_researched_brain(tmp_path, phase, capsys)
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": brain}))
    phase.setattr(research, "ingest", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert build(tmp_path, brain) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["state"] == "done" and "could not be put into the brain" in built["research"]["error"]
    assert "disk full" not in json.dumps(built)


def test_without_the_json_flag_the_build_says_in_a_line_what_was_landed(phase, capsys, tmp_path):
    brain = a_researched_brain(tmp_path, phase, capsys)
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": brain}))
    assert start.main(["build", "--company", "Lantern Works", "--home", str(tmp_path / "home"),
                       "--user-instructions", str(tmp_path / "CLAUDE.md")]) == 0
    assert "research: 4 pages written, profile drafted" in capsys.readouterr().out


# ----- fix round 1 -----------------------------------------------------------------------------------------
def test_a_real_junction_is_not_entered_where_python_cannot_see_it_as_a_link(tmp_path):
    """Windows, Python 3.11: `is_symlink()` is False for a junction, its reparse tag is the proof."""
    try:
        import _winapi
    except ImportError:
        pytest.skip("junctions are Windows'")
    outside = touch(tmp_path / "Elsewhere" / "Lantern Works brand.pdf").parent
    (tmp_path / "Documents").mkdir()
    try:
        _winapi.CreateJunction(str(outside), str(tmp_path / "Documents" / "Lantern Works"))
    except OSError:
        pytest.skip("this system gives no right to make a junction")
    assert (tmp_path / "Documents" / "Lantern Works" / "Lantern Works brand.pdf").exists()   # the junction works
    assert research.company_files(tmp_path, TERMS, others=[]) == []
    touch(tmp_path / "Documents" / "Lantern Works brand.pdf")                                # a plain file beside it is found
    assert names(research.company_files(tmp_path, TERMS, others=[])) == ["Lantern Works brand.pdf"]


def test_a_domain_is_cut_to_the_host_without_the_scheme_the_path_the_port_or_www():
    for given, host in (("www.lanternworks.example", "lanternworks.example"), ("https://www.LanternWorks.example/about?x=1#top", "lanternworks.example"),
                        ("http://lanternworks.example:8080/", "lanternworks.example"), ("  lanternworks.example. ", "lanternworks.example"),
                        ("me@lanternworks.example", "lanternworks.example"), ("shop.lanternworks.example", "shop.lanternworks.example"),
                        ("lanternworks", ""), ("not a host", ""), ("", ""), (None, ""), ("https://", "")):
        assert research.clean_domain(given) == host, given
    assert research.domain_label("lanternworks.example") == "lanternworks" and research.domain_label("shop.lanternworks.co.uk") == "lanternworks"
    assert research.domain_label("lanternworks.nl") == "lanternworks" and research.domain_label("") == ""
    assert research.terms({"name": "Lanternworks", "domain": "lanternworks.example"}) == ["lanternworks"]


def test_a_domain_given_with_www_researches_the_company_not_a_file_called_www(phase, capsys, tmp_path):
    touch(tmp_path / "_home" / "Documents" / "www notes.pdf")
    touch(tmp_path / "_home" / "Documents" / "Lantern Works" / "Brand book.pdf")
    phase.setattr(start, "identity", lambda name=None, email=None, offline=False: {"name": "Ada Okafor", "email": "ada@gmail.com", "login": ""})
    readers = Readers(phase)
    code = start.main(["research", "--json", "--email", "ada@gmail.com", "--domain", "https://www.LanternWorks.example/about"])
    capsys.readouterr()
    assert code == 0 and readers.asked("site")[0]["tools"][0] == "WebFetch(domain:lanternworks.example)"
    assert journal()["company"]["domain"] == "lanternworks.example" and journal()["company"]["name"] == "Lantern Works"
    assert [p.name for p in readers.asked("files")[0]["files"]] == ["Brand book.pdf"]   # `www` is nobody's name


def test_a_run_again_keeps_the_name_the_site_gave_not_the_domains_label(phase, capsys):
    the_apps(starts_after=dt.datetime.now().astimezone() + dt.timedelta(minutes=7))
    Readers(phase)
    start.main(["research", "--json", "--email", ADA])
    capsys.readouterr()
    assert journal()["company"]["name"] == "Lantern Works" and "documents:Google Drive" not in journal()["sources_done"]
    the_apps(starts_after=dt.datetime.now().astimezone() - dt.timedelta(seconds=1))
    readers = Readers(phase)
    start.main(["research", "--json", "--email", ADA])
    capsys.readouterr()
    assert readers.asked("site") == [] and readers.asked("documents")   # only the documents are read this time
    assert journal()["company"]["name"] == "Lantern Works"             # the site is not read again: its name stays
    for ask in (readers.asked("documents")[0], readers.asked("merge")[0]):
        assert "Lantern Works" in ask["prompt"] and "Lanternworks" not in ask["prompt"]
    # a name the person gives wins; a company asked for that is not the one researched starts over
    Readers(phase)
    start.main(["research", "--json", "--email", ADA, "--company", "Lantern Works Ltd"])
    capsys.readouterr()
    assert journal()["company"]["name"] == "Lantern Works Ltd"


def test_other_companies_come_from_the_journal_the_organisations_and_the_mail_domains_and_their_folders_are_skipped(phase, capsys, tmp_path):
    home = tmp_path / "_home"
    touch(home / "Documents" / "Lantern Works" / "Brand book.pdf", 1_790_000_000)
    touch(home / "Documents" / "Beacon Labs" / "Lantern Works pitch.pdf", 1_800_000_000)       # made for a client with a mailbox here
    touch(home / "Documents" / "Acme Studio" / "Lantern Works deck.pdf", 1_800_000_000)        # made for an organisation of the person
    touch(home / "Documents" / "Lantern Works x Acme Studio" / "Brand.pdf", 1_780_000_000)    # ours: it holds our name
    setup_journal.set_phase("signins", "done", github="ada-okafor", namespace="lantern", orgs=[
        {"login": "lantern", "name": "Lantern Works"}, {"login": "acme-studio", "name": "Acme Studio"}, {"login": "ada-okafor", "name": ""}])
    setup_journal.set_phase("research", "running", others=["Zed Corp"])                           # one the person listed
    mailboxes = [ADA, "ada@beaconlabs.example", "ada@gmail.com", "ada@hotmail.nl", "ada@gmx.de", "ada@mail.beacon-two.example"]
    setup_journal.set_phase("apps", "done", verdicts=[
        {"app": f"Mail {n}", "kinds": ["mail"], "read": [], "write": [], "send": [], "account": a, "verdict": "work", "per_month": {}, "why": "w"}
        for n, a in enumerate(mailboxes)], card={"starts_after": (dt.datetime.now().astimezone() - dt.timedelta(minutes=1)).isoformat()})
    readers = Readers(phase)
    run(capsys)
    # lantern (ours) and the person's own account are not rivals; a free mailbox is nobody's company
    assert journal()["others"] == ["Zed Corp", "acme-studio", "Acme Studio", "beaconlabs", "beacon-two"]
    assert [(p.parent.name, p.name) for p in readers.asked("files")[0]["files"]] == [
        ("Lantern Works", "Brand book.pdf"), ("Lantern Works x Acme Studio", "Brand.pdf")]


def _signed_in(monkeypatch):
    """The doctor's rows for gh and claude are fine, and both sign-ins read back."""
    rows = {"gh": doctor.Row("gh", True, "gh 2.101.0", "gh --version"),
            "claude command line": doctor.Row("claude command line", True, "2.1.303 (Claude Code)", "claude --version")}
    monkeypatch.setattr(start.doctor, "tool_row", lambda name, m=None, fix=True: rows[name])
    monkeypatch.setattr(start.signin, "github_signed_in", lambda m: True)
    monkeypatch.setattr(start.signin, "claude_state", lambda m: "account")
    monkeypatch.setattr(start.signin, "claude_identity", lambda m: {})


def test_the_organisations_are_kept_in_the_journal_when_the_namespace_is_settled(phase, capsys):
    """The writer of `signins.orgs`: what `member_orgs` returned, kept so that research finds the other companies."""
    orgs = [{"login": "acme", "name": "Acme Studio"}, {"login": "lantern", "name": "Lantern Works"}]
    phase.setattr(start, "member_orgs", lambda: list(orgs))
    _signed_in(phase)
    phase.setattr(start, "identity", lambda name=None, email=None, offline=False: {"name": "Ada Okafor", "email": ADA, "login": "ada-okafor"})
    start.main(["signins", "--company", "Lantern Works", "--json"])
    capsys.readouterr()
    record = setup_journal.load()["signins"]
    assert record["namespace"] == "lantern" and record["orgs"] == orgs


def test_a_build_lands_only_the_research_of_the_company_it_builds(phase, capsys, tmp_path):
    the_home(tmp_path)
    Readers(phase)
    run(capsys)                                                   # the research on this machine is Lantern Works'
    other = make_space(tmp_path / "home" / "beacon", profile="brain", name="beacon-labs")
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": other}))
    assert start.main(["build", "--company", "Beacon Labs", "--home", str(tmp_path / "home"), "--json",
                       "--user-instructions", str(tmp_path / "CLAUDE.md")]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["state"] == "done" and "another company" in built["research"]["skipped"] and "written" not in built["research"]
    assert not list((other / "knowledge" / "sources").rglob("*.md")) and not (other / "identity" / "profile.md").exists()
    assert "another company" in setup_journal.load()["build"]["research_skipped"]                  # and the journal says so
    # the same records, in a brain that is Lantern Works': landed
    mine = make_space(tmp_path / "home" / "lantern", profile="brain", name="lantern-works")
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": mine}))
    assert build(tmp_path, mine) == 0
    assert json.loads(capsys.readouterr().out)["research"]["written"] >= 4


def test_the_same_company_is_told_by_the_names_the_brain_and_the_research_hold():
    card = {"name": "Lantern Works", "domain": "lanternworks.example"}
    for brain in ("lantern-works", "lanternworks", "lantern", "lantern-works-ltd", "Lantern_Works"):
        assert start._same_company(card, brain), brain
    for brain in ("beacon-labs", "", "brain", "works"):
        assert not start._same_company(card, brain), brain
    assert not start._same_company({}, "lantern-works") and not start._same_company(None, "lantern-works")
    assert start._same_company({"name": "Lanternworks", "domain": ""}, "lantern-works")
    # a short name is the same company when it is the same name; the four-letter floor is for the prefix rule only
    for name, brain in (("QXR", "qxr"), ("Zed", "zed"), ("Ono Ltd", "ono-ltd"), ("Q X R", "qxr"), ("qxr", "QXR")):
        assert start._same_company({"name": name, "domain": ""}, brain), (name, brain)
    for name, brain in (("QXR", "qxrx"), ("QXR", "qx"), ("Zed", "zedcorp"), ("QXR", "lantern-works"), ("lw", "lantern-works")):
        assert not start._same_company({"name": name, "domain": ""}, brain), (name, brain)


def test_the_build_says_what_it_did_with_the_research_not_what_it_hoped(phase, capsys, tmp_path):
    brain = a_researched_brain(tmp_path, phase, capsys)
    phase.setattr(start, "start", lambda *a, **k: onboard.Report(case="start", spaces={"brain": brain}))
    plain = ["build", "--company", "Lantern Works", "--home", str(tmp_path / "home"), "--user-instructions", str(tmp_path / "CLAUDE.md")]
    research.profile_path().write_text("{}\n", encoding="utf-8")                  # the merge found no line to cite
    assert start.main(plain) == 0
    assert "research: 4 pages written, no profile drafted" in capsys.readouterr().out
    assert not (brain / "identity" / "profile.md").exists()
    research.profile_path().write_text(yaml.safe_dump({"offering": [{"text": "Signage", "source": "site, About", "at": "2026-10-24"}]}),
                                       encoding="utf-8")
    assert start.main(plain) == 0
    assert "research: 4 already there, profile drafted" in capsys.readouterr().out   # the pages are there; the profile is new
    assert start.main(plain) == 0
    assert "research: 4 already there, profile drafted" in capsys.readouterr().out   # said again: nothing new, nothing claimed


# ===== Fix wave F2 (Ruling R45) ===================================================================================
def test_a_site_whose_reader_answered_nothing_is_unread_and_tried_again(phase, capsys, tmp_path):
    """The site's reader fetched pages and kept nothing: the site is not done, it says why plainly, and the next run
    reads it again (R45, Minor 14; dry run B M-3)."""
    the_home(tmp_path)
    the_apps()
    Readers(phase, {"site": {"records": []}})
    code, out = run(capsys)
    assert code == 0 and journal()["unread"]["site"] == start.EMPTY_SITE_WHY and "site" not in journal()["sources_done"]
    readers = Readers(phase)
    run(capsys)
    assert readers.asked("site") and "site" in journal()["sources_done"] and "site" not in (journal().get("unread") or {})


def test_the_research_uses_the_probe_the_apps_phase_just_made(phase, capsys, tmp_path):
    """One probe for both phases: the research reads the apps phase's own when it is fresh (R45; dry run B N-4)."""
    probes = []
    phase.setattr(routine, "probe_tools", lambda *a, **k: probes.append(1) or PROBE)
    assert start.main(["apps", "--probe", "--json"]) == 0
    capsys.readouterr()
    the_apps()
    readers = Readers(phase)
    code, out = run(capsys)
    assert code == 0 and len(probes) == 1 and readers.asked("site")
    assert all(s.get("known_tools") == PROBE["tools"] for s in readers.seen if s["name"] != "files")
